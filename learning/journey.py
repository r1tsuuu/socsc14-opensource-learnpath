"""What the plan page shows: where the learner stands and the one thing to do next.

Rule-based on purpose: every suggestion comes from recorded evidence (quiz results,
check answers, plan statuses), never from guesses. Engagement alone (visits, time
on the page) never counts as progress.
"""

from dataclasses import dataclass

from django.db.models import Max
from django.urls import reverse
from django.utils import timezone

from diagnose.models import TopicMastery

from .models import PlanItem, QuizAttempt
from .planner import estimate_weeks, plan_tracks

# A topic passed on a quiz comes back for a short review after this long.
REVIEW_AFTER_DAYS = 7

Status = TopicMastery.Status


@dataclass
class NextAction:
    kind: str  # review, continue, start, project
    title: str
    text: str
    url: str
    button: str


def due_reviews(profile, plan):
    """Topics to review now: ones that need a review, or quiz passes a week old or more."""
    done = {
        item.topic_id: item.topic
        for item in plan.items.select_related("topic").filter(status=PlanItem.Status.DONE)
    }
    masteries = {
        m.topic_id: m for m in TopicMastery.objects.filter(learner=profile, topic_id__in=done)
    }
    last_quiz = dict(
        QuizAttempt.objects.filter(learner=profile, quiz_item__topic_id__in=done)
        .values("quiz_item__topic_id").annotate(last=Max("created_at"))
        .values_list("quiz_item__topic_id", "last")
    )
    cutoff = timezone.now() - timezone.timedelta(days=REVIEW_AFTER_DAYS)
    due = []
    for topic_id, topic in done.items():
        mastery = masteries.get(topic_id)
        if mastery is None:
            continue
        if mastery.status == Status.NEEDS_REVIEW:
            due.append(topic)
        elif mastery.source == TopicMastery.Source.QUIZ and topic_id in last_quiz and last_quiz[topic_id] <= cutoff:
            due.append(topic)
    return due


def next_action(profile, plan, items, reviews):
    """The single clearest next step: a due review, the topic in progress, or the next one."""
    if reviews:
        topic = reviews[0]
        return NextAction(
            "review", f"Quick review: {topic.title}",
            "You learned this a while ago. A short quiz now helps it stick.",
            reverse("quiz", args=[topic.slug]), "Take the review quiz",
        )
    for status, kind, button in ((PlanItem.Status.IN_PROGRESS, "continue", "Continue"),
                                 (PlanItem.Status.TODO, "start", "Start")):
        item = next((i for i in items if i.status == status), None)
        if item is not None:
            text = item.reason or item.topic.summary or "Read the links, try things out, then take the short quiz."
            return NextAction(kind, item.topic.title, text, reverse("lesson", args=[item.topic.slug]), button)
    project = getattr(plan, "project", None)
    if project is not None:
        return NextAction(
            "project", f"Finish your project: {project.title}",
            "You've worked through every topic. Bring it together in your project.",
            "#project", "See the project steps",
        )
    return None


def starting_point(profile, plan):
    """What the learner has shown, grouped, for the topics their plan's tracks cover."""
    groups = {"shown": [], "partly": [], "to_work_on": []}
    for mastery in TopicMastery.objects.filter(
        learner=profile, topic__track__in=plan_tracks(profile)
    ).select_related("topic").order_by("topic__track", "topic__order"):
        if mastery.status == Status.DEMONSTRATED:
            groups["shown"].append(mastery.topic.title)
        elif mastery.status in (Status.EMERGING, Status.NEEDS_REVIEW):
            groups["partly"].append(mastery.topic.title)
        elif mastery.status == Status.NOT_YET:
            groups["to_work_on"].append(mastery.topic.title)
    return groups


def estimate(profile, plan, items):
    """Weeks left at the learner's pace, and whether that fits the date they gave."""
    remaining = sum(
        i.topic.estimated_minutes for i in items
        if i.status not in (PlanItem.Status.DONE, PlanItem.Status.SKIPPED)
    )
    low, high = estimate_weeks(remaining, profile.hours_per_week)
    weeks_left = None
    if profile.target_weeks:
        elapsed = (timezone.now() - plan.created_at).days // 7
        weeks_left = max(profile.target_weeks - elapsed, 0)
    return {
        "low": low,
        "high": high,
        "hours": profile.hours_per_week or 1,
        "weeks_left": weeks_left,
        "fits": weeks_left is None or high <= weeks_left,
    }


def unchecked_topic_ids(profile, items):
    """Topics in the plan with no evidence yet: the learner may already know them."""
    seen = set(
        TopicMastery.objects.filter(learner=profile, topic_id__in=[i.topic_id for i in items])
        .exclude(status=Status.UNKNOWN).values_list("topic_id", flat=True)
    )
    return {i.topic_id for i in items if i.topic_id not in seen}


def milestones_by_week(plan):
    project = getattr(plan, "project", None)
    result = {}
    for milestone in (project.milestones if project else []):
        result.setdefault(milestone.get("week"), []).append(milestone["text"])
    return result
