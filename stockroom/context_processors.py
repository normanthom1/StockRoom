from django.conf import settings


def ai_enabled(request):
    return {"ai_enabled": bool(settings.AI_API_KEY)}


def ask_turns(request):
    """The chat on Home: its last day of questions and answers, and any change cards still live."""
    if not settings.AI_API_KEY or not hasattr(request, "session"):
        return {}
    from assistant.views import chat_turns

    return {"ask_turns": chat_turns(request.session)}
