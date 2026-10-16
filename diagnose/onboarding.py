"""Guided onboarding: one question at a time, each with tappable choices.

The steps are fixed here so every learner gives the answers a good plan needs, and
none is asked twice:

1. Goal: what they want to make (a path, not a language), then a real project.
2. Starting point: their experience, the topics they've used, and a short check on
   those topics, so the plan rests on evidence rather than self-rating alone.
3. Constraints: time per week, an optional date they're aiming for, and what helps
   when they're stuck.

Every question can be answered with "not sure", and beginners skip what doesn't apply.
The learner then reviews a summary (with what the check showed and whether the goal
fits their time) and can change any answer before the plan is built.

Answers live in the session (so a refresh or a lost connection loses nothing) until
the learner confirms them. The AI is only used to read a typed answer to the first
question; everything else works with the AI off.
"""

import math
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from accounts.models import LearnerProfile
from assistant.service import ask_model_json
from content.models import Path, ProjectIdea, Topic
from diagnose.models import TopicMastery
from learning.planner import estimate_weeks

NOT_SURE = "not_sure"
SUGGEST = "suggest"
TYPED = "typed"
MAX_TEXT = 300
MAX_IDEAS = 3
DEFAULT_HOURS = 3

STEPS = ["path", "project", "experience", "known", "check", "hours", "deadline", "stuck"]
# Shown above each question instead of a step count, since beginners skip steps.
PHASES = {
    "path": "Your goal", "project": "Your goal",
    "experience": "Where you're starting", "known": "Where you're starting", "check": "Where you're starting",
    "hours": "Your time", "deadline": "Your time", "stuck": "How you like help",
    "review": "Check your answers",
}
# Answers that depend on an earlier one, cleared when that one changes.
DEPENDENTS = {
    "path": ("project", "known", "check"),
    "experience": ("known", "check"),
    "known": ("check",),
}

EXPERIENCE_CHOICES = [
    (LearnerProfile.Experience.NONE, "I've never written code"),
    (LearnerProfile.Experience.A_LITTLE, "I've followed tutorials or a class"),
    (LearnerProfile.Experience.COMFORTABLE, "I've built something small on my own"),
    (NOT_SURE, "I'm not sure how to describe it"),
]
HOURS_CHOICES = [
    ("2", "1–2 hours"), ("4", "3–5 hours"), ("8", "6–10 hours"), ("12", "More than 10 hours"),
    (NOT_SURE, "Not sure yet"),
]
DEADLINE_CHOICES = [
    ("0", "No date, I'm learning at my own pace"),
    ("4", "In about a month"),
    ("12", "In about 3 months"),
    ("16", "By the end of this semester"),
]
STUCK_CHOICES = list(LearnerProfile.HintStyle.choices) + [(NOT_SURE, "Not sure, mix it up")]


@dataclass
class Choice:
    value: str
    label: str
    detail: str = ""
    disabled: bool = False


@dataclass
class Question:
    step: str
    title: str
    help: str
    choices: list = field(default_factory=list)
    free_text: str = ""  # Placeholder for a typed answer; empty means no text box.
    multiple: bool = False


class AnswerError(Exception):
    """The answer can't be used; the message is shown to the learner."""


# --- Paths -----------------------------------------------------------------


_paths_cache = ContextVar("onboarding_paths", default=None)


@contextmanager
def cached_paths():
    """Look the paths up once for a whole request instead of on every question and label."""
    token = _paths_cache.set([])
    try:
        yield
    finally:
        _paths_cache.reset(token)


def offered_paths():
    """Active paths in order, each with whether learners can start it yet."""
    cache = _paths_cache.get()
    if cache:
        return cache[0]
    paths = Path.objects.filter(is_active=True).prefetch_related("path_tracks__track__topics")
    result = [(p, p.is_available()) for p in paths]
    if cache is not None:
        cache.append(result)
    return result


def available_paths():
    return [p for p, available in offered_paths() if available]


def get_path(slug):
    return next((p for p in available_paths() if p.slug == slug), None)


def default_path():
    paths = available_paths()
    return paths[0] if paths else None


