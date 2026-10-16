from django.urls import path

from . import views

urlpatterns = [
    path("plan/build/", views.plan_build, name="plan_build"),
    path("plan/", views.plan, name="plan"),
    path("plan/items/<int:item_id>/status/", views.plan_item_status, name="plan_item_status"),
    path("plan/pace/", views.plan_pace, name="plan_pace"),
    path("plan/report/", views.plan_report, name="plan_report"),
    path("learn/<slug:track>/setup/", views.setup_guide, name="setup_guide"),
    path("learn/<slug:topic>/", views.lesson, name="lesson"),
    path("learn/<slug:topic>/quiz/", views.quiz, name="quiz"),
    path("learn/<slug:topic>/quiz/result/", views.quiz_result, name="quiz_result"),
]
