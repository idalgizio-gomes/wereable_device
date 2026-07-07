"""Testes unitários para `bridge/crypto_utils.py` (cifra real dos campos
sensíveis da BD -- item pendente em PROJECT_STATUS.md, secção "Base de
Dados SQL Completa" -> "Próximas fases": "Cifra real dos campos sensíveis
(derivação de chave com argon2)").

`crypto_utils.py` deriva a chave AES-256-GCM (Argon2id) UMA VEZ, à
importação do módulo, a partir das variáveis de ambiente
`CAREWEAR_DB_ENCRYPTION_KEY`/`CAREWEAR_DB_ENCRYPTION_SALT_HEX` -- por isso
estes testes usam `importlib.reload()` depois de `monkeypatch.setenv`/
`delenv`, para exercitar as duas configurações (cifra ativa/inativa) sem
depender da ordem de execução dos testes.
"""
import importlib
import os

import pytest

import crypto_utils


@pytest.fixture(autouse=True)
def _restore_module_state():
    """Garante que o módulo fica no estado 'desativado' (sem env vars) no
    fim de cada teste, para não vazar uma chave derivada para outros
    testes/ficheiros que corram a seguir na mesma sessão pytest."""
    yield
    os.environ.pop("CAREWEAR_DB_ENCRYPTION_KEY", None)
    os.environ.pop("CAREWEAR_DB_ENCRYPTION_SALT_HEX", None)
    importlib.reload(crypto_utils)


class TestEncryptionDisabled:
    """Sem as duas variáveis de ambiente, o módulo degrada para texto
    simples de forma visível -- nunca finge cifrar com uma chave previsível."""

    def test_not_configured_by_default(self, monkeypatch):
        monkeypatch.delenv("CAREWEAR_DB_ENCRYPTION_KEY", raising=False)
        monkeypatch.delenv("CAREWEAR_DB_ENCRYPTION_SALT_HEX", raising=False)
        mod = importlib.reload(crypto_utils)
        assert mod.encryption_configured() is False

    def test_encrypt_field_returns_plaintext_unchanged(self, monkeypatch):
        monkeypatch.delenv("CAREWEAR_DB_ENCRYPTION_KEY", raising=False)
        monkeypatch.delenv("CAREWEAR_DB_ENCRYPTION_SALT_HEX", raising=False)
        mod = importlib.reload(crypto_utils)
        assert mod.encrypt_field("123456789") == "123456789"

    def test_decrypt_field_returns_plaintext_unchanged(self, monkeypatch):
        monkeypatch.delenv("CAREWEAR_DB_ENCRYPTION_KEY", raising=False)
        monkeypatch.delenv("CAREWEAR_DB_ENCRYPTION_SALT_HEX", raising=False)
        mod = importlib.reload(crypto_utils)
        assert mod.decrypt_field("Rua Teste, 123") == "Rua Teste, 123"

    def test_missing_salt_alone_also_disables(self, monkeypatch):
        monkeypatch.setenv("CAREWEAR_DB_ENCRYPTION_KEY", "passphrase-de-teste")
        monkeypatch.delenv("CAREWEAR_DB_ENCRYPTION_SALT_HEX", raising=False)
        mod = importlib.reload(crypto_utils)
        assert mod.encryption_configured() is False

    def test_salt_too_short_disables(self, monkeypatch):
        monkeypatch.setenv("CAREWEAR_DB_ENCRYPTION_KEY", "passphrase-de-teste")
        monkeypatch.setenv("CAREWEAR_DB_ENCRYPTION_SALT_HEX", os.urandom(8).hex())
        mod = importlib.reload(crypto_utils)
        assert mod.encryption_configured() is False

    def test_salt_not_hex_disables(self, monkeypatch):
        monkeypatch.setenv("CAREWEAR_DB_ENCRYPTION_KEY", "passphrase-de-teste")
        monkeypatch.setenv("CAREWEAR_DB_ENCRYPTION_SALT_HEX", "isto-nao-e-hex")
        mod = importlib.reload(crypto_utils)
        assert mod.encryption_configured() is False


class TestEncryptionEnabled:
    """Com as duas variáveis de ambiente presentes, a cifra real entra em ação."""

    @pytest.fixture
    def enabled(self, monkeypatch):
        monkeypatch.setenv("CAREWEAR_DB_ENCRYPTION_KEY", "passphrase-de-teste")
        monkeypatch.setenv("CAREWEAR_DB_ENCRYPTION_SALT_HEX", os.urandom(16).hex())
        return importlib.reload(crypto_utils)

    def test_configured(self, enabled):
        assert enabled.encryption_configured() is True

    def test_roundtrip(self, enabled):
        ciphertext = enabled.encrypt_field("123456789")
        assert ciphertext != "123456789"
        assert ciphertext.startswith("enc:")
        assert enabled.decrypt_field(ciphertext) == "123456789"

    def test_roundtrip_preserves_unicode(self, enabled):
        original = "Rua das Açácias, nº 12, 3º Dto — Lisboa"
        ciphertext = enabled.encrypt_field(original)
        assert enabled.decrypt_field(ciphertext) == original

    def test_same_plaintext_yields_different_ciphertext(self, enabled):
        """Nonce aleatório por chamada -- confirma por que schema.sql deixou
        de ter UNIQUE sobre a coluna cifrada (ciphertext nunca repete)."""
        first = enabled.encrypt_field("123456789")
        second = enabled.encrypt_field("123456789")
        assert first != second
        assert enabled.decrypt_field(first) == enabled.decrypt_field(second) == "123456789"

    def test_none_stays_none(self, enabled):
        assert enabled.encrypt_field(None) is None
        assert enabled.decrypt_field(None) is None

    def test_legacy_plaintext_value_still_readable(self, enabled):
        """Um valor gravado ANTES da cifra estar configurada (sem prefixo
        'enc:') tem de continuar legível depois de a cifra ser ativada --
        nunca rebentar a ler dados antigos."""
        assert enabled.decrypt_field("999999999") == "999999999"

    def test_decrypt_fails_closed_without_key(self, enabled, monkeypatch):
        """Um valor cifrado só pode ser lido pela instância com a chave
        certa -- se a variável de ambiente desaparecer entretanto, falha
        de forma explícita (RuntimeError), nunca devolve lixo/None."""
        ciphertext = enabled.encrypt_field("123456789")
        monkeypatch.delenv("CAREWEAR_DB_ENCRYPTION_KEY", raising=False)
        disabled_again = importlib.reload(crypto_utils)
        with pytest.raises(RuntimeError):
            disabled_again.decrypt_field(ciphertext)

    def test_wrong_passphrase_fails_to_decrypt(self, enabled, monkeypatch):
        ciphertext = enabled.encrypt_field("123456789")
        monkeypatch.setenv("CAREWEAR_DB_ENCRYPTION_KEY", "outra-passphrase-completamente-diferente")
        wrong_key_module = importlib.reload(crypto_utils)
        with pytest.raises(Exception):
            wrong_key_module.decrypt_field(ciphertext)
