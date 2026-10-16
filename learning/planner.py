"""Plan builder. Pure ordering and week rules first, then one function that saves a plan.

- Topics the learner already knows are skipped.
- Every prerequisite comes before the topics that need it; otherwise the
  content team's `order` is kept.
- If the prerequisites contain a cycle, fall back to `order` (never loop forever).
- Weeks fill up to hours_per_week * 60 minutes (minimum 1 hour). A topic that
  doesn't fit starts the next week; a topic longer than a week gets one to itself.
"""

import heapq
import math

from django.db import transaction
from django.utils import timezone

from content.models import Topic
from diagnose.models import TopicMastery
from diagnose.scoring import record_mastery

from .models import LearningPlan, PlanItem


def _sort_key(topic, track_rank=None):
    return ((track_rank or {}).get(getattr(topic, "track_id", None), 0), topic.order, topic.title, topic.pk)


def order_topics(topics, prerequisites, track_rank=None):
    """Order topics so prerequisites come first, breaking ties by content order.

    `prerequisites` maps a topic id to the ids of the topics it needs. Ids not
    in `topics` (for example, topics the learner already knows) are ignored.
    `track_rank` maps a track id to its place in the learner's path, so a path's
    first track comes before its second.
    """
    by_id = {t.pk: t for t in topics}
    needs = {pk: set(prerequisites.get(pk, ())) & by_id.keys() - {pk} for pk in by_id}
    needed_by = {pk: [] for pk in by_id}
    for pk, reqs in needs.items():
        for req in reqs:
            needed_by[req].append(pk)

    ready = [(_sort_key(by_id[pk], track_rank), pk) for pk, reqs in needs.items() if not reqs]
    heapq.heapify(ready)
    remaining = {pk: len(reqs) for pk, reqs in needs.items()}
    ordered = []
    while ready:
        _, pk = heapq.heappop(ready)
        ordered.append(by_id[pk])
        for dependent in needed_by[pk]:
            remaining[dependent] -= 1
            if remaining[dependent] == 0:
                heapq.heappush(ready, (_sort_key(by_id[dependent], track_rank), dependent))

    if len(ordered) < len(by_id):
        # A prerequisite cycle: some topics never became ready. Keep the content order.
        return sorted(topics, key=lambda t: _sort_key(t, track_rank))
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


def estimate_weeks(minutes, hours_per_week):
    """A rough range of weeks for this much study. An estimate, never a promise."""
    if minutes <= 0:
        return (0, 0)
    capacity = max(hours_per_week or 1, 1) * 60
    low = max(math.ceil(minutes * 0.8 / capacity), 1)
    high = max(math.ceil(minutes * 1.25 / capacity), low)
    return (low, high)


def with_prerequisites(selected_ids, prerequisites, skip_ids):
    """Add every prerequisite (and theirs) of the selected topics, except skipped ones."""
    result = set(selected_ids)
    stack = list(selected_ids)
    while stack:
        for prereq in prerequisites.get(stack.pop(), ()):
            if prereq not in result and prereq not in skip_ids:
                result.add(prereq)
                stack.append(prereq)
    return result


EVIDENCE_REASONS = {
    TopicMastery.Status.EMERGING: "You were partly there on the check, so a quick pass will lock it in.",
    TopicMastery.Status.NOT_YET: "A skill to work on, from your check.",
    TopicMastery.Status.NEEDS_REVIEW: "You knew this before; a quick review will bring it back.",
}


def plan_tracks(profile):
    """The tracks a plan covers: the path's live tracks in order, or just the learner's track."""
    tracks = profile.path.active_tracks() if profile.path_id else []
    if profile.track and profile.track not in tracks:
        tracks.insert(0, profile.track)
    return tracks


