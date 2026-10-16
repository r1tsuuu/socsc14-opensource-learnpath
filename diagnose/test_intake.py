from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import get_profile
from assistant.models import AIUsage
from assistant.service import parse_json_object
from content.models import SkillQuestion, Topic, Track
from learning.models import LearningPlan, PlanProject

from .intake import MAX_TURNS, available_tracks, parse_profile, take_turn
from .models import SkillCheckAttempt, TopicMastery
from .scoring import finish_quick_check

AI_ON = {"GROQ_API_KEY": "test", "CF_ACCOUNT_ID": "", "CF_API_TOKEN": "", "AI_INTAKE_DAILY_LIMIT": 1}
AI_OFF = {"GROQ_API_KEY": "", "CF_ACCOUNT_ID": "", "CF_API_TOKEN": ""}


class IntakeContentMixin:
    def make_content(self):
        self.track = Track.objects.create(name="Python", slug="python")
        Track.objects.create(name="SQL", slug="sql", is_active=False)
        self.topics = [
            Topic.objects.create(track=self.track, title=t, slug=f"python-{t.lower()}", order=i, estimated_minutes=60)
            for i, t in enumerate(["Variables", "Loops", "Lists", "Functions", "Files"], 1)
        ]
        for before, after in zip(self.topics, self.topics[1:]):
            after.prerequisites.add(before)
        self.q_vars = SkillQuestion.objects.create(topic=self.topics[0], prompt="v?", choices=["a", "b"], correct_index=0)
        self.q_loops = SkillQuestion.objects.create(topic=self.topics[1], prompt="l?", choices=["a", "b"], correct_index=1)
        self.user = get_user_model().objects.create_user("ana", "ana@example.com", first_name="Ana")
        self.profile = get_profile(self.user)

    def done_reply(self, **profile):
        base = {"track": "python", "experience": "a_little", "hours_per_week": 2,
                "goal": "Automate my org's sign-up sheet", "project_idea": "A sign-up counter",
                "known_topics": ["python-variables"]}
        base.update(profile)
        return {"reply": "Great, let's build your plan!", "done": True, "profile": base}


class ParseTests(IntakeContentMixin, TestCase):
    def setUp(self):
        self.make_content()
        self.tracks = available_tracks()

    def test_only_active_tracks_with_topics_are_offered(self):
        self.assertEqual([t.slug for t in self.tracks], ["python"])

    def test_valid_profile(self):
        profile = parse_profile(self.done_reply(hours_per_week=99, known_topics=["python-variables", "made-up", "sql-select"]), self.tracks)
        self.assertEqual(profile["track_id"], self.track.pk)
        self.assertEqual(profile["hours_per_week"], 40)
        self.assertEqual(profile["known_topics"], ["python-variables"])

    def test_unknown_or_inactive_track_is_rejected(self):
        self.assertIsNone(parse_profile(self.done_reply(track="sql"), self.tracks))
        self.assertIsNone(parse_profile(self.done_reply(track="rust"), self.tracks))

    def test_not_done_has_no_profile(self):
        self.assertIsNone(parse_profile({"reply": "How many hours?", "done": False, "profile": None}, self.tracks))

    def test_bad_values_get_safe_defaults(self):
        profile = parse_profile(self.done_reply(experience="expert", hours_per_week="lots"), self.tracks)
        self.assertEqual(profile["experience"], "none")
        self.assertEqual(profile["hours_per_week"], 3)

    def test_json_parsing_tolerates_fences(self):
        self.assertEqual(parse_json_object('```json\n{"a": 1}\n```'), {"a": 1})
        with self.assertRaises(ValueError):
            parse_json_object("no json here")


@override_settings(**AI_ON)
class TakeTurnTests(IntakeContentMixin, TestCase):
    def setUp(self):
        self.make_content()
        self.tracks = available_tracks()

    @mock.patch("diagnose.intake.ask_model_json", return_value={"reply": "How many hours a week?", "done": False, "profile": None})
    def test_follow_up_question(self, ask):
        history, profile = take_turn([], "I want to automate stuff", self.tracks)
        self.assertIsNone(profile)
        self.assertEqual(history[-1], {"role": "assistant", "content": "How many hours a week?"})
        system = ask.call_args.args[0][0]["content"]
        self.assertIn("python-loops (Loops)", system)
        self.assertNotIn("sql", system)

    @mock.patch("diagnose.intake.ask_model_json", return_value={"reply": "Still curious...", "done": False, "profile": None})
    def test_forces_finish_after_max_turns(self, ask):
        history = []
        for i in range(MAX_TURNS - 1):
            history += [{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}]
        result = take_turn(history, "last one", self.tracks)
        self.assertEqual(result, (None, None))
        self.assertIn("Finish now", ask.call_args.args[0][-1]["content"])

    @mock.patch("diagnose.intake.ask_model_json", return_value=None)
    def test_ai_failure(self, ask):
        self.assertEqual(take_turn([], "hi", self.tracks), (None, None))


