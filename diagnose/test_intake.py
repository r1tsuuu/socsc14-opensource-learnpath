from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import get_profile
from assistant.models import AIUsage
from assistant.service import parse_json_object
from content.models import Path, PathTrack, SkillQuestion, Topic, Track
from learning.models import LearningPlan, PlanProject

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
        automation = Path.objects.create(title="Automate boring tasks", slug="automation", tagline="Small tools.", order=1)
        PathTrack.objects.create(path=automation, track=self.track)
        website = Path.objects.create(title="Make a website", slug="website", tagline="A page.", order=2)
        PathTrack.objects.create(path=website, track=Track.objects.get(slug="sql"))
        self.user = get_user_model().objects.create_user("ana", "ana@example.com", first_name="Ana")
        self.profile = get_profile(self.user)


class JsonParsingTests(TestCase):
    def test_json_parsing_tolerates_fences(self):
        self.assertEqual(parse_json_object('```json\n{"a": 1}\n```'), {"a": 1})
        with self.assertRaises(ValueError):
            parse_json_object("no json here")


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
        self.assertTrue(known["python-lists"])  # not claimed: earlier evidence is kept
        self.assertTrue(known["python-functions"])  # quiz pass is never undone
        files = TopicMastery.objects.get(learner=self.profile, topic=self.topics[4])
        self.assertEqual(files.status, "unknown")  # never checked: no evidence, not a gap
        self.assertEqual(attempt.answers.count(), 2)


