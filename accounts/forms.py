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


def code_field(label, help_text, lengths=(2, 4), widget=forms.TextInput):
    return forms.CharField(
        label=label,
        min_length=min(lengths),
        max_length=max(lengths),
        validators=[pin_validator],
        help_text=help_text,
        widget=widget(attrs={
            "inputmode": "numeric",
            "pattern": "|".join(f"[0-9]{{{n}}}" for n in lengths),
            "autocomplete": "off",
        }),
    )


def check_code_length(form, field, pin, role):
    if pin and role and len(pin) != User.CODE_LENGTH[role]:
        form.add_error(field, "A manager needs a 4-digit code." if role == User.Role.ADMIN
                       else "An assistant uses a 2-digit code.")


def check_code_is_free(organisation, pin, member=None):
    taken = User.objects.for_org(organisation).filter(pin=pin).exclude(pk=getattr(member, "pk", None)).first()
    if taken:
        raise ValidationError(f"{taken.name} already uses {pin}. Pick another code.")


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
    pin = code_field("Your 4-digit code", "What you'll tap to sign in on any device. Managers use 4 digits.",
                     lengths=(4,), widget=forms.PasswordInput)

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
    role = forms.ChoiceField(label="Role", choices=User.Role.choices, initial=User.Role.ASSISTANT, widget=forms.RadioSelect)
    pin = code_field("Their code", "2 digits for an assistant, like 07. 4 digits for a manager.")

    def __init__(self, *args, organisation, **kwargs):
        self.organisation = organisation
        super().__init__(*args, **kwargs)

    def clean_pin(self):
        pin = self.cleaned_data["pin"]
        check_code_is_free(self.organisation, pin)
        return pin

    def clean(self):
        cleaned = super().clean()
        check_code_length(self, "pin", cleaned.get("pin"), cleaned.get("role"))
        return cleaned

    def save(self):
        return User.objects.create_staff(self.organisation, **self.cleaned_data)


class NewCodeForm(forms.Form):
    """A new code for someone changing role: managers need 4 digits and
    assistants 2, so a role change always comes with a new code. The person
    themselves types it, twice, so only they know it."""

    def __init__(self, *args, member, role, **kwargs):
        self.member = member
        self.role = role
        super().__init__(*args, **kwargs)
        length = User.CODE_LENGTH[role]
        self.fields["pin"] = code_field(f"New {length}-digit code", None, lengths=(length,), widget=forms.PasswordInput)
        self.fields["pin"].widget.attrs["autofocus"] = True
        self.fields["pin_again"] = code_field("Type it again", None, lengths=(length,), widget=forms.PasswordInput)

    def clean_pin(self):
        pin = self.cleaned_data["pin"]
        check_code_is_free(self.member.organisation, pin, self.member)
        return pin

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("pin") and cleaned.get("pin_again") and cleaned["pin"] != cleaned["pin_again"]:
            self.add_error("pin_again", "Those don't match. Try again.")
        return cleaned
