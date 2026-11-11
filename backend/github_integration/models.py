from django.conf import settings
from django.db import models


class OAuthState(models.Model):
    """One-time, short-lived OAuth state. Only a SHA-256 hash of the random value is stored.

    The state binds a GitHub callback to the PRSense user who started the flow, without trusting
    any identifier that comes back from the browser.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="github_oauth_states")
    state_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    used_at = models.DateTimeField(null=True, blank=True)


class GitHubConnection(models.Model):
    """GitHub account linked to one PRSense user. Tokens are stored encrypted and never serialized."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="github_connection")
    github_user_id = models.BigIntegerField(db_index=True)
    github_username = models.CharField(max_length=255)
    access_token_encrypted = models.TextField()
    refresh_token_encrypted = models.TextField(blank=True)
    access_token_expires_at = models.DateTimeField(null=True, blank=True)
    refresh_token_expires_at = models.DateTimeField(null=True, blank=True)
    needs_reconnect = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"GitHub connection for {self.github_username}"

    def __repr__(self) -> str:
        # Deliberately excludes credentials so they cannot leak through logs or debug output.
        return f"<GitHubConnection user_id={self.user_id} github_username={self.github_username!r}>"


class WebhookEvent(models.Model):
    """Audit record for each accepted GitHub webhook delivery. github_delivery_id makes deliveries idempotent."""

    class Status(models.TextChoices):
        RECEIVED = "RECEIVED", "Received"
        IGNORED = "IGNORED", "Ignored"

    github_delivery_id = models.CharField(max_length=128, unique=True)
    event_type = models.CharField(max_length=64, db_index=True)
    repository = models.ForeignKey(
        "repositories.Repository",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="webhook_events",
    )
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=10, choices=Status.choices)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
