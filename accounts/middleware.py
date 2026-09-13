"""Practice login + staff codes.

A practice signs in on a device once, with its practice login (email and
password). After that, whoever picks up the device enters their code (2 digits
for an assistant, 4 for a manager) and works as themselves. The session
remembers which practice login opened it, so a code only ever switches between
that practice's staff, and changing the practice password signs every device out.
"""

from django.conf import settings
from django.contrib.auth import login, logout
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import redirect
from django.utils.crypto import constant_time_compare

from .models import User

PRACTICE_LOGIN = "practice_login"
PRACTICE_LOGIN_HASH = "practice_login_hash"

# What the practice login can reach on its own: the code pad, staff
# management, its own password and logging out. Everything else needs a code.
PRACTICE_LOGIN_PATHS = ("/accounts/", "/sw.js", "/offline/", "/healthz")


def practice_login_for(request):
    """The practice login this session was opened with, or None if it's gone,
    been deactivated or had its password changed since."""
    user = request.user
    if user.is_practice_login:
        return user
    practice = User.objects.filter(
        pk=request.session.get(PRACTICE_LOGIN), organisation_id=user.organisation_id,
        is_practice_login=True, is_active=True,
    ).first()
    if practice and constant_time_compare(request.session.get(PRACTICE_LOGIN_HASH, ""), practice.get_session_auth_hash()):
        return practice
    return None


def switch_to(request, staff, practice):
    login(request, staff)  # a new user, so this starts a fresh session
    request.session[PRACTICE_LOGIN] = practice.pk
    request.session[PRACTICE_LOGIN_HASH] = practice.get_session_auth_hash()


class PracticeLoginMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if user.is_authenticated and user.organisation_id:
            if user.is_practice_login:
                if not request.path.startswith((*PRACTICE_LOGIN_PATHS, settings.STATIC_URL)):
                    return redirect("enter_code")
            elif user.pin is not None and practice_login_for(request) is None:
                logout(request)
                return redirect_to_login(request.get_full_path())
        return self.get_response(request)