class QuickCheckScoringTests(IntakeContentMixin, TestCase):
    def setUp(self):
        self.make_content()

    def test_scores_claimed_and_marks_the_rest_unknown(self):
        TopicMastery.objects.create(learner=self.profile, topic=self.topics[2], known=True, source="skill_check")
        TopicMastery.objects.create(learner=self.profile, topic=self.topics[3], known=True, source="quiz")
        attempt = SkillCheckAttempt.objects.create(learner=self.profile, track=self.track)
        results = finish_quick_check(attempt, {self.topics[0].pk, self.topics[1].pk}, {self.q_vars.pk: 0, self.q_loops.pk: 0})
        known = dict(TopicMastery.objects.filter(learner=self.profile).values_list("topic__slug", "known"))
        self.assertEqual(len(results), 2)
        self.assertTrue(known["python-variables"])
        self.assertFalse(known["python-loops"])
        self.assertFalse(known["python-lists"])
        self.assertTrue(known["python-functions"])  # quiz pass is never undone
        self.assertEqual(attempt.answers.count(), 2)


@override_settings(**AI_ON)
class IntakeFlowTests(IntakeContentMixin, TestCase):
    def setUp(self):
        self.make_content()
        self.client.force_login(self.user)

    def tailor_reply(self):
        return {
            "topics": [{"slug": "python-lists", "reason": "To store the sign-ups"}, {"slug": "made-up", "reason": "x"}],
            "project": {
                "title": "Sign-up counter",
                "description": "A small script that counts sign-ups.",
                "milestones": [{"topics": ["python-lists"], "text": "Store names in a list"},
                               {"topics": ["python-loops"], "text": "Loop over the names"}],
                "done_when": ["It counts sign-ups", "It runs without errors"],
            },
        }

    def test_welcome_points_to_intake(self):
        self.assertContains(self.client.get(reverse("welcome")), reverse("intake"))

    def test_full_flow(self):
        with mock.patch("diagnose.intake.ask_model_json", return_value={"reply": "How many hours?", "done": False, "profile": None}):
            self.assertRedirects(self.client.post(reverse("intake"), {"message": "I want to automate my org"}), reverse("intake"))
        page = self.client.get(reverse("intake"))
        self.assertContains(page, "I want to automate my org")
        self.assertContains(page, "How many hours?")

        with mock.patch("diagnose.intake.ask_model_json", return_value=self.done_reply()):
            self.assertRedirects(self.client.post(reverse("intake"), {"message": "2 hours"}), reverse("intake_check"))
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.onboarded)
        self.assertEqual(self.profile.hours_per_week, 2)
        self.assertNotIn("intake_chat", self.client.session)

        check = self.client.get(reverse("intake_check"))
        self.assertEqual(len(check.context["form"].questions), 1)  # only the claimed topic

        with mock.patch("learning.tailoring.ask_model_json", return_value=self.tailor_reply()):
            response = self.client.post(reverse("intake_check"), {f"question_{self.q_vars.pk}": "0"})
        self.assertRedirects(response, reverse("plan"))
        plan = LearningPlan.objects.get(learner=self.profile, is_active=True)
        items = list(plan.items.order_by("order"))
        # Lists was picked; Loops is added as its prerequisite; Variables is known.
        self.assertEqual([i.topic.slug for i in items], ["python-loops", "python-lists"])
        self.assertEqual(items[1].reason, "To store the sign-ups")
        self.assertEqual(plan.project.source, "ai")
        self.assertEqual([m["week"] for m in plan.project.milestones], [1, 1])
        page = self.client.get(reverse("plan"))
        self.assertContains(page, "Your project: Sign-up counter")
        self.assertContains(page, "Why: To store the sign-ups")
        self.assertNotIn("intake_result", self.client.session)
        self.assertEqual(AIUsage.objects.get(learner=self.profile).intakes_used, 1)

    def test_bad_project_falls_back_to_template(self):
        self.client.session  # ensure session exists
        with mock.patch("diagnose.intake.ask_model_json", return_value=self.done_reply(known_topics=[])):
            self.client.post(reverse("intake"), {"message": "hi"})
        reply = self.tailor_reply()
        reply["project"] = {"title": "", "description": ""}
        with mock.patch("learning.tailoring.ask_model_json", return_value=reply):
            self.client.post(reverse("intake_check"), {})
        plan = LearningPlan.objects.get(learner=self.profile, is_active=True)
        self.assertEqual(plan.project.source, "template")
        self.assertEqual(plan.items.count(), 3)  # lists + prerequisites loops, variables

    def test_tailoring_failure_gives_full_plan_and_template(self):
        with mock.patch("diagnose.intake.ask_model_json", return_value=self.done_reply(known_topics=[])):
            self.client.post(reverse("intake"), {"message": "hi"})
        with mock.patch("learning.tailoring.ask_model_json", return_value=None):
            self.assertRedirects(self.client.post(reverse("intake_check"), {}), reverse("plan"))
        plan = LearningPlan.objects.get(learner=self.profile, is_active=True)
        self.assertEqual(plan.items.count(), 5)
        self.assertEqual(plan.project.source, "template")

    def test_busy_ai_keeps_the_chat_and_the_unsent_message(self):
        with mock.patch("diagnose.intake.ask_model_json", return_value={"reply": "Q?", "done": False, "profile": None}):
            self.client.post(reverse("intake"), {"message": "hi"})
        with mock.patch("diagnose.intake.ask_model_json", return_value=None):
            self.assertRedirects(self.client.post(reverse("intake"), {"message": "5 hours"}), reverse("intake"))
        page = self.client.get(reverse("intake"))
        self.assertContains(page, "Q?")
        self.assertContains(page, ">5 hours</textarea>")
        with mock.patch("diagnose.intake.ask_model_json", return_value={"reply": "Next?", "done": False, "profile": None}):
            self.client.post(reverse("intake"), {"message": "5 hours"})
        self.assertNotContains(self.client.get(reverse("intake")), ">5 hours</textarea>")
        self.assertEqual(AIUsage.objects.get().intakes_used, 1)

    def test_busy_on_first_message_does_not_use_another_intake(self):
        with mock.patch("diagnose.intake.ask_model_json", return_value=None):
            self.client.post(reverse("intake"), {"message": "hi"})
        with mock.patch("diagnose.intake.ask_model_json", return_value={"reply": "Q?", "done": False, "profile": None}):
            self.assertRedirects(self.client.post(reverse("intake"), {"message": "hi"}), reverse("intake"))
        self.assertEqual(AIUsage.objects.get().intakes_used, 1)

    def test_repeated_ai_failure_falls_back_to_form(self):
        with mock.patch("diagnose.intake.ask_model_json", return_value=None):
            self.assertRedirects(self.client.post(reverse("intake"), {"message": "hi"}), reverse("intake"))
            self.assertRedirects(self.client.post(reverse("intake"), {"message": "hi"}), reverse("about_you"))
        self.assertNotIn("intake_chat", self.client.session)

    def test_daily_cap_sends_to_form(self):
        with mock.patch("diagnose.intake.ask_model_json", return_value={"reply": "Q?", "done": False, "profile": None}):
            self.client.post(reverse("intake"), {"message": "hi"})
            self.client.post(reverse("intake"), {"action": "restart"})
            self.assertRedirects(self.client.post(reverse("intake"), {"message": "again"}), reverse("about_you"))
        self.assertRedirects(self.client.get(reverse("intake")), reverse("about_you"))

    def test_restart_clears_chat(self):
        with mock.patch("diagnose.intake.ask_model_json", return_value={"reply": "Q?", "done": False, "profile": None}):
            self.client.post(reverse("intake"), {"message": "hi"})
        self.client.post(reverse("intake"), {"action": "restart"})
        self.assertNotIn("intake_chat", self.client.session)

    def test_check_without_intake_goes_back(self):
        self.assertRedirects(self.client.get(reverse("intake_check")), reverse("intake"))

    def test_chat_text_is_never_saved_to_the_database(self):
        with mock.patch("diagnose.intake.ask_model_json", return_value=self.done_reply()):
            self.client.post(reverse("intake"), {"message": "my secret goal text xyz"})
        from django.apps import apps
        for model in apps.get_models():
            if model._meta.app_label in {"accounts", "diagnose", "learning", "assistant"}:
                for row in model.objects.values():
                    self.assertNotIn("my secret goal text xyz", str(row))


