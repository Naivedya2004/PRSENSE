from django.db.models import Count
from rest_framework.exceptions import ValidationError
from rest_framework.viewsets import ReadOnlyModelViewSet

from .models import PullRequest, Repository
from .serializers import PullRequestSerializer, RepositorySerializer


class RepositoryViewSet(ReadOnlyModelViewSet):
    """Repositories synchronized by the current user. Ownership is enforced in the queryset."""

    serializer_class = RepositorySerializer

    def get_queryset(self):
        return (
            Repository.objects.filter(user=self.request.user)
            .annotate(pull_request_count=Count("pull_requests"))
            .order_by("full_name")
        )


class PullRequestViewSet(ReadOnlyModelViewSet):
    """Pull requests from the current user's repositories, optionally filtered by ?repository=<id>&state=OPEN."""

    serializer_class = PullRequestSerializer

    def get_queryset(self):
        queryset = PullRequest.objects.filter(repository__user=self.request.user).select_related("repository")
        repository_id = self.request.query_params.get("repository")
        if repository_id is not None:
            if not repository_id.isdigit():
                raise ValidationError({"repository": "Must be a numeric repository id."})
            queryset = queryset.filter(repository_id=int(repository_id))
        state = self.request.query_params.get("state")
        if state is not None:
            if state not in PullRequest.State.values:
                raise ValidationError({"state": f"Must be one of {', '.join(PullRequest.State.values)}."})
            queryset = queryset.filter(state=state)
        return queryset