@override_settings(**AI_ON)
class OnboardingFlowTests(IntakeContentMixin, TestCase):
    def setUp(self):
        self.make_content()
        self.client.force_login(self.user)
        # No real AI calls in tests: unless a test says otherwise, the AI gives nothing usable.
        for target in ("learning.tailoring.ask_model_json", "diagnose.onboarding.ask_model_json"):
            patcher = mock.patch(target, return_value=None)
            patcher.start()
            self.addCleanup(patcher.stop)

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

    def answer(self, **data):
        """Answer the current question, sending its step like the page does."""
        if "action" not in data:
            data["step"] = self.client.get(reverse("intake")).context["question"].step
        return self.client.post(reverse("intake"), data)

    def answer_all(self, path="automation", project=None, experience="a_little", known=("python-variables",),
                   check=None, hours="2", deadline="0", stuck="think"):
        self.answer(choice=path)
        self.answer(**(project or {"choice": "suggest"}))
        self.answer(choice=experience)
        if experience != "none":
            self.answer(picked=list(known))
            if known:
                default = {f"question_{q.pk}": "dont_know" for q in (self.q_vars, self.q_loops)}
                if list(known) == ["python-variables"]:
                    default = {f"question_{self.q_vars.pk}": "0"}
                self.assertRedirects(self.client.post(reverse("intake_check"), check or default), reverse("intake"))
        self.answer(choice=hours)
        self.answer(choice=deadline)
        self.answer(choice=stuck)

    def step(self):
        return self.client.get(reverse("intake")).context.get("question")

    def test_welcome_points_to_intake(self):
        self.assertContains(self.client.get(reverse("welcome")), reverse("intake"))

    def test_first_question_offers_paths_not_languages(self):
        page = self.client.get(reverse("intake"))
        self.assertContains(page, "What do you want to be able to make?")
        self.assertContains(page, "Automate boring tasks")
        self.assertContains(page, "(coming soon)")  # website path: its first track is inactive
        self.assertContains(page, "Your goal")
        self.assertContains(page, "This isn&#x27;t a test")

    def test_full_flow(self):
        self.answer(choice="automation")
        self.answer(choice="suggest")
        self.answer(choice="a_little")
        self.answer(picked=["python-variables"])
        # The quick check comes before the review, only on the ticked topic.
        self.assertRedirects(self.client.get(reverse("intake")), reverse("intake_check"))
        check = self.client.get(reverse("intake_check"))
        self.assertEqual(len(check.context["form"].questions), 1)
        self.assertContains(check, "isn't a test")
        self.client.post(reverse("intake_check"), {f"question_{self.q_vars.pk}": "0"})
        self.answer(choice="2")
        self.answer(choice="0")
        self.answer(choice="think")

        review = self.client.get(reverse("intake"))
        self.assertTrue(review.context["review"])
        self.assertContains(review, "Did we get this right?")
        self.assertContains(review, "You showed:</strong> Variables")
        self.assertEqual((review.context["fit"]["low"], review.context["fit"]["high"]), (2, 3))  # 240 min at 2 h/week
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.onboarded)  # nothing saved to the profile before confirming

        with mock.patch("learning.tailoring.ask_model_json", return_value=self.tailor_reply()):
            self.assertRedirects(self.answer(action="confirm"), reverse("plan"))
        self.profile.refresh_from_db()
        self.assertEqual((self.profile.path.slug, self.profile.track, self.profile.hours_per_week), ("automation", self.track, 2))
        self.assertEqual((self.profile.experience, self.profile.hint_style, self.profile.target_weeks), ("a_little", "think", None))
        self.assertTrue(self.profile.onboarded)
        plan = LearningPlan.objects.get(learner=self.profile, is_active=True)
        # Lists was picked; Loops is added as its prerequisite; Variables was shown.
        self.assertEqual([i.topic.slug for i in plan.items.order_by("order")], ["python-loops", "python-lists"])
        self.assertEqual(plan.project.source, "ai")
        self.assertEqual(AIUsage.objects.get(learner=self.profile).intakes_used, 1)
        self.assertNotIn("onboarding", self.client.session)
        mastery = TopicMastery.objects.get(learner=self.profile, topic=self.topics[0])
        self.assertEqual((mastery.status, mastery.evidence), ("demonstrated", "1 of 1 correct on the quick check"))

        # The plan opens on one clear first activity, and starting it works.
        next_action = self.client.get(reverse("plan")).context["next_action"]
        self.assertEqual((next_action.kind, next_action.title), ("start", "Loops"))
        self.assertEqual(self.client.get(next_action.url).status_code, 200)
        self.assertEqual(plan.items.get(topic__slug="python-loops").status, "in_progress")

    def test_every_question_accepts_not_sure(self):
        self.answer_all(experience="not_sure", known=("not_sure",), hours="not_sure", stuck="not_sure")
        review = self.client.get(reverse("intake"))
        self.assertContains(review, "we&#x27;ll start with about 3 hours")
        self.assertContains(review, "Not sure yet:</strong> Variables, Loops")
        self.answer(action="confirm")
        self.profile.refresh_from_db()
        self.assertEqual((self.profile.experience, self.profile.hours_per_week, self.profile.hint_style), ("a_little", 3, ""))

    def test_goal_that_does_not_fit_the_date_explains_the_trade_off(self):
        self.answer_all(experience="none", hours="2", deadline="4")  # 300 min at 2 h/week is 3-4 weeks
        fit = self.client.get(reverse("intake")).context["fit"]
        self.assertTrue(fit["fits"])
        self.answer(action="change", step="hours")
        self.answer(choice="not_sure")  # 3 h/week: still fits
        self.answer(action="change", step="deadline")
        self.client.post(reverse("intake"), {"step": "deadline", "choice": "4"})
        from .onboarding import fit as plan_fit
        answers = dict(self.client.session["onboarding"], hours="2", deadline=1)
        result = plan_fit(answers)
        self.assertFalse(result["fits"])
        self.assertEqual(result["hours_needed"], 5)  # 300 minutes in one week

    def test_unavailable_path_cannot_be_picked(self):
        self.answer(choice="website")
        self.assertEqual(self.step().step, "path")

    def test_typed_path_is_read_by_the_ai(self):
        with mock.patch("diagnose.onboarding.ask_model_json", return_value={"path": "automation"}) as ask:
            self.answer(text="gusto ko i-automate yung attendance namin")
        self.assertIn("automation: Automate boring tasks", ask.call_args.args[0][0]["content"])
        self.assertNotIn("website", ask.call_args.args[0][0]["content"])  # unavailable paths aren't offered
        self.assertEqual(self.step().step, "project")

    def test_typed_path_is_offered_as_the_project(self):
        with mock.patch("diagnose.onboarding.ask_model_json", return_value={"path": "automation"}):
            self.answer(text="attendance tracker para sa org")
        choice = self.step().choices[0]
        self.assertEqual((choice.value, choice.label), ("typed", "attendance tracker para sa org"))
        self.answer(choice="typed")
        self.assertEqual(self.client.session["onboarding"]["project"]["text"], "attendance tracker para sa org")

    def test_double_click_does_not_answer_the_next_question(self):
        self.answer(choice="automation")
        self.client.post(reverse("intake"), {"step": "path", "choice": "automation"})  # the second click
        answers = self.client.session["onboarding"]
        self.assertNotIn("project", answers)
        self.assertEqual(self.step().step, "project")

    def test_review_shows_only_the_summary(self):
        self.answer_all()
        review = self.client.get(reverse("intake"))
        self.assertNotContains(review, 'class="chat"')
        self.assertContains(review, "your plan takes roughly")

    def test_typed_path_that_matches_nothing_asks_to_tap(self):
        with mock.patch("diagnose.onboarding.ask_model_json", return_value={"path": "none"}):
            response = self.client.post(reverse("intake"), {"step": "path", "text": "I want to fly"}, follow=True)
        self.assertContains(response, "Tap the one that&#x27;s closest")
        self.assertEqual(response.context["question"].step, "path")

    def test_not_sure_path_gets_the_first_available_one(self):
        self.answer(choice="not_sure")
        self.assertEqual(self.client.session["onboarding"]["path"], "automation")

    def test_beginners_skip_the_known_topics_and_the_check(self):
        self.answer(choice="automation")
        self.answer(choice="suggest")
        self.answer(choice="none")
        page = self.client.get(reverse("intake"))
        self.assertEqual(page.context["question"].step, "hours")
        self.assertEqual((page.context["done_count"], page.context["step_count"]), (3, 6))
        self.assertRedirects(self.client.get(reverse("intake_check")), reverse("intake"))

    def test_ticking_nothing_skips_the_check(self):
        self.answer(choice="automation")
        self.answer(choice="suggest")
        self.answer(choice="a_little")
        self.answer(picked=[])
        self.assertEqual(self.step().step, "hours")

    def test_not_sure_learner_gets_the_full_check(self):
        self.answer(choice="automation")
        self.answer(choice="suggest")
        self.answer(choice="a_little")
        self.answer(picked=["not_sure", "python-variables"])
        page = self.client.get(reverse("intake_check"))
        self.assertContains(page, "It just shows where to start")
        self.assertContains(page, "v?")
        self.assertContains(page, "l?")

    def test_change_re_asks_only_that_step(self):
        self.answer_all()
        self.answer(action="change", step="hours")
        self.assertEqual(self.step().step, "hours")
        self.answer(choice="8")
        answers = self.client.session["onboarding"]
        self.assertEqual((answers["hours"], answers["stuck"], answers["experience"]), ("8", "think", "a_little"))
        self.assertIn("check", answers)

    def test_changing_the_path_clears_answers_that_depend_on_it(self):
        from content.models import Path, PathTrack
        data = Path.objects.create(title="Work with data", slug="data", tagline="Count things.", order=3)
        PathTrack.objects.create(path=data, track=self.track)
        self.answer_all()
        self.answer(action="change", step="path")
        self.answer(choice="data")
        answers = self.client.session["onboarding"]
        self.assertNotIn("project", answers)
        self.assertNotIn("known", answers)
        self.assertNotIn("check", answers)
        self.assertEqual(answers["hours"], "2")
        self.assertEqual(self.step().step, "project")

    def test_curated_idea_is_offered_and_passed_to_the_planner(self):
        from content.models import ProjectIdea
        idea = ProjectIdea.objects.create(track=self.track, title="Attendance helper", brief="Count who came to each meeting.")
        self.answer(choice="automation")
        self.assertIn(f"idea:{idea.pk}", [c.value for c in self.step().choices])
        self.answer_all(project={"choice": f"idea:{idea.pk}"})
        with mock.patch("diagnose.views.tailor_plan", return_value=(None, True)) as tailor:
            self.answer(action="confirm")
        self.assertEqual(tailor.call_args.args[1], "Attendance helper: Count who came to each meeting.")

    def test_own_project_idea_becomes_the_goal(self):
        self.answer_all(project={"text": "a sign-up sheet  for our org"})
        with mock.patch("learning.tailoring.ask_model_json", return_value=None):
            self.answer(action="confirm")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.goal_text, "Automate boring tasks: a sign-up sheet for our org")

    def test_cap_reached_still_builds_a_plan_without_ai(self):
        from django.utils import timezone
        AIUsage.objects.create(learner=self.profile, date=timezone.localdate(), intakes_used=1)
        self.answer_all(experience="none")
        with mock.patch("learning.tailoring.ask_model_json") as ask:
            self.assertRedirects(self.answer(action="confirm"), reverse("plan"))
        ask.assert_not_called()
        plan = LearningPlan.objects.get(learner=self.profile, is_active=True)
        self.assertEqual((plan.items.count(), plan.project.source), (5, "template"))

    def test_tailoring_failure_gives_full_plan_and_template(self):
        self.answer_all(experience="none")
        with mock.patch("learning.tailoring.ask_model_json", return_value=None):
            self.assertRedirects(self.answer(action="confirm"), reverse("plan"))
        plan = LearningPlan.objects.get(learner=self.profile, is_active=True)
        self.assertEqual((plan.items.count(), plan.project.source), (5, "template"))

    def test_confirm_before_finishing_does_nothing(self):
        self.answer(choice="automation")
        self.assertRedirects(self.answer(action="confirm"), reverse("intake"))
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.onboarded)

    def test_restart_clears_answers(self):
        self.answer(choice="automation")
        self.answer(action="restart")
        self.assertNotIn("onboarding", self.client.session)

    def test_no_paths_goes_to_form(self):
        from content.models import Path
        Path.objects.all().delete()
        self.assertRedirects(self.client.get(reverse("intake")), reverse("about_you"))

    def test_check_without_onboarding_goes_back(self):
        self.assertRedirects(self.client.get(reverse("intake_check")), reverse("intake"))

    def test_change_my_answers_starts_from_the_saved_profile(self):
        self.answer_all(project={"text": "attendance helper"})
        with mock.patch("learning.tailoring.ask_model_json", return_value=None):
            self.answer(action="confirm")
        self.assertRedirects(self.client.get(reverse("intake") + "?edit=1"), reverse("intake"))
        review = self.client.get(reverse("intake"))
        self.assertTrue(review.context["review"])
        self.assertContains(review, "attendance helper")
        self.assertContains(review, "You showed:</strong> Variables")

    def test_changing_the_ticked_topics_redoes_the_check(self):
        self.answer_all()
        self.answer(action="change", step="known")
        self.answer(picked=["python-loops"])
        self.assertNotIn("check", self.client.session["onboarding"])
        self.assertEqual(len(self.client.get(reverse("intake_check")).context["form"].questions), 1)


