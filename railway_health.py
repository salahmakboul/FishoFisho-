from django.http import HttpResponse
from django.urls import path

def railway_health(request):
    return HttpResponse("Railway Health Check: OK", status=200)

urlpatterns = [
    path('railway-health/', railway_health),
]
