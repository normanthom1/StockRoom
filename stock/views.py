import json

from django.http import HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.decorators import admin_required


def home(request):
    count = request.session.get("demo_count", 0)
    return render(request, "stock/home.html", {"count": count})


def _coming_soon(request, title):
    return render(request, "stock/coming_soon.html", {"title": title})


def log_usage(request):
    return _coming_soon(request, "Log usage")


def reorder_list(request):
    return _coming_soon(request, "Reorder list")


def deliveries(request):
    return _coming_soon(request, "Deliveries")


@admin_required
def items(request):
    return _coming_soon(request, "Stock")


@admin_required
def suppliers(request):
    return _coming_soon(request, "Suppliers")


@admin_required
def spending(request):
    return _coming_soon(request, "Spending")


def demo_sheet(request):
    return render(request, "stock/home.html#demo_sheet")


def _toast_response(message, undo_url=None):
    response = HttpResponse(status=204)
    response["HX-Trigger"] = json.dumps({"toast": {"message": message, "undo_url": undo_url}})
    return response


@require_POST
def demo_toast(request):
    return _toast_response("Logged: running low on gloves", reverse("stock:demo_undo"))


@require_POST
def demo_undo(request):
    return _toast_response("Undone.")


def ping(request):
    now = timezone.localtime().strftime("%H:%M:%S")
    return render(request, "stock/home.html#ping_result", {"ping_message": f"Server replied at {now}."})


@require_POST
def bump(request):
    count = request.session.get("demo_count", 0) + 1
    request.session["demo_count"] = count
    return render(request, "stock/home.html#counter", {"count": count})
