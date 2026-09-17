from functools import wraps

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import Http404


def ai_required(view):
    """Every AI view 404s unless AI_API_KEY is set, so nothing half-works without one."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not settings.AI_API_KEY:
            raise Http404
        return view(request, *args, **kwargs)

    return wrapper


def admin_required(view):
    """Practice managers and owners only; everyone else gets a 403.

    That includes platform superusers, who belong to no practice. Login itself
    is enforced for every view by LoginRequiredMiddleware.
    """

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not getattr(request.user, "is_org_admin", False):
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapper
