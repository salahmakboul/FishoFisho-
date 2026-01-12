from django.http import HttpResponse


def simple_test(request):
    return HttpResponse("OK - Simple test works")
