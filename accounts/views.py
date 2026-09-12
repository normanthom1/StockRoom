from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.tokens import default_token_generator
from django.core import signing
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.views.decorators.http import require_POST

from .decorators import admin_required
from .forms import AcceptInviteForm, EmailLoginForm, InviteForm, SignupForm
from .mailto import build_mailto_link
from .models import Organisation, User
from .ratelimit import login_rate_limit, rate_limit

INVITE_SALT = "accounts.invite"
INVITE_MAX_AGE = 60 * 60 * 24 * 7  # 7 days


@method_decorator(login_rate_limit, name="dispatch")
class LoginView(auth_views.LoginView):
    authentication_form = EmailLoginForm

    def get_context_data(self, **kwargs):
        return super().get_context_data(signup_enabled=settings.SIGNUP_ENABLED, **kwargs)


@login_not_required
@rate_limit("signup", per_ip=5, window=60 * 60)
def signup(request):
    if not settings.SIGNUP_ENABLED:
        raise Http404
    if request.user.is_authenticated:
        return redirect(settings.LOGIN_REDIRECT_URL)
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        login(request, form.save())
        return redirect(settings.LOGIN_REDIRECT_URL)
    return render(request, "registration/signup.html", {"form": form})


def _is_last_active_admin(org, member):
    if member.role != User.Role.ADMIN or not member.is_active:
        return False
    others = User.objects.for_org(org).filter(role=User.Role.ADMIN, is_active=True).exclude(pk=member.pk)
    return not others.exists()


def _team_context(org, **extra):
    members = User.objects.for_org(org).order_by("email")
    return {"members": members, **extra}


@admin_required
def team(request):
    org = request.user.organisation
    return render(request, "accounts/team.html", _team_context(org, invite_form=InviteForm()))


@admin_required
@require_POST
def team_invite(request):
    org = request.user.organisation
    form = InviteForm(request.POST)
    if not form.is_valid():
        return render(request, "accounts/team.html", _team_context(org, invite_form=form))

    email = form.cleaned_data["email"]
    role = form.cleaned_data["role"]
    token = signing.dumps({"org": org.pk, "email": email, "role": role}, salt=INVITE_SALT)
    link = request.build_absolute_uri(reverse("invite_accept", args=[token]))
    inviter = request.user.name or request.user.email
    body = (
        f"Kia ora,\n\n{inviter} has invited you to join {org.name} on StockRoom. "
        f"Set up your account here:\n\n{link}\n\nThe link works for 7 days.\n"
    )
    mailto = build_mailto_link(email, f"Join {org.name} on StockRoom", body)
    return render(request, "accounts/invite_ready.html", {"email": email, "link": link, "mailto": mailto})


@login_not_required
@rate_limit("invite", per_ip=10)
def invite_accept(request, token):
    if request.user.is_authenticated:
        return redirect(settings.LOGIN_REDIRECT_URL)

    try:
        data = signing.loads(token, salt=INVITE_SALT, max_age=INVITE_MAX_AGE)
    except signing.SignatureExpired:
        return render(request, "accounts/invite_invalid.html", {"reason": "expired"})
    except signing.BadSignature:
        return render(request, "accounts/invite_invalid.html", {"reason": "invalid"})

    org = get_object_or_404(Organisation, pk=data["org"])
    email = data["email"]
    role = data["role"]
    if role not in User.Role.values:
        raise Http404
    if User.objects.filter(email__iexact=email).exists():
        return render(request, "accounts/invite_invalid.html", {"reason": "used"})

    form = AcceptInviteForm(request.POST or None, email=email)
    if request.method == "POST" and form.is_valid():
        user = User.objects.create_user(
            email, form.cleaned_data["password"], name=form.cleaned_data["name"], organisation=org, role=role
        )
        login(request, user)
        return redirect(settings.LOGIN_REDIRECT_URL)
    return render(
        request,
        "accounts/accept_invite.html",
        {"form": form, "org": org, "email": email, "role_label": User.Role(role).label},
    )


@admin_required
@require_POST
def team_role(request, pk):
    org = request.user.organisation
    member = get_object_or_404(User.objects.for_org(org), pk=pk)
    new_role = request.POST.get("role")
    if new_role not in User.Role.values:
        raise Http404

    label = member.name or member.email
    if new_role != User.Role.ADMIN and _is_last_active_admin(org, member):
        messages.error(request, f"{label} is the only admin — make someone else admin first.")
    else:
        member.role = new_role
        member.save(update_fields=["role"])
        messages.success(request, f"{label} is now {member.get_role_display()}.")
    return redirect("team")


@admin_required
@require_POST
def team_set_active(request, pk):
    org = request.user.organisation
    member = get_object_or_404(User.objects.for_org(org), pk=pk)
    make_active = request.POST.get("active") == "1"

    label = member.name or member.email
    if make_active:
        member.is_active = True
        member.save(update_fields=["is_active"])
        messages.success(request, f"{label} is active again.")
    elif member == request.user:
        messages.error(request, "You can't deactivate yourself.")
    elif _is_last_active_admin(org, member):
        messages.error(request, f"{label} is the only admin — make someone else admin before deactivating them.")
    else:
        member.is_active = False
        member.save(update_fields=["is_active"])
        messages.success(request, f"{label} is deactivated.")
    return redirect("team")


@admin_required
@require_POST
def team_reset_link(request, pk):
    org = request.user.organisation
    member = get_object_or_404(User.objects.for_org(org), pk=pk)

    uidb64 = urlsafe_base64_encode(force_bytes(member.pk))
    token = default_token_generator.make_token(member)
    link = request.build_absolute_uri(reverse("password_reset_confirm", kwargs={"uidb64": uidb64, "token": token}))
    sender = request.user.name or request.user.email
    body = (
        f"Kia ora{' ' + member.name if member.name else ''},\n\n{sender} set up a password reset link for you. "
        f"Set a new password here:\n\n{link}\n\nThe link works once.\n"
    )
    mailto = build_mailto_link(member.email, "Reset your StockRoom password", body)
    return render(request, "accounts/reset_link_ready.html", {"member": member, "link": link, "mailto": mailto})
