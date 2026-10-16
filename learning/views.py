from itertools import groupby

from django.contrib import messages
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import get_profile
from content.models import Resource, Topic, Track

from . import journey
from .forms import QuizForm
from .grading import PASS_PERCENT, latest_result, submit_quiz
from .models import PlanFeedback, PlanItem, QuizAttempt
from .planner import active_plan, plan_item_for_topic, plan_tracks, repace_plan, set_item_status, start_item
from .tailoring import build_plan_with_template


@require_POST
def plan_build(request):
    profile = get_profile(request.user)
    if not profile.onboarded or profile.track is None:
        return redirect("about_you")
    build_plan_with_template(profile)
    messages.success(request, "Your learning plan is ready.")
    return redirect("plan")


def plan(request):
    profile = get_profile(request.user)
    current = active_plan(profile)
    if current is None:
        return redirect("result")
    items = list(
        current.items.select_related("topic__track")
        .prefetch_related(
            Prefetch("topic__resources", queryset=Resource.objects.filter(is_active=True)),
            "topic__tips",
            "topic__quiz_items",
        )
        .order_by("order")
    )
    milestones = journey.milestones_by_week(current)
    weeks = []
    for week, group in groupby(items, key=lambda item: item.week):
        group = list(group)
        weeks.append({
            "number": week,
            "items": group,
            "milestones": milestones.get(week, []),
            "done": sum(1 for i in group if i.status in (PlanItem.Status.DONE, PlanItem.Status.SKIPPED)),
        })
    reviews = journey.due_reviews(profile, current)
    starting = journey.starting_point(profile, current)
    unchecked = journey.unchecked_topic_ids(profile, items)
    next_action = journey.next_action(profile, current, items, reviews)
    # The week to open: the one with the next topic, else the first with anything left.
    unfinished = [i for i in items if i.status in (PlanItem.Status.TODO, PlanItem.Status.IN_PROGRESS)]
    current_week = next(
        (i.week for i in unfinished if next_action and i.topic_id == next_action.topic_id),
        unfinished[0].week if unfinished else None,
    )
    return render(
        request,
        "learning/plan.html",
        {
            "plan": current,
            "profile": profile,
            "tracks": plan_tracks(profile),
            "weeks": weeks,
            "progress": current.progress_percent(),
            "project": getattr(current, "project", None),
            "next_action": next_action,
            "current_week": current_week,
            "done_count": sum(1 for i in items if i.status == PlanItem.Status.DONE),
            "counted": sum(1 for i in items if i.status != PlanItem.Status.SKIPPED),
            "reviews": reviews,
            "starting": starting,
            "unchecked_count": len(unchecked),
            "unchecked": unchecked,
            "estimate": journey.estimate(profile, current, items),
            "hours_choices": [1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20],
        },
    )


@require_POST
def plan_pace(request):
    """Change weekly time: the same plan, spread over different weeks."""
    profile = get_profile(request.user)
    current = active_plan(profile)
    try:
        hours = min(max(int(request.POST.get("hours", "")), 1), 40)
    except ValueError:
        messages.error(request, "Pick how many hours a week.")
        return redirect("plan")
    if current is not None:
        profile.hours_per_week = hours
        profile.save(update_fields=["hours_per_week"])
        repace_plan(current, hours)
        messages.success(request, f"Your plan now fits {hours} hour{'s' if hours != 1 else ''} a week. Your progress is kept.")
    return redirect("plan")


@require_POST
def plan_report(request):
    """A learner flags something in their plan that looks wrong (AI reasons, project, links)."""
    profile = get_profile(request.user)
    current = active_plan(profile)
    text = " ".join(request.POST.get("text", "").split())[:500]
    if current is None or not text:
        messages.error(request, "Tell us what looks wrong first.")
        return redirect("plan")
    PlanFeedback.objects.create(learner=profile, plan=current, text=text)
    messages.success(request, "Thanks! The team will take a look.")
    return redirect("plan")


ALLOWED_STATUSES = {PlanItem.Status.DONE, PlanItem.Status.SKIPPED, PlanItem.Status.TODO}


@require_POST
def plan_item_status(request, item_id):
    item = get_object_or_404(
        PlanItem.objects.select_related("plan__learner", "topic"),
        pk=item_id,
        plan__learner__user=request.user,
        plan__is_active=True,
    )
    status = request.POST.get("status")
    if status not in ALLOWED_STATUSES:
        messages.error(request, "That status isn't allowed.")
        return redirect("plan")
    set_item_status(item, status)
    messages.success(request, f"{item.topic.title}: {item.get_status_display().lower()}.")
    return redirect("plan")


def setup_guide(request, track):
    track = get_object_or_404(Track, slug=track, is_active=True)
    return render(
        request,
        "learning/setup_guide.html",
        {"track": track, "steps": track.setup_steps.all()},
    )


def _active_topic(slug):
    return get_object_or_404(Topic.objects.select_related("track"), slug=slug, track__is_active=True)


def lesson(request, topic):
    topic = _active_topic(topic)
    item = plan_item_for_topic(get_profile(request.user), topic)
    start_item(item)
    # What to build with this topic: the project step for the week it's in.
    build_steps = journey.milestones_by_week(item.plan).get(item.week, []) if item else []
    return render(
        request,
        "learning/lesson.html",
        {
            "topic": topic,
            "item": item,
            "build_steps": build_steps,
            "resources": topic.resources.filter(is_active=True),
            "tips": topic.tips.all(),
            "prerequisites": topic.prerequisites.all(),
            "has_quiz": topic.quiz_items.exists(),
        },
    )


def quiz(request, topic):
    topic = _active_topic(topic)
    items = list(topic.quiz_items.all())
    form = QuizForm(request.POST or None, items=items)
    if request.method == "POST" and items and form.is_valid():
        result = submit_quiz(get_profile(request.user), topic, items, form.answers())
        if result.passed:
            messages.success(request, f"You passed with {result.percent}%! {topic.title} is done.")
        else:
            messages.error(request, f"You got {result.percent}%. You need {PASS_PERCENT}% to pass. Try again!")
        return redirect("quiz_result", topic=topic.slug)
    return render(
        request,
        "learning/quiz.html",
        {"topic": topic, "form": form, "pass_percent": PASS_PERCENT},
    )


def _after_pass(profile):
    """Momentum after a pass: plan progress and the next step to go to."""
    current = active_plan(profile)
    if current is None:
        return {}
    items = list(current.items.select_related("topic").order_by("order"))
    return {
        "plan_progress": current.progress_percent(),
        "next_action": journey.next_action(profile, current, items, journey.due_reviews(profile, current)),
    }


def quiz_result(request, topic):
    topic = _active_topic(topic)
    profile = get_profile(request.user)
    items = list(topic.quiz_items.all())
    result = latest_result(profile, items)
    if result is None:
        return redirect("quiz", topic=topic.slug)
    # Rough count of tries: each submission saves one attempt per item.
    tries = QuizAttempt.objects.filter(learner=profile, quiz_item__in=items).count() // max(len(items), 1)
    return render(
        request,
        "learning/quiz_result.html",
        {
            "topic": topic,
            "result": result,
            "pass_percent": PASS_PERCENT,
            # After a couple of misses, point back to what this topic builds on.
            "struggling": not result.passed and tries >= 2,
            **(_after_pass(profile) if result.passed else {}),
            "prerequisites": topic.prerequisites.filter(track__is_active=True),
            "resources": topic.resources.filter(is_active=True),
        },
    )
