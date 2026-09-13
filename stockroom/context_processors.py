from django.conf import settings


def ai_enabled(request):
    return {"ai_enabled": bool(settings.AI_API_KEY)}
