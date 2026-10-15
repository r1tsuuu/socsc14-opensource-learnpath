"""Plan builder. Pure ordering and week rules first, then one function that saves a plan.

- Topics the learner already knows are skipped.
- Every prerequisite comes before the topics that need it; otherwise the
  content team's `order` is kept.
- If the prerequisites contain a cycle, fall back to `order` (never loop forever).
- Weeks fill up to hours_per_week * 60 minutes (minimum 1 hour). A topic that
  doesn't fit starts the next week; a topic longer than a week gets one to itself.
"""

import heapq

from django.db import transaction
from django.utils import timezone

from content.models import Topic
from diagnose.models import TopicMastery

from .models import LearningPlan, PlanItem


def _sort_key(topic):
    return (topic.order, topic.title, topic.pk)


def order_topics(topics, prerequisites):
    """Order topics so prerequisites come first, breaking ties by content order.

    `prerequisites` maps a topic id to the ids of the topics it needs. Ids not
    in `topics` (for example, topics the learner already knows) are ignored.
    """
    by_id = {t.pk: t for t in topics}
    needs = {pk: set(prerequisites.get(pk, ())) & by_id.keys() - {pk} for pk in by_id}
    needed_by = {pk: [] for pk in by_id}
    for pk, reqs in needs.items():
        for req in reqs:
            needed_by[req].append(pk)

    ready = [(_sort_key(by_id[pk]), pk) for pk, reqs in needs.items() if not reqs]
    heapq.heapify(ready)
    remaining = {pk: len(reqs) for pk, reqs in needs.items()}
    ordered = []
    while ready:
        _, pk = heapq.heappop(ready)
        ordered.append(by_id[pk])
        for dependent in needed_by[pk]:
            remaining[dependent] -= 1
            if remaining[dependent] == 0:
                heapq.heappush(ready, (_sort_key(by_id[dependent]), dependent))

    if len(ordered) < len(by_id):
        # A prerequisite cycle: some topics never became ready. Keep the content order.
        return sorted(topics, key=_sort_key)
    return ordered


def assign_weeks(topics, hours_per_week):
    """Return a week number (starting at 1) for each topic, in the same order."""
    capacity = max(hours_per_week or 1, 1) * 60
    weeks = []
    week, used = 1, 0
    for topic in topics:
        minutes = topic.estimated_minutes
        if used > 0 and used + minutes > capacity:
            week, used = week + 1, 0
        weeks.append(week)
        used += minutes
    return weeks


@transaction.atomic
def build_plan(profile):
    """Replace the learner's active plan for their track with a fresh one."""
    track = profile.track
    known = set(
        TopicMastery.objects.filter(learner=profile, topic__track=track, known=True)
        .values_list("topic_id", flat=True)
    )
    topics = list(Topic.objects.filter(track=track).exclude(pk__in=known))
    prerequisites = {}
    for topic_id, prereq_id in Topic.prerequisites.through.objects.filter(
        from_topic__track=track
    ).values_list("from_topic_id", "to_topic_id"):
        prerequisites.setdefault(topic_id, set()).add(prereq_id)

    ordered = order_topics(topics, prerequisites)
    weeks = assign_weeks(ordered, profile.hours_per_week)

    LearningPlan.objects.filter(learner=profile, track=track, is_active=True).update(is_active=False)
    plan = LearningPlan.objects.create(learner=profile, track=track)
    PlanItem.objects.bulk_create(
        PlanItem(plan=plan, topic=topic, order=i, week=week)
        for i, (topic, week) in enumerate(zip(ordered, weeks), 1)
    )
    return plan


def active_plan(profile):
    if profile.track is None:
        return None
    return LearningPlan.objects.filter(learner=profile, track=profile.track, is_active=True).first()


@transaction.atomic
def set_item_status(item, status):
    """Change a plan item's status. Marking done also records the topic as known (manual)."""
    item.status = status
    item.completed_at = timezone.now() if status == PlanItem.Status.DONE else None
    item.save(update_fields=["status", "completed_at"])

    learner = item.plan.learner
    mastery = TopicMastery.objects.filter(learner=learner, topic=item.topic).first()
    if status == PlanItem.Status.DONE:
        if mastery is None or not mastery.known:
            TopicMastery.objects.update_or_create(
                learner=learner, topic=item.topic,
                defaults={"known": True, "source": TopicMastery.Source.MANUAL},
            )
    elif mastery and mastery.known and mastery.source == TopicMastery.Source.MANUAL:
        # Undoing a manual "done" means the learner doesn't know it after all.
        mastery.known = False
        mastery.save(update_fields=["known"])


def plan_item_for_topic(profile, topic):
    """The learner's item for this topic in their active plan, if any."""
    return PlanItem.objects.filter(
        plan__learner=profile, plan__track=topic.track, plan__is_active=True, topic=topic
    ).first()


def start_item(item):
    """Opening a lesson moves a todo item to in progress, and never undoes done or skipped."""
    if item is not None and item.status == PlanItem.Status.TODO:
        item.status = PlanItem.Status.IN_PROGRESS
        item.save(update_fields=["status"])
