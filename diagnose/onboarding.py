"""Guided onboarding: a few fixed questions, each with tappable choices.

The steps and choices are fixed here so every learner gives the answers a good plan
needs: what they want to make (a path, not a language), a real project, where they
start, their pace and what helps when they're stuck. The AI is only used to read a
typed answer to the first question; everything else works with the AI off.

Answers live in the session until the learner confirms them, then they are saved to
the profile. Nothing typed here is sent anywhere except that one optional AI call.
"""

import math
from dataclasses import dataclass, field

from accounts.models import LearnerProfile
from assistant.service import ask_model_json
from content.models import Path, ProjectIdea

NOT_SURE = "not_sure"
SUGGEST = "suggest"
MAX_TEXT = 300
MAX_IDEAS = 3

STEPS = ["path", "project", "experience", "known", "hours", "stuck"]

EXPERIENCE_CHOICES = [
    (LearnerProfile.Experience.NONE, "I've never written code"),
    (LearnerProfile.Experience.A_LITTLE, "I've followed tutorials or a class"),
    (LearnerProfile.Experience.COMFORTABLE, "I've built something small on my own"),
]
HOURS_CHOICES = [(2, "1–2 hours"), (4, "3–5 hours"), (8, "6–10 hours"), (12, "More than 10 hours")]


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


# --- Paths -----------------------------------------------------------------


def offered_paths():
    """Active paths in order, each with whether learners can start it yet."""
    paths = Path.objects.filter(is_active=True).prefetch_related("path_tracks__track__topics")
    return [(p, p.is_available()) for p in paths]


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


def ideas_for(path):
    track = path.first_track() if path else None
    if track is None:
        return []
    return list(ProjectIdea.objects.filter(track=track, is_active=True)[:MAX_IDEAS])


# --- Steps -----------------------------------------------------------------


def steps_for(answers):
    """The steps this learner sees. Beginners skip "which topics do you know"."""
    if answers.get("experience") == LearnerProfile.Experience.NONE:
        return [s for s in STEPS if s != "known"]
    return list(STEPS)


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
            "path", "What do you want to be able to make?",
            "Pick the closest one. You're learning a skill, so we choose the languages for you.",
            choices, free_text="Or tell us in your own words",
        )
    if step == "project":
        choices = [Choice(f"idea:{i.pk}", i.title, i.brief) for i in ideas_for(path)]
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
            "Tick what you've done. You'll get a quick check on those, so your plan skips what you already know.",
            choices, multiple=True,
        )
    if step == "hours":
        return Question(
            "hours", "How much time can you give it each week?",
            "Be honest. A small, steady pace beats a big plan you can't keep.",
            [Choice(str(value), label) for value, label in HOURS_CHOICES],
        )
    if step == "stuck":
        return Question(
            "stuck", "When you get stuck, what helps you most?",
            "Your AI helper will start this way. You can always ask for more.",
            [Choice(value, label) for value, label in LearnerProfile.HintStyle.choices],
        )
    raise ValueError(f"unknown step {step!r}")


class AnswerError(Exception):
    """The answer can't be used; the message is shown to the learner."""


def apply_answer(answers, step, choice="", text="", picked=()):
    """Validate one answer and return the updated answers (a new dict).

    Raises AnswerError with a message for the learner when the answer can't be used.
    Changing the path clears the answers that depend on it.
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
            for dependent in ("project", "known"):
                answers.pop(dependent, None)
        answers["path"] = path.slug
        return answers

    if step == "project":
        path = get_path(answers.get("path", ""))
        ideas = {f"idea:{i.pk}": i for i in ideas_for(path)}
        if choice in ideas:
            idea = ideas[choice]
            answers["project"] = {"text": idea.title, "idea_id": idea.pk}
        elif choice == SUGGEST:
            answers["project"] = {"text": "", "idea_id": None}
        elif text:
            answers["project"] = {"text": text, "idea_id": None}
        else:
            raise AnswerError("Pick an idea, write your own, or let us suggest one.")
        return answers

    if step == "experience":
        if choice not in dict(EXPERIENCE_CHOICES):
            raise AnswerError("Pick one of the options.")
        answers["experience"] = choice
        if choice == LearnerProfile.Experience.NONE:
            answers.pop("known", None)
        return answers

    if step == "known":
        path = get_path(answers.get("path", ""))
        track = path.first_track() if path else None
        slugs = set(track.topics.values_list("slug", flat=True)) if track else set()
        not_sure = NOT_SURE in picked
        answers["known"] = {
            "slugs": [] if not_sure else sorted(s for s in picked if s in slugs),
            "not_sure": not_sure,
        }
        return answers

    if step == "hours":
        if choice not in {str(value) for value, _ in HOURS_CHOICES}:
            raise AnswerError("Pick one of the options.")
        answers["hours"] = int(choice)
        return answers

    if step == "stuck":
        if choice not in LearnerProfile.HintStyle.values:
            raise AnswerError("Pick one of the options.")
        answers["stuck"] = choice
        return answers

    raise AnswerError("Something went wrong. Start over and try again.")


# --- Showing and saving answers --------------------------------------------


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
        path = get_path(answers.get("path", ""))
        track = path.first_track() if path else None
        titles = list(track.topics.filter(slug__in=value["slugs"]).values_list("title", flat=True)) if track else []
        return ", ".join(titles) or "None of these yet"
    if step == "hours":
        return dict(HOURS_CHOICES).get(value, "")
    if step == "stuck":
        return LearnerProfile.HintStyle(value).label
    return ""


def transcript(answers):
    """The questions answered so far with the learner's answers, for the chat view."""
    return [
        {"step": step, "question": question_for(step, answers).title, "answer": answer_label(step, answers)}
        for step in steps_for(answers) if step in answers
    ]


def estimated_weeks(answers):
    """Rough plan length: the first track's topics the learner doesn't claim, at their pace."""
    path = get_path(answers.get("path", ""))
    track = path.first_track() if path else None
    if track is None:
        return None
    known = set((answers.get("known") or {}).get("slugs", []))
    minutes = sum(t.estimated_minutes for t in track.topics.all() if t.slug not in known)
    return max(math.ceil(minutes / (answers.get("hours", 1) * 60)), 1)


def is_complete(answers):
    return next_step(answers) == "review" and get_path(answers.get("path", "")) is not None


def save_answers(profile, answers):
    """Save confirmed answers to the profile. Returns what the quick check and planner need."""
    path = get_path(answers["path"])
    track = path.first_track()
    project = answers["project"]
    known = answers.get("known") or {"slugs": [], "not_sure": False}
    if known["not_sure"]:
        known_slugs = list(track.topics.values_list("slug", flat=True))
    else:
        known_slugs = known["slugs"]

    idea = ProjectIdea.objects.filter(pk=project["idea_id"]).first() if project["idea_id"] else None
    if idea is not None:
        project_idea = f"{idea.title}: {idea.brief}"
    else:
        project_idea = project["text"]

    profile.path = path
    profile.track = track
    profile.experience = answers["experience"]
    profile.hours_per_week = answers["hours"]
    profile.hint_style = answers["stuck"]
    profile.goal_text = (f"{path.title}: {project['text']}" if project["text"] else path.title)[:300]
    profile.onboarded = True
    profile.save()
    return {"known_topics": sorted(set(known_slugs)), "not_sure": known["not_sure"], "project_idea": project_idea}
