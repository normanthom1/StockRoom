from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction

from .models import Organisation, User


class EmailLoginForm(AuthenticationForm):
    # An email input, so phones show the @ keyboard.
    username = forms.EmailField(
        label="Email", widget=forms.EmailInput(attrs={"autofocus": True, "autocomplete": "email"})
    )


class SignupForm(forms.Form):
    practice_name = forms.CharField(
        label="Practice name",
        max_length=200,
        widget=forms.TextInput(attrs={"autofocus": True, "autocomplete": "organization"}),
    )
    name = forms.CharField(label="Your name", max_length=150, widget=forms.TextInput(attrs={"autocomplete": "name"}))
    email = forms.EmailField(label="Email", widget=forms.EmailInput(attrs={"autocomplete": "email"}))
    password = forms.CharField(
        label="Password",
        strip=False,
        help_text="At least 8 characters, and not a common password.",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    def clean_email(self):
        email = self.cleaned_data["email"]
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError("Someone already uses that email address. Log in instead?")
        return email

    def clean(self):
        cleaned = super().clean()
        if password := cleaned.get("password"):
            try:
                validate_password(password, User(email=cleaned.get("email", ""), name=cleaned.get("name", "")))
            except ValidationError as error:
                self.add_error("password", error)
        return cleaned

    @transaction.atomic
    def save(self):
        """The practice and its first manager, together or not at all."""
        org = Organisation.objects.create(name=self.cleaned_data["practice_name"])
        return User.objects.create_user(
            self.cleaned_data["email"],
            self.cleaned_data["password"],
            name=self.cleaned_data["name"],
            organisation=org,
            role=User.Role.ADMIN,
        )
