from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse

from accounts.models import get_profile
from content.models import Topic, Track
from diagnose.models import TopicMastery

from .models import LearningPlan, PlanItem
from .planner import assign_weeks, build_plan, order_topics


def topic(pk, order, minutes=60):
    return SimpleNamespace(pk=pk, order=order, title=f"T{pk}", estimated_minutes=minutes)


class OrderTopicsTests(TestCase):
    def test_keeps_content_order_without_prerequisites(self):
        topics = [topic(3, 3), topic(1, 1), topic(2, 2)]
        self.assertEqual([t.pk for t in order_topics(topics, {})], [1, 2, 3])

    def test_puts_prerequisites_first(self):
        # Topic 1 comes first by order but needs topic 3.
        topics = [topic(1, 1), topic(2, 2), topic(3, 3)]
        ordered = [t.pk for t in order_topics(topics, {1: {3}})]
        self.assertLess(ordered.index(3), ordered.index(1))
        self.assertEqual(ordered, [2, 3, 1])

    def test_ignores_prerequisites_outside_the_plan(self):
        topics = [topic(2, 2), topic(3, 3)]
        self.assertEqual([t.pk for t in order_topics(topics, {2: {1}, 3: {2}})], [2, 3])

    def test_survives_a_cycle(self):
        topics = [topic(1, 1), topic(2, 2), topic(3, 3)]
        ordered = order_topics(topics, {1: {2}, 2: {1}})
        self.assertEqual([t.pk for t in ordered], [1, 2, 3])


class AssignWeeksTests(TestCase):
    def test_splits_by_hours_per_week(self):
        topics = [topic(i, i, 60) for i in range(1, 6)]
        self.assertEqual(assign_weeks(topics, 2), [1, 1, 2, 2, 3])

    def test_topic_that_does_not_fit_starts_next_week(self):
        topics = [topic(1, 1, 45), topic(2, 2, 30)]
        self.assertEqual(assign_weeks(topics, 1), [1, 2])

    def test_topic_longer_than_a_week_gets_its_own_week(self):
        topics = [topic(1, 1, 30), topic(2, 2, 120), topic(3, 3, 30)]
        self.assertEqual(assign_weeks(topics, 1), [1, 2, 3])

    def test_minimum_one_hour(self):
        topics = [topic(1, 1, 60), topic(2, 2, 60)]
        self.assertEqual(assign_weeks(topics, 0), [1, 2])
        self.assertEqual(assign_weeks(topics, None), [1, 2])


class PlanTestMixin:
    def make_learner(self, hours=2):
        self.track = Track.objects.create(name="Python", slug="python")
        self.topics = [
            Topic.objects.create(track=self.track, title=f"Topic {i}", slug=f"topic-{i}", order=i, estimated_minutes=60)
            for i in range(1, 5)
        ]
        for before, after in zip(self.topics, self.topics[1:]):
            after.prerequisites.add(before)
        self.user = get_user_model().objects.create_user("ana")
        self.profile = get_profile(self.user)
        self.profile.track = self.track
        self.profile.hours_per_week = hours
        self.profile.onboarded = True
        self.profile.save()


class BuildPlanTests(PlanTestMixin, TestCase):
    def setUp(self):
        self.make_learner()

    def test_skips_known_topics_and_assigns_weeks(self):
        TopicMastery.objects.create(learner=self.profile, topic=self.topics[0], known=True, source="skill_check")
        TopicMastery.objects.create(learner=self.profile, topic=self.topics[1], known=False, source="skill_check")
        plan = build_plan(self.profile)
        items = list(plan.items.order_by("order"))
        self.assertEqual([i.topic for i in items], self.topics[1:])
        self.assertEqual([i.week for i in items], [1, 1, 2])

    def test_rebuilding_deactivates_the_old_plan(self):
        old = build_plan(self.profile)
        new = build_plan(self.profile)
        old.refresh_from_db()
        self.assertFalse(old.is_active)
        self.assertTrue(new.is_active)
        self.assertEqual(LearningPlan.objects.filter(learner=self.profile, is_active=True).count(), 1)

    def test_database_allows_only_one_active_plan(self):
        LearningPlan.objects.create(learner=self.profile, track=self.track)
        with self.assertRaises(IntegrityError):
            LearningPlan.objects.create(learner=self.profile, track=self.track)

    def test_real_cycle_in_content_still_builds(self):
        self.topics[0].prerequisites.add(self.topics[3])
        plan = build_plan(self.profile)
        self.assertEqual([i.topic for i in plan.items.order_by("order")], self.topics)

    def test_progress_ignores_skipped(self):
        plan = build_plan(self.profile)
        items = list(plan.items.order_by("order"))
        items[0].status = "done"; items[0].save()
        items[1].status = "skipped"; items[1].save()
        self.assertEqual(plan.progress_percent(), 33)


class PlanViewTests(PlanTestMixin, TestCase):
    def setUp(self):
        self.make_learner()
        self.client.force_login(self.user)

    def test_home_goes_to_plan_and_plan_without_one_goes_to_result(self):
        self.assertRedirects(self.client.get(reverse("home")), reverse("plan"), fetch_redirect_response=False)
        self.assertRedirects(self.client.get(reverse("plan")), reverse("result"), fetch_redirect_response=False)

    def test_build_then_view_plan(self):
        self.assertRedirects(self.client.post(reverse("plan_build")), reverse("plan"))
        response = self.client.get(reverse("plan"))
        self.assertContains(response, "Week 1")
        self.assertContains(response, "Week 2")
        self.assertContains(response, "0% done")

    def test_build_needs_post(self):
        self.assertEqual(self.client.get(reverse("plan_build")).status_code, 405)

    def test_mark_done_updates_progress_and_mastery(self):
        plan = build_plan(self.profile)
        item = plan.items.order_by("order").first()
        url = reverse("plan_item_status", args=[item.pk])
        self.assertRedirects(self.client.post(url, {"status": "done"}), reverse("plan"))
        item.refresh_from_db()
        self.assertEqual(item.status, "done")
        self.assertIsNotNone(item.completed_at)
        self.assertTrue(TopicMastery.objects.get(learner=self.profile, topic=item.topic).known)
        self.assertContains(self.client.get(reverse("plan")), "25% done")

        self.client.post(url, {"status": "todo"})
        item.refresh_from_db()
        self.assertIsNone(item.completed_at)
        self.assertFalse(TopicMastery.objects.get(learner=self.profile, topic=item.topic).known)

    def test_rejects_unknown_status(self):
        item = build_plan(self.profile).items.first()
        self.client.post(reverse("plan_item_status", args=[item.pk]), {"status": "in_progress"})
        item.refresh_from_db()
        self.assertEqual(item.status, PlanItem.Status.TODO)

    def test_cannot_change_someone_elses_item(self):
        item = build_plan(self.profile).items.first()
        other = get_user_model().objects.create_user("ben")
        self.client.force_login(other)
        response = self.client.post(reverse("plan_item_status", args=[item.pk]), {"status": "done"})
        self.assertEqual(response.status_code, 404)
