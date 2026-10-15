"""AI hint service.

Grounded only in the current topic's tips and resource titles. Hints step up
through three levels and never give full solutions. Groq is tried first, then
Cloudflare Workers AI. When the daily cap is hit or both providers fail, the
learner gets the topic's tips and links instead, so the app works with the AI off.

Never sent to the model: accepted quiz answers, names, emails.
"""

import logging
from dataclasses import dataclass, field

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from openai import OpenAI

from .models import AIUsage

logger = logging.getLogger(__name__)

MAX_TOKENS = 300
TIMEOUT_SECONDS = 20

HINT_LEVELS = {
    1: "Ask one guiding question that points the learner toward the idea they are missing.",
    2: "Explain the concept the learner needs, in plain words, without solving their problem.",
    3: "Show a short, similar but different example, then let the learner apply it themselves.",
}


@dataclass
class Provider:
    name: str
    api_key: str
    base_url: str
    model: str


def providers():
    """Configured providers in the order they are tried. Missing keys mean the provider is off."""
    result = []
    if settings.GROQ_API_KEY:
        result.append(Provider("groq", settings.GROQ_API_KEY,
                               "https://api.groq.com/openai/v1", settings.GROQ_MODEL))
    if settings.CF_ACCOUNT_ID and settings.CF_API_TOKEN:
        result.append(Provider(
            "cloudflare", settings.CF_API_TOKEN,
            f"https://api.cloudflare.com/client/v4/accounts/{settings.CF_ACCOUNT_ID}/ai/v1",
            settings.CF_MODEL,
        ))
    return result


@dataclass
class HelpRequest:
    topic: object
    question: str
    hint_level: int = 1
    screen: str = "plan"
    quiz_item: object = None
    learner_answer: str = ""


@dataclass
class HelpReply:
    mode: str
    reply: str
    hint_level: int
    remaining_today: int
    tips: list = field(default_factory=list)
    resources: list = field(default_factory=list)

    def as_dict(self):
        return {
            "mode": self.mode,
            "reply": self.reply,
            "hint_level": self.hint_level,
            "remaining_today": self.remaining_today,
            "tips": self.tips,
            "resources": self.resources,
        }


def topic_tips(topic):
    return [tip.text for tip in topic.tips.all()]


def topic_resources(topic):
    return [{"title": r.title, "url": r.url} for r in topic.resources.filter(is_active=True)]


def build_messages(request):
    """The chat messages sent to the model. Only topic content and the learner's own words."""
    topic = request.topic
    tips = "\n".join(f"- {t}" for t in topic_tips(topic)) or "- (none)"
    resources = "\n".join(f"- {r['title']}" for r in topic_resources(topic)) or "- (none)"
    system = (
        "You are a patient tutor helping a beginner programmer.\n"
        f"Topic: {topic.title} ({topic.track.name}).\n"
        f"Topic summary: {topic.summary or '(none)'}\n"
        f"Tips from our team:\n{tips}\n"
        f"Resources the learner has:\n{resources}\n\n"
        "Rules:\n"
        "- Stay on this topic. Use only the ideas above and basic programming knowledge.\n"
        "- Never give the full solution or the exact answer to a quiz blank.\n"
        f"- This is hint level {request.hint_level} of 3. {HINT_LEVELS[request.hint_level]}\n"
        "- Keep the reply under 120 words.\n"
        "- Reply in the learner's language: English, or Taglish if they write in Taglish.\n"
        "- If the question is not about programming, gently steer back to the topic."
    )
    user = f"My question: {request.question}"
    if request.screen == "quiz" and request.quiz_item is not None:
        # The prompt only. accepted_answers never leave the server.
        user = (
            f"I'm on this quiz blank: {request.quiz_item.prompt}\n"
            f"My answer so far: {request.learner_answer or '(blank)'}\n{user}"
        )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def call_provider(provider, messages):
    client = OpenAI(api_key=provider.api_key, base_url=provider.base_url,
                    timeout=TIMEOUT_SECONDS, max_retries=0)
    # gpt-oss models think before answering; keep that short so it fits in MAX_TOKENS.
    extra = {"reasoning_effort": "low"} if provider.model.startswith("openai/gpt-oss") else {}
    response = client.chat.completions.create(
        model=provider.model, messages=messages, max_tokens=MAX_TOKENS, temperature=0.4, **extra,
    )
    text = (response.choices[0].message.content or "").strip()
    if not text:
        raise ValueError("empty reply")
    return text


def ask_model(messages):
    """Try each provider in order. Returns the reply text, or None if all fail."""
    for provider in providers():
        try:
            return call_provider(provider, messages)
        except Exception:  # Any provider error means "try the next one".
            logger.warning("AI provider %s failed", provider.name, exc_info=True)
    return None


def daily_limit():
    return settings.AI_DAILY_LIMIT


def remaining_today(learner):
    usage = AIUsage.objects.filter(learner=learner, date=timezone.localdate()).first()
    return max(daily_limit() - (usage.messages_used if usage else 0), 0)


def reserve_message(learner):
    """Count one message against today's cap. Returns False if the cap is already reached."""
    with transaction.atomic():
        usage, _ = AIUsage.objects.get_or_create(learner=learner, date=timezone.localdate())
        usage = AIUsage.objects.select_for_update().get(pk=usage.pk)
        if usage.messages_used >= daily_limit():
            return False
        usage.messages_used = F("messages_used") + 1
        usage.save(update_fields=["messages_used"])
    return True


def release_message(learner):
    """Give back a reserved message when no provider answered."""
    AIUsage.objects.filter(
        learner=learner, date=timezone.localdate(), messages_used__gt=0
    ).update(messages_used=F("messages_used") - 1)


def fallback(request, learner, reason):
    reply = reason
    if request.screen == "quiz" and request.quiz_item is not None and request.quiz_item.hint:
        reply = f"{reason} Hint for this blank: {request.quiz_item.hint}"
    return HelpReply(
        mode="fallback",
        reply=reply,
        hint_level=request.hint_level,
        remaining_today=remaining_today(learner),
        tips=topic_tips(request.topic),
        resources=topic_resources(request.topic),
    )


def ask(learner, request):
    """Answer a help request with an AI hint, or fall back to the topic's tips and links."""
    if not providers():
        return fallback(request, learner, "The AI helper is off right now. These tips and links should help.")
    if not reserve_message(learner):
        return fallback(request, learner, "You've used all your AI messages for today. These tips and links should help.")

    text = ask_model(build_messages(request))
    if text is None:
        release_message(learner)
        return fallback(request, learner, "The AI helper is busy right now. These tips and links should help.")

    return HelpReply(
        mode="ai",
        reply=text,
        hint_level=request.hint_level,
        remaining_today=remaining_today(learner),
        tips=topic_tips(request.topic),
        resources=topic_resources(request.topic),
    )
