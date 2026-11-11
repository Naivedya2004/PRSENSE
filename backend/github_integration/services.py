"""Use cases for a user's GitHub connection: saving tokens, refreshing them, and handing out an authorized client.

Views call these functions. They never touch raw tokens themselves.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .client import (
    GitHubAuthError,
    GitHubClient,
    GitHubError,
    GitHubNotConnectedError,
    GitHubReconnectRequired,
)
from .crypto import decrypt_secret, encrypt_secret
from .models import GitHubConnection
from .oauth import TokenBundle, refresh_access_token

# Refresh slightly before expiry so a request does not start with a token that expires mid-call.
REFRESH_MARGIN = timedelta(seconds=60)


def parse_github_user(data) -> tuple[int, str]:
    if not isinstance(data, dict) or not isinstance(data.get("id"), int) or not isinstance(data.get("login"), str):
        raise GitHubError("GitHub returned an unexpected user profile.")
    return data["id"], data["login"]


@transaction.atomic
def save_connection(user, bundle: TokenBundle, github_user_id: int, github_username: str) -> GitHubConnection:
    now = timezone.now()
    connection, _ = GitHubConnection.objects.update_or_create(
        user=user,
        defaults={
            "github_user_id": github_user_id,
            "github_username": github_username,
            "access_token_encrypted": encrypt_secret(bundle.access_token),
            "refresh_token_encrypted": encrypt_secret(bundle.refresh_token) if bundle.refresh_token else "",
            "access_token_expires_at": now + timedelta(seconds=bundle.expires_in) if bundle.expires_in else None,
            "refresh_token_expires_at": (
                now + timedelta(seconds=bundle.refresh_token_expires_in)
                if bundle.refresh_token_expires_in
                else None
            ),
            "needs_reconnect": False,
        },
    )
    return connection


def mark_needs_reconnect(user) -> None:
    GitHubConnection.objects.filter(user=user).update(needs_reconnect=True)


def _refresh_if_needed(connection_id: int) -> GitHubConnection:
    """Refresh the access token under a row lock so concurrent requests do not both spend the refresh token.

    Failures are recorded inside the transaction, but the exception is raised only after it commits.
    Raising inside the atomic block would roll back the needs_reconnect flag.
    """
    failure: str | None = None
    with transaction.atomic():
        connection = GitHubConnection.objects.select_for_update().get(pk=connection_id)
        if connection.needs_reconnect:
            failure = "GitHub must be reconnected to continue."
        else:
            expires_at = connection.access_token_expires_at
            if expires_at is None or expires_at > timezone.now() + REFRESH_MARGIN:
                return connection

            refresh_expired = (
                connection.refresh_token_expires_at is not None
                and connection.refresh_token_expires_at <= timezone.now()
            )
            if not connection.refresh_token_encrypted or refresh_expired:
                connection.needs_reconnect = True
                connection.save(update_fields=["needs_reconnect", "updated_at"])
                failure = "GitHub access has expired. Reconnect GitHub to continue."
            else:
                try:
                    bundle = refresh_access_token(decrypt_secret(connection.refresh_token_encrypted))
                except GitHubAuthError:
                    connection.needs_reconnect = True
                    connection.save(update_fields=["needs_reconnect", "updated_at"])
                    failure = "GitHub access was revoked. Reconnect GitHub to continue."
                else:
                    now = timezone.now()
                    connection.access_token_encrypted = encrypt_secret(bundle.access_token)
                    if bundle.refresh_token:
                        connection.refresh_token_encrypted = encrypt_secret(bundle.refresh_token)
                    connection.access_token_expires_at = (
                        now + timedelta(seconds=bundle.expires_in) if bundle.expires_in else None
                    )
                    connection.refresh_token_expires_at = (
                        now + timedelta(seconds=bundle.refresh_token_expires_in)
                        if bundle.refresh_token_expires_in
                        else None
                    )
                    connection.save()
                    return connection

    raise GitHubReconnectRequired(failure)


@contextmanager
def authorized_client(user):
    """Yield a GitHubClient for the user's connection.

    If GitHub rejects the stored token during the block, the connection is marked as needing reconnection,
    and the caller receives GitHubReconnectRequired instead of a raw 401.
    """
    connection = GitHubConnection.objects.filter(user=user).only("id").first()
    if connection is None:
        raise GitHubNotConnectedError("Connect a GitHub account first.")

    connection = _refresh_if_needed(connection.pk)
    client = GitHubClient(decrypt_secret(connection.access_token_encrypted))
    try:
        yield client
    except GitHubAuthError as exc:
        mark_needs_reconnect(user)
        raise GitHubReconnectRequired("GitHub access was revoked. Reconnect GitHub to continue.") from exc
    finally:
        client.close()
