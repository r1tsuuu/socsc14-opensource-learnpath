"""Skill check scoring. Pure rules first, then the functions that save the results.

A topic is known (demonstrated) only if every question on it is answered correctly.
"I don't know yet" (stored as None) is never counted as right. Each topic also gets a
status with the evidence behind it, so the app never claims more than it saw:

- no questions answered, or "I don't know yet" on all of them: unknown (no evidence)
- every answer right: demonstrated
- some right: emerging
- none right: not yet

A topic with no questions is never known, so it stays in the learner's plan.
"""

from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from content.models import SkillQuestion, Topic

from .models import SkillAnswer, TopicMastery


Status = TopicMastery.Status


@dataclass
class TopicResult:
    topic: Topic
    correct: int
    total: int
    answered: int = 0

    @property
    def known(self):
        return self.total > 0 and self.correct == self.total

    @property
    def status(self):
        if self.total == 0 or self.answered == 0:
            return Status.UNKNOWN
        if self.correct == self.total:
            return Status.DEMONSTRATED
        return Status.EMERGING if self.correct else Status.NOT_YET

    @property
    def reason(self):
        if self.total == 0:
            return "No skill check questions for this topic yet"
        if self.answered == 0:
            return "You weren't sure yet, so it stays in your plan"
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
        chosen = answers.get(question.pk)
        if chosen is not None:
            result.answered += 1
            if chosen == question.correct_index:
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
        save_check_result(attempt.learner, result, "skill check")
    attempt.finished_at = timezone.now()
    attempt.save(update_fields=["finished_at"])
    return results


def record_mastery(learner, topic, status, evidence, source, force=False):
    """Save what the learner showed on a topic.

    Unless `force` is set: "unknown" never replaces evidence already recorded, and a
    skill check never overrides a quiz result (the quiz is the stronger evidence).
    """
    existing = TopicMastery.objects.filter(learner=learner, topic=topic).first()
    if existing and not force:
        if status == Status.UNKNOWN:
            return existing
        if source == TopicMastery.Source.SKILL_CHECK and existing.source == TopicMastery.Source.QUIZ:
            return existing
    mastery, _ = TopicMastery.objects.update_or_create(
        learner=learner,
        topic=topic,
        defaults={
            "status": status,
            "known": status == Status.DEMONSTRATED,
            "evidence": evidence[:200],
            "source": source,
        },
    )
    return mastery


def save_check_result(learner, result, check_name):
    evidence = result.reason if result.status == Status.UNKNOWN else f"{result.reason} on the {check_name}"
    return record_mastery(learner, result.topic, result.status, evidence, TopicMastery.Source.SKILL_CHECK)


def results_for_attempt(attempt):
    """Rebuild the per-topic results of a finished attempt from its stored answers."""
    topics, questions = track_topics_and_questions(attempt.track)
    answers = dict(attempt.answers.values_list("question_id", "chosen_index"))
    return score_topics(topics, questions, answers)


@transaction.atomic
def finish_quick_check(attempt, claimed_topic_ids, answers):
    """Score only the topics the learner said they know (guided onboarding).

    Topics they didn't claim are recorded as unknown: no evidence either way, so they
    stay in the plan without being labelled as gaps. Evidence already recorded (for
    example a quiz pass) is kept.
    """
    topics, questions = track_topics_and_questions(attempt.track)
    claimed = set(claimed_topic_ids)
    claimed_questions = [q for q in questions if q.topic_id in claimed]
    SkillAnswer.objects.filter(attempt=attempt).delete()
    SkillAnswer.objects.bulk_create(
        SkillAnswer(attempt=attempt, question=q, chosen_index=answers.get(q.pk)) for q in claimed_questions
    )
    results = score_topics([t for t in topics if t.pk in claimed], claimed_questions, answers)
    for result in results:
        save_check_result(attempt.learner, result, "quick check")
    for topic in topics:
        if topic.pk not in claimed:
            record_mastery(attempt.learner, topic, Status.UNKNOWN, "", TopicMastery.Source.SKILL_CHECK)
    attempt.finished_at = timezone.now()
    attempt.save(update_fields=["finished_at"])
    return results
