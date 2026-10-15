from itertools import groupby

from django.contrib import messages
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import get_profile
from content.models import Resource, Topic, Track

from .forms import QuizForm
from .grading import PASS_PERCENT, latest_result, submit_quiz
from .models import PlanItem
from .planner import active_plan, build_plan, plan_item_for_topic, set_item_status, start_item


@require_POST
def plan_build(request):
    profile = get_profile(request.user)
    if not profile.onboarded or profile.track is None:
        return redirect("about_you")
    build_plan(profile)
    messages.success(request, "Your learning plan is ready.")
    return redirect("plan")


def plan(request):
    profile = get_profile(request.user)
    current = active_plan(profile)
    if current is None:
        return redirect("result")
    items = (
        current.items.select_related("topic")
        .prefetch_related(
            Prefetch("topic__resources", queryset=Resource.objects.filter(is_active=True)),
            "topic__tips",
        )
        .order_by("order")
    )
    weeks = [(week, list(group)) for week, group in groupby(items, key=lambda item: item.week)]
    return render(
        request,
        "learning/plan.html",
        {"plan": current, "weeks": weeks, "progress": current.progress_percent()},
    )


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
    return render(
        request,
        "learning/lesson.html",
        {
            "topic": topic,
            "item": item,
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


def quiz_result(request, topic):
    topic = _active_topic(topic)
    result = latest_result(get_profile(request.user), list(topic.quiz_items.all()))
    if result is None:
        return redirect("quiz", topic=topic.slug)
    return render(
        request,
        "learning/quiz_result.html",
        {"topic": topic, "result": result, "pass_percent": PASS_PERCENT},
    )
