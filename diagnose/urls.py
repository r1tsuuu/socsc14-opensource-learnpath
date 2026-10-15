from django.urls import path

from . import views

urlpatterns = [
    path("start/", views.intake, name="intake"),
    path("start/check/", views.intake_check, name="intake_check"),
    path("about/", views.about_you, name="about_you"),
    path("skill-check/", views.skill_check, name="skill_check"),
    path("skill-check/questions/", views.skill_check_questions, name="skill_check_questions"),
    path("result/", views.result, name="result"),
]
