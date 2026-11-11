"""GitHub App user-to-server OAuth: authorization URL, code exchange, token refresh, and one-time state."""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlencode

import httpx
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import client as github_client
from .client import GitHubAuthError, GitHubError, GitHubUnavailableError
from .models import OAuthState

OAUTH_STATE_TTL = timedelta(minutes=10)


class OAuthStateError(Exception):
    """The callback state is missing, unknown, expired, or already used."""


@dataclass(frozen=True, repr=False)
class TokenBundle:
    access_token: str
    expires_in: int | None
    refresh_token: str | None
    refresh_token_expires_in: int | None

    def __repr__(self) -> str:
        return "<TokenBundle [redacted]>"


def build_authorization_url(state: str) -> str:
    params = {
        "client_id": settings.GITHUB_CLIENT_ID,
        "redirect_uri": settings.GITHUB_REDIRECT_URI,
        "state": state,
    }
    return f"{settings.GITHUB_WEB_BASE_URL}/login/oauth/authorize?{urlencode(params)}"


def _hash_state(raw_state: str) -> str:
    return hashlib.sha256(raw_state.encode()).hexdigest()


def create_oauth_state(user) -> str:
    """Create a single-use state for `user`. Only its hash is stored; the raw value goes to the browser once."""
    raw_state = secrets.token_urlsafe(32)
    now = timezone.now()
    OAuthState.objects.filter(user=user, expires_at__lt=now).delete()
    OAuthState.objects.create(user=user, state_hash=_hash_state(raw_state), expires_at=now + OAUTH_STATE_TTL)
    return raw_state


def consume_oauth_state(raw_state: str):
    """Atomically mark a state as used and return its user. A replayed or expired state raises OAuthStateError."""
    if not raw_state:
        raise OAuthStateError("Missing OAuth state.")
    now = timezone.now()
    state_hash = _hash_state(raw_state)
    with transaction.atomic():
        # A conditional UPDATE is race-safe: if two callbacks arrive together, only one row changes.
        updated = OAuthState.objects.filter(
            state_hash=state_hash, used_at__isnull=True, expires_at__gt=now
        ).update(used_at=now)
        if updated != 1:
            raise OAuthStateError("OAuth state is invalid, expired, or already used.")
        return OAuthState.objects.select_related("user").get(state_hash=state_hash).user


def _post_to_token_endpoint(data: dict[str, str]) -> TokenBundle:
    body = {
        **data,
        "client_id": settings.GITHUB_CLIENT_ID,
        "client_secret": settings.GITHUB_CLIENT_SECRET,
    }
    try:
        with httpx.Client(
            timeout=httpx.Timeout(settings.GITHUB_HTTP_TIMEOUT_SECONDS, connect=5.0),
            transport=github_client.get_transport(),
            follow_redirects=False,
        ) as http:
            response = http.post(
                f"{settings.GITHUB_WEB_BASE_URL}/login/oauth/access_token",
                data=body,
                headers={"Accept": "application/json"},
            )
    except httpx.HTTPError as exc:
        raise GitHubUnavailableError("GitHub could not be reached. Try again later.") from exc

    if response.status_code >= 500:
        raise GitHubUnavailableError("GitHub is temporarily unavailable. Try again later.", status_code=response.status_code)
    try:
        payload = response.json()
    except ValueError as exc:
        raise GitHubError("GitHub returned a malformed token response.") from exc
    if not isinstance(payload, dict):
        raise GitHubError("GitHub returned a malformed token response.")

    if "error" in payload:
        # Only the stable error code is surfaced. The description is not, because it can change and is not for users.
        raise GitHubAuthError(f"GitHub did not issue a token ({payload.get('error')}). Reconnect GitHub and try again.")

    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise GitHubError("GitHub token response did not contain an access token.")
    refresh_token = payload.get("refresh_token")
    return TokenBundle(
        access_token=access_token,
        expires_in=_optional_int(payload.get("expires_in")),
        refresh_token=refresh_token if isinstance(refresh_token, str) and refresh_token else None,
        refresh_token_expires_in=_optional_int(payload.get("refresh_token_expires_in")),
    )


def _optional_int(value) -> int | None:
    return value if isinstance(value, int) and value > 0 else None


def exchange_code(code: str) -> TokenBundle:
    return _post_to_token_endpoint(
        {"code": code, "redirect_uri": settings.GITHUB_REDIRECT_URI}
    )


def refresh_access_token(refresh_token: str) -> TokenBundle:
    return _post_to_token_endpoint({"grant_type": "refresh_token", "refresh_token": refresh_token})
