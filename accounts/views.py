from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_not_required
from django.http import Http404
from django.shortcuts import redirect, render

from .forms import EmailLoginForm, SignupForm


class LoginView(auth_views.LoginView):
    authentication_form = EmailLoginForm

    def get_context_data(self, **kwargs):
        return super().get_context_data(signup_enabled=settings.SIGNUP_ENABLED, **kwargs)


@login_not_required
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