def classify_path(text, paths):
    """Ask the AI which path a typed answer means. Returns a path, or None."""
    if not paths:
        return None
    listed = "\n".join(f"- {p.slug}: {p.title} ({p.tagline})" for p in paths)
    messages = [
        {"role": "system", "content": (
            "A beginner said what they want to make with code. Pick the closest learning path.\n"
            f"Paths:\n{listed}\n"
            'Return only JSON: {"path": "slug from the list, or none if nothing fits"}'
        )},
        {"role": "user", "content": text[:MAX_TEXT]},
    ]
    data = ask_model_json(messages, max_tokens=300)
    slug = str((data or {}).get("path") or "").strip()
    return next((p for p in paths if p.slug == slug), None)


def first_track(answers):
    path = get_path(answers.get("path", ""))
    return path.first_track() if path else None


def ideas_for(path):
    track = path.first_track() if path else None
    if track is None:
        return []
    return list(ProjectIdea.objects.filter(track=track, is_active=True)[:MAX_IDEAS])


# --- Steps -----------------------------------------------------------------


def claimed_slugs(answers, track=None):
    """Topics to check: the ones ticked, or every topic when they're not sure."""
    known = answers.get("known") or {}
    if known.get("not_sure"):
        track = track or first_track(answers)
        return sorted(track.topics.values_list("slug", flat=True)) if track else []
    return list(known.get("slugs", []))


def steps_for(answers):
    """The steps this learner sees. Beginners skip the topics and the check."""
    steps = list(STEPS)
    if answers.get("experience") == LearnerProfile.Experience.NONE:
        steps = [s for s in steps if s not in ("known", "check")]
    elif "known" in answers and not claimed_slugs(answers):
        steps.remove("check")  # Nothing claimed, so nothing to check.
    return steps


def next_step(answers):
    """The first unanswered step, or "review" when everything is answered."""
    for step in steps_for(answers):
        if step not in answers:
            return step
    return "review"


def question_for(step, answers):
    path = get_path(answers.get("path", ""))
    if step == "path":
        choices = [
            Choice(p.slug, p.title, p.tagline if available else f"{p.tagline} (coming soon)", not available)
            for p, available in offered_paths()
        ]
        choices.append(Choice(NOT_SURE, "I'm not sure yet", "We'll start you with a great first skill."))
        return Question(
            "path", "Hi! What do you want to be able to make?",
            "We'll ask a few quick questions to fit the plan to you. This isn't a test. "
            "You're learning a skill, so we choose the languages for you.",
            choices, free_text="Or tell us in your own words",
        )
    if step == "project":
        choices = [Choice(f"idea:{i.pk}", i.title, i.brief) for i in ideas_for(path)]
        if answers.get("path_text"):
            # What they typed for the first question often already names their project.
            choices.insert(0, Choice(TYPED, answers["path_text"], "What you told us"))
        choices.append(Choice(SUGGEST, "Suggest one for me", "We'll pick one that fits your plan."))
        return Question(
            "project", "What's one real thing you'd like to make?",
            "Something from your own life works best, like a page for your org or a tool for a chore. "
            "You'll build it step by step as you learn.",
            choices, free_text="e.g. a sign-up page for our org's event",
        )
    if step == "experience":
        return Question(
            "experience", "Have you written code before?",
            "No wrong answer. This only sets where you start.",
            [Choice(value, label) for value, label in EXPERIENCE_CHOICES],
        )
    if step == "known":
        track = path.first_track() if path else None
        topics = list(track.topics.all()) if track else []
        choices = [Choice(t.slug, t.title) for t in topics]
        choices.append(Choice(NOT_SURE, "I'm not sure, check me on everything"))
        return Question(
            "known", f"Which of these {track.name if track else ''} topics have you used before?",
            "Tick what you've done, or leave them all empty. Next you'll get a few questions on the "
            "ones you tick, so your plan can skip what you already know.",
            choices, multiple=True,
        )
    if step == "hours":
        return Question(
            "hours", "How much time can you give it each week?",
            "Be honest. A small, steady pace beats a big plan you can't keep. You can change it later.",
            [Choice(value, label) for value, label in HOURS_CHOICES],
        )
    if step == "deadline":
        return Question(
            "deadline", "Is there a date you're aiming for?",
            "Only if you have one, like a class or an event. We'll tell you if the plan doesn't fit.",
            [Choice(value, label) for value, label in DEADLINE_CHOICES],
        )
    if step == "stuck":
        return Question(
            "stuck", "When you get stuck, what helps you most?",
            "Your AI helper will start this way. You can always ask for more.",
            [Choice(value, label) for value, label in STUCK_CHOICES],
        )
    raise ValueError(f"unknown step {step!r}")


def _clear_dependents(answers, step):
    for dependent in DEPENDENTS.get(step, ()):
        answers.pop(dependent, None)


