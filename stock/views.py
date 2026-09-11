from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_POST


def home(request):
    count = request.session.get("demo_count", 0)
    return render(request, "stock/home.html", {"count": count})


def ping(request):
    now = timezone.localtime().strftime("%H:%M:%S")
    return render(request, "stock/home.html#ping_result", {"ping_message": f"Server replied at {now}."})


@require_POST
def bump(request):
    count = request.session.get("demo_count", 0) + 1
    request.session["demo_count"] = count
    return render(request, "stock/home.html#counter", {"count": count})