@override_settings(**AI_OFF)
class OnboardingAIOffTests(IntakeContentMixin, TestCase):
    def setUp(self):
        self.make_content()
        self.client.force_login(self.user)

    def test_taps_still_work_and_typing_asks_to_tap(self):
        response = self.client.post(reverse("intake"), {"step": "path", "text": "automate stuff"}, follow=True)
        self.assertEqual(response.context["question"].step, "path")
        self.client.post(reverse("intake"), {"step": "path", "choice": "automation"})
        self.assertEqual(self.client.get(reverse("intake")).context["question"].step, "project")

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


class BuildMessagesTests(TestCase):
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


@override_settings(**AI_ON)
class ProjectIdeaTests(IntakeContentMixin, TestCase):
    def setUp(self):
        from content.models import ProjectIdea
        self.make_content()
        self.profile.track = self.track
        self.profile.hours_per_week = 3
        self.profile.save()
        ProjectIdea.objects.create(track=self.track, title="Hidden idea", brief="Off.", order=0, is_active=False)
        self.quiz_game = ProjectIdea.objects.create(
            track=self.track, title="Quiz game", brief="A quiz that keeps score.", order=1,
            done_when=["It asks 5 questions", "It shows the final score"],
        )
        self.budget = ProjectIdea.objects.create(track=self.track, title="Budget tracker", brief="Track spending.", order=2)

    def ai_project(self, **extra):
        project = {"title": "Org trivia night", "description": "A quiz about the org.",
                   "milestones": [{"topics": ["python-loops"], "text": "Ask one question"}],
                   "done_when": ["It keeps score"], **extra}
        return {"topics": [{"slug": "python-loops", "reason": "r"}], "project": project}

    def test_curated_ideas_are_sent_to_the_model(self):
        from learning.tailoring import active_ideas, build_tailor_messages
        system = build_tailor_messages(self.profile, "", self.topics, active_ideas(self.track))[0]["content"]
        self.assertIn("1. Quiz game: A quiz that keeps score.", system)
        self.assertIn("2. Budget tracker", system)
        self.assertNotIn("Hidden idea", system)

    def test_adapted_project_remembers_its_idea(self):
        from learning.tailoring import tailor_plan
        with mock.patch("learning.tailoring.ask_model_json", return_value=self.ai_project(idea=2)):
            plan, _ = tailor_plan(self.profile)
        self.assertEqual(plan.project.title, "Org trivia night")
        self.assertEqual(plan.project.idea, self.budget)
        self.assertEqual(plan.project.source, "ai")

    def test_bad_idea_number_is_ignored_and_checks_fall_back_to_the_idea(self):
        from learning.tailoring import tailor_plan
        with mock.patch("learning.tailoring.ask_model_json", return_value=self.ai_project(idea=9)):
            self.assertIsNone(tailor_plan(self.profile)[0].project.idea)
        with mock.patch("learning.tailoring.ask_model_json", return_value=self.ai_project(idea=1, done_when=[])):
            project = tailor_plan(self.profile)[0].project
        self.assertEqual(project.done_when, ["It asks 5 questions", "It shows the final score"])

    def test_without_ai_the_first_active_idea_is_the_project(self):
        from learning.tailoring import tailor_plan
        with mock.patch("learning.tailoring.ask_model_json", return_value=None):
            project = tailor_plan(self.profile)[0].project
        self.assertEqual((project.title, project.idea, project.source), ("Quiz game", self.quiz_game, "template"))
        self.assertEqual(project.done_when, ["It asks 5 questions", "It shows the final score"])
        self.assertTrue(project.milestones)
