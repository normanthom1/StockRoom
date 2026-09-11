from functools import wraps

from django.core.exceptions import PermissionDenied


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
