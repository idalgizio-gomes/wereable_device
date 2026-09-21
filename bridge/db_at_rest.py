#!/usr/bin/env python3
"""GDPR-005 — cifra do ficheiro SQLite inteiro em repouso (AES-256-GCM), reaproveitando a
mesma chave derivada (Argon2id) de crypto_utils.py via CAREWEAR_DB_ENCRYPTION_KEY/SALT_HEX.

SQLCipher (cifra nativa ao nível do motor) não tem wheel pré-compilada para Python 3.14 no
Windows e este ambiente não tem toolchain C para o compilar a partir do código-fonte — por
isso a cifra é feita ao nível do ficheiro: o .db cifrado (sufixo .enc) é decifrado para um
ficheiro de trabalho em texto simples no arranque, e recifrado em checkpoints periódicos e no
encerramento controlado. Isto deixa uma janela em texto simples em disco durante a sessão ativa
e em caso de crash antes do checkpoint seguinte — ver SECURITY_STATUS.md (GDPR-005)."""

from __future__ import annotations

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from crypto_utils import encryption_configured, get_encryption_key

_NONCE_LEN = 12


def encrypt_db_file(plain_path: str, enc_path: str) -> None:
    """Cifra plain_path para enc_path. Sem chave configurada ou sem ficheiro, não faz nada."""
    key = get_encryption_key()
    if key is None or not os.path.exists(plain_path):
        return
    with open(plain_path, "rb") as f:
        data = f.read()
    aesgcm = AESGCM(key)
    nonce = os.urandom(_NONCE_LEN)
    ciphertext = aesgcm.encrypt(nonce, data, None)
    tmp_path = enc_path + ".tmp"
    with open(tmp_path, "wb") as f:
        f.write(nonce + ciphertext)
    os.replace(tmp_path, enc_path)


def decrypt_db_file(enc_path: str, plain_path: str) -> None:
    """Decifra enc_path para plain_path. Sem chave configurada ou sem ficheiro cifrado, não faz nada
    (deixa o plain_path como estiver — primeira execução antes de existir qualquer .enc)."""
    key = get_encryption_key()
    if key is None or not os.path.exists(enc_path):
        return
    with open(enc_path, "rb") as f:
        raw = f.read()
    nonce, ciphertext = raw[:_NONCE_LEN], raw[_NONCE_LEN:]
    aesgcm = AESGCM(key)
    data = aesgcm.decrypt(nonce, ciphertext, None)
    tmp_path = plain_path + ".tmp"
    with open(tmp_path, "wb") as f:
        f.write(data)
    os.replace(tmp_path, plain_path)


def is_db_encryption_configured() -> bool:
    return encryption_configured()
