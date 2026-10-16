from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase

from .forms import QuizItemForm, SkillQuestionForm, TopicForm
from .models import QuizItem, SkillQuestion, Topic, Track


class FixtureTests(TestCase):
    def test_sample_content_loads(self):
        call_command("loaddata", "fixtures/content.json", verbosity=0)
        python = Track.objects.get(slug="python")
        self.assertTrue(python.is_active)
        self.assertEqual(python.topics.count(), 8)
        self.assertFalse(Track.objects.exclude(slug="python").filter(is_active=True).exists())
        for topic in python.topics.all():
            self.assertEqual(topic.resources.count(), 2)
            self.assertEqual(topic.tips.count(), 2)
            self.assertIn(topic.skill_questions.count(), [1, 2])
            self.assertIn(topic.quiz_items.count(), [2, 3])
            for question in topic.skill_questions.all():
                question.full_clean()
            for item in topic.quiz_items.all():
                item.full_clean()


class ContentValidationTests(TestCase):
    def setUp(self):
        self.track = Track.objects.create(name="Python", slug="python")
        self.topic = Topic.objects.create(track=self.track, title="Loops", slug="loops")

    def test_correct_index_must_point_at_a_choice(self):
        question = SkillQuestion(topic=self.topic, prompt="?", choices=["a", "b"], correct_index=2)
        with self.assertRaises(ValidationError):
            question.full_clean()

    def test_quiz_prompt_needs_a_blank(self):
        item = QuizItem(topic=self.topic, prompt="no blank here", accepted_answers=["x"])
        with self.assertRaises(ValidationError):
            item.full_clean()

    def test_lines_field_turns_lines_into_a_list(self):
        form = SkillQuestionForm(data={
            "topic": self.topic.pk, "prompt": "?", "choices": "4\n 5 \n\n6",
            "correct_index": 1, "order": 0,
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["choices"], ["4", "5", "6"])

    def test_quiz_form_needs_an_answer(self):
        form = QuizItemForm(data={"topic": self.topic.pk, "prompt": "for i in ___(5):", "accepted_answers": " \n"})
        self.assertFalse(form.is_valid())

    def test_prerequisites_must_be_in_the_same_track(self):
        other = Track.objects.create(name="SQL", slug="sql")
        foreign = Topic.objects.create(track=other, title="Select", slug="select")
        form = TopicForm(instance=self.topic, data={
            "track": self.track.pk, "title": "Loops", "slug": "loops", "order": 0,
            "estimated_minutes": 60, "prerequisites": [foreign.pk],
        })
        self.assertFalse(form.is_valid())
        self.assertIn("prerequisites", form.errors)

    def test_topic_cannot_require_itself(self):
        form = TopicForm(instance=self.topic, data={
            "track": self.track.pk, "title": "Loops", "slug": "loops", "order": 0,
            "estimated_minutes": 60, "prerequisites": [self.topic.pk],
        })
        self.assertFalse(form.is_valid())


class AdminPagesTests(TestCase):
    def test_topic_add_page_loads_for_staff(self):
        admin = get_user_model().objects.create_superuser("admin", "a@example.com", "pw")
        self.client.force_login(admin)
        call_command("loaddata", "fixtures/content.json", verbosity=0)
        self.assertEqual(self.client.get("/admin/content/topic/add/").status_code, 200)
        self.assertEqual(self.client.get("/admin/content/topic/1/change/").status_code, 200)
        self.assertEqual(self.client.get("/admin/content/track/1/change/").status_code, 200)


class ProjectIdeaAdminTests(TestCase):
    def test_done_when_is_edited_one_check_per_line(self):
        from .forms import ProjectIdeaForm
        track = Track.objects.create(name="Python", slug="python")
        form = ProjectIdeaForm({"track": track.pk, "title": "Quiz game", "brief": "A quiz.",
                                "done_when": "It runs\n\n It keeps score ", "order": 0, "is_active": True})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().done_when, ["It runs", "It keeps score"])
