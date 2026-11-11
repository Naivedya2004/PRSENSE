from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from repositories.models import PullRequest, Repository

from .base import GitHubTestCase

User = get_user_model()


class OwnershipTests(GitHubTestCase):
    def setUp(self):
        super().setUp()
        self.alice = User.objects.create_user(username="alice", email="a@example.com", password="correct-horse-9")
        self.bob = User.objects.create_user(username="bob", email="b@example.com", password="correct-horse-9")
        self.alice_repo = Repository.objects.create(user=self.alice, github_repo_id=1, owner="o", name="a", full_name="o/a")
        self.bob_repo = Repository.objects.create(user=self.bob, github_repo_id=2, owner="o", name="b", full_name="o/b")
        self.alice_pr = PullRequest.objects.create(
            repository=self.alice_repo, github_pr_id=10, number=1, title="A1", state="OPEN",
            created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z",
        )
        PullRequest.objects.create(
            repository=self.bob_repo, github_pr_id=20, number=1, title="B1", state="MERGED",
            created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z",
        )
        self.api = APIClient()
        self.api.force_authenticate(user=self.alice)

    def test_user_lists_only_own_repositories_with_counts(self):
        response = self.api.get("/api/repositories/")
        self.assertEqual(response.status_code, 200)
        results = response.json()["results"]
        self.assertEqual([r["full_name"] for r in results], ["o/a"])
        self.assertEqual(results[0]["pull_request_count"], 1)

    def test_other_users_repository_detail_is_not_found(self):
        self.assertEqual(self.api.get(f"/api/repositories/{self.bob_repo.pk}/").status_code, 404)

    def test_pull_requests_are_scoped_and_filterable(self):
        response = self.api.get("/api/pull-requests/")
        titles = [pr["title"] for pr in response.json()["results"]]
        self.assertEqual(titles, ["A1"])
        self.assertEqual(self.api.get(f"/api/pull-requests/?repository={self.alice_repo.pk}").status_code, 200)
        self.assertEqual(self.api.get("/api/pull-requests/?repository=abc").status_code, 400)
        self.assertEqual(self.api.get("/api/pull-requests/?state=BOGUS").status_code, 400)
        self.assertEqual(self.api.get(f"/api/pull-requests/{self.alice_pr.pk}/").json()["repository_full_name"], "o/a")

    def test_unauthenticated_access_is_rejected(self):
        self.assertEqual(APIClient().get("/api/repositories/").status_code, 401)