@override_settings(**AI_OFF)
class IntakeAIOffTests(IntakeContentMixin, TestCase):
    def setUp(self):
        self.make_content()
        self.client.force_login(self.user)

    def test_goes_to_form(self):
        self.assertRedirects(self.client.get(reverse("intake")), reverse("about_you"))

    def test_form_path_plan_gets_template_project(self):
        self.profile.track = self.track
        self.profile.hours_per_week = 2
        self.profile.onboarded = True
        self.profile.save()
        self.assertRedirects(self.client.post(reverse("plan_build")), reverse("plan"))
        plan = LearningPlan.objects.get(learner=self.profile, is_active=True)
        self.assertEqual(plan.project.source, "template")
        self.assertEqual(plan.project.milestones[0]["week"], 1)
        self.assertEqual(PlanProject.objects.count(), 1)


class BuildMessagesTests(IntakeContentMixin, TestCase):
    def setUp(self):
        self.make_content()

    def test_greeting_is_not_sent_to_the_model(self):
        from .intake import build_messages, new_history
        history = new_history() + [{"role": "user", "content": "hi"}]
        sent = build_messages(history, available_tracks())
        self.assertEqual([m["role"] for m in sent], ["system", "user"])

    @override_settings(GROQ_API_KEY="k", GROQ_MODEL="small", GROQ_PLAN_MODEL="big")
    def test_planning_uses_the_plan_model(self):
        from assistant.service import providers
        self.assertEqual(providers()[0].model, "small")
        self.assertEqual([p.model for p in providers(purpose="plan")], ["big", "small"])
        self.assertEqual([p.model for p in providers()], ["small"])


