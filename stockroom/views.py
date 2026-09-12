from django.contrib.auth.decorators import login_not_required
from django.db import connection
from django.http import HttpResponse


@login_not_required
def healthz(request):
    """Used by Railway's healthcheck. A real query, not just a 200, so a
    database that's down or still migrating shows as unhealthy too."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return HttpResponse("ok")


# Served from the site root (not /static/) so its scope covers the whole
# app, not just one subdirectory. The actual caching strategy lands in #30 -
# this just registers a controller so the app is installable now.
SERVICE_WORKER_JS = """\
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});
"""


@login_not_required
def service_worker(request):
    return HttpResponse(SERVICE_WORKER_JS, content_type="application/javascript")
