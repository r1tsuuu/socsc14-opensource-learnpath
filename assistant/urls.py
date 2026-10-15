from django.urls import path

from . import views

urlpatterns = [
    path("help/ask/", views.ask, name="help_ask"),
    path("help/test/", views.test_page, name="help_test"),
]
