"""
Django settings for stockroom project.
"""

import os
import sys
from pathlib import Path

import dj_database_url
from django.utils.csp import CSP
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name, default=False):
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def env_list(name, default=""):
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get("SECRET_KEY", "django-insecure-dev-only-key-change-me")

DEBUG = env_bool("DEBUG", True)

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1")

CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_tailwind_cli",
    "accounts",
    "stock",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Every view needs a login unless it's marked @login_not_required.
    "django.contrib.auth.middleware.LoginRequiredMiddleware",
    # A practice login only opens the device; stock work needs a staff code.
    "accounts.middleware.PracticeLoginMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django.middleware.csp.ContentSecurityPolicyMiddleware",
    "stockroom.demo.DemoResetMiddleware",
]

# Every script is self-hosted and none is inline (see static/js/app.js), so
# nothing needs 'unsafe-inline' or 'unsafe-eval'. Alpine is the CSP build and
# htmx runs with allowEval off.
SECURE_CSP = {
    "default-src": [CSP.SELF],
    "script-src": [CSP.SELF],
    "style-src": [CSP.SELF],
    "img-src": [CSP.SELF],
    "connect-src": [CSP.SELF],
    "manifest-src": [CSP.SELF],
    "worker-src": [CSP.SELF],
    "object-src": [CSP.NONE],
    "base-uri": [CSP.NONE],
    "form-action": [CSP.SELF],
    "frame-ancestors": [CSP.NONE],
}

# Not /admin/, so drive-by scanners don't find the login. Set ADMIN_URL on
# Railway to keep the real one out of this public repo.
ADMIN_URL = os.environ.get("ADMIN_URL", "platform/")

ROOT_URLCONF = "stockroom.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "stockroom.wsgi.application"


# Database
# https://docs.djangoproject.com/en/6.1/ref/settings/#databases

DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=600,
    )
}


AUTH_USER_MODEL = "accounts.User"

LOGIN_REDIRECT_URL = "stock:home"
LOGOUT_REDIRECT_URL = "login"

# Staff log in on their phones once and stay logged in.
SESSION_COOKIE_AGE = 60 * 60 * 24 * 60  # 60 days

# The public portfolio demo: one-click sign-in to the demo practice, a nightly
# reset of it (see stockroom.demo.DemoResetMiddleware), and
# sign-up hidden by default (a demo visitor uses the demo practice, not
# their own) unless explicitly turned back on.
DEMO_MODE = env_bool("DEMO_MODE", False)
SIGNUP_ENABLED = env_bool("SIGNUP_ENABLED", not DEMO_MODE)

# Deliberately no provider (#13). Staff have no email or password of their
# own (they sign in with a code), so the only mail is the practice login's
# self-service password reset link. It prints to stdout, which is Railway's
# logs in production, and stays a manual last resort.
MAILERS = {"default": {"BACKEND": "stockroom.mail.ReadableConsoleBackend"}}
DEFAULT_FROM_EMAIL = "StockRoom <no-reply@stockroom.invalid>"


# Password validation
# https://docs.djangoproject.com/en/6.1/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

if "test" in sys.argv:
    # Real password hashing is deliberately slow; tests create a lot of users.
    # https://docs.djangoproject.com/en/stable/topics/testing/overview/#password-hashing
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    # The cache only holds rate-limit counts, and tests don't reset it, so a
    # real one would 429 whichever test happened to log in 31st. The rate-limit
    # tests switch a real cache back on for themselves.
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}}


# Internationalization
# https://docs.djangoproject.com/en/6.1/topics/i18n/

LANGUAGE_CODE = "en-nz"

TIME_ZONE = "Pacific/Auckland"

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.1/howto/static-files/

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        # The manifest storage needs collectstatic to have run, so {% static %}
        # can only resolve hashed filenames once that manifest exists. Plain
        # storage in DEBUG lets runserver and tests serve static files as-is.
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        )
    },
}

# Tailwind CSS (django-tailwind-cli): our own source file carries the design
# tokens below, so the library never regenerates it (see .claude/skills/ui-design).
TAILWIND_CLI_SRC_CSS = "src/tailwind.css"
TAILWIND_CLI_DIST_CSS = "css/tailwind.css"


# Deployment (Railway terminates TLS at the edge and proxies plain HTTP to us).

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
    # Railway's healthcheck calls the container over plain HTTP, with no proxy header.
    SECURE_REDIRECT_EXEMPT = [r"^healthz$"]
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

# Preloading is a commitment for the whole domain once submitted; decide on it
# with a custom domain, not on *.up.railway.app.
SILENCED_SYSTEM_CHECKS = ["security.W021"]

# Railway's edge puts the visitor's address in X-Real-IP; REMOTE_ADDR is the
# proxy. Locally and in tests there's no proxy, so REMOTE_ADDR is right.
CLIENT_IP_HEADER = None if DEBUG else "HTTP_X_REAL_IP"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
