from django.urls import path
from rest_framework.routers import SimpleRouter

from accounts.views import LoginView, MeView, RegisterView
from github_integration.views import (
    GitHubCallbackView,
    GitHubConnectionStatusView,
    GitHubConnectView,
    GitHubRepositoriesView,
    GitHubWebhookView,
    SyncPullRequestsView,
)
from repositories.views import PullRequestViewSet, RepositoryViewSet

from .views import HealthView

from rest_framework_simplejwt.views import TokenRefreshView

router = SimpleRouter()
router.register("repositories", RepositoryViewSet, basename="repository")
router.register("pull-requests", PullRequestViewSet, basename="pull-request")

urlpatterns = [
    path("health/", HealthView.as_view(), name="health"),
    path("auth/register/", RegisterView.as_view(), name="auth-register"),
    path("auth/login/", LoginView.as_view(), name="auth-login"),
    path("auth/refresh/", TokenRefreshView.as_view(), name="auth-refresh"),
    path("auth/me/", MeView.as_view(), name="auth-me"),
    path("github/connect/", GitHubConnectView.as_view(), name="github-connect"),
    path("github/callback/", GitHubCallbackView.as_view(), name="github-callback"),
    path("github/connection/", GitHubConnectionStatusView.as_view(), name="github-connection"),
    path("github/repositories/", GitHubRepositoriesView.as_view(), name="github-repositories"),
    path(
        "github/repositories/<int:repository_id>/sync-pull-requests/",
        SyncPullRequestsView.as_view(),
        name="github-sync-pull-requests",
    ),
    path("github/webhook/", GitHubWebhookView.as_view(), name="github-webhook"),
] + router.urls
