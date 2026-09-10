#!/usr/bin/env python3
"""Autenticação por-utilizador (API-002) e rate limiting (API-003) para a API REST (api.py)."""
from __future__ import annotations

import collections
import hashlib
import secrets
import time
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Session, relationship

# Base partilhada: mantém ApiKey no mesmo registo de mappers que User/Patient etc.
from storage_advanced import Base, User, get_db_session

API_KEY_PREFIX = "cw_"


class ApiKey(Base):
    """Chave de API por-utilizador (API-002). Guarda só o hash SHA-256, nunca a chave em claro."""
    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    key_hash = Column(String(64), unique=True, nullable=False)  # SHA-256 hex, 64 chars
    label = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)
    revoked_at = Column(DateTime)  # NULL = ativa
    last_used_at = Column(DateTime)

    user = relationship("User")


def _hash_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def generate_api_key() -> tuple[str, str]:
    """Devolve (plaintext, key_hash); plaintext só é mostrado uma vez."""
    plaintext = API_KEY_PREFIX + secrets.token_urlsafe(32)
    return plaintext, _hash_key(plaintext)


def _resolve_api_key_row(db: Session, presented: Optional[str]) -> Optional[ApiKey]:
    # lookup por hash de 256 bits imprevisível, sem prefixo adivinhável — sem risco de timing attack
    if not presented:
        return None
    key_hash = _hash_key(presented)
    return (
        db.query(ApiKey)
        .filter(ApiKey.key_hash == key_hash, ApiKey.revoked_at.is_(None))
        .first()
    )


def resolve_api_key(db: Session, presented: Optional[str]) -> Optional[User]:
    row = _resolve_api_key_row(db, presented)
    return row.user if row is not None else None


class RateLimitMiddleware:
    """Rate limiter ASGI por janela deslizante (60s) em memória, por (ip, prefixo-da-chave, leitura/escrita).

    Corre antes da autenticação para também travar força-bruta à chave. Pedidos rejeitados (429)
    não empurram a janela, e buckets vazios são removidos do dicionário.
    """

    WINDOW_SECONDS = 60
    READ_METHODS = frozenset({"GET", "HEAD"})
    READ_LIMIT = 60
    WRITE_LIMIT = 10
    EXEMPT_PATHS = frozenset({"/health"})

    def __init__(self, app, clock=time.monotonic):
        self.app = app
        self._clock = clock
        # (ip, key_prefix, kind) -> deque[timestamp]
        self._hits: dict[tuple, collections.deque] = {}

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "GET").upper()
        if path in self.EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return

        ip = "-"
        client = scope.get("client")
        if client:
            ip = client[0]

        key_prefix = "-"
        for name, value in scope.get("headers", []):
            if name == b"x-api-key":
                presented = value.decode("latin-1")
                if presented:
                    key_prefix = presented[:8]
                break

        is_read = method in self.READ_METHODS
        limit = self.READ_LIMIT if is_read else self.WRITE_LIMIT
        bucket = (ip, key_prefix, "read" if is_read else "write")

        now = self._clock()
        cutoff = now - self.WINDOW_SECONDS
        dq = self._hits.get(bucket)
        if dq is not None:
            while dq and dq[0] <= cutoff:
                dq.popleft()
            if not dq:
                del self._hits[bucket]
                dq = None

        if dq is not None and len(dq) >= limit:
            retry_after = int(dq[0] + self.WINDOW_SECONDS - now)
            if retry_after < 1:
                retry_after = 1
            await self._send_429(send, retry_after)
            return

        if dq is None:
            dq = collections.deque()
            self._hits[bucket] = dq
        dq.append(now)

        await self.app(scope, receive, send)

    @staticmethod
    async def _send_429(send, retry_after: int):
        body = b'{"detail":"Demasiados pedidos"}'
        await send({
            "type": "http.response.start",
            "status": 429,
            "headers": [
                (b"content-type", b"application/json"),
                (b"retry-after", str(retry_after).encode("ascii")),
            ],
        })
        await send({"type": "http.response.body", "body": body})


def _cli():
    import click

    @click.group()
    def cli():
        """Gestão de chaves de API (API-002)."""

    @cli.command()
    @click.option("--email", required=True, help="Email do utilizador dono da chave.")
    @click.option("--label", default=None, help="Rótulo descritivo da chave.")
    def create(email, label):
        db = get_db_session()
        try:
            user = db.query(User).filter(User.email == email).first()
            if user is None:
                raise click.ClickException(f"Utilizador não encontrado: {email}")
            plaintext, key_hash = generate_api_key()
            db.add(ApiKey(user_id=user.id, key_hash=key_hash, label=label))
            db.commit()
            click.echo("Chave criada (guarde-a agora, não será mostrada de novo):")
            click.echo(plaintext)
        finally:
            db.close()

    @cli.command()
    @click.option("--id", "key_id", required=True, type=int, help="ID da chave a revogar.")
    def revoke(key_id):
        db = get_db_session()
        try:
            row = db.get(ApiKey, key_id)
            if row is None:
                raise click.ClickException(f"Chave não encontrada: id={key_id}")
            if row.revoked_at is not None:
                click.echo(f"Chave id={key_id} já estava revogada em {row.revoked_at.isoformat()}.")
                return
            row.revoked_at = datetime.utcnow()
            db.commit()
            click.echo(f"Chave id={key_id} revogada.")
        finally:
            db.close()

    cli()


if __name__ == "__main__":
    _cli()
