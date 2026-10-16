"""Seed the starting paths. Curators edit or add paths in admin afterwards.

A path is only created when its tracks exist, so empty databases (tests) stay empty.
"""

from django.db import migrations

PATHS = [
    ("website", "Make a website", "A page for your org, a portfolio, an event site.", ["html", "css", "javascript"]),
    ("automation", "Automate boring tasks", "Rename files, tidy spreadsheets, make small helper tools.", ["python"]),
    ("data", "Work with data", "Sort, count and answer questions about real data, like survey results.", ["python", "sql"]),
    ("games", "Make games and fun projects", "Text adventures, quizzes, small simulations.", ["python", "javascript"]),
    ("cs-class", "Get ahead in CS class", "Feel ready for programming classes and exams.", ["python", "java"]),
]


def seed(apps, schema_editor):
    Path = apps.get_model("content", "Path")
    PathTrack = apps.get_model("content", "PathTrack")
    Track = apps.get_model("content", "Track")
    tracks = {t.slug: t for t in Track.objects.all()}
    for order, (slug, title, tagline, track_slugs) in enumerate(PATHS, 1):
        found = [tracks[s] for s in track_slugs if s in tracks]
        if not found or Path.objects.filter(slug=slug).exists():
            continue
        path = Path.objects.create(slug=slug, title=title, tagline=tagline, order=order)
        for i, track in enumerate(found, 1):
            PathTrack.objects.create(path=path, track=track, order=i)


class Migration(migrations.Migration):
    dependencies = [("content", "0003_paths")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
