from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class PRSenseUserAdmin(UserAdmin):
    list_display = ("username", "email", "is_staff", "is_active", "created_at")
    search_fields = ("username", "email")
    readonly_fields = ("created_at", "updated_at", "last_login", "date_joined")
