"""HTTP layer for GitHub integration. Business logic lives in services.py, oauth.py, sync.py, and webhooks.py."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from urllib.parse import urlencode

from django.conf import settings
from django.db import IntegrityError, transaction
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from api.pagination import StandardPagination
from repositories.models import Repository
from repositories.serializers import RepositorySerializer

from .client import (
    GitHubClient,
    GitHubError,
    GitHubNotConnectedError,
    GitHubNotFoundError,
    GitHubPermissionError,
    GitHubRateLimitError,
    GitHubReconnectRequired,
    GitHubUnavailableError,
)
from .models import GitHubConnection, WebhookEvent
from .oauth import (
    OAuthStateError,
    build_authorization_url,
    consume_oauth_state,
    create_oauth_state,
    exchange_code,
)
from .services import authorized_client, parse_github_user, save_connection
from .sync import sync_pull_requests, sync_repositories
from .webhooks import extract_pull_request_number, extract_repository_id, verify_signature

logger = logging.getLogger(__name__)

SUPPORTED_EVENTS = {"pull_request"}
MAX_DELIVERY_ID_LENGTH = 128

_ERROR_STATUS = [
    (GitHubNotConnectedError, status.HTTP_409_CONFLICT),
    (GitHubReconnectRequired, status.HTTP_409_CONFLICT),
    (GitHubRateLimitError, status.HTTP_429_TOO_MANY_REQUESTS),
    (GitHubPermissionError, status.HTTP_403_FORBIDDEN),
    (GitHubNotFoundError, status.HTTP_404_NOT_FOUND),
    (GitHubUnavailableError, status.HTTP_502_BAD_GATEWAY),
]


def github_error_response(exc: GitHubError) -> Response:
    """Map a GitHub integration error to a safe, consistent API error response."""
    http_status = next((code for kind, code in _ERROR_STATUS if isinstance(exc, kind)), status.HTTP_502_BAD_GATEWAY)
    return Response(
        {"error": {"code": exc.code, "message": str(exc), "details": None}},
        status=http_status,
    )


def _frontend_redirect(outcome: str, reason: str | None = None) -> HttpResponseRedirect:
    params = {"status": outcome}
    if reason:
        params["reason"] = reason
    return HttpResponseRedirect(f"{settings.FRONTEND_URL}/github/callback?{urlencode(params)}")


class GitHubConnectView(APIView):
    """Returns the GitHub authorization URL. The frontend navigates there.

    This is JSON rather than a 302 because the PRSense JWT is sent in an Authorization header,
    and a browser navigation cannot carry that header without putting the token in a URL.
    """

    def get(self, request):
        state = create_oauth_state(request.user)
        return Response({"authorization_url": build_authorization_url(state)})


class GitHubCallbackView(APIView):
    """Browser redirect target from GitHub. Authenticated by the single-use state, not by a JWT."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        if request.query_params.get("error"):
            return _frontend_redirect("error", "github_denied")

        code = request.query_params.get("code")
        raw_state = request.query_params.get("state")
        if not code or not raw_state:
            return _frontend_redirect("error", "invalid_callback")

        try:
            user = consume_oauth_state(raw_state)
        except OAuthStateError:
            return _frontend_redirect("error", "invalid_state")

        try:
            bundle = exchange_code(code)
            with GitHubClient(bundle.access_token) as client:
                github_user_id, github_username = parse_github_user(client.get_json("/user"))
            save_connection(user, bundle, github_user_id, github_username)
        except GitHubError as exc:
            logger.warning("GitHub connection failed for user_id=%s (%s)", user.pk, exc.code)
            return _frontend_redirect("error", exc.code)

        return _frontend_redirect("connected")


class GitHubConnectionStatusView(APIView):
    def get(self, request):
        connection = GitHubConnection.objects.filter(user=request.user).first()
        if connection is None:
            return Response({"connected": False, "github_username": None, "needs_reconnect": False})
        return Response(
            {
                "connected": True,
                "github_username": connection.github_username,
                "needs_reconnect": connection.needs_reconnect,
            }
        )


