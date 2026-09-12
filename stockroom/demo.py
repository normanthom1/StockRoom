"""Resets the public portfolio demo overnight with no separate Railway
service. See issue #35: instead of a cron job, the first request each day
(NZT) past midnight re-seeds "Demo Dental" inline, on whichever visitor's
request happens to cross the boundary.
"""

from django.conf import settings
from django.core.cache import cache
from django.core.management import call_command
from django.utils import timezone

from stock.models import DemoResetState

# Comfortably longer than seed_demo takes (recreates ~44 items and their history).
RESET_LOCK_SECONDS = 120


class DemoResetMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # WhiteNoise normally answers /static/ before this ever runs, but
        # only once `collectstatic` has actually populated STATIC_ROOT (it
        # hasn't in CI) - check explicitly rather than relying on that.
        # Django normalises STATIC_URL to always start with "/", regardless
        # of how it's written in settings.py ("static/" here).
        if settings.DEMO_MODE and not request.path.startswith(settings.STATIC_URL):
            _reset_if_stale()
        return self.get_response(request)


def _reset_if_stale():
    today = timezone.localdate()
    state = DemoResetState.objects.filter(pk=1).first()
    if state and state.date >= today:
        return
    # cache.add() only succeeds for the first caller to use this key: the
    # first request past the boundary runs the reset, any others racing it
    # today see the lock held and skip straight through.
    if not cache.add(f"demo-reset-lock:{today}", True, RESET_LOCK_SECONDS):
        return
    call_command("seed_demo", reset=True)
    DemoResetState.objects.update_or_create(pk=1, defaults={"date": today})
