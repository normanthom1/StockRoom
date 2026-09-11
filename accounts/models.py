from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """Stub custom user model.

    Kept as a plain AbstractUser subclass for now so AUTH_USER_MODEL is set
    before any migrations exist. Organisation and role fields land in #10.
    """
