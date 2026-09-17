from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_not_required
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.views.decorators.http import require_POST

from stockroom.demo import DEMO_EMAIL, DEMO_ORG, DEMO_PASSWORD

from .decorators import admin_required
from .forms import EmailLoginForm, NewCodeForm, SignupForm, StaffForm
from .middleware import practice_login_for, switch_to
from .models import User
from .ratelimit import (
    WINDOW,
    code_entry_locked,
    login_rate_limit,
    rate_limit,
    record_wrong_code,
)


@method_decorator(login_rate_limit, name="dispatch")
class LoginView(auth_views.LoginView):
    authentication_form = EmailLoginForm

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            signup_enabled=settings.SIGNUP_ENABLED, demo_mode=settings.DEMO_MODE,
            demo_email=DEMO_EMAIL, demo_password=DEMO_PASSWORD, **kwargs
        )


@login_not_required
@require_POST
def demo_login(request):
    if not settings.DEMO_MODE:
        raise Http404
    practice = get_object_or_404(User, email=DEMO_EMAIL, organisation__name=DEMO_ORG, is_practice_login=True)
    login(request, practice)
    return redirect("enter_code")


@login_not_required
@rate_limit("signup", per_ip=5, window=60 * 60)
def signup(request):
    if not settings.SIGNUP_ENABLED:
        raise Http404
    if request.user.is_authenticated:
        return redirect(settings.LOGIN_REDIRECT_URL)
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        practice, manager = form.save()
        switch_to(request, manager, practice)
        # Straight into setup: a brand new practice has nothing for Home to show.
        return redirect("stock:setup")
    return render(request, "registration/signup.html", {"form": form})


def enter_code(request):
    """The code pad: whoever's holding the device says who they are."""
    practice = practice_login_for(request)
    if practice is None:
        # Someone with their own email login; there's no one to switch to.
        raise PermissionDenied
    staff = User.objects.for_org(practice.organisation).filter(pin__isnull=False, is_active=True)
    error = None
    if request.method == "POST":
        if code_entry_locked(practice):
            return render(request, "429.html", {"minutes": WINDOW // 60}, status=429)
        person = staff.filter(pin=request.POST.get("pin", "")).first()
        if person:
            switch_to(request, person, practice)
            return redirect(settings.LOGIN_REDIRECT_URL)
        record_wrong_code(practice)
        error = "That code doesn't match anyone here. Try again."
    return render(request, "accounts/enter_code.html", {
        "practice": practice,
        "has_staff": staff.exists(),
        "error": error,
        # The demo practice's codes are public anyway; a real practice's never are.
        "demo_staff": staff.order_by("pin") if settings.DEMO_MODE and practice.organisation.name == DEMO_ORG else None,
    })


def _members(org):
    """Everyone on the Team page: staff, and anyone with their own email login.
    Never the practice login itself, so no one can demote or lock it out."""
    return User.objects.for_org(org).filter(is_practice_login=False)


def _is_last_active_admin(org, member):
    if member.role != User.Role.ADMIN or not member.is_active:
        return False
    # The practice login counts: it can always hand admin back to someone.
    others = User.objects.for_org(org).filter(role=User.Role.ADMIN, is_active=True).exclude(pk=member.pk)
    return not others.exists()


def _team_page(request, form=None):
    org = request.user.organisation
    return render(request, "accounts/team.html", {
        "members": _members(org).order_by("-is_active", "name", "email"),
        "practice_logins": User.objects.for_org(org).filter(is_practice_login=True),
        "form": form or StaffForm(organisation=org),
    })


@admin_required
def team(request):
    return _team_page(request)


@admin_required
@require_POST
def team_add(request):
    form = StaffForm(request.POST, organisation=request.user.organisation)
    if not form.is_valid():
        return _team_page(request, form)
    member = form.save()
    messages.success(request, f"{member.name} can now sign in with {member.pin}.")
    return redirect("team")


@admin_required
def team_role(request, pk):
    """Staff changing role pick a new code on the way (4 digits for a
    manager, 2 for an assistant), typed by the person themselves. Someone
    with their own email login just changes role."""
    org = request.user.organisation
    member = get_object_or_404(_members(org), pk=pk)
    new_role = request.POST.get("role") or request.GET.get("role")
    if new_role not in User.Role.values:
        raise Http404

    label = member.name or member.email
    if new_role != User.Role.ADMIN and _is_last_active_admin(org, member):
        messages.error(request, f"{label} is the only manager. Make someone else a manager first.")
        return redirect("team")

    form = None
    if member.pin is not None:
        form = NewCodeForm(request.POST or None, member=member, role=new_role)
        if request.method != "POST" or not form.is_valid():
            return render(request, "accounts/team_role.html", {"member": member, "role": new_role, "form": form})
        member.pin = form.cleaned_data["pin"]
    elif request.method != "POST":
        return redirect("team")

    member.role = new_role
    member.save(update_fields=["role", "pin"])
    code_note = " with their new code" if form else ""
    messages.success(request, f"{label} is now {member.get_role_display()}{code_note}.")
    return redirect("team")


@admin_required
@require_POST
def team_set_active(request, pk):
    org = request.user.organisation
    member = get_object_or_404(_members(org), pk=pk)
    make_active = request.POST.get("active") == "1"

    label = member.name or member.email
    if make_active:
        member.is_active = True
        member.save(update_fields=["is_active"])
        messages.success(request, f"{label} is active again.")
    elif member == request.user:
        messages.error(request, "You can't deactivate yourself.")
    elif _is_last_active_admin(org, member):
        messages.error(request, f"{label} is the only manager. Make someone else a manager before deactivating them.")
    else:
        member.is_active = False
        member.save(update_fields=["is_active"])
        messages.success(request, f"{label} is deactivated.")
    return redirect("team")