def _pick(choice, choices):
    if choice not in {value for value, _ in choices}:
        raise AnswerError("Pick one of the options.")
    return choice


def apply_answer(answers, step, choice="", text="", picked=()):
    """Validate one answer and return the updated answers (a new dict).

    Raises AnswerError with a message for the learner when the answer can't be used.
    Changing an answer clears the answers that depend on it.
    """
    answers = dict(answers)
    text = " ".join(text.split())[:MAX_TEXT]

    if step == "path":
        if choice == NOT_SURE:
            path = default_path()
        elif choice:
            path = get_path(choice)
        elif text:
            path = classify_path(text, available_paths())
            if path is None:
                raise AnswerError("We couldn't match that to a path yet. Tap the one that's closest.")
        else:
            path = None
        if path is None:
            raise AnswerError("Pick one of the options.")
        if answers.get("path") != path.slug:
            _clear_dependents(answers, "path")
        answers["path"] = path.slug
        if text and not choice:
            answers["path_text"] = text
        else:
            answers.pop("path_text", None)
        return answers

    if step == "project":
        ideas = {f"idea:{i.pk}": i for i in ideas_for(get_path(answers.get("path", "")))}
        if choice == TYPED and answers.get("path_text"):
            answers["project"] = {"text": answers["path_text"], "idea_id": None}
        elif choice in ideas:
            answers["project"] = {"text": ideas[choice].title, "idea_id": ideas[choice].pk}
        elif choice == SUGGEST:
            answers["project"] = {"text": "", "idea_id": None}
        elif text:
            answers["project"] = {"text": text, "idea_id": None}
        else:
            raise AnswerError("Pick an idea, write your own, or let us suggest one.")
        return answers

    if step == "experience":
        value = _pick(choice, EXPERIENCE_CHOICES)
        if answers.get("experience") != value:
            _clear_dependents(answers, "experience")
        answers["experience"] = value
        return answers

    if step == "known":
        track = first_track(answers)
        slugs = set(track.topics.values_list("slug", flat=True)) if track else set()
        not_sure = NOT_SURE in picked
        _clear_dependents(answers, "known")
        answers["known"] = {
            "slugs": [] if not_sure else sorted(s for s in picked if s in slugs),
            "not_sure": not_sure,
        }
        return answers

    if step == "hours":
        answers["hours"] = _pick(choice, HOURS_CHOICES)
        return answers

    if step == "deadline":
        answers["deadline"] = int(_pick(choice, DEADLINE_CHOICES))
        return answers

    if step == "stuck":
        answers["stuck"] = _pick(choice, STUCK_CHOICES)
        return answers

    raise AnswerError("Something went wrong. Start over and try again.")


def record_check(answers, results):
    """Store a short summary of the quick check in the answers, for the review."""
    answers = dict(answers)
    answers["check"] = summarize_results(results)
    return answers


def summarize_results(results):
    groups = {"shown": [], "partly": [], "to_work_on": [], "unsure": []}
    key = {
        TopicMastery.Status.DEMONSTRATED: "shown",
        TopicMastery.Status.EMERGING: "partly",
        TopicMastery.Status.NOT_YET: "to_work_on",
        TopicMastery.Status.UNKNOWN: "unsure",
    }
    for result in results:
        groups[key[result.status]].append(result.topic.title)
    groups["shown_slugs"] = [r.topic.slug for r in results if r.status == TopicMastery.Status.DEMONSTRATED]
    return groups


# --- Showing and saving answers --------------------------------------------


def hours_per_week(answers):
    value = answers.get("hours")
    return DEFAULT_HOURS if value in (None, NOT_SURE) else int(value)


def answer_label(step, answers):
    """How an answer reads back to the learner."""
    value = answers.get(step)
    if step == "path":
        path = get_path(value or "")
        return path.title if path else ""
    if step == "project":
        return value["text"] or "Suggest one for me"
    if step == "experience":
        return dict(EXPERIENCE_CHOICES).get(value, "")
    if step == "known":
        if value["not_sure"]:
            return "Not sure, check me on everything"
        titles = list(Topic.objects.filter(slug__in=value["slugs"]).values_list("title", flat=True))
        return ", ".join(titles) or "None of these yet"
    if step == "check":
        parts = [
            (label, value.get(key))
            for key, label in (("shown", "Shown"), ("partly", "Partly there"),
                               ("to_work_on", "To work on"), ("unsure", "Not sure yet"))
        ]
        return " · ".join(f"{label}: {', '.join(titles)}" for label, titles in parts if titles) or "Done"
    if step == "hours":
        if value == NOT_SURE:
            return f"Not sure yet (we'll start with about {DEFAULT_HOURS} hours)"
        return dict(HOURS_CHOICES).get(value) or f"{value} hours"
    if step == "deadline":
        return dict(DEADLINE_CHOICES).get(str(value)) or f"In about {value} weeks"
    if step == "stuck":
        return dict(STUCK_CHOICES).get(value, "")
    return ""


