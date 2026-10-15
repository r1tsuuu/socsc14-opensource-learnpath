"""AI drafts of topic tips, skill check questions and quiz items, for curators to review.

Run once per topic from a laptop (see the draft_topic command), never per learner.
The AI never writes links: models invent URLs, so curators add resources themselves.
"""

import json

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils.text import slugify
from openai import OpenAI

from .models import QuizItem, SkillQuestion, Tip, Topic

DRAFT_MAX_TOKENS = 4000

SYSTEM_PROMPT = """You write short knowledge checks for LearnPath, a free app for beginner programmers.
LearnPath does not teach: learners study official docs, MDN, freeCodeCamp or W3Schools,
then LearnPath checks their understanding. Write for complete beginners. Plain English.

Return only a JSON object with exactly these keys:
{
  "summary": "one sentence on what the learner can do after this topic",
  "estimated_minutes": integer between 30 and 120,
  "tips": ["2 short, practical tips about common beginner mistakes"],
  "skill_questions": [
    {"prompt": "question", "code_snippet": "short code or empty string",
     "choices": ["4 choices"], "correct_index": 0-3}
  ],
  "quiz_items": [
    {"prompt": "one line of code or a sentence with exactly one blank written as ___",
     "accepted_answers": ["every reasonable correct answer"], "hint": "a nudge, never the answer"}
  ]
}
Rules: exactly 2 tips, 2 skill_questions, 4 quiz_items. Each blank's answer is one
keyword, function name, attribute, symbol or value that a learner could only spell one or
two ways. Never a phrase, a whole tag pair, or anything the learner could choose freely
(like a page title or variable name). Questions test this topic only, at beginner level.
Vary the position of the correct choice. No links or URLs anywhere."""


class DraftError(Exception):
    pass


def request_draft(client, model, track, title, earlier_titles):
    """Ask the model for one topic's draft. Returns the parsed JSON dict."""
    before = ", ".join(earlier_titles) or "nothing yet"
    user = (
        f"Track: {track.name}\nTopic: {title}\n"
        f"The learner has already covered: {before}. Don't test those topics directly."
    )
    extra = {"reasoning_effort": "medium"} if model.startswith("openai/gpt-oss") else {}
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}],
        response_format={"type": "json_object"},
        max_tokens=DRAFT_MAX_TOKENS,
        temperature=0.3,
        **extra,
    )
    try:
        return json.loads(response.choices[0].message.content or "")
    except json.JSONDecodeError as error:
        raise DraftError(f"model did not return JSON: {error}") from error


def _clamp_minutes(value):
    try:
        return min(max(int(value), 15), 180)
    except (TypeError, ValueError):
        return 60


@transaction.atomic
def save_draft(track, title, data, prerequisite=None):
    """Validate the draft and save it as a new topic. Nothing is saved if any part is invalid."""
    slug = slugify(f"{track.slug}-{title}")[:200]
    if Topic.objects.filter(slug=slug).exists():
        raise DraftError(f"a topic with slug {slug!r} already exists")

    order = (Topic.objects.filter(track=track).aggregate(m=Max("order"))["m"] or 0) + 1
    topic = Topic(
        track=track, title=title, slug=slug, order=order,
        summary=str(data.get("summary", ""))[:1000],
        estimated_minutes=_clamp_minutes(data.get("estimated_minutes")),
    )
    objects = [topic]
    try:
        topic.full_clean()
        topic.save()
        if prerequisite is not None:
            topic.prerequisites.add(prerequisite)
        for i, text in enumerate(data.get("tips") or [], 1):
            objects.append(Tip(topic=topic, text=str(text), order=i))
        for i, q in enumerate(data.get("skill_questions") or [], 1):
            objects.append(SkillQuestion(
                topic=topic, prompt=str(q.get("prompt", "")),
                code_snippet=str(q.get("code_snippet") or ""),
                choices=[str(c) for c in q.get("choices") or []],
                correct_index=q.get("correct_index"), order=i,
            ))
        for q in data.get("quiz_items") or []:
            objects.append(QuizItem(
                topic=topic, prompt=str(q.get("prompt", "")),
                accepted_answers=[str(a) for a in q.get("accepted_answers") or []],
                hint=str(q.get("hint") or ""),
            ))
        for obj in objects[1:]:
            obj.full_clean()
            obj.save()
    except (ValidationError, AttributeError, TypeError) as error:
        raise DraftError(f"invalid draft for {title!r}: {error}") from error

    counts = {
        "tips": topic.tips.count(),
        "skill_questions": topic.skill_questions.count(),
        "quiz_items": topic.quiz_items.count(),
    }
    if counts["skill_questions"] == 0 or counts["quiz_items"] == 0:
        raise DraftError(f"draft for {title!r} has no questions or no quiz items")
    return topic, counts


def make_client(api_key):
    return OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1", timeout=120, max_retries=1)
