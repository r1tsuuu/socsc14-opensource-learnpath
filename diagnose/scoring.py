"""Skill check scoring. Pure rules first, then one function that saves the results.

A topic is known only if every question on it is answered correctly.
"I don't know yet" (stored as None) counts as wrong, and a topic with
no questions is never known, so it stays in the learner's plan.
"""

from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from content.models import SkillQuestion, Topic

from .models import SkillAnswer, TopicMastery


@dataclass
class TopicResult:
    topic: Topic
    correct: int
    total: int

    @property
    def known(self):
        return self.total > 0 and self.correct == self.total

    @property
    def reason(self):
        if self.total == 0:
            return "No skill check questions for this topic yet"
        return f"{self.correct} of {self.total} correct"


def score_topics(topics, questions, answers):
    """Return one TopicResult per topic, in the order given.

    `answers` maps question id to the chosen index, or None for "I don't know yet".
    A question missing from `answers` counts as "I don't know yet".
    """
    results = {topic.pk: TopicResult(topic=topic, correct=0, total=0) for topic in topics}
    for question in questions:
        result = results.get(question.topic_id)
        if result is None:
            continue
        result.total += 1
        if answers.get(question.pk) == question.correct_index:
            result.correct += 1
    return list(results.values())


def track_topics_and_questions(track):
    topics = list(Topic.objects.filter(track=track).order_by("order", "title"))
    questions = list(
        SkillQuestion.objects.filter(topic__track=track)
        .select_related("topic")
        .order_by("topic__order", "topic__title", "order", "pk")
    )
    return topics, questions


@transaction.atomic
def finish_attempt(attempt, answers):
    """Save the answers, update TopicMastery and close the attempt. Returns the results."""
    topics, questions = track_topics_and_questions(attempt.track)
    SkillAnswer.objects.filter(attempt=attempt).delete()
    SkillAnswer.objects.bulk_create(
        SkillAnswer(attempt=attempt, question=q, chosen_index=answers.get(q.pk)) for q in questions
    )
    results = score_topics(topics, questions, answers)
    for result in results:
        save_skill_check_mastery(attempt.learner, result.topic, result.known)
    attempt.finished_at = timezone.now()
    attempt.save(update_fields=["finished_at"])
    return results


def save_skill_check_mastery(learner, topic, known):
    """Record a skill check result, but never undo a topic already passed through a quiz."""
    existing = TopicMastery.objects.filter(learner=learner, topic=topic).first()
    if existing and existing.known and existing.source == TopicMastery.Source.QUIZ:
        return existing
    mastery, _ = TopicMastery.objects.update_or_create(
        learner=learner,
        topic=topic,
        defaults={"known": known, "source": TopicMastery.Source.SKILL_CHECK},
    )
    return mastery


def results_for_attempt(attempt):
    """Rebuild the per-topic results of a finished attempt from its stored answers."""
    topics, questions = track_topics_and_questions(attempt.track)
    answers = dict(attempt.answers.values_list("question_id", "chosen_index"))
    return score_topics(topics, questions, answers)
