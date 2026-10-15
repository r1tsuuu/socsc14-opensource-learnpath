from django.shortcuts import redirect, render

from .models import LearnerProfile


def home(request):
    profile, _ = LearnerProfile.objects.get_or_create(user=request.user)
    if not profile.onboarded:
        return redirect("welcome")
    # Onboarded learners go to their plan once M7 lands.
    return redirect("welcome")


def welcome(request):
    return render(request, "accounts/welcome.html")