QUESTION_TITLES = {"check": "Quick check on what you've used"}


def transcript(answers):
    """The questions answered so far with the learner's answers, for the chat view."""
    return [
        {
            "step": step,
            "question": QUESTION_TITLES.get(step) or question_for(step, answers).title,
            "answer": answer_label(step, answers),
        }
        for step in steps_for(answers) if step in answers
    ]


def remaining_minutes(answers):
    """Study time for the path's live tracks, minus topics the check showed they know."""
    path = get_path(answers.get("path", ""))
    if path is None:
        return 0
    shown = set((answers.get("check") or {}).get("shown_slugs", []))
    return sum(
        t.estimated_minutes
        for track in path.active_tracks() for t in track.topics.all() if t.slug not in shown
    )


def fit(answers):
    """How long the plan should take at their pace, and whether it fits their date."""
    low, high = estimate_weeks(remaining_minutes(answers), hours_per_week(answers))
    deadline = answers.get("deadline") or 0
    return {
        "low": low,
        "high": high,
        "deadline": deadline,
        "fits": not deadline or high <= deadline,
        "tight": bool(deadline) and low <= deadline < high,
        "hours_needed": math.ceil(remaining_minutes(answers) / 60 / deadline) if deadline else None,
    }


def is_complete(answers):
    return next_step(answers) == "review" and get_path(answers.get("path", "")) is not None


def save_answers(profile, answers):
    """Save confirmed answers to the profile. Returns the project idea for the planner."""
    path = get_path(answers["path"])
    project = answers["project"]
    idea = ProjectIdea.objects.filter(pk=project["idea_id"]).first() if project["idea_id"] else None
    experience = answers["experience"]

    profile.path = path
    profile.track = path.first_track()
    profile.experience = LearnerProfile.Experience.A_LITTLE if experience == NOT_SURE else experience
    profile.hours_per_week = hours_per_week(answers)
    profile.target_weeks = answers.get("deadline") or None
    profile.hint_style = "" if answers["stuck"] == NOT_SURE else answers["stuck"]
    profile.goal_text = (f"{path.title}: {project['text']}" if project["text"] else path.title)[:300]
    profile.onboarded = True
    profile.save()
    return f"{idea.title}: {idea.brief}" if idea else project["text"]


def answers_from_profile(profile):
    """Rebuild a finished set of answers from a saved profile, so a learner can change one
    answer and rebuild their plan without starting over."""
    path = profile.path
    if path is None or not path.is_available():
        return {}
    track = path.first_track()
    prefix = f"{path.title}: "
    project_text = profile.goal_text[len(prefix):] if profile.goal_text.startswith(prefix) else ""
    checked = list(
        TopicMastery.objects.filter(learner=profile, topic__track=track)
        .exclude(status=TopicMastery.Status.UNKNOWN)
        .select_related("topic").order_by("topic__order")
    )
    answers = {
        "path": path.slug,
        "project": {"text": project_text, "idea_id": None},
        "experience": profile.experience or NOT_SURE,
        "hours": str(profile.hours_per_week) if profile.hours_per_week else NOT_SURE,
        "deadline": profile.target_weeks or 0,
        "stuck": profile.hint_style or NOT_SURE,
    }
    if answers["experience"] != LearnerProfile.Experience.NONE:
        answers["known"] = {"slugs": sorted(m.topic.slug for m in checked), "not_sure": False}
        if checked:
            groups = {"shown": [], "partly": [], "to_work_on": [], "unsure": [], "shown_slugs": []}
            key = {"demonstrated": "shown", "emerging": "partly", "not_yet": "to_work_on", "needs_review": "partly"}
            for m in checked:
                groups[key[m.status]].append(m.topic.title)
                if m.status == TopicMastery.Status.DEMONSTRATED:
                    groups["shown_slugs"].append(m.topic.slug)
            answers["check"] = groups
    return answers
