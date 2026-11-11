from rest_framework import serializers

from .models import PullRequest, Repository


class RepositorySerializer(serializers.ModelSerializer):
    pull_request_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Repository
        fields = [
            "id",
            "github_repo_id",
            "owner",
            "name",
            "full_name",
            "default_branch",
            "is_active",
            "pull_request_count",
            "updated_at",
        ]
        read_only_fields = fields


class PullRequestSerializer(serializers.ModelSerializer):
    repository_id = serializers.IntegerField(source="repository.id", read_only=True)
    repository_full_name = serializers.CharField(source="repository.full_name", read_only=True)

    class Meta:
        model = PullRequest
        fields = [
            "id",
            "repository_id",
            "repository_full_name",
            "github_pr_id",
            "number",
            "title",
            "description",
            "author",
            "source_branch",
            "target_branch",
            "state",
            "url",
            "created_at",
            "updated_at",
            "merged_at",
            "synced_at",
        ]
        read_only_fields = fields
