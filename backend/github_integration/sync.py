"""Synchronize repositories and pull requests from GitHub into PRSense's database.

GitHub remains the source of truth. Each sync is a full read of what GitHub currently exposes.
Network reads happen before any database write, so no transaction stays open during HTTP calls.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from django.conf import settings
from django.db import transaction
from django.utils.dateparse import parse_datetime

from repositories.models import PullRequest, Repository

from .client import GitHubClient, GitHubError


@dataclass(frozen=True)
class RepositorySyncResult:
    created: int
    updated: int
    deactivated: int
    total: int


@dataclass(frozen=True)
class PullRequestSyncResult:
    created: int
    updated: int
    total: int


def _require_int(data: dict, key: str) -> int:
    value = data.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise GitHubError(f"GitHub returned an unexpected value for '{key}'.")
    return value


def _require_str(data: dict, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise GitHubError(f"GitHub returned an unexpected value for '{key}'.")
    return value


def _require_timestamp(data: dict, key: str):
    parsed = parse_datetime(_require_str(data, key))
    if parsed is None:
        raise GitHubError(f"GitHub returned an invalid timestamp for '{key}'.")
    return parsed


def parse_repository(data: dict) -> dict:
    owner = data.get("owner")
    if not isinstance(owner, dict):
        raise GitHubError("GitHub returned an unexpected repository payload.")
    return {
        "github_repo_id": _require_int(data, "id"),
        "owner": _require_str(owner, "login")[:255],
        "name": _require_str(data, "name")[:255],
        "full_name": _require_str(data, "full_name")[:512],
        "default_branch": data["default_branch"][:255] if isinstance(data.get("default_branch"), str) else "",
    }


def parse_pull_request(data: dict) -> dict:
    github_state = _require_str(data, "state")
    if github_state not in {"open", "closed"}:
        raise GitHubError("GitHub returned an unknown pull request state.")
    merged_at = data.get("merged_at")
    if merged_at is not None and not isinstance(merged_at, str):
        raise GitHubError("GitHub returned an invalid merged_at value.")

    if merged_at:
        state = PullRequest.State.MERGED
    elif github_state == "open":
        state = PullRequest.State.OPEN
    else:
        state = PullRequest.State.CLOSED

    head = data.get("head") if isinstance(data.get("head"), dict) else {}
    base = data.get("base") if isinstance(data.get("base"), dict) else {}
    author = data.get("user") if isinstance(data.get("user"), dict) else {}
    return {
        "github_pr_id": _require_int(data, "id"),
        "number": _require_int(data, "number"),
        "title": _require_str(data, "title")[:512],
        "description": data.get("body") or "",
        "author": str(author.get("login") or "")[:255],
        "source_branch": str(head.get("ref") or "")[:255],
        "target_branch": str(base.get("ref") or "")[:255],
        "state": state,
        "url": str(data.get("html_url") or "")[:500],
        "created_at": _require_timestamp(data, "created_at"),
        "updated_at": _require_timestamp(data, "updated_at"),
        "merged_at": parse_datetime(merged_at) if merged_at else None,
    }


def sync_repositories(user, client: GitHubClient) -> RepositorySyncResult:
    """Sync every repository the GitHub App installation exposes to this user.

    With a GitHub App, a user's accessible repositories come from their installations, so we list
    installations first and then each installation's repositories.
    """
    fetched: dict[int, dict] = {}
    for installation in client.paginate("/user/installations", items_key="installations"):
        installation_id = _require_int(installation, "id")
        for repo_data in client.paginate(
            f"/user/installations/{installation_id}/repositories", items_key="repositories"
        ):
            parsed = parse_repository(repo_data)
            fetched[parsed["github_repo_id"]] = parsed

    created = updated = 0
    with transaction.atomic():
        for github_repo_id, fields in fetched.items():
            _, was_created = Repository.objects.update_or_create(
                user=user,
                github_repo_id=github_repo_id,
                defaults={**fields, "is_active": True},
            )
            if was_created:
                created += 1
            else:
                updated += 1
        # Repositories missing from GitHub's list are kept (their PRs stay as history) but marked inactive.
        deactivated = (
            Repository.objects.filter(user=user, is_active=True)
            .exclude(github_repo_id__in=fetched.keys())
            .update(is_active=False)
        )
    return RepositorySyncResult(created=created, updated=updated, deactivated=deactivated, total=len(fetched))


def sync_pull_requests(repository: Repository, client: GitHubClient) -> PullRequestSyncResult:
    path = f"/repos/{quote(repository.owner, safe='')}/{quote(repository.name, safe='')}/pulls"
    fetched = [
        parse_pull_request(item)
        for item in client.paginate(
            path,
            params={"state": "all", "sort": "updated", "direction": "desc"},
            max_pages=settings.GITHUB_PR_SYNC_MAX_PAGES,
        )
    ]

    created = updated = 0
    with transaction.atomic():
        for fields in fetched:
            number = fields.pop("number")
            _, was_created = PullRequest.objects.update_or_create(
                repository=repository,
                number=number,
                defaults=fields,
            )
            if was_created:
                created += 1
            else:
                updated += 1
    return PullRequestSyncResult(created=created, updated=updated, total=len(fetched))
