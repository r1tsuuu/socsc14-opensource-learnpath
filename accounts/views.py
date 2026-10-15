from django.shortcuts import redirect, render

from .models import get_profile


def home(request):
    profile = get_profile(request.user)
    if not profile.onboarded:
        return redirect("welcome")
    # Onboarded learners go to their plan once M7 lands; until then, their result.
    return redirect("result")


def welcome(request):
    return render(request, "accounts/welcome.html")
