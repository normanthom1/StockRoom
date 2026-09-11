from django.http import HttpResponse


def home(request):
    return HttpResponse("StockRoom is running.")
