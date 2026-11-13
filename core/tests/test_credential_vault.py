"""Tests — credential_vault encrypt/decrypt round-trip (Phase 5 Bloc A5)."""
import os

import pytest
from cryptography.fernet import Fernet


# On injecte une clé de test avant d'importer credential_vault
@pytest.fixture(autouse=True)
def set_encryption_key(monkeypatch):
    test_key = Fernet.generate_key().decode()
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", test_key)
    # Forcer la re-lecture de la config (pydantic-settings lit depuis os.environ)
    import importlib
    import core.config as config_module
    config_module.settings = config_module.Settings()
    yield
    # Nettoyage : reset settings
    config_module.settings = config_module.Settings()


def test_encrypt_returns_string():
    from credential_vault import encrypt
    result = encrypt("hello")
    assert isinstance(result, str)
    assert result != "hello"


def test_round_trip():
    from credential_vault import encrypt, decrypt
    plaintext = '{"api_key": "sk-ant-test-123"}'
    assert decrypt(encrypt(plaintext)) == plaintext


def test_round_trip_json_object():
    from credential_vault import encrypt, decrypt
    import json
    data = {"api_key": "sk-or-v1-abc", "base_url": "https://openrouter.ai/api/v1"}
    plaintext = json.dumps(data)
    assert json.loads(decrypt(encrypt(plaintext))) == data


def test_different_ciphertexts_same_plaintext():
    """Fernet produit un ciphertext différent à chaque appel (IV aléatoire)."""
    from credential_vault import encrypt
    c1 = encrypt("secret")
    c2 = encrypt("secret")
    assert c1 != c2


def test_missing_key_raises():
    import importlib
    import core.config as config_module
    config_module.settings = config_module.Settings(_env_file=None, credential_encryption_key="")
    import credential_vault
    importlib.reload(credential_vault)
    with pytest.raises(RuntimeError, match="CREDENTIAL_ENCRYPTION_KEY"):
        credential_vault.encrypt("test")
