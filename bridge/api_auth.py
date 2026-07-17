#!/usr/bin/env python3
"""
api_auth.py — Autenticação por-utilizador (API-002) e rate limiting (API-003)
para a API REST (`api.py`).

Nasce da decisão registada em SECURITY_STATUS.md (API-002/API-003): a chave
estática partilhada `CAREWEAR_API_KEY` do protótipo é exatamente o vetor a
eliminar — uma única chave, sem rotação, sem por-utilizador, sem forma de
revogar um cuidador sem invalidar todos. Aqui vive:

  * O modelo `ApiKey` (uma chave por linha, por utilizador, revogável).
  * `generate_api_key` / `resolve_api_key` — geração e resolução por hash.
  * `RateLimitMiddleware` — janela deslizante em memória, escrito à mão (sem
    dependência nova, como o SECURITY_STATUS.md recomenda).
  * Um CLI mínimo (`create` / `revoke`) para provisionar chaves — substitui o
    bootstrap por variável de ambiente.

**Porque é que o modelo vive AQUI e não em `storage_advanced.py`**: para não
tocar em `storage_advanced.py` (pertence a outro lote de trabalho), o modelo
novo importa a `Base` partilhada e regista-se no mesmo registo de mappers.
Como `api.py` importa este módulo, tanto `create_all_tables()` como o
`drop_all`/`create_all` dos testes apanham a tabela `api_keys` sem qualquer
alteração a `storage_advanced.py`.
"""
from __future__ import annotations

import collections
import hashlib
import secrets
import time
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Session, relationship

# A Base partilhada — importá-la (em vez de criar outra) é o que garante que o
# mapper `ApiKey` fica no mesmo registo que `User`, `Patient`, etc., e que a
# tabela é criada/apagada em conjunto com as restantes.
from storage_advanced import Base, User, get_db_session

# Prefixo legível das chaves emitidas — permite distingui-las à vista e é o
# valor cujos primeiros 8 chars servem de "bucket" no rate limiter.
API_KEY_PREFIX = "cw_"


# ============================================================
# MODELO
# ============================================================

class ApiKey(Base):
    """Chave de API por-utilizador (API-002).

    Guardamos apenas o SHA-256 hex da chave completa (`key_hash`), nunca a
    chave em claro — se a base de dados vazar, as chaves não são recuperáveis.
    A revogação é por linha (`revoked_at`), o que satisfaz o requisito de
    rotação do API-002: revogar um cuidador é preencher `revoked_at` numa
    linha, sem afetar as chaves dos outros.
    """
    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    # SHA-256 hex tem exatamente 64 chars.
    key_hash = Column(String(64), unique=True, nullable=False)
    label = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)
    revoked_at = Column(DateTime)  # NULL = ativa; preenchido = revogada
    last_used_at = Column(DateTime)

    user = relationship("User")


# ============================================================
# GERAÇÃO / RESOLUÇÃO
# ============================================================

def _hash_key(plaintext: str) -> str:
    """SHA-256 hex da chave apresentada."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def generate_api_key() -> tuple[str, str]:
    """Gera uma chave nova.

    Devolve `(plaintext, key_hash)`. O `plaintext` (`cw_` + 32 bytes de
    entropia em base64-url) é mostrado UMA única vez ao operador; só o
    `key_hash` é persistido.
    """
    plaintext = API_KEY_PREFIX + secrets.token_urlsafe(32)
    return plaintext, _hash_key(plaintext)


def _resolve_api_key_row(db: Session, presented: Optional[str]) -> Optional[ApiKey]:
    """Devolve a linha `ApiKey` ativa correspondente ao valor apresentado.

    Faz o lookup por `key_hash` (SHA-256 do valor apresentado) filtrando
    `revoked_at IS NULL`. **Não usa `hmac.compare_digest`** — e isso é
    intencional, não um esquecimento: a comparação é feita pela BD sobre o
    hash de um valor de 256 bits imprevisível (não uma string curta e
    adivinhável), pelo que um ataque de temporização não dá vantagem
    nenhuma ao atacante (não há prefixo "parcialmente certo" a otimizar — ou
    tem a chave inteira ou não tem). Ver SECURITY_STATUS.md, API-002.
    """
    if not presented:
        return None
    key_hash = _hash_key(presented)
    return (
        db.query(ApiKey)
        .filter(ApiKey.key_hash == key_hash, ApiKey.revoked_at.is_(None))
        .first()
    )


def resolve_api_key(db: Session, presented: Optional[str]) -> Optional[User]:
    """Resolve o valor apresentado para o `User` dono da chave (ou `None`)."""
    row = _resolve_api_key_row(db, presented)
    return row.user if row is not None else None


# ============================================================
# RATE LIMITING (API-003) — middleware ASGI, janela deslizante em memória
# ============================================================

class RateLimitMiddleware:
    """Rate limiter ASGI puro por janela deslizante (60s) em memória.

    Contadores separados por `(ip, prefixo-da-chave)` e por classe de método:
    leitura (GET/HEAD) 60/min, escrita (POST/PUT/PATCH/DELETE) 10/min. O
    `/health` é isento (não expõe dados). Ao exceder devolve 429 com
    `Retry-After` (segundos até o timestamp mais antigo sair da janela).

    Porque corre ANTES da autenticação (é o middleware mais externo por
    natureza ASGI), também trava a força-bruta à própria chave — a
    preocupação explícita do API-003.

    Padrão "tentativa rejeitada não empurra a janela" (mesmo espírito do
    `_prune_stale_fragments` de `ble_bridge.py`, mas NÃO o seu
    `_check_write_rate_limit`, que é intervalo-mínimo fixo, não janela
    deslizante): um pedido que já leva 429 **não** é acrescentado ao deque,
    para que um atacante a martelar o endpoint não mantenha a janela cheia
    para sempre. Higiene de memória: quando um deque fica vazio, a sua chave
    é removida do dicionário.

    `clock` é injetável (por omissão `time.monotonic`) para que os testes não
    dependam de `sleep` reais.
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
                # Higiene de memória: nada recente para este bucket.
                del self._hits[bucket]
                dq = None

        if dq is not None and len(dq) >= limit:
            # Excedido — NÃO empurra a janela (o pedido 429 não conta).
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


# ============================================================
# CLI
# ============================================================

def _cli():
    import click

    @click.group()
    def cli():
        """Gestão de chaves de API (API-002)."""

    @cli.command()
    @click.option("--email", required=True, help="Email do utilizador dono da chave.")
    @click.option("--label", default=None, help="Rótulo descritivo da chave.")
    def create(email, label):
        """Cria uma chave nova para um utilizador; imprime-a UMA vez."""
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
        """Revoga (por linha) uma chave existente."""
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
