import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import get_profile
from content.models import QuizItem, Topic, Track

from .models import AIUsage
from .service import HelpRequest, ask, build_messages

AI_ON = {"GROQ_API_KEY": "groq-test", "CF_ACCOUNT_ID": "acct", "CF_API_TOKEN": "cf-test", "AI_DAILY_LIMIT": 2}
AI_OFF = {"GROQ_API_KEY": "", "CF_ACCOUNT_ID": "", "CF_API_TOKEN": ""}


class AssistantTestMixin:
    def make_content(self):
        self.track = Track.objects.create(name="Python", slug="python")
        self.topic = Topic.objects.create(track=self.track, title="Loops", slug="loops", summary="Repeat code.")
        self.topic.tips.create(text="range(5) stops before 5.")
        self.topic.resources.create(title="For Loops", url="https://www.w3schools.com/python/python_for_loops.asp")
        self.item = QuizItem.objects.create(
            topic=self.topic, prompt="for i in ___(5):", accepted_answers=["secret-answer"], hint="A built-in."
        )
        self.user = get_user_model().objects.create_user(
            "ana", "ana.santos@example.com", first_name="Ana", last_name="Santos"
        )
        self.learner = get_profile(self.user)


@override_settings(**AI_ON)
class ServiceTests(AssistantTestMixin, TestCase):
    def setUp(self):
        self.make_content()

    def request(self, **kwargs):
        return HelpRequest(topic=self.topic, question="How do loops work?", **kwargs)

    @mock.patch("assistant.service.call_provider", return_value="What does range(5) give you?")
    def test_ai_reply_counts_against_cap(self, call):
        reply = ask(self.learner, self.request())
        self.assertEqual(reply.mode, "ai")
        self.assertEqual(reply.remaining_today, 1)
        self.assertEqual(reply.tips, ["range(5) stops before 5."])
        self.assertEqual(call.call_args.args[0].name, "groq")

    @mock.patch("assistant.service.call_provider", side_effect=[RuntimeError("groq down"), "Try range."])
    def test_falls_back_to_cloudflare(self, call):
        with self.assertLogs("assistant.service", "WARNING"):
            reply = ask(self.learner, self.request())
        self.assertEqual(reply.mode, "ai")
        self.assertEqual(call.call_args.args[0].name, "cloudflare")

    @mock.patch("assistant.service.call_provider", side_effect=RuntimeError("down"))
    def test_both_fail_returns_fallback_and_refunds(self, call):
        with self.assertLogs("assistant.service", "WARNING"):
            reply = ask(self.learner, self.request())
        self.assertEqual(reply.mode, "fallback")
        self.assertEqual(reply.resources[0]["title"], "For Loops")
        self.assertEqual(reply.remaining_today, 2)
        self.assertEqual(call.call_count, 2)

    @mock.patch("assistant.service.call_provider", return_value="hint")
    def test_daily_cap(self, call):
        AIUsage.objects.create(learner=self.learner, date=timezone.localdate(), messages_used=2)
        reply = ask(self.learner, self.request())
        self.assertEqual(reply.mode, "fallback")
        self.assertEqual(reply.remaining_today, 0)
        call.assert_not_called()

    @mock.patch("assistant.service.call_provider", side_effect=RuntimeError("down"))
    def test_quiz_fallback_includes_item_hint(self, call):
        with self.assertLogs("assistant.service", "WARNING"):
            reply = ask(self.learner, self.request(screen="quiz", quiz_item=self.item))
        self.assertIn("A built-in.", reply.reply)

    def test_messages_never_leak_answers_or_identity(self):
        messages = build_messages(self.request(screen="quiz", quiz_item=self.item, learner_answer="list", hint_level=3))
        text = json.dumps(messages)
        self.assertNotIn("secret-answer", text)
        self.assertNotIn("Ana", text)
        self.assertNotIn("Santos", text)
        self.assertNotIn("example.com", text)
        self.assertIn("for i in ___(5):", text)
        self.assertIn("range(5) stops before 5.", text)
        self.assertIn("similar but different example", text)
        self.assertIn("120 words", text)

    @mock.patch("assistant.service.call_provider", return_value="hint")
    def test_question_text_is_never_stored(self, call):
        ask(self.learner, self.request())
        usage = AIUsage.objects.get(learner=self.learner)
        self.assertEqual(usage.messages_used, 1)
        self.assertEqual({f.name for f in AIUsage._meta.fields}, {"id", "learner", "date", "messages_used", "intakes_used"})


@override_settings(**AI_OFF)
class AIOffTests(AssistantTestMixin, TestCase):
    def setUp(self):
        self.make_content()

    @mock.patch("assistant.service.call_provider")
    def test_works_with_ai_off(self, call):
        reply = ask(self.learner, HelpRequest(topic=self.topic, question="?"))
        self.assertEqual(reply.mode, "fallback")
        call.assert_not_called()
        self.assertFalse(AIUsage.objects.exists())


@override_settings(**AI_ON)
class AskViewTests(AssistantTestMixin, TestCase):
    def setUp(self):
        self.make_content()
        self.client.force_login(self.user)

    def post(self, body):
        return self.client.post(reverse("help_ask"), body, content_type="application/json")

    @mock.patch("assistant.service.call_provider", return_value="What does range do?")
    def test_returns_reply_shape(self, call):
        response = self.post({"topic": "loops", "question": "help", "hint_level": 9, "screen": "plan"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(set(data), {"mode", "reply", "hint_level", "remaining_today", "tips", "resources"})
        self.assertEqual(data["mode"], "ai")
        self.assertEqual(data["hint_level"], 3)

    def test_bad_input_is_still_200(self):
        for body in ["not json", "[]", json.dumps({"topic": "nope"})]:
            response = self.client.post(reverse("help_ask"), body, content_type="application/json")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["mode"], "fallback")

    def test_quiz_item_from_another_topic_is_ignored(self):
        other = Topic.objects.create(track=self.track, title="Lists", slug="lists")
        with mock.patch("assistant.service.call_provider", side_effect=RuntimeError), \
                self.assertLogs("assistant.service", "WARNING"):
            data = self.post({"topic": other.slug, "screen": "quiz", "quiz_item_id": self.item.pk}).json()
        self.assertNotIn("A built-in.", data["reply"])

    def test_requires_login(self):
        self.client.logout()
        self.assertEqual(self.post({"topic": "loops"}).status_code, 302)

    def test_test_page_is_staff_only(self):
        self.assertEqual(self.client.get(reverse("help_test")).status_code, 302)
        self.user.is_staff = True
        self.user.save()
        self.assertContains(self.client.get(reverse("help_test")), "AI helper test")
