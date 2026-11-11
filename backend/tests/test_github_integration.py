import hashlib
import hmac
import json
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from github_integration.client import GitHubClient, GitHubError
from github_integration.crypto import decrypt_secret, encrypt_secret
from github_integration.models import GitHubConnection, OAuthState, WebhookEvent
from github_integration.oauth import OAuthStateError, TokenBundle, consume_oauth_state, create_oauth_state
from github_integration.services import save_connection
from github_integration.webhooks import verify_signature
from repositories.models import PullRequest, Repository

from .base import GitHubTestCase

User = get_user_model()
PASSWORD = "correct-horse-9"
CALLBACK = "/api/github/callback/"
SECRET_TOKEN = "gho_super_secret_value_123"


def make_user(name="octo", email=None):
    return User.objects.create_user(username=name, email=email or f"{name}@example.com", password=PASSWORD)


def connect(user, token=SECRET_TOKEN, expires_in=None, refresh=None, refresh_expires_in=None):
    bundle = TokenBundle(token, expires_in, refresh, refresh_expires_in)
    return save_connection(user, bundle, 42, "octocat")


def repo_payload(repo_id, name="hello", owner="octo"):
    return {
        "id": repo_id,
        "name": name,
        "full_name": f"{owner}/{name}",
        "owner": {"login": owner},
        "default_branch": "main",
    }


def pr_payload(pr_id, number, state="open", merged_at=None, title="Change"):
    return {
        "id": pr_id,
        "number": number,
        "title": title,
        "body": "desc",
        "state": state,
        "merged_at": merged_at,
        "created_at": "2026-01-01T10:00:00Z",
        "updated_at": "2026-01-02T10:00:00Z",
        "html_url": f"https://github.test/octo/hello/pull/{number}",
        "user": {"login": "dev"},
        "head": {"ref": "feature"},
        "base": {"ref": "main"},
    }


