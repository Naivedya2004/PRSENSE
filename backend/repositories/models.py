from django.conf import settings
from django.db import models


class Repository(models.Model):
    """A GitHub repository that a PRSense user has synchronized.

    GitHub's numeric repository ID is the external identity. The same GitHub repository
    can appear once per PRSense user, because each user has their own access and sync state.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="repositories")
    github_repo_id = models.BigIntegerField()
    owner = models.CharField(max_length=255)
    name = models.CharField(max_length=255)
    full_name = models.CharField(max_length=512)
    default_branch = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True, help_text="False when GitHub no longer exposes this repo to the user.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["full_name"]
        constraints = [
            models.UniqueConstraint(fields=["user", "github_repo_id"], name="uniq_repository_per_user"),
        ]
        indexes = [models.Index(fields=["user", "is_active"], name="repo_user_active_idx")]

    def __str__(self) -> str:
        return self.full_name


class PullRequest(models.Model):
    class State(models.TextChoices):
        OPEN = "OPEN", "Open"
        CLOSED = "CLOSED", "Closed"
        MERGED = "MERGED", "Merged"

    repository = models.ForeignKey(Repository, on_delete=models.CASCADE, related_name="pull_requests")
    github_pr_id = models.BigIntegerField(unique=True)
    number = models.PositiveIntegerField()
    title = models.CharField(max_length=512)
    description = models.TextField(blank=True)
    author = models.CharField(max_length=255, blank=True)
    source_branch = models.CharField(max_length=255, blank=True)
    target_branch = models.CharField(max_length=255, blank=True)
    state = models.CharField(max_length=10, choices=State.choices)
    url = models.URLField(max_length=500, blank=True)
    created_at = models.DateTimeField(help_text="Timestamp from GitHub.")
    updated_at = models.DateTimeField(help_text="Timestamp from GitHub.")
    merged_at = models.DateTimeField(null=True, blank=True)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-number"]
        constraints = [
            models.UniqueConstraint(fields=["repository", "number"], name="uniq_pr_number_per_repository"),
        ]
        indexes = [models.Index(fields=["repository", "state"], name="pr_repo_state_idx")]

    def __str__(self) -> str:
        return f"{self.repository.full_name}#{self.number}"
