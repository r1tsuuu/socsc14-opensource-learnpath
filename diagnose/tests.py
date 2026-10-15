from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import get_profile
from content.models import SkillQuestion, Topic, Track

from .models import SkillAnswer, SkillCheckAttempt, TopicMastery
from .scoring import finish_attempt, save_skill_check_mastery, score_topics


class ContentMixin:
    def make_content(self):
        self.track = Track.objects.create(name="Python", slug="python")
        self.variables = Topic.objects.create(track=self.track, title="Variables", slug="variables", order=1)
        self.loops = Topic.objects.create(track=self.track, title="Loops", slug="loops", order=2)
        self.empty = Topic.objects.create(track=self.track, title="Files", slug="files", order=3)
        self.q1 = SkillQuestion.objects.create(topic=self.variables, prompt="a", choices=["4", "5"], correct_index=1, order=1)
        self.q2 = SkillQuestion.objects.create(topic=self.variables, prompt="b", choices=["x", "y"], correct_index=0, order=2)
        self.q3 = SkillQuestion.objects.create(topic=self.loops, prompt="c", choices=["2", "3"], correct_index=1, order=1)

    def score(self, answers):
        topics = [self.variables, self.loops, self.empty]
        results = score_topics(topics, [self.q1, self.q2, self.q3], answers)
        return {r.topic.slug: r for r in results}


class ScoringRuleTests(ContentMixin, TestCase):
    def setUp(self):
        self.make_content()

    def test_all_correct_means_known(self):
        results = self.score({self.q1.pk: 1, self.q2.pk: 0, self.q3.pk: 1})
        self.assertTrue(results["variables"].known)
        self.assertEqual(results["variables"].reason, "2 of 2 correct")
        self.assertTrue(results["loops"].known)

    def test_one_wrong_means_not_known(self):
        results = self.score({self.q1.pk: 1, self.q2.pk: 1, self.q3.pk: 1})
        self.assertFalse(results["variables"].known)
        self.assertEqual(results["variables"].reason, "1 of 2 correct")

    def test_dont_know_means_not_known(self):
        results = self.score({self.q1.pk: 1, self.q2.pk: 0, self.q3.pk: None})
        self.assertFalse(results["loops"].known)
        self.assertEqual(results["loops"].reason, "0 of 1 correct")

    def test_missing_answer_counts_as_dont_know(self):
        self.assertFalse(self.score({})["loops"].known)

    def test_topic_without_questions_is_not_known(self):
        results = self.score({self.q1.pk: 1, self.q2.pk: 0, self.q3.pk: 1})
        self.assertFalse(results["files"].known)
        self.assertEqual(results["files"].total, 0)


class FinishAttemptTests(ContentMixin, TestCase):
    def setUp(self):
        self.make_content()
        self.profile = get_profile(get_user_model().objects.create_user("ana"))

    def test_saves_answers_mastery_and_finish_time(self):
        attempt = SkillCheckAttempt.objects.create(learner=self.profile, track=self.track)
        finish_attempt(attempt, {self.q1.pk: 1, self.q2.pk: 0, self.q3.pk: None})
        attempt.refresh_from_db()
        self.assertIsNotNone(attempt.finished_at)
        self.assertEqual(SkillAnswer.objects.filter(attempt=attempt).count(), 3)
        mastery = {m.topic.slug: m for m in TopicMastery.objects.filter(learner=self.profile)}
        self.assertTrue(mastery["variables"].known)
        self.assertFalse(mastery["loops"].known)
        self.assertFalse(mastery["files"].known)
        self.assertEqual(mastery["variables"].source, "skill_check")

    def test_retake_updates_instead_of_duplicating(self):
        for answers in ({self.q3.pk: 1}, {self.q3.pk: 0}):
            attempt = SkillCheckAttempt.objects.create(learner=self.profile, track=self.track)
            finish_attempt(attempt, answers)
        loops = TopicMastery.objects.get(learner=self.profile, topic=self.loops)
        self.assertFalse(loops.known)

    def test_never_undoes_a_quiz_pass(self):
        TopicMastery.objects.create(learner=self.profile, topic=self.loops, known=True, source="quiz")
        save_skill_check_mastery(self.profile, self.loops, known=False)
        loops = TopicMastery.objects.get(learner=self.profile, topic=self.loops)
        self.assertTrue(loops.known)
        self.assertEqual(loops.source, "quiz")


class DiagnoseFlowTests(ContentMixin, TestCase):
    def setUp(self):
        self.make_content()
        Track.objects.create(name="SQL", slug="sql", is_active=False)
        self.user = get_user_model().objects.create_user("ana")
        self.client.force_login(self.user)

    def about_you(self):
        return self.client.post(reverse("about_you"), {
            "track": self.track.pk, "experience": "a_little", "hours_per_week": 3, "goal_text": "",
        })

    def test_about_you_onboards_and_goes_to_skill_check(self):
        self.assertRedirects(self.about_you(), reverse("skill_check"))
        profile = get_profile(self.user)
        self.assertTrue(profile.onboarded)
        self.assertEqual(profile.track, self.track)
        self.assertEqual(profile.hours_per_week, 3)

    def test_about_you_only_offers_active_tracks(self):
        response = self.client.get(reverse("about_you"))
        self.assertContains(response, "Python")
        self.assertNotContains(response, "SQL")

    def test_skill_check_needs_about_you_first(self):
        self.assertRedirects(self.client.get(reverse("skill_check")), reverse("about_you"))

    def test_full_flow_shows_reasons(self):
        self.about_you()
        page = self.client.get(reverse("skill_check_questions"))
        self.assertContains(page, "I don&#x27;t know yet")
        response = self.client.post(reverse("skill_check_questions"), {
            f"question_{self.q1.pk}": "1", f"question_{self.q2.pk}": "0", f"question_{self.q3.pk}": "dont_know",
        })
        self.assertRedirects(response, reverse("result"))
        self.assertEqual(SkillCheckAttempt.objects.count(), 1)
        result = self.client.get(reverse("result"))
        self.assertContains(result, "2 of 2 correct")
        self.assertContains(result, "0 of 1 correct")
        self.assertContains(result, "No skill check questions for this topic yet")
        self.assertEqual(result.context["known"][0].topic, self.variables)

    def test_unanswered_question_shows_error(self):
        self.about_you()
        response = self.client.post(reverse("skill_check_questions"), {f"question_{self.q1.pk}": "1"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Answer every question")
        self.assertFalse(SkillCheckAttempt.objects.filter(finished_at__isnull=False).exists())

    def test_result_without_attempt_goes_to_skill_check(self):
        self.about_you()
        self.assertRedirects(self.client.get(reverse("result")), reverse("skill_check"))
