from itertools import groupby

from django.contrib import messages
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import get_profile
from content.models import Resource

from .models import PlanItem
from .planner import active_plan, build_plan, set_item_status


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
