from django.contrib import messages
from django.shortcuts import redirect, render

from accounts.models import get_profile
from assistant.service import ai_available, intakes_left_today, reserve_intake
from content.models import Topic
from learning.tailoring import tailor_plan

from .forms import AboutYouForm, SkillCheckForm
from .intake import MAX_MESSAGE_LENGTH, available_tracks, new_history, save_profile, take_turn
from .models import SkillCheckAttempt
from .scoring import (
    finish_attempt,
    finish_quick_check,
    results_for_attempt,
    track_topics_and_questions,
)


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
    form = SkillCheckForm(request.POST if request.method == "POST" else None, questions=questions)

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


INTAKE_KEY = "intake_chat"
INTAKE_RESULT_KEY = "intake_result"
INTAKE_DRAFT_KEY = "intake_draft"
INTAKE_FAILURES_KEY = "intake_failures"
# When the AI is busy the chat is kept so the learner can resend; after this many
# failed sends in a row they get the short form instead.
MAX_INTAKE_FAILURES = 2


def _end_intake(request):
    for key in (INTAKE_KEY, INTAKE_RESULT_KEY, INTAKE_DRAFT_KEY, INTAKE_FAILURES_KEY):
        request.session.pop(key, None)


def _to_form(request, text):
    _end_intake(request)
    messages.info(request, text)
    return redirect("about_you")


def intake(request):
    """Chat with the AI about what the learner wants, then go to the quick check."""
    tracks = available_tracks()
    if not ai_available() or not tracks:
        return _to_form(request, "The AI guide is off right now, so here's the short form instead.")

    history = request.session.get(INTAKE_KEY)
    if request.method == "POST":
        if request.POST.get("action") == "restart":
            _end_intake(request)
            return redirect("intake")
        text = request.POST.get("message", "").strip()
        if not text:
            messages.error(request, "Type a message first.")
            return redirect("intake")
        profile = get_profile(request.user)
        if history is None:
            if not reserve_intake(profile):
                return _to_form(request, "You've planned with the AI guide enough for today, so here's the short form instead.")
            history = new_history()
        reply_history, result = take_turn(history, text, tracks)
        if reply_history is None:
            failures = request.session.get(INTAKE_FAILURES_KEY, 0) + 1
            if failures >= MAX_INTAKE_FAILURES:
                return _to_form(request, "The AI guide is busy right now, so here's the short form instead.")
            # Keep the chat (and the intake already counted today) so a resend continues it.
            request.session[INTAKE_KEY] = history
            request.session[INTAKE_DRAFT_KEY] = text
            request.session[INTAKE_FAILURES_KEY] = failures
            messages.error(request, "The AI guide is busy right now. Wait a minute, then send your message again.")
            return redirect("intake")
        history = reply_history
        request.session.pop(INTAKE_DRAFT_KEY, None)
        request.session.pop(INTAKE_FAILURES_KEY, None)
        if result is None:
            request.session[INTAKE_KEY] = history
            return redirect("intake")
        save_profile(profile, result)
        request.session.pop(INTAKE_KEY, None)
        request.session[INTAKE_RESULT_KEY] = {
            "known_topics": result["known_topics"],
            "project_idea": result["project_idea"],
        }
        messages.success(request, history[-1]["content"])
        return redirect("intake_check")

    if history is None and intakes_left_today(get_profile(request.user)) == 0:
        return _to_form(request, "You've planned with the AI guide enough for today, so here's the short form instead.")
    return render(
        request,
        "diagnose/intake.html",
        {
            "history": history or new_history(),
            "started": history is not None,
            "draft": request.session.get(INTAKE_DRAFT_KEY, ""),
            "max_length": MAX_MESSAGE_LENGTH,
        },
    )


def intake_check(request):
    """Quick check on the topics the learner said they know, then build the tailored plan."""
    result = request.session.get(INTAKE_RESULT_KEY)
    profile = _profile_with_track(request)
    if result is None or profile is None:
        return redirect("intake")
    _, questions = track_topics_and_questions(profile.track)
    claimed_ids = set(
        Topic.objects.filter(track=profile.track, slug__in=result["known_topics"]).values_list("pk", flat=True)
    )
    questions = [q for q in questions if q.topic_id in claimed_ids]
    form = SkillCheckForm(request.POST if request.method == "POST" else None, questions=questions)

    if request.method == "POST" and form.is_valid():
        attempt = SkillCheckAttempt.objects.create(learner=profile, track=profile.track)
        finish_quick_check(attempt, claimed_ids, form.answers())
        _, used_ai = tailor_plan(profile, result.get("project_idea", ""))
        _end_intake(request)
        if used_ai:
            messages.success(request, "Your plan and project are ready, made for your goal.")
        else:
            messages.success(request, "Your plan is ready.")
        return redirect("plan")

    return render(
        request,
        "diagnose/intake_check.html",
        {"track": profile.track, "form": form, "has_questions": bool(questions)},
    )
