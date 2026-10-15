"""Checkpoint quiz grading.

Answers are normalized by trimming, collapsing internal whitespace and
lowercasing (unless the item is case sensitive), then matched against any
accepted answer. A learner passes at 70% or more. Retries are unlimited.
"""

import re
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from diagnose.models import TopicMastery

from .models import PlanItem, QuizAttempt

PASS_PERCENT = 70


def normalize(text, case_sensitive=False):
    text = re.sub(r"\s+", " ", (text or "").strip())
    return text if case_sensitive else text.lower()


def is_correct(answer, item):
    given = normalize(answer, item.case_sensitive)
    return any(given == normalize(accepted, item.case_sensitive) for accepted in item.accepted_answers)


@dataclass
class ItemResult:
    item: object
    answer: str
    correct: bool


@dataclass
class QuizResult:
    items: list

    @property
    def correct(self):
        return sum(1 for r in self.items if r.correct)

    @property
    def total(self):
        return len(self.items)

    @property
    def percent(self):
        return round(self.correct * 100 / self.total) if self.total else 0

    @property
    def passed(self):
        # Compare exact fractions so rounding never turns 69.5% into a pass.
        return self.total > 0 and self.correct * 100 >= PASS_PERCENT * self.total


def grade(items, answers):
    """`answers` maps quiz item id to the learner's text answer."""
    return QuizResult(
        items=[ItemResult(item, answers.get(item.pk, ""), is_correct(answers.get(item.pk, ""), item)) for item in items]
    )


@transaction.atomic
def submit_quiz(profile, topic, items, answers):
    """Grade and save a quiz. Passing marks the plan item done and the topic known (source quiz)."""
    result = grade(items, answers)
    QuizAttempt.objects.bulk_create(
        QuizAttempt(learner=profile, quiz_item=r.item, answer=r.answer[:500], is_correct=r.correct)
        for r in result.items
    )
    if result.passed:
        PlanItem.objects.filter(
            plan__learner=profile, plan__is_active=True, topic=topic
        ).exclude(status=PlanItem.Status.DONE).update(
            status=PlanItem.Status.DONE, completed_at=timezone.now()
        )
        TopicMastery.objects.update_or_create(
            learner=profile, topic=topic,
            defaults={"known": True, "source": TopicMastery.Source.QUIZ},
        )
    return result


def latest_result(profile, items):
    """Rebuild the learner's most recent result from their latest attempt at each item."""
    latest = {}
    for attempt in QuizAttempt.objects.filter(learner=profile, quiz_item__in=items):
        latest.setdefault(attempt.quiz_item_id, attempt)
    if not latest:
        return None
    return QuizResult(
        items=[
            ItemResult(item, latest[item.pk].answer, latest[item.pk].is_correct)
            for item in items
            if item.pk in latest
        ]
    )
