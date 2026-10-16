from django.contrib import messages
from django.shortcuts import redirect, render

from accounts.models import get_profile
from assistant.service import ai_available, reserve_intake
from content.models import Topic
from learning.tailoring import build_plan_with_template, tailor_plan

from . import onboarding
from .forms import AboutYouForm, SkillCheckForm
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


ONBOARDING_KEY = "onboarding"


def _end_intake(request):
    request.session.pop(ONBOARDING_KEY, None)


def _build_plan(profile, project_idea):
    """Tailor the plan with AI when it's on and under today's cap; otherwise rules only."""
    if ai_available() and reserve_intake(profile):
        _, used_ai = tailor_plan(profile, project_idea)
        return used_ai
    build_plan_with_template(profile)
    return False


def intake(request):
    """Guided onboarding: one question at a time with tappable choices, then a review."""
    if not onboarding.available_paths():
        messages.info(request, "Guided planning isn't set up yet, so here's the short form instead.")
        return redirect("about_you")

    answers = request.session.get(ONBOARDING_KEY, {})
    if request.method == "GET" and request.GET.get("edit") and not answers:
        # "Change my answers" from the plan: start from the saved profile, on the review.
        answers = onboarding.answers_from_profile(get_profile(request.user))
        request.session[ONBOARDING_KEY] = answers
        return redirect("intake")

    if request.method == "POST":
        action = request.POST.get("action", "answer")
        if action == "restart":
            _end_intake(request)
            return redirect("intake")
        if action == "change":
            # Re-ask one step; the other answers are kept.
            step = request.POST.get("step", "")
            if step in onboarding.STEPS:
                answers = {k: v for k, v in answers.items() if k != step}
                request.session[ONBOARDING_KEY] = answers
            return redirect("intake")
        if action == "confirm":
            if not onboarding.is_complete(answers):
                return redirect("intake")
            profile = get_profile(request.user)
            project_idea = onboarding.save_answers(profile, answers)
            used_ai = _build_plan(profile, project_idea)
            _end_intake(request)
            if used_ai:
                messages.success(request, "Your plan is ready, built around your project. Start with the first step below.")
            else:
                messages.success(request, "Your plan is ready. Start with the first step below.")
            return redirect("plan")

        step = onboarding.next_step(answers)
        if request.POST.get("step") != step:
            # A double-click or an old tab answering a question that's already done.
            return redirect("intake")
        try:
            answers = onboarding.apply_answer(
                answers, step,
                choice=request.POST.get("choice", ""),
                text=request.POST.get("text", ""),
                picked=request.POST.getlist("picked"),
            )
        except onboarding.AnswerError as error:
            messages.error(request, str(error))
        else:
            request.session[ONBOARDING_KEY] = answers
        return redirect("intake")

    step = onboarding.next_step(answers)
    if step == "check":
        return redirect("intake_check")
    steps = onboarding.steps_for(answers)
    context = {
        "transcript": onboarding.transcript(answers),
        "started": bool(answers),
        "phase": onboarding.PHASES[step],
        "done_count": len([s for s in steps if s in answers]),
        "step_count": len(steps),
        "max_length": onboarding.MAX_TEXT,
    }
    if step == "review":
        context.update(
            review=True,
            fit=onboarding.fit(answers),
            hours=onboarding.hours_per_week(answers),
            path=onboarding.get_path(answers["path"]),
            check=answers.get("check"),
        )
    else:
        context["question"] = onboarding.question_for(step, answers)
    return render(request, "diagnose/intake.html", context)


def intake_check(request):
    """The quick check, inside onboarding: questions only on the topics the learner ticked."""
    answers = request.session.get(ONBOARDING_KEY, {})
    if onboarding.next_step(answers) != "check":
        return redirect("intake")
    track = onboarding.first_track(answers)
    slugs = onboarding.claimed_slugs(answers, track)
    claimed_ids = set(Topic.objects.filter(track=track, slug__in=slugs).values_list("pk", flat=True))
    _, questions = track_topics_and_questions(track)
    questions = [q for q in questions if q.topic_id in claimed_ids]
    form = SkillCheckForm(request.POST if request.method == "POST" else None, questions=questions)

    if request.method == "POST" and (form.is_valid() or not questions):
        profile = get_profile(request.user)
        attempt = SkillCheckAttempt.objects.create(learner=profile, track=track)
        results = finish_quick_check(attempt, claimed_ids, form.answers() if questions else {})
        request.session[ONBOARDING_KEY] = onboarding.record_check(answers, results)
        return redirect("intake")

    steps = onboarding.steps_for(answers)
    return render(
        request,
        "diagnose/intake_check.html",
        {
            "track": track,
            "form": form,
            "has_questions": bool(questions),
            "not_sure": (answers.get("known") or {}).get("not_sure", False),
            "phase": onboarding.PHASES["check"],
            "done_count": steps.index("check"),
            "step_count": len(steps),
        },
    )