@transaction.atomic
def build_plan(profile, selected_topic_ids=None, reasons=None):
    """Replace the learner's current plan with a fresh one.

    The plan covers every live track of the learner's path, in path order. With
    `selected_topic_ids` (from the AI), it holds only those topics plus any
    prerequisites they need; otherwise every topic the learner hasn't shown.
    `reasons` maps topic id to a one-line reason; topics without one get a reason
    from their evidence when there is any.
    """
    tracks = plan_tracks(profile)
    track_rank = {t.pk: i for i, t in enumerate(tracks)}
    masteries = {
        m.topic_id: m for m in TopicMastery.objects.filter(learner=profile, topic__track__in=tracks)
    }
    known = {pk for pk, m in masteries.items() if m.known}
    prerequisites = {}
    for topic_id, prereq_id in Topic.prerequisites.through.objects.filter(
        from_topic__track__in=tracks
    ).values_list("from_topic_id", "to_topic_id"):
        prerequisites.setdefault(topic_id, set()).add(prereq_id)

    topics = Topic.objects.filter(track__in=tracks).exclude(pk__in=known)
    if selected_topic_ids:
        wanted = with_prerequisites(set(selected_topic_ids) - known, prerequisites, known)
        topics = topics.filter(pk__in=wanted)
    topics = list(topics)

    ordered = order_topics(topics, prerequisites, track_rank)
    weeks = assign_weeks(ordered, profile.hours_per_week)
    reasons = dict(reasons or {})
    for topic in ordered:
        mastery = masteries.get(topic.pk)
        if not reasons.get(topic.pk) and mastery is not None:
            reasons[topic.pk] = EVIDENCE_REASONS.get(mastery.status, "")

    LearningPlan.objects.filter(learner=profile, is_active=True).update(is_active=False)
    plan = LearningPlan.objects.create(learner=profile, track=tracks[0])
    PlanItem.objects.bulk_create(
        PlanItem(plan=plan, topic=topic, order=i, week=week, reason=reasons.get(topic.pk, "")[:300])
        for i, (topic, week) in enumerate(zip(ordered, weeks), 1)
    )
    return plan


@transaction.atomic
def repace_plan(plan, hours_per_week):
    """Spread the same plan over a new weekly time. Statuses are kept; nothing is rebuilt."""
    items = list(plan.items.select_related("topic").order_by("order"))
    for item, week in zip(items, assign_weeks([i.topic for i in items], hours_per_week)):
        if item.week != week:
            item.week = week
            item.save(update_fields=["week"])
    project = getattr(plan, "project", None)
    if project is not None:
        weeks = {i.topic.slug: i.week for i in items}
        project.milestones = milestone_weeks(project.milestones, weeks)
        project.save(update_fields=["milestones"])


def milestone_weeks(milestones, weeks_by_slug):
    """Recompute each milestone's week from the topics it uses; never let a step go back a week."""
    last_week = max(weeks_by_slug.values(), default=1)
    result = []
    for milestone in milestones:
        topic_weeks = [weeks_by_slug[s] for s in milestone.get("topics", []) if s in weeks_by_slug]
        week = max(topic_weeks) if topic_weeks else min(milestone.get("week", last_week), last_week)
        result.append({**milestone, "week": week})
    for previous, current in zip(result, result[1:]):
        current["week"] = max(current["week"], previous["week"])
    return result


def active_plan(profile):
    return LearningPlan.objects.filter(learner=profile, is_active=True).order_by("-created_at").first()


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
            record_mastery(learner, item.topic, TopicMastery.Status.DEMONSTRATED,
                           "You marked it done", TopicMastery.Source.MANUAL, force=True)
    elif mastery and mastery.known and mastery.source == TopicMastery.Source.MANUAL:
        # Undoing a manual "done" takes back the only evidence there was.
        record_mastery(learner, item.topic, TopicMastery.Status.UNKNOWN, "", TopicMastery.Source.MANUAL, force=True)


def plan_item_for_topic(profile, topic):
    """The learner's item for this topic in their active plan, if any."""
    return PlanItem.objects.filter(
        plan__learner=profile, plan__is_active=True, topic=topic
    ).order_by("-plan__created_at").first()


def start_item(item):
    """Opening a lesson moves a todo item to in progress, and never undoes done or skipped."""
    if item is not None and item.status == PlanItem.Status.TODO:
        item.status = PlanItem.Status.IN_PROGRESS
        item.save(update_fields=["status"])
