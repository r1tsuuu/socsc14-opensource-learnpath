from io import StringIO
from types import SimpleNamespace
from unittest import mock

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from .drafting import DraftError, save_draft
from .models import Topic, Track

GOOD = {
    "summary": "Build a basic page.",
    "estimated_minutes": 45,
    "tips": ["Close your tags.", "Put the title in head."],
    "skill_questions": [
        {"prompt": "Which tag sets the tab text?", "code_snippet": "", "choices": ["<head>", "<title>", "<meta>", "<body>"], "correct_index": 1},
        {"prompt": "First line of HTML5?", "code_snippet": "", "choices": ["<!DOCTYPE html>", "<html>"], "correct_index": 0},
    ],
    "quiz_items": [
        {"prompt": "<!DOCTYPE ___>", "accepted_answers": ["html"], "hint": "The language name."},
        {"prompt": "<html ___=\"en\">", "accepted_answers": ["lang"], "hint": "Language attribute."},
    ],
}


def fake_client(payload):
    message = SimpleNamespace(content=payload)
    response = SimpleNamespace(choices=[SimpleNamespace(message=message)])
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=mock.Mock(return_value=response))))


class SaveDraftTests(TestCase):
    def setUp(self):
        self.track = Track.objects.create(name="HTML", slug="html", is_active=False)

    def test_saves_topic_with_children_and_prerequisite(self):
        first = Topic.objects.create(track=self.track, title="Intro", slug="html-intro", order=1)
        topic, counts = save_draft(self.track, "Page structure", GOOD, prerequisite=first)
        self.assertEqual(topic.slug, "html-page-structure")
        self.assertEqual(topic.order, 2)
        self.assertEqual(list(topic.prerequisites.all()), [first])
        self.assertEqual(counts, {"tips": 2, "skill_questions": 2, "quiz_items": 2})

    def test_invalid_part_saves_nothing(self):
        bad = dict(GOOD, quiz_items=[{"prompt": "no blank", "accepted_answers": ["x"]}])
        with self.assertRaises(DraftError):
            save_draft(self.track, "Page structure", bad)
        self.assertFalse(Topic.objects.exists())

    def test_wrong_correct_index_saves_nothing(self):
        bad = dict(GOOD, skill_questions=[dict(GOOD["skill_questions"][0], correct_index=7)])
        with self.assertRaises(DraftError):
            save_draft(self.track, "Page structure", bad)
        self.assertFalse(Topic.objects.exists())

    def test_existing_slug_is_skipped(self):
        save_draft(self.track, "Page structure", GOOD)
        with self.assertRaises(DraftError):
            save_draft(self.track, "Page structure", GOOD)

    def test_draft_without_questions_is_rejected(self):
        with self.assertRaises(DraftError):
            save_draft(self.track, "Empty", dict(GOOD, skill_questions=[]))
        self.assertFalse(Topic.objects.exists())


@override_settings(GROQ_API_KEY="test-key")
class DraftTopicCommandTests(TestCase):
    def setUp(self):
        self.track = Track.objects.create(name="HTML", slug="html", is_active=False)

    def run_command(self, *args, payload='{}'):
        import json
        client = fake_client(payload if isinstance(payload, str) else json.dumps(payload))
        out, err = StringIO(), StringIO()
        with mock.patch("content.management.commands.draft_topic.make_client", return_value=client):
            call_command("draft_topic", *args, stdout=out, stderr=err)
        return out.getvalue(), err.getvalue(), client

    def test_drafts_and_chains_topics(self):
        out, err, client = self.run_command("html", "Page structure", "Text", payload=GOOD)
        self.assertIn("2 drafted, 0 skipped", out)
        text = Topic.objects.get(slug="html-text")
        self.assertEqual([p.slug for p in text.prerequisites.all()], ["html-page-structure"])
        self.assertEqual(client.chat.completions.create.call_count, 2)

    def test_never_asks_for_links(self):
        _, _, client = self.run_command("html", "Page structure", payload=GOOD)
        prompt = str(client.chat.completions.create.call_args.kwargs["messages"])
        self.assertIn("No links or URLs", prompt)

    def test_dry_run_saves_nothing(self):
        out, _, _ = self.run_command("html", "Page structure", "--dry-run", payload=GOOD)
        self.assertIn("Close your tags.", out)
        self.assertFalse(Topic.objects.exists())

    def test_bad_json_is_skipped(self):
        out, err, _ = self.run_command("html", "Page structure", payload="not json")
        self.assertIn("skipped", err)
        self.assertIn("0 drafted, 1 skipped", out)

    def test_refuses_live_track(self):
        self.track.is_active = True
        self.track.save()
        with self.assertRaises(CommandError):
            self.run_command("html", "Page structure", payload=GOOD)

    def test_unknown_track(self):
        with self.assertRaises(CommandError):
            self.run_command("nope", "Page structure", payload=GOOD)

    @override_settings(GROQ_API_KEY="")
    def test_needs_api_key(self):
        with self.assertRaises(CommandError):
            self.run_command("html", "Page structure", payload=GOOD)
