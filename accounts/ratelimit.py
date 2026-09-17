"""Cache-based rate limiting for the views someone can hammer: login,
sign-up, the staff code pad, and anything that calls Gemini.

ponytail: the default cache is per-process memory. That's one bucket today
(gunicorn runs a single worker on Railway); with more workers or instances,
point CACHES at Redis or the database so the counts are shared.
"""

import hashlib
from functools import wraps

from django.conf import settings
from django.core.cache import cache
from django.shortcuts import render

WINDOW = 15 * 60


def client_ip(request):
    header = settings.CLIENT_IP_HEADER
    return (header and request.META.get(header)) or request.META.get("REMOTE_ADDR", "")


def _cache_key(key):
    return "ratelimit:" + hashlib.sha256(key.encode()).hexdigest()


def _over_limit(key, limit, window):
    """Count one attempt against key. True once it's past limit in this window."""
    key = _cache_key(key)
    cache.add(key, 0, window)  # the window starts at the first attempt
    try:
        count = cache.incr(key)
    except ValueError:  # expired between add() and incr()
        cache.set(key, 1, window)
        count = 1
    return count > limit


def rate_limit(scope, *, per_ip, per_field=None, window=WINDOW):
    """Limit a view's POSTs per client IP, and optionally per submitted value.

    per_field is (field_name, limit): e.g. ("username", 5) caps attempts on one
    account however many addresses they come from. The IP limit is looser,
    because a whole practice shares one office connection.
    """

    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if request.method == "POST":
                checks = [(f"{scope}:ip:{client_ip(request)}", per_ip)]
                if per_field and request.POST.get(per_field[0]):
                    value = request.POST[per_field[0]].strip().lower()
                    checks.append((f"{scope}:{per_field[0]}:{value}", per_field[1]))
                # Every key counts the attempt, so work them all out before any().
                over = [_over_limit(key, limit, window) for key, limit in checks]
                if any(over):
                    return render(request, "429.html", {"minutes": window // 60}, status=429)
            return view(request, *args, **kwargs)

        return wrapped

    return decorator


login_rate_limit = rate_limit("login", per_ip=30, per_field=("username", 5))


# Staff codes: only wrong codes count, so a busy practice can switch people
# all day. ponytail: two digits is 100 guesses, so this slows a guesser down
# rather than stopping one; the practice password is the real lock.
CODE_FAILURES = 10


def code_entry_locked(practice):
    return cache.get(_cache_key(f"code:{practice.pk}"), 0) >= CODE_FAILURES


def record_wrong_code(practice):
    _over_limit(f"code:{practice.pk}", CODE_FAILURES, WINDOW)


# Gemini calls (assistant/): each costs money, so every one counts.
AI_PER_HOUR = 20


def ai_limited(request, calls=1):
    """True once this person has had AI_PER_HOUR answers this hour, or every
    practice together has had settings.AI_DAILY_LIMIT today. On the demo,
    visitors share the same staff accounts, so it's per device instead.
    A batch of calls (invoices imported together) is one of this person's,
    but every call counts towards the daily total.
    """
    who = f"ip:{client_ip(request)}" if settings.DEMO_MODE else f"user:{request.user.pk}"
    # Someone already over their own limit doesn't use up everyone's daily total.
    if _over_limit(f"ai:{who}", AI_PER_HOUR, 60 * 60):
        return True
    over = [_over_limit("ai:all", settings.AI_DAILY_LIMIT, 24 * 60 * 60) for _ in range(calls)]  # each one counts
    return any(over)
