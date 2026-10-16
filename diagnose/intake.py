"""AI intake: an open-ended chat that fills in the learner's profile.

The learner types what they want ("I want to learn to code so I can..."), the AI asks a
few follow-up questions, then returns a structured profile. The chat lives only in the
session and is deleted when the intake ends; it is never saved to the database.
"""

import json

from assistant.service import ask_model_json
from accounts.models import LearnerProfile
from content.models import Track

MAX_TURNS = 6
MAX_MESSAGE_LENGTH = 500

GREETING = (
    "Hi! Tell me what you want to learn to code for, in your own words. "
    "For example: \"I want to make a simple website for our org\" or "
    "\"I'm taking CS 11 and want to get ahead.\""
)

SYSTEM_PROMPT = """You are LearnPath's friendly intake guide for beginner programmers.
LearnPath sends learners to free resources (official docs, W3Schools, MDN) and checks
their understanding. Your job: understand the learner so the app can build their plan.
The learner has already been asked what they want to learn and why; their first
message answers that.

Before each reply, reread everything the learner has said. Never ask for something they
already told you, even in passing (e.g. "4 hours a week", "I printed hello world once" means
a little experience). Ask only for the next thing still missing, ONE short question at a
time, at most 5 questions in total. Briefly acknowledge their last answer first.

What you need:
1. Their goal (why they want to learn, what they want to make).
2. Their experience: none, a little, or fairly comfortable.
3. How many hours a week they can study (a number from 1 to 40).
4. Which track fits best. ONLY these tracks are available:
{tracks}
   If what they want isn't available, say so kindly and suggest the closest one.
   If only one track is listed, don't ask them to choose: briefly tell them that's the
   track they'll start with (more are coming soon) and move on to the next question.
5. A small project idea they'd enjoy (suggest one if they have none).
6. Which of the track's topics they already know, if any (they will get a quick check).
   If they aren't sure how good they are, don't push: tell them a short skill check
   will figure it out, and set "not_sure" to true.

Return only a JSON object, every time:
{{"reply": "your message to the learner", "done": false, "profile": null}}
When you have enough, or when told to finish, return:
{{"reply": "a short, warm wrap-up", "done": true, "profile": {{
  "track": "track slug", "experience": "none|a_little|comfortable",
  "hours_per_week": 3, "goal": "one sentence", "project_idea": "one sentence",
  "known_topics": ["topic slugs from that track they say they know"],
  "not_sure": false}}}}
When talking to the learner, name topics by their titles (e.g. "Loops"), never by slug;
slugs go only in the profile. Keep replies under 60 words. Plain English, or Taglish if the learner writes in Taglish.
Never ask for their name, email, school or other personal details."""

FINISH_NOW = "Finish now: set done to true and fill the profile with your best guesses."


def available_tracks():
    """Active tracks that have topics, with their topics, in display order."""
    tracks = Track.objects.filter(is_active=True, topics__isnull=False).distinct().prefetch_related("topics")
    return list(tracks)


def describe_tracks(tracks):
    lines = []
    for track in tracks:
        topics = ", ".join(f"{t.slug} ({t.title})" for t in track.topics.all())
        lines.append(f"- {track.slug}: {track.name}. Topics: {topics}")
    return "\n".join(lines)


def new_history():
    return [{"role": "assistant", "content": GREETING}]


def learner_turns(history):
    return sum(1 for m in history if m["role"] == "user")


def build_messages(history, tracks, finish_now=False):
    messages = [{"role": "system", "content": SYSTEM_PROMPT.format(tracks=describe_tracks(tracks))}]
    # The greeting is shown to the learner but not sent: some models break when the
    # chat starts with an assistant message instead of the learner's.
    turns = list(history)
    while turns and turns[0]["role"] == "assistant":
        turns.pop(0)
    for m in turns:
        content = m["content"]
        if m["role"] == "assistant":
            # Show the model its earlier replies in the JSON shape it must keep using.
            content = json.dumps({"reply": content, "done": False, "profile": None}, ensure_ascii=False)
        messages.append({"role": m["role"], "content": content})
    if finish_now:
        messages.append({"role": "system", "content": FINISH_NOW})
    return messages


def parse_profile(data, tracks):
    """Validate the AI's profile. Returns a clean dict, or None if it can't be used."""
    profile = data.get("profile")
    if not data.get("done") or not isinstance(profile, dict):
        return None
    track = next((t for t in tracks if t.slug == str(profile.get("track", "")).strip()), None)
    if track is None:
        return None
    experience = str(profile.get("experience", "")).strip()
    if experience not in LearnerProfile.Experience.values:
        experience = LearnerProfile.Experience.NONE
    try:
        hours = min(max(int(profile.get("hours_per_week")), 1), 40)
    except (TypeError, ValueError):
        hours = 3
    track_slugs = {t.slug for t in track.topics.all()}
    not_sure = profile.get("not_sure") is True
    if not_sure:
        # They can't say what they know, so the quick check covers every topic.
        known = list(track_slugs)
    else:
        known = [s for s in profile.get("known_topics") or [] if isinstance(s, str) and s in track_slugs]
    return {
        "track_id": track.pk,
        "experience": experience,
        "hours_per_week": hours,
        "goal": " ".join(str(profile.get("goal") or "").split())[:300],
        "project_idea": " ".join(str(profile.get("project_idea") or "").split())[:300],
        "known_topics": sorted(set(known)),
        "not_sure": not_sure,
    }


def take_turn(history, user_text, tracks):
    """Add the learner's message and get the AI's reply.

    Returns (history, profile). `profile` is set once the intake is complete.
    Returns (None, None) if the AI couldn't answer or gave nothing usable at the end.
    """
    history = history + [{"role": "user", "content": user_text.strip()[:MAX_MESSAGE_LENGTH]}]
    finish_now = learner_turns(history) >= MAX_TURNS
    data = ask_model_json(build_messages(history, tracks, finish_now), max_tokens=1200, text_ok=not finish_now)
    if data is None:
        return None, None
    reply = " ".join(str(data.get("reply") or "").split())[:800]
    profile = parse_profile(data, tracks)
    if profile is None and (finish_now or not reply):
        return None, None
    return history + [{"role": "assistant", "content": reply or "Thanks!"}], profile


def save_profile(learner_profile, profile):
    learner_profile.track_id = profile["track_id"]
    learner_profile.experience = profile["experience"]
    learner_profile.hours_per_week = profile["hours_per_week"]
    learner_profile.goal_text = profile["goal"]
    learner_profile.onboarded = True
    learner_profile.save()
