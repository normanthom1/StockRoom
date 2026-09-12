import hashlib
import json

from django.contrib.auth.decorators import login_not_required
from django.contrib.staticfiles import finders
from django.db import connection
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.urls import reverse


@login_not_required
def healthz(request):
    """Used by Railway's healthcheck. A real query, not just a 200, so a
    database that's down or still migrating shows as unhealthy too."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return HttpResponse("ok")


PRECACHE_STATIC = [
    "css/tailwind.css",
    "vendor/htmx.min.js",
    "vendor/alpine.min.js",
    "manifest.webmanifest",
    "icons/icon.svg",
    "icons/favicon-32.png",
    "icons/apple-touch-icon.png",
]


def _offline_html():
    # Rendered without the request, so the precached copy can never carry a
    # user's name or practice, whoever happened to be logged in when it was fetched.
    return render_to_string("offline.html")


@login_not_required
def offline(request):
    return HttpResponse(_offline_html())


def sw_version(offline_html):
    """Hash of the precached files' contents and the offline page: changes
    exactly when a deploy ships something the service worker has cached."""
    digest = hashlib.sha256(offline_html.encode())
    for path in PRECACHE_STATIC:
        found = finders.find(path)
        if found:  # css/tailwind.css only exists once `tailwind build` has run (not in CI)
            with open(found, "rb") as f:
                digest.update(f.read())
    return digest.hexdigest()[:12]


# Served from the site root (not /static/) so its scope covers the whole app.
@login_not_required
def service_worker(request):
    offline_html = _offline_html()
    offline_url = reverse("offline")
    js = render_to_string("sw.js", {
        "version": sw_version(offline_html),
        "precache": json.dumps([static(path) for path in PRECACHE_STATIC] + [offline_url]),
        "offline_url": json.dumps(offline_url),
        "capture_page_url": json.dumps(reverse("stock:log_usage")),
        "session_urls": json.dumps([reverse("login"), reverse("logout")]),
        "offline_fragment": json.dumps(render_to_string("offline.html#message").strip()),
    })
    return HttpResponse(js, content_type="application/javascript")
