import zoneinfo

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q
from django.db.models.functions import Length, Lower
from django.db.models.lookups import Exact
from django.utils.text import slugify


def validate_timezone(value):
    if value not in zoneinfo.available_timezones():
        raise ValidationError(f"{value} isn't a known timezone.")


class Organisation(models.Model):
    """A dental practice: the top-level account that staff and stock belong to."""

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, unique=True)
    timezone = models.CharField(max_length=64, default="Pacific/Auckland", validators=[validate_timezone])
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._unique_slug()
        super().save(*args, **kwargs)

    def _unique_slug(self):
        # ponytail: two sign-ups with the same practice name at the same instant
        # can race here and one hits the unique constraint; retry if that ever shows up.
        base = slugify(self.name)[:190] or "practice"
        slug, n = base, 2
        while Organisation.objects.filter(slug=slug).exists():
            slug, n = f"{base}-{n}", n + 1
        return slug


class OrgQuerySet(models.QuerySet):
    def for_org(self, org):
        """Rows belonging to org. None matches nothing, so a user without a
        practice (a platform superuser) can never see practice data, and
        User.objects.for_org(None) can't list the superusers themselves."""
        if org is None:
            return self.none()
        return self.filter(organisation=org)


class OrgOwned(models.Model):
    """Base for everything a practice owns. Always query it through
    Model.objects.for_org(request.user.organisation)."""

    # CASCADE is safe: an organisation can't be deleted while it has users
    # (User.organisation is PROTECT), so this only fires on a deliberate wipe.
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE)

    objects = OrgQuerySet.as_manager()

    class Meta:
        abstract = True


class UserManager(BaseUserManager.from_queryset(OrgQuerySet)):
    use_in_migrations = True

    def get_by_natural_key(self, email):
        # Login ignores case: Tom@Example.com and tom@example.com are one person.
        return self.get(email__iexact=email)

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("An email address is required.")
        user = self.model(email=self.normalize_email(email), **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_staff(self, organisation, name, pin, role="assistant"):
        """A staff member: no email or password, just a code entered on a
        device the practice login has opened."""
        user = self.model(organisation=organisation, name=name, pin=pin, role=role)
        user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields["is_staff"] is not True or extra_fields["is_superuser"] is not True:
            raise ValueError("A superuser needs is_staff=True and is_superuser=True.")
        return self._create_user(email, password, **extra_fields)


pin_validator = RegexValidator(r"^(\d{2}|\d{4})$", "Use digits only: 2 for an assistant, 4 for a manager.")


class User(AbstractUser):
    """Someone who uses StockRoom. A practice signs in on a device with its
    practice login (email and password), then each staff member picks
    themselves with a code (2 digits for an assistant, 4 for a manager).
    Superusers are platform staff and belong to no practice."""

    class Role(models.TextChoices):
        ADMIN = "admin", "Practice manager or owner"
        ASSISTANT = "assistant", "Dental assistant"

    # Managers see prices and run the team, so their code is harder to guess.
    CODE_LENGTH = {Role.ADMIN: 4, Role.ASSISTANT: 2}

    # Email is the login, and one name field fits how practices refer to people.
    username = None
    first_name = None
    last_name = None
    name = models.CharField(max_length=150, blank=True)
    # Staff have no email; they sign in with their code instead.
    email = models.EmailField("email address", unique=True, null=True, blank=True)
    pin = models.CharField("code", max_length=4, null=True, blank=True, validators=[pin_validator])
    # The practice's shared login. It opens a device and manages staff; the
    # stock work itself is always done as a staff member (PracticeLoginMiddleware).
    is_practice_login = models.BooleanField(default=False)
    organisation = models.ForeignKey(
        Organisation, on_delete=models.PROTECT, null=True, blank=True, related_name="users"
    )
    # Least privilege by default: someone has to decide a user is an admin.
    role = models.CharField(max_length=20, choices=Role, default=Role.ASSISTANT)

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta(AbstractUser.Meta):
        constraints = [
            # unique=True on email is case-sensitive and only there because Django's
            # auth checks want USERNAME_FIELD unique. This is the real rule.
            models.UniqueConstraint(
                Lower("email"),
                name="accounts_user_email_ci_unique",
                violation_error_message="Someone already uses that email address.",
            ),
            models.CheckConstraint(
                condition=Q(is_superuser=True) | Q(organisation__isnull=False),
                name="accounts_user_has_organisation",
                violation_error_message="Everyone except platform superusers must belong to a practice.",
            ),
            models.UniqueConstraint(
                fields=["organisation", "pin"],
                name="accounts_user_pin_unique_per_practice",
                violation_error_message="Someone at this practice already uses that code.",
            ),
            models.CheckConstraint(
                condition=Q(email__isnull=False) | Q(pin__isnull=False),
                name="accounts_user_has_a_way_in",
                violation_error_message="Everyone needs an email address or a code to sign in with.",
            ),
            models.CheckConstraint(
                condition=Q(pin__isnull=True)
                | Q(Q(role="admin"), Exact(Length("pin"), 4))
                | Q(Q(role="assistant"), Exact(Length("pin"), 2)),
                name="accounts_user_code_length_matches_role",
                violation_error_message="Managers need a 4-digit code, and assistants a 2-digit one.",
            ),
        ]

    def __str__(self):
        return self.email or self.name

    def clean(self):
        super().clean()
        # AbstractUser.clean() turns a missing email into "", which the unique
        # constraint would then treat as one shared address between staff.
        self.email = self.email or None

    def get_full_name(self):
        return self.name

    def get_short_name(self):
        return self.name

    @property
    def is_org_admin(self):
        return self.organisation_id is not None and self.role == self.Role.ADMIN
