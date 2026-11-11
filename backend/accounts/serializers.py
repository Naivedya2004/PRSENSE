from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

User = get_user_model()


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, style={"input_type": "password"}, trim_whitespace=False)

    class Meta:
        model = User
        fields = ["id", "username", "email", "password", "created_at"]
        read_only_fields = ["id", "created_at"]

    def validate_password(self, value: str) -> str:
        # Django's validators (length, common passwords, similarity to username/email) run here.
        validate_password(value)
        return value

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)


class CurrentUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "username", "email", "created_at"]
        read_only_fields = fields