class GitHubRepositoriesView(APIView):
    """Synchronize repositories from GitHub, then return the user's repositories.

    The spec names this a GET. It has a side effect (it writes to the database), so it is a
    candidate for POST in a later revision. Documented in the API section of the README.
    """

    def get(self, request):
        try:
            with authorized_client(request.user) as client:
                summary = sync_repositories(request.user, client)
        except GitHubError as exc:
            return github_error_response(exc)

        queryset = (
            Repository.objects.filter(user=request.user, is_active=True)
            .order_by("full_name")
        )
        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        response = paginator.get_paginated_response(RepositorySerializer(page, many=True).data)
        response.data["sync"] = asdict(summary)
        return response


class SyncPullRequestsView(APIView):
    def post(self, request, repository_id: int):
        # Ownership check: a repository owned by someone else is indistinguishable from a missing one.
        repository = get_object_or_404(Repository, pk=repository_id, user=request.user)
        if not repository.is_active:
            return Response(
                {"error": {"code": "repository_inactive", "message": "This repository is no longer accessible on GitHub.", "details": None}},
                status=status.HTTP_409_CONFLICT,
            )
        try:
            with authorized_client(request.user) as client:
                summary = sync_pull_requests(repository, client)
        except GitHubError as exc:
            return github_error_response(exc)
        return Response(asdict(summary), status=status.HTTP_200_OK)


class GitHubWebhookView(APIView):
    """Receives GitHub webhook deliveries.

    Authentication is the HMAC signature, not a user session. The body is read raw because the signature
    covers the exact bytes. Accepted deliveries are recorded and acknowledged with 202. Expensive
    processing will run asynchronously in Phase 7; this phase only records the event.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        body = request.body
        if not verify_signature(settings.GITHUB_WEBHOOK_SECRET, body, request.headers.get("X-Hub-Signature-256")):
            logger.warning("Rejected webhook delivery with invalid signature")
            return Response({"error": {"code": "invalid_signature", "message": "Invalid signature.", "details": None}}, status=401)

        delivery_id = request.headers.get("X-GitHub-Delivery", "")
        event_type = request.headers.get("X-GitHub-Event", "")
        if not delivery_id or len(delivery_id) > MAX_DELIVERY_ID_LENGTH or not event_type:
            return Response({"error": {"code": "invalid_headers", "message": "Missing or invalid GitHub headers.", "details": None}}, status=400)

        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return Response({"error": {"code": "invalid_payload", "message": "Payload is not valid JSON.", "details": None}}, status=400)
        if not isinstance(payload, dict):
            return Response({"error": {"code": "invalid_payload", "message": "Payload must be a JSON object.", "details": None}}, status=400)

        if WebhookEvent.objects.filter(github_delivery_id=delivery_id).exists():
            return Response({"status": "duplicate"}, status=status.HTTP_200_OK)

        if event_type not in SUPPORTED_EVENTS:
            # Recorded (so the delivery is idempotent) but not processed.
            WebhookEvent.objects.create(
                github_delivery_id=delivery_id,
                event_type=event_type[:64],
                payload={},
                status=WebhookEvent.Status.IGNORED,
            )
            return Response({"status": "ignored"}, status=status.HTTP_200_OK)

        if event_type == "pull_request" and (
            extract_repository_id(payload) is None or extract_pull_request_number(payload) is None
        ):
            return Response({"error": {"code": "invalid_payload", "message": "Pull request payload is missing required fields.", "details": None}}, status=400)

        repository = None
        if event_type == "pull_request":
            matches = list(Repository.objects.filter(github_repo_id=extract_repository_id(payload))[:2])
            # Only link when unambiguous. The same GitHub repo may be tracked by several users.
            repository = matches[0] if len(matches) == 1 else None

        try:
            with transaction.atomic():
                WebhookEvent.objects.create(
                    github_delivery_id=delivery_id,
                    event_type=event_type,
                    repository=repository,
                    payload=payload,
                    status=WebhookEvent.Status.RECEIVED,
                )
        except IntegrityError:
            # A concurrent delivery with the same ID won the race on the unique constraint.
            return Response({"status": "duplicate"}, status=status.HTTP_200_OK)

        return Response({"status": "received"}, status=status.HTTP_202_ACCEPTED)
