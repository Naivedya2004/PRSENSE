from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient


class HealthEndpointTests(TestCase):
    def test_health_reports_ok_when_database_is_reachable(self):
        response = APIClient().get(reverse("health"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "database": "ok"})


class CustomUserTests(TestCase):
    def test_custom_user_model_is_active(self):
        self.assertEqual(get_user_model()._meta.label, "accounts.User")

    def test_email_is_unique(self):
        User = get_user_model()
        User.objects.create_user(username="alice", email="alice@example.com", password="correct-horse-9")
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.create_user(username="alice2", email="alice@example.com", password="correct-horse-9")

    def test_password_is_hashed_not_stored_plaintext(self):
        user = get_user_model().objects.create_user(
            username="bob", email="bob@example.com", password="correct-horse-9"
        )
        self.assertNotIn("correct-horse-9", user.password)
        self.assertTrue(user.check_password("correct-horse-9"))

    def test_timestamps_are_set(self):
        user = get_user_model().objects.create_user(
            username="carol", email="carol@example.com", password="correct-horse-9"
        )
        self.assertIsNotNone(user.created_at)
        self.assertIsNotNone(user.updated_at)


class ErrorShapeTests(TestCase):
    def test_api_error_uses_standard_envelope(self):
        # POST to a GET-only endpoint produces a DRF-handled 405 that goes through safe_exception_handler.
        response = APIClient().post("/api/health/", {}, format="json")
        self.assertEqual(response.status_code, 405)
        body = response.json()
        self.assertEqual(body["error"]["code"], "method_not_allowed")
        self.assertIn("message", body["error"])
