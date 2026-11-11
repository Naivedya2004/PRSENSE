"""Low-level HTTP client for the GitHub REST API.

Design points:
- Every request has a timeout. Connect time is capped separately.
- Transient failures (network errors, 5xx) are retried with exponential backoff, a bounded number of times.
- Rate-limit responses (429, or 403 with x-ratelimit-remaining: 0) are reported as a distinct error.
- Pagination follows only the `next` Link header, and only when it points at the configured API host.
  This stops a malicious or buggy response from sending our token to another server.
- Error messages are safe for users. Response bodies and tokens are never included in messages or logs.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from typing import Any

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

TRANSIENT_STATUS_CODES = frozenset({500, 502, 503, 504})


class GitHubError(Exception):
    """Base class. `code` is a stable machine-readable identifier used by the API layer."""

    code = "github_error"

    def __init__(self, message: str = "GitHub request failed.", *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class GitHubAuthError(GitHubError):
    code = "github_auth_failed"


class GitHubPermissionError(GitHubError):
    code = "github_permission_denied"


class GitHubNotFoundError(GitHubError):
    code = "github_not_found"


class GitHubRateLimitError(GitHubError):
    code = "github_rate_limited"


class GitHubUnavailableError(GitHubError):
    code = "github_unavailable"


class GitHubNotConnectedError(GitHubError):
    code = "github_not_connected"


class GitHubReconnectRequired(GitHubError):
    code = "github_reconnect_required"


def get_transport() -> httpx.BaseTransport | None:
    """Hook for tests: they replace this to route requests to httpx.MockTransport. Production uses None."""
    return None


class GitHubClient:
    """Authenticated client for one GitHub user token. Use as a context manager to close the connection pool."""

    def __init__(
        self,
        access_token: str,
        *,
        max_retries: int = 2,
        backoff_seconds: float = 0.5,
        sleep=time.sleep,
    ) -> None:
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds
        self._sleep = sleep
        self._api_base = settings.GITHUB_API_BASE_URL
        self._http = httpx.Client(
            base_url=self._api_base,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {access_token}",
                "X-GitHub-Api-Version": settings.GITHUB_API_VERSION,
                "User-Agent": "PRSense",
            },
            timeout=httpx.Timeout(settings.GITHUB_HTTP_TIMEOUT_SECONDS, connect=5.0),
            transport=get_transport(),
            follow_redirects=False,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def _send(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        attempt = 0
        while True:
            try:
                response = self._http.get(path, params=params)
            except httpx.HTTPError as exc:
                # Timeouts and connection errors. We log the exception class only, never the URL with query data.
                if attempt < self._max_retries:
                    attempt += 1
                    self._sleep(self._backoff_seconds * (2 ** (attempt - 1)))
                    continue
                logger.warning("GitHub request to %s failed (%s)", path, type(exc).__name__)
                raise GitHubUnavailableError("GitHub could not be reached. Try again later.") from exc

            if response.status_code in TRANSIENT_STATUS_CODES and attempt < self._max_retries:
                attempt += 1
                self._sleep(self._backoff_seconds * (2 ** (attempt - 1)))
                continue
            return response

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.is_success:
            return
        status = response.status_code
        logger.info("GitHub responded with HTTP %s", status)
        if status == 429 or (status == 403 and response.headers.get("x-ratelimit-remaining") == "0"):
            raise GitHubRateLimitError("GitHub rate limit reached. Try again later.", status_code=status)
        if status == 401:
            raise GitHubAuthError("GitHub rejected the stored credentials.", status_code=status)
        if status == 403:
            raise GitHubPermissionError("GitHub denied access to this resource.", status_code=status)
        if status == 404:
            raise GitHubNotFoundError("The GitHub resource was not found or is not accessible.", status_code=status)
        if status >= 500:
            raise GitHubUnavailableError("GitHub is temporarily unavailable. Try again later.", status_code=status)
        raise GitHubError(f"GitHub returned an unexpected HTTP {status} response.", status_code=status)

    @staticmethod
    def _parse_json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise GitHubError("GitHub returned a malformed response.") from exc

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = self._send(path, params)
        self._raise_for_status(response)
        return self._parse_json(response)

    def paginate(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        items_key: str | None = None,
        max_pages: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield every item across pages. Some endpoints wrap the list in an object; pass items_key for those."""
        next_url: str | None = path
        request_params = {"per_page": 100, **(params or {})}
        pages_fetched = 0
        while next_url:
            response = self._send(next_url, request_params)
            self._raise_for_status(response)
            payload = self._parse_json(response)
            items = payload.get(items_key) if items_key and isinstance(payload, dict) else payload
            if not isinstance(items, list):
                raise GitHubError("GitHub returned an unexpected response shape.")
            for item in items:
                if not isinstance(item, dict):
                    raise GitHubError("GitHub returned an unexpected response shape.")
                yield item

            pages_fetched += 1
            if max_pages is not None and pages_fetched >= max_pages:
                return
            next_link = response.links.get("next", {}).get("url")
            if next_link and not next_link.startswith(self._api_base + "/"):
                # Never follow a pagination link to another host: it would receive our bearer token.
                raise GitHubError("GitHub returned a pagination link to an unexpected host.")
            next_url = next_link
            request_params = None
