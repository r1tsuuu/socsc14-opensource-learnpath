"""Draft topics with AI for curators to review in admin.

    python manage.py draft_topic html "Page structure" "Text and headings"

Each title costs one Groq call. Topics are added after the track's existing ones,
each requiring the one before. Links are never generated; add 2 per topic in admin.
"""

import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from content.drafting import DraftError, make_client, request_draft, save_draft
from content.models import Topic, Track

DEFAULT_DRAFT_MODEL = "openai/gpt-oss-120b"


class Command(BaseCommand):
    help = "Use AI to draft tips, skill check questions and quiz items for new topics."

    def add_arguments(self, parser):
        parser.add_argument("track", help="Track slug, e.g. html")
        parser.add_argument("titles", nargs="+", help="Topic titles, in learning order")
        parser.add_argument("--model", default=DEFAULT_DRAFT_MODEL, help="Groq model id")
        parser.add_argument("--dry-run", action="store_true", help="Print drafts without saving")
        parser.add_argument(
            "--allow-active-track", action="store_true",
            help="Save into a track learners can already see (drafts go live before review)",
        )

    def handle(self, *args, track, titles, model, dry_run, allow_active_track, **options):
        if not settings.GROQ_API_KEY:
            raise CommandError("Set GROQ_API_KEY in .env first.")
        try:
            track = Track.objects.get(slug=track)
        except Track.DoesNotExist:
            raise CommandError(f"No track with slug {track!r}. Create it in admin first.")
        if track.is_active and not dry_run and not allow_active_track:
            raise CommandError(
                f"{track.name} is live, so drafts would reach learners before review. "
                "Deactivate it in admin first, or pass --allow-active-track."
            )

        client = make_client(settings.GROQ_API_KEY)
        existing = list(Topic.objects.filter(track=track).order_by("order", "title"))
        earlier_titles = [t.title for t in existing]
        previous = existing[-1] if existing else None
        saved = failed = 0

        for title in titles:
            self.stdout.write(f"Drafting {track.name}: {title} ...")
            try:
                data = request_draft(client, model, track, title, earlier_titles)
                if dry_run:
                    self.stdout.write(json.dumps(data, indent=2, ensure_ascii=False))
                else:
                    previous, counts = save_draft(track, title, data, prerequisite=previous)
                    self.stdout.write(self.style.SUCCESS(
                        f"  saved {previous.slug}: {counts['tips']} tips, "
                        f"{counts['skill_questions']} questions, {counts['quiz_items']} quiz items"
                    ))
                    saved += 1
                earlier_titles.append(title)
            except DraftError as error:
                failed += 1
                self.stderr.write(self.style.ERROR(f"  skipped: {error}"))
            except Exception as error:  # Network or API errors: report and move on.
                failed += 1
                self.stderr.write(self.style.ERROR(f"  skipped: Groq call failed ({error})"))

        if not dry_run:
            self.stdout.write(
                f"\n{saved} drafted, {failed} skipped. Next: review each topic in admin, "
                "fix anything wrong, add 2 links per topic, then activate the track."
            )