@override_settings(**AI_ON)
class JsonSalvageTests(TestCase):
    def test_plain_text_reply_is_kept_for_chat(self):
        from assistant import service
        with mock.patch.object(service, "call_provider", return_value="How many hours a week?"):
            self.assertEqual(service.ask_model_json([], text_ok=True)["reply"], "How many hours a week?")
            with self.assertLogs("assistant.service", "WARNING"):
                self.assertIsNone(service.ask_model_json([]))

    def test_groq_rejected_generation_is_salvaged(self):
        from assistant import service
        error = Exception("400")
        error.body = {"error": {"code": "json_validate_failed", "failed_generation": "What do you want to build?"}}
        with mock.patch.object(service, "call_provider", side_effect=error):
            self.assertEqual(service.ask_model_json([], text_ok=True)["reply"], "What do you want to build?")

    def test_earlier_replies_are_sent_as_json(self):
        from .intake import build_messages
        sent = build_messages([{"role": "user", "content": "hi"}, {"role": "assistant", "content": "Hours?"}], [])
        self.assertEqual(sent[-1]["content"], '{"reply": "Hours?", "done": false, "profile": null}')


@override_settings(GROQ_API_KEY="k", GROQ_MODEL="small", GROQ_PLAN_MODEL="big", CF_ACCOUNT_ID="", CF_API_TOKEN="")
class RateLimitTests(TestCase):
    def rate_limit(self, retry_after):
        from openai import RateLimitError
        response = mock.Mock(status_code=429, headers={"retry-after": retry_after} if retry_after else {})
        return RateLimitError("429", response=response, body=None)

    @mock.patch("assistant.service.time.sleep")
    def test_short_wait_is_retried_on_the_same_model(self, sleep):
        from assistant import service
        with mock.patch.object(service, "call_provider", side_effect=[self.rate_limit("1"), '{"reply": "ok"}']) as call:
            self.assertEqual(service.ask_model_json([])["reply"], "ok")
        sleep.assert_called_once_with(1.0)
        self.assertEqual([c.args[0].model for c in call.call_args_list], ["big", "big"])

    @mock.patch("assistant.service.time.sleep")
    def test_long_wait_moves_to_the_small_model(self, sleep):
        from assistant import service
        with mock.patch.object(service, "call_provider", side_effect=[self.rate_limit("40"), '{"reply": "ok"}']) as call:
            with self.assertLogs("assistant.service", "WARNING"):
                self.assertEqual(service.ask_model_json([])["reply"], "ok")
        sleep.assert_not_called()
        self.assertEqual([c.args[0].model for c in call.call_args_list], ["big", "small"])


class MilestoneOrderTests(IntakeContentMixin, TestCase):
    def setUp(self):
        self.make_content()

    def test_steps_keep_their_order_and_weeks_never_go_back(self):
        from learning.planner import build_plan
        from learning.tailoring import parse_project
        self.profile.track = self.track
        self.profile.hours_per_week = 1
        self.profile.save()
        plan = build_plan(self.profile)  # 5 topics, one per week
        project = parse_project({"project": {
            "title": "T", "description": "D", "done_when": ["x"],
            "milestones": [
                {"topics": ["python-lists"], "text": "first"},
                {"topics": ["python-variables"], "text": "second"},
                {"topics": ["python-files"], "text": "third"},
            ],
        }}, plan)
        self.assertEqual([(m["text"], m["week"]) for m in project["milestones"]],
                         [("first", 3), ("second", 3), ("third", 5)])
