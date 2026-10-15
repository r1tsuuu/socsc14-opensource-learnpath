from django.contrib import messages
from django.shortcuts import redirect, render

from accounts.models import get_profile

from .forms import AboutYouForm, SkillCheckForm
from .models import SkillCheckAttempt
from .scoring import finish_attempt, results_for_attempt, track_topics_and_questions


def about_you(request):
    profile = get_profile(request.user)
    form = AboutYouForm(request.POST or None, instance=profile)
    if request.method == "POST" and form.is_valid():
        profile = form.save(commit=False)
        profile.onboarded = True
        profile.save()
        return redirect("skill_check")
    return render(request, "diagnose/about_you.html", {"form": form})


def _profile_with_track(request):
    profile = get_profile(request.user)
    if not profile.onboarded or profile.track is None:
        return None
    return profile


def skill_check(request):
    profile = _profile_with_track(request)
    if profile is None:
        return redirect("about_you")
    _, questions = track_topics_and_questions(profile.track)
    return render(
        request,
        "diagnose/skill_check.html",
        {"track": profile.track, "question_count": len(questions)},
    )


def skill_check_questions(request):
    profile = _profile_with_track(request)
    if profile is None:
        return redirect("about_you")
    _, questions = track_topics_and_questions(profile.track)
    form = SkillCheckForm(request.POST or None, questions=questions)

    if request.method == "POST" and form.is_valid():
        attempt = (
            SkillCheckAttempt.objects.filter(
                learner=profile, track=profile.track, finished_at__isnull=True
            ).first()
            or SkillCheckAttempt.objects.create(learner=profile, track=profile.track)
        )
        finish_attempt(attempt, form.answers())
        messages.success(request, "Skill check done. Here's what we found.")
        return redirect("result")

    if request.method == "GET":
        # Record when the learner started; reuse an unfinished attempt if they reload.
        SkillCheckAttempt.objects.get_or_create(
            learner=profile, track=profile.track, finished_at__isnull=True
        )
    return render(
        request,
        "diagnose/skill_check_questions.html",
        {"track": profile.track, "form": form},
    )


def result(request):
    profile = _profile_with_track(request)
    if profile is None:
        return redirect("about_you")
    attempt = (
        SkillCheckAttempt.objects.filter(
            learner=profile, track=profile.track, finished_at__isnull=False
        )
        .order_by("-finished_at")
        .first()
    )
    if attempt is None:
        return redirect("skill_check")
    results = results_for_attempt(attempt)
    return render(
        request,
        "diagnose/result.html",
        {
            "track": profile.track,
            "known": [r for r in results if r.known],
            "to_learn": [r for r in results if not r.known],
        },
    )
