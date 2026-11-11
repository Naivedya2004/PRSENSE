from unittest import mock

from cryptography.fernet import Fernet
from django.test import TestCase, override_settings

from .fake_github import FakeGitHub

TEST_SETTINGS = {
    "TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode(),
    "GITHUB_CLIENT_ID": "test-client-id",
    "GITHUB_CLIENT_SECRET": "test-client-secret",
    "GITHUB_REDIRECT_URI": "http://testserver/api/github/callback/",
    "GITHUB_WEBHOOK_SECRET": "test-webhook-secret",
    "FRONTEND_URL": "http://frontend.test",
    "GITHUB_API_BASE_URL": "https://api.github.test",
    "GITHUB_WEB_BASE_URL": "https://github.test",
    "GITHUB_HTTP_TIMEOUT_SECONDS": 5.0,
    "GITHUB_PR_SYNC_MAX_PAGES": 10,
}


@override_settings(**TEST_SETTINGS)
class GitHubTestCase(TestCase):
    """Routes all GitHub HTTP traffic to self.fake and makes retry backoff instant."""

    def setUp(self):
        self.fake = FakeGitHub()
        transport_patch = mock.patch(
            "github_integration.client.get_transport", side_effect=lambda: self.fake.transport()
        )
        transport_patch.start()
        self.addCleanup(transport_patch.stop)
        sleep_patch = mock.patch("time.sleep")
        sleep_patch.start()
        self.addCleanup(sleep_patch.stop)
