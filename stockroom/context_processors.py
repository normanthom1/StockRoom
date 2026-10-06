from django.conf import settings


def ai_enabled(request):
    return {"ai_enabled": bool(settings.AI_API_KEY)}


def ask_turns(request):
    """The chat on Home: its last day of questions and answers."""
    if not settings.AI_API_KEY or not hasattr(request, "session"):
        return {}
    from assistant.views import recent_turns

    return {"ask_turns": recent_turns(request.session)}
