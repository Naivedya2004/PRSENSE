"""GitHub webhook signature verification and payload checks."""
from __future__ import annotations

import hashlib
import hmac
from typing import Any


def verify_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    """Check X-Hub-Signature-256 with a constant-time comparison. Fails closed when the secret is unset."""
    if not secret or not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def extract_repository_id(payload: dict[str, Any]) -> int | None:
    repository = payload.get("repository")
    if isinstance(repository, dict) and isinstance(repository.get("id"), int) and not isinstance(repository.get("id"), bool):
        return repository["id"]
    return None


def extract_pull_request_number(payload: dict[str, Any]) -> int | None:
    pull_request = payload.get("pull_request")
    number = payload.get("number")
    if isinstance(pull_request, dict) and isinstance(number, int) and not isinstance(number, bool):
        return number
    return None
