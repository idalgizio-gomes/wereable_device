#!/usr/bin/env python3
"""Cifra AES-256-GCM dos campos sensíveis da BD (NIF, morada); chave derivada com Argon2id.
Sem CAREWEAR_DB_ENCRYPTION_KEY/SALT_HEX definidas, degrada para texto simples com aviso."""

from __future__ import annotations

import base64
import os

from argon2.low_level import Type, hash_secret_raw
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_KEY_ENV = "CAREWEAR_DB_ENCRYPTION_KEY"
_SALT_ENV = "CAREWEAR_DB_ENCRYPTION_SALT_HEX"
_PREFIX = "enc:"  # distingue valores cifrados por este módulo de texto simples legado

# Argon2id recomendado pela OWASP para derivação de chave (não hashing de password)
_ARGON2_TIME_COST = 3
_ARGON2_MEMORY_COST_KIB = 65536
_ARGON2_PARALLELISM = 4
_KEY_LEN = 32  # AES-256


def _derive_key() -> bytes | None:
    passphrase = os.environ.get(_KEY_ENV)
    salt_hex = os.environ.get(_SALT_ENV)
    if not passphrase or not salt_hex:
        return None
    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        print(f"[DB] AVISO: {_SALT_ENV} nao e' hexadecimal valido — ignorada")
        return None
    if len(salt) < 16:
        print(f"[DB] AVISO: {_SALT_ENV} tem menos de 16 bytes — ignorada (sal fraco)")
        return None
    return hash_secret_raw(
        secret=passphrase.encode("utf-8"),
        salt=salt,
        time_cost=_ARGON2_TIME_COST,
        memory_cost=_ARGON2_MEMORY_COST_KIB,
        parallelism=_ARGON2_PARALLELISM,
        hash_len=_KEY_LEN,
        type=Type.ID,
    )


_ENCRYPTION_KEY = _derive_key()
if _ENCRYPTION_KEY is None:
    print(
        f"[DB] AVISO: {_KEY_ENV}/{_SALT_ENV} nao definidas — campos sensiveis "
        "(NIF, morada) ficam em texto simples na base de dados. Para gerar "
        f"um sal novo: python3 -c \"import os; print(os.urandom(16).hex())\""
    )


def encryption_configured() -> bool:
    """Indica se a cifra real está ativa (ambas as variáveis de ambiente presentes)."""
    return _ENCRYPTION_KEY is not None


def get_encryption_key() -> bytes | None:
    """Devolve a chave AES-256 já derivada, para reutilização por outros módulos (ex.: db_at_rest.py
    para cifra do ficheiro .db em repouso). None se as variáveis de ambiente não estiverem definidas."""
    return _ENCRYPTION_KEY


def encrypt_field(plaintext: str | None) -> str | None:
    """Cifra uma string sensível. Devolve texto simples se a cifra não estiver configurada."""
    if plaintext is None:
        return None
    if _ENCRYPTION_KEY is None:
        return plaintext
    aesgcm = AESGCM(_ENCRYPTION_KEY)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return _PREFIX + base64.b64encode(nonce + ciphertext).decode("ascii")


def decrypt_field(stored_value: str | None) -> str | None:
    """Decifra um valor de encrypt_field(); sem o prefixo enc: trata como texto simples legado."""
    if stored_value is None:
        return None
    if not stored_value.startswith(_PREFIX):
        return stored_value
    if _ENCRYPTION_KEY is None:
        raise RuntimeError(
            f"Valor cifrado encontrado mas {_KEY_ENV}/{_SALT_ENV} nao estao "
            "configuradas nesta instância — impossível decifrar."
        )
    raw = base64.b64decode(stored_value[len(_PREFIX):])
    nonce, ciphertext = raw[:12], raw[12:]
    aesgcm = AESGCM(_ENCRYPTION_KEY)
    return aesgcm.decrypt(nonce, ciphertext, None).decode("utf-8")
