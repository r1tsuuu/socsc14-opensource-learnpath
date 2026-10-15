from django.urls import path

from . import views

urlpatterns = [
    path("plan/build/", views.plan_build, name="plan_build"),
    path("plan/", views.plan, name="plan"),
    path("plan/items/<int:item_id>/status/", views.plan_item_status, name="plan_item_status"),
]
