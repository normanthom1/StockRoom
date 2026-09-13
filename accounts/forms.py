from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction

from .models import Organisation, User, pin_validator


class EmailLoginForm(AuthenticationForm):
    # An email input, so phones show the @ keyboard.
    username = forms.EmailField(
        label="Email", widget=forms.EmailInput(attrs={"autofocus": True, "autocomplete": "email"})
    )


def code_field(help_text):
    return forms.CharField(
        label="2-digit code",
        min_length=2,
        max_length=2,
        validators=[pin_validator],
        help_text=help_text,
        widget=forms.TextInput(attrs={"inputmode": "numeric", "pattern": "[0-9]{2}", "autocomplete": "off"}),
    )


class SignupForm(forms.Form):
    practice_name = forms.CharField(
        label="Practice name",
        max_length=200,
        widget=forms.TextInput(attrs={"autofocus": True, "autocomplete": "organization"}),
    )
    email = forms.EmailField(
        label="Practice email",
        help_text="The whole practice signs in with this, e.g. reception@yourpractice.co.nz.",
        widget=forms.EmailInput(attrs={"autocomplete": "email"}),
    )
    password = forms.CharField(
        label="Practice password",
        strip=False,
        help_text="At least 8 characters, and not a common password.",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    name = forms.CharField(label="Your name", max_length=150, widget=forms.TextInput(attrs={"autocomplete": "name"}))
    pin = code_field("What you'll tap to sign in on any device, like 07.")

    def clean_email(self):
        email = self.cleaned_data["email"]
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError("Someone already uses that email address. Log in instead?")
        return email

    def clean(self):
        cleaned = super().clean()
        if password := cleaned.get("password"):
            try:
                validate_password(password, User(email=cleaned.get("email", ""), name=cleaned.get("practice_name", "")))
            except ValidationError as error:
                self.add_error("password", error)
        return cleaned

    @transaction.atomic
    def save(self):
        """The practice, its practice login and its first manager, together or not at all.
        Returns (practice login, manager)."""
        org = Organisation.objects.create(name=self.cleaned_data["practice_name"])
        practice = User.objects.create_user(
            self.cleaned_data["email"],
            self.cleaned_data["password"],
            organisation=org,
            role=User.Role.ADMIN,
            is_practice_login=True,
        )
        manager = User.objects.create_staff(org, self.cleaned_data["name"], self.cleaned_data["pin"], User.Role.ADMIN)
        return practice, manager


class StaffForm(forms.Form):
    name = forms.CharField(label="Name", max_length=150, widget=forms.TextInput(attrs={"autocomplete": "off"}))
    pin = code_field("What they'll tap to sign in, like 07.")
    role = forms.ChoiceField(label="Role", choices=User.Role.choices, initial=User.Role.ASSISTANT, widget=forms.RadioSelect)

    def __init__(self, *args, organisation, **kwargs):
        self.organisation = organisation
        super().__init__(*args, **kwargs)

    def clean_pin(self):
        pin = self.cleaned_data["pin"]
        taken = User.objects.for_org(self.organisation).filter(pin=pin).first()
        if taken:
            raise ValidationError(f"{taken.name} already uses {pin}. Pick another code.")
        return pin

    def save(self):
        return User.objects.create_staff(self.organisation, **self.cleaned_data)
