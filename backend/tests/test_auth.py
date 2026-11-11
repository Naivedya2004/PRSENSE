from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

User = get_user_model()
PASSWORD = "correct-horse-9"


class AuthTests(TestCase):
    def setUp(self):
        self.client_api = APIClient()

    def test_register_creates_hashed_user_and_hides_password(self):
        response = self.client_api.post(
            "/api/auth/register/",
            {"username": "alice", "email": "alice@example.com", "password": PASSWORD},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertNotIn("password", response.json())
        user = User.objects.get(username="alice")
        self.assertTrue(user.check_password(PASSWORD))
        self.assertNotEqual(user.password, PASSWORD)

    def test_register_rejects_weak_password_with_error_envelope(self):
        response = self.client_api.post(
            "/api/auth/register/",
            {"username": "bob", "email": "bob@example.com", "password": "123"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "validation_error")
        self.assertFalse(User.objects.filter(username="bob").exists())

    def test_register_rejects_duplicate_email(self):
        User.objects.create_user(username="carol", email="dup@example.com", password=PASSWORD)
        response = self.client_api.post(
            "/api/auth/register/",
            {"username": "carol2", "email": "dup@example.com", "password": PASSWORD},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_login_returns_tokens_and_rejects_wrong_password(self):
        User.objects.create_user(username="dave", email="dave@example.com", password=PASSWORD)
        ok = self.client_api.post("/api/auth/login/", {"username": "dave", "password": PASSWORD}, format="json")
        self.assertEqual(ok.status_code, 200)
        self.assertIn("access", ok.json())
        self.assertIn("refresh", ok.json())
        bad = self.client_api.post("/api/auth/login/", {"username": "dave", "password": "wrong-password"}, format="json")
        self.assertEqual(bad.status_code, 401)

    def test_me_requires_authentication_and_returns_current_user(self):
        self.assertEqual(self.client_api.get("/api/auth/me/").status_code, 401)
        user = User.objects.create_user(username="erin", email="erin@example.com", password=PASSWORD)
        self.client_api.force_authenticate(user=user)
        response = self.client_api.get("/api/auth/me/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["username"], "erin")
        self.assertNotIn("password", response.json())

    def test_refresh_issues_new_access_token(self):
        User.objects.create_user(username="frank", email="frank@example.com", password=PASSWORD)
        login = self.client_api.post("/api/auth/login/", {"username": "frank", "password": PASSWORD}, format="json")
        response = self.client_api.post("/api/auth/refresh/", {"refresh": login.json()["refresh"]}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.json())
