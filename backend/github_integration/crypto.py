"""Symmetric encryption for GitHub credentials stored in the database.

Fernet (AES-128-CBC + HMAC-SHA256) is used because it is authenticated: tampered ciphertext
fails to decrypt instead of producing garbage. The key lives only in the environment
(TOKEN_ENCRYPTION_KEY); losing it means users must reconnect GitHub.
"""
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


class CredentialDecryptionError(Exception):
    """Raised when a stored credential cannot be decrypted (wrong key or corrupted data)."""


def _fernet() -> Fernet:
    key = getattr(settings, "TOKEN_ENCRYPTION_KEY", "")
    if not key:
        raise ImproperlyConfigured("TOKEN_ENCRYPTION_KEY must be set to store GitHub credentials.")
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise ImproperlyConfigured("TOKEN_ENCRYPTION_KEY is not a valid Fernet key.") from exc


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise CredentialDecryptionError("Stored GitHub credential could not be decrypted.") from exc
