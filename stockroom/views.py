from django.db import connection
from django.http import HttpResponse


def healthz(request):
    """Used by Railway's healthcheck. A real query, not just a 200, so a
    database that's down or still migrating shows as unhealthy too."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return HttpResponse("ok")
