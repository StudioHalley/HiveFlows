"""Credential Vault — chiffrement/déchiffrement Fernet pour les credentials provider.

Usage:
    from credential_vault import encrypt, decrypt

    ciphertext = encrypt('{"api_key": "sk-ant-..."}')
    plaintext = decrypt(ciphertext)
"""
from cryptography.fernet import Fernet

from config import settings


def _get_fernet() -> Fernet:
    key = settings.credential_encryption_key
    if not key:
        raise RuntimeError(
            "CREDENTIAL_ENCRYPTION_KEY est vide. "
            "Génère une clé avec: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(plaintext: str) -> str:
    """Chiffre une chaîne en texte clair et retourne le ciphertext (base64 URL-safe)."""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Déchiffre un ciphertext Fernet et retourne le texte clair."""
    return _get_fernet().decrypt(ciphertext.encode()).decode()
