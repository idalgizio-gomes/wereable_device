from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Optional

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Session, relationship

from storage_advanced import Base, User

SESSION_TTL = timedelta(hours=12)
_ph = PasswordHasher()


class UserSession(Base):
    __tablename__ = "user_sessions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    token_hash = Column(String(64), unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime)
    last_used_at = Column(DateTime)

    user = relationship("User")


def hash_password(plaintext: str) -> str:
    return _ph.hash(plaintext)


def verify_password(plaintext: str, password_hash: str) -> bool:
    try:
        return _ph.verify(password_hash, plaintext)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def _hash_token(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def login(db: Session, email: str, password: str) -> Optional[str]:
    user = db.query(User).filter(User.email == email, User.deleted_at.is_(None)).first()
    if user is None:
        return None
    if not verify_password(password, user.password_hash):
        return None
    plaintext = secrets.token_urlsafe(32)
    db.add(UserSession(
        user_id=user.id,
        token_hash=_hash_token(plaintext),
        expires_at=datetime.utcnow() + SESSION_TTL,
    ))
    db.commit()
    return plaintext


def resolve_session(db: Session, presented: Optional[str]) -> Optional[User]:
    if not presented:
        return None
    row = (
        db.query(UserSession)
        .filter(UserSession.token_hash == _hash_token(presented))
        .first()
    )
    if row is None or row.revoked_at is not None:
        return None
    if row.expires_at < datetime.utcnow():
        return None
    row.last_used_at = datetime.utcnow()
    return row.user


def logout(db: Session, presented: Optional[str]) -> None:
    if not presented:
        return
    row = (
        db.query(UserSession)
        .filter(UserSession.token_hash == _hash_token(presented))
        .first()
    )
    if row is not None and row.revoked_at is None:
        row.revoked_at = datetime.utcnow()
        db.commit()


def _cli():
    import click

    from storage_advanced import get_db_session

    @click.group()
    def cli():
        pass

    @cli.command()
    @click.option("--email", required=True)
    def set_password(email):
        password = click.prompt("Password", hide_input=True, confirmation_prompt=True)
        db = get_db_session()
        try:
            user = db.query(User).filter(User.email == email).first()
            if user is None:
                raise click.ClickException(f"Utilizador não encontrado: {email}")
            user.password_hash = hash_password(password)
            db.commit()
            click.echo(f"Password atualizada para {email}.")
        finally:
            db.close()

    cli()


if __name__ == "__main__":
    _cli()
