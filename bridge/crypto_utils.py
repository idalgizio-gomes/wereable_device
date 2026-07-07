#!/usr/bin/env python3
"""
crypto_utils.py — Cifra real dos campos sensíveis da BD (NIF, morada).

Contexto: storage_advanced.py já marcava `Patient.nif_encrypted`/
`address_encrypted` como "encriptado" na documentação, mas guardava os
valores em texto simples — este módulo fecha essa lacuna (ver
PROJECT_STATUS.md, "Próximas fases" da Base de Dados SQL Completa).

Desenho:
  - Chave derivada com Argon2id (`argon2-cffi`) a partir de uma frase-passe
    (`CAREWEAR_DB_ENCRYPTION_KEY`) + sal (`CAREWEAR_DB_ENCRYPTION_SALT_HEX`,
    hex de pelo menos 16 bytes) — ambas variáveis de ambiente, nunca no
    código-fonte, mesmo padrão já usado para `CAREWEAR_AES_KEY_HEX` no
    bridge BLE.
  - Cifra por campo com AES-256-GCM (autenticada — ao contrário do
    AES-CTR usado no streaming BLE, aqui a latência de um MAC completo por
    campo não é um problema, por isso não há razão para abrir mão da
    autenticação).
  - Sem as duas variáveis de ambiente configuradas, degrada de forma
    visível: `encrypt_field()` devolve o texto simples (com aviso único no
    arranque), nunca finge cifrar com uma chave previsível — mesma decisão
    já tomada para a cifra BLE quando `CAREWEAR_AES_KEY_HEX` está ausente.
"""

from __future__ import annotations

import base64
import os

from argon2.low_level import Type, hash_secret_raw
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_KEY_ENV = "CAREWEAR_DB_ENCRYPTION_KEY"
_SALT_ENV = "CAREWEAR_DB_ENCRYPTION_SALT_HEX"
_PREFIX = "enc:"  # distingue valores cifrados por este módulo de texto simples legado

# Parâmetros Argon2id recomendados pela OWASP para derivação de chave
# (não hashing de password): time_cost=3, memory_cost=64MiB, parallelism=4.
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
    """Decifra um valor guardado por `encrypt_field()`.

    Valores sem o prefixo `enc:` são tratados como texto simples legado
    (guardados antes da cifra estar configurada) e devolvidos tal como
    estão — nunca rebenta ao ler dados antigos.
    """
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
