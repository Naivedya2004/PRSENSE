from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """PRSense user account.

    A custom model is used from day one so that fields can be added later without
    a painful migration away from django.contrib.auth.models.User. Passwords are
    hashed by Django's default PBKDF2 hasher (see AUTH_PASSWORD_VALIDATORS for rules).
    """

    email = models.EmailField("email address", unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    REQUIRED_FIELDS = ["email"]

    class Meta:
        db_table = "accounts_user"
        ordering = ["id"]

    def __str__(self) -> str:
        return self.username