class OAuthFlowTests(GitHubTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user()
        self.api = APIClient()

    def _grant_token_and_profile(self):
        self.fake.route(
            "POST",
            "/login/oauth/access_token",
            (200, {"access_token": SECRET_TOKEN, "expires_in": 28800, "refresh_token": "ghr_refresh_1", "refresh_token_expires_in": 15897600}, None),
        )
        self.fake.route("GET", "/user", (200, {"id": 42, "login": "octocat"}, None))

    def test_connect_requires_authentication(self):
        self.assertEqual(APIClient().get("/api/github/connect/").status_code, 401)

    def test_connect_returns_authorization_url_and_stores_only_state_hash(self):
        self.api.force_authenticate(user=self.user)
        response = self.api.get("/api/github/connect/")
        self.assertEqual(response.status_code, 200)
        url = urlparse(response.json()["authorization_url"])
        self.assertEqual(url.netloc, "github.test")
        params = parse_qs(url.query)
        self.assertEqual(params["client_id"], ["test-client-id"])
        raw_state = params["state"][0]
        stored = OAuthState.objects.get(user=self.user)
        self.assertEqual(stored.state_hash, hashlib.sha256(raw_state.encode()).hexdigest())
        self.assertNotEqual(stored.state_hash, raw_state)

    def test_callback_success_saves_encrypted_token_and_redirects_without_token(self):
        raw_state = create_oauth_state(self.user)
        self._grant_token_and_profile()
        response = APIClient().get(CALLBACK, {"code": "good-code", "state": raw_state})

        self.assertEqual(response.status_code, 302)
        location = response["Location"]
        self.assertTrue(location.startswith("http://frontend.test/github/callback?status=connected"))
        self.assertNotIn("gho_", location)
        self.assertNotIn("ghr_", location)

        connection = GitHubConnection.objects.get(user=self.user)
        self.assertEqual(connection.github_username, "octocat")
        self.assertNotIn(SECRET_TOKEN, connection.access_token_encrypted)
        self.assertEqual(decrypt_secret(connection.access_token_encrypted), SECRET_TOKEN)
        self.assertEqual(decrypt_secret(connection.refresh_token_encrypted), "ghr_refresh_1")

    def test_callback_state_cannot_be_replayed(self):
        raw_state = create_oauth_state(self.user)
        self._grant_token_and_profile()
        APIClient().get(CALLBACK, {"code": "c1", "state": raw_state})
        replay = APIClient().get(CALLBACK, {"code": "c2", "state": raw_state})
        self.assertIn("reason=invalid_state", replay["Location"])

    def test_expired_state_is_rejected(self):
        raw_state = create_oauth_state(self.user)
        OAuthState.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        with self.assertRaises(OAuthStateError):
            consume_oauth_state(raw_state)

    def test_callback_rejects_missing_or_unknown_state_and_provider_errors(self):
        self.assertIn("reason=invalid_callback", APIClient().get(CALLBACK, {"code": "x"})["Location"])
        self.assertIn("reason=invalid_state", APIClient().get(CALLBACK, {"code": "x", "state": "unknown"})["Location"])
        self.assertIn("reason=github_denied", APIClient().get(CALLBACK, {"error": "access_denied"})["Location"])
        self.assertFalse(GitHubConnection.objects.exists())

    def test_failed_code_exchange_stores_nothing_and_reports_safe_reason(self):
        raw_state = create_oauth_state(self.user)
        self.fake.route("POST", "/login/oauth/access_token", (200, {"error": "bad_verification_code"}, None))
        response = APIClient().get(CALLBACK, {"code": "stale", "state": raw_state})
        self.assertIn("reason=github_auth_failed", response["Location"])
        self.assertFalse(GitHubConnection.objects.exists())

    def test_connection_status_never_exposes_tokens(self):
        connect(self.user)
        self.api.force_authenticate(user=self.user)
        body = self.api.get("/api/github/connection/").json()
        self.assertEqual(body, {"connected": True, "github_username": "octocat", "needs_reconnect": False})
        self.assertNotIn(SECRET_TOKEN, json.dumps(body))


class RepositorySyncTests(GitHubTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user()
        connect(self.user)
        self.api = APIClient()
        self.api.force_authenticate(user=self.user)

    def test_sync_creates_repositories_and_is_idempotent(self):
        self.fake.route("GET", "/user/installations", (200, {"installations": [{"id": 7}]}, None))
        self.fake.route(
            "GET",
            "/user/installations/7/repositories",
            (200, {"repositories": [repo_payload(100), repo_payload(101, name="other")]}, None),
        )
        first = self.api.get("/api/github/repositories/")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["sync"], {"created": 2, "updated": 0, "deactivated": 0, "total": 2})

        second = self.api.get("/api/github/repositories/")
        self.assertEqual(second.json()["sync"]["created"], 0)
        self.assertEqual(Repository.objects.filter(user=self.user).count(), 2)

    def test_repository_missing_from_github_is_deactivated_not_deleted(self):
        Repository.objects.create(user=self.user, github_repo_id=999, owner="octo", name="gone", full_name="octo/gone")
        self.fake.route("GET", "/user/installations", (200, {"installations": [{"id": 7}]}, None))
        self.fake.route("GET", "/user/installations/7/repositories", (200, {"repositories": [repo_payload(100)]}, None))
        response = self.api.get("/api/github/repositories/")
        self.assertEqual(response.json()["sync"]["deactivated"], 1)
        self.assertFalse(Repository.objects.get(github_repo_id=999).is_active)

    def test_sync_without_connection_returns_clear_conflict(self):
        other = make_user("nogh", "nogh@example.com")
        api = APIClient()
        api.force_authenticate(user=other)
        response = api.get("/api/github/repositories/")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "github_not_connected")

    def test_github_401_marks_connection_for_reconnect(self):
        self.fake.route("GET", "/user/installations", (401, {"message": "Bad credentials"}, None))
        response = self.api.get("/api/github/repositories/")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "github_reconnect_required")
        self.assertTrue(GitHubConnection.objects.get(user=self.user).needs_reconnect)

    def test_rate_limit_is_reported_as_429(self):
        self.fake.route(
            "GET",
            "/user/installations",
            (403, {"message": "rate limited"}, {"x-ratelimit-remaining": "0"}),
        )
        response = self.api.get("/api/github/repositories/")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["error"]["code"], "github_rate_limited")

    def test_transient_503_is_retried_then_succeeds(self):
        self.fake.route(
            "GET",
            "/user/installations",
            (503, {}, None),
            (200, {"installations": []}, None),
        )
        response = self.api.get("/api/github/repositories/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.fake.calls.count(("GET", "/user/installations")), 2)

    def test_expired_access_token_is_refreshed_and_rotated(self):
        connection = GitHubConnection.objects.get(user=self.user)
        connection.access_token_expires_at = timezone.now() - timedelta(minutes=1)
        connection.refresh_token_encrypted = encrypt_secret("ghr_old")
        connection.refresh_token_expires_at = timezone.now() + timedelta(days=30)
        connection.save()
        self.fake.route(
            "POST",
            "/login/oauth/access_token",
            (200, {"access_token": "gho_new", "expires_in": 28800, "refresh_token": "ghr_rotated"}, None),
        )
        self.fake.route("GET", "/user/installations", (200, {"installations": []}, None))
        response = self.api.get("/api/github/repositories/")
        self.assertEqual(response.status_code, 200)
        connection.refresh_from_db()
        self.assertEqual(decrypt_secret(connection.access_token_encrypted), "gho_new")
        self.assertEqual(decrypt_secret(connection.refresh_token_encrypted), "ghr_rotated")

    def test_revoked_refresh_token_requires_reconnect(self):
        connection = GitHubConnection.objects.get(user=self.user)
        connection.access_token_expires_at = timezone.now() - timedelta(minutes=1)
        connection.refresh_token_encrypted = encrypt_secret("ghr_revoked")
        connection.save()
        self.fake.route("POST", "/login/oauth/access_token", (200, {"error": "bad_refresh_token"}, None))
        response = self.api.get("/api/github/repositories/")
        self.assertEqual(response.json()["error"]["code"], "github_reconnect_required")
        connection.refresh_from_db()
        self.assertTrue(connection.needs_reconnect)


class PullRequestSyncTests(GitHubTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user()
        connect(self.user)
        self.repo = Repository.objects.create(
            user=self.user, github_repo_id=100, owner="octo", name="hello", full_name="octo/hello"
        )
        self.api = APIClient()
        self.api.force_authenticate(user=self.user)
        self.url = f"/api/github/repositories/{self.repo.pk}/sync-pull-requests/"

    def test_states_are_mapped_and_resync_updates_without_duplicates(self):
        self.fake.route(
            "GET",
            "/repos/octo/hello/pulls",
            (200, [
                pr_payload(1, 1, state="open"),
                pr_payload(2, 2, state="closed"),
                pr_payload(3, 3, state="closed", merged_at="2026-01-03T00:00:00Z"),
            ], None),
        )
        first = self.api.post(self.url)
        self.assertEqual(first.json(), {"created": 3, "updated": 0, "total": 3})
        states = dict(PullRequest.objects.values_list("number", "state"))
        self.assertEqual(states, {1: "OPEN", 2: "CLOSED", 3: "MERGED"})
        self.assertIsNotNone(PullRequest.objects.get(number=3).merged_at)

        second = self.api.post(self.url)
        self.assertEqual(second.json(), {"created": 0, "updated": 3, "total": 3})
        self.assertEqual(PullRequest.objects.count(), 3)

    def test_pagination_follows_next_link_on_api_host(self):
        self.fake.route(
            "GET",
            "/repos/octo/hello/pulls",
            (200, [pr_payload(1, 1)], {"Link": '<https://api.github.test/repos/octo/hello/pulls?page=2>; rel="next"'}),
            (200, [pr_payload(2, 2)], None),
        )
        response = self.api.post(self.url)
        self.assertEqual(response.json()["total"], 2)

    def test_pagination_link_to_foreign_host_is_refused(self):
        self.fake.route(
            "GET",
            "/repos/octo/hello/pulls",
            (200, [pr_payload(1, 1)], {"Link": '<https://attacker.example/steal?page=2>; rel="next"'}),
        )
        response = self.api.post(self.url)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(self.fake.calls.count(("GET", "/repos/octo/hello/pulls")), 1)
        self.assertFalse(PullRequest.objects.exists())

    def test_other_users_repository_is_not_found(self):
        intruder = make_user("intruder", "intruder@example.com")
        api = APIClient()
        api.force_authenticate(user=intruder)
        response = api.post(self.url)
        self.assertEqual(response.status_code, 404)
        self.assertFalse(self.fake.calls)


class ClientTests(GitHubTestCase):
    def test_client_sends_api_version_and_bearer_token(self):
        seen = {}

        def capture(request):
            import httpx

            seen["auth"] = request.headers["Authorization"]
            seen["version"] = request.headers["X-GitHub-Api-Version"]
            return httpx.Response(200, json={"id": 1, "login": "x"})

        self.fake.route("GET", "/user", capture)
        with GitHubClient("tok_123") as client:
            client.get_json("/user")
        self.assertEqual(seen["auth"], "Bearer tok_123")
        self.assertEqual(seen["version"], "2026-03-10")

    def test_error_messages_never_include_response_body_or_token(self):
        self.fake.route("GET", "/user", (500, {"message": "secret internal detail tok_123"}, None))
        with GitHubClient("tok_123") as client:
            with self.assertRaises(GitHubError) as ctx:
                client.get_json("/user")
        self.assertNotIn("tok_123", str(ctx.exception))
        self.assertNotIn("internal detail", str(ctx.exception))


class WebhookTests(GitHubTestCase):
    URL = "/api/github/webhook/"

    def setUp(self):
        super().setUp()
        self.user = make_user()
        self.repo = Repository.objects.create(
            user=self.user, github_repo_id=100, owner="octo", name="hello", full_name="octo/hello"
        )
        self.api = APIClient()

    def _post(self, body: bytes, delivery="d-1", event="pull_request", secret="test-webhook-secret"):
        signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return self.api.post(
            self.URL,
            data=body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=signature,
            HTTP_X_GITHUB_DELIVERY=delivery,
            HTTP_X_GITHUB_EVENT=event,
        )

    def _pr_body(self, number=5):
        return json.dumps({"action": "opened", "number": number, "repository": {"id": 100}, "pull_request": pr_payload(55, number)}).encode()

    def test_invalid_signature_is_rejected_and_not_recorded(self):
        response = self._post(self._pr_body(), secret="wrong-secret")
        self.assertEqual(response.status_code, 401)
        self.assertFalse(WebhookEvent.objects.exists())

    def test_valid_pull_request_delivery_is_accepted_and_linked(self):
        response = self._post(self._pr_body())
        self.assertEqual(response.status_code, 202)
        event = WebhookEvent.objects.get(github_delivery_id="d-1")
        self.assertEqual(event.status, WebhookEvent.Status.RECEIVED)
        self.assertEqual(event.repository, self.repo)

    def test_duplicate_delivery_is_idempotent(self):
        self._post(self._pr_body())
        response = self._post(self._pr_body())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "duplicate"})
        self.assertEqual(WebhookEvent.objects.count(), 1)

    def test_unsupported_event_is_recorded_as_ignored(self):
        response = self._post(b'{"zen":"hi"}', delivery="d-ping", event="ping")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(WebhookEvent.objects.get().status, WebhookEvent.Status.IGNORED)

    def test_malformed_pull_request_payload_is_rejected(self):
        response = self._post(b'{"action":"opened"}', delivery="d-bad")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(WebhookEvent.objects.exists())

    def test_invalid_json_is_rejected(self):
        response = self._post(b"not-json", delivery="d-json")
        self.assertEqual(response.status_code, 400)

    def test_signature_verification_fails_closed_without_secret(self):
        body = b"{}"
        self.assertFalse(verify_signature("", body, "sha256=" + hmac.new(b"", body, hashlib.sha256).hexdigest()))
        self.assertFalse(verify_signature("s", body, None))
        self.assertFalse(verify_signature("s", body, "md5=abc"))


class CryptoTests(GitHubTestCase):
    def test_round_trip_and_tamper_detection(self):
        ciphertext = encrypt_secret("gho_value")
        self.assertNotIn("gho_value", ciphertext)
        self.assertEqual(decrypt_secret(ciphertext), "gho_value")
        from github_integration.crypto import CredentialDecryptionError

        with self.assertRaises(CredentialDecryptionError):
            decrypt_secret(ciphertext[:-4] + "AAAA")

    def test_wrong_key_cannot_decrypt(self):
        ciphertext = encrypt_secret("gho_value")
        with override_settings(TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode()):
            from github_integration.crypto import CredentialDecryptionError

            with self.assertRaises(CredentialDecryptionError):
                decrypt_secret(ciphertext)
