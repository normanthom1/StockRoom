from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import AdminUserCreationForm, UserChangeForm

from .models import Organisation, User
from .ratelimit import login_rate_limit

# Django admin is for platform staff only. Practice admins use the in-app
# screens, which are scoped to their own practice; this site isn't.
admin.site.has_permission = lambda request: request.user.is_active and request.user.is_superuser
# Its login is a second way to guess a (superuser's) password; limit it like ours.
admin.site.login = login_rate_limit(admin.site.login)


class UserCreationAdminForm(AdminUserCreationForm):
    class Meta:
        model = User
        fields = ("email",)


class UserChangeAdminForm(UserChangeForm):
    class Meta:
        model = User
        fields = "__all__"


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    form = UserChangeAdminForm
    add_form = UserCreationAdminForm
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Practice", {"fields": ("name", "organisation", "role")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "name", "organisation", "role", "usable_password", "password1", "password2"),
            },
        ),
    )
    list_display = ("email", "name", "organisation", "role", "is_superuser")
    list_filter = ("role", "is_superuser", "is_active", "organisation")
    search_fields = ("email", "name")
    ordering = ("email",)


@admin.register(Organisation)
class OrganisationAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "timezone", "is_active", "created_at")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
