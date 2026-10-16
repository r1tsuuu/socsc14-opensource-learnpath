"""Learning content. Edited by the content team in Django admin only."""

from django.core.exceptions import ValidationError
from django.db import models


class Track(models.Model):
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(
        default=True, help_text="Only active tracks are shown to learners."
    )

    class Meta:
        ordering = ["order", "name"]

    def __str__(self):
        return self.name


class Path(models.Model):
    """A skill learners pick by what they want to make (e.g. "Make a website").

    Learners choose a path, not a language: the path decides which tracks they learn,
    in order. A path is offered once its first track is active.
    """

    title = models.CharField(max_length=100, help_text='What they can make, e.g. "Make a website".')
    slug = models.SlugField(unique=True)
    tagline = models.CharField(
        max_length=200, help_text='Real examples, e.g. "A page for your org, a portfolio, an event site."'
    )
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    tracks = models.ManyToManyField(Track, through="PathTrack", related_name="paths")

    class Meta:
        ordering = ["order", "title"]

    def __str__(self):
        return self.title

    def ordered_tracks(self):
        return [pt.track for pt in self.path_tracks.select_related("track").order_by("order", "pk")]

    def active_tracks(self):
        """The tracks learners can study now, in path order. Later tracks join as they go live."""
        return [t for t in self.ordered_tracks() if t.is_active and t.topics.exists()]

    def first_track(self):
        tracks = self.ordered_tracks()
        return tracks[0] if tracks else None

    def is_available(self):
        track = self.first_track()
        return bool(self.is_active and track and track.is_active and track.topics.exists())


class PathTrack(models.Model):
    path = models.ForeignKey(Path, on_delete=models.CASCADE, related_name="path_tracks")
    track = models.ForeignKey(Track, on_delete=models.CASCADE, related_name="+")
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["path", "order"]
        constraints = [models.UniqueConstraint(fields=["path", "track"], name="unique_track_per_path")]

    def __str__(self):
        return f"{self.path}: {self.track}"


class Topic(models.Model):
    track = models.ForeignKey(Track, on_delete=models.CASCADE, related_name="topics")
    title = models.CharField(max_length=200)
    slug = models.SlugField(
        max_length=200,
        unique=True,
        help_text="Used in lesson URLs, so it must be unique across all tracks "
        "(e.g. js-loops if Python already has loops).",
    )
    summary = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0)
    estimated_minutes = models.PositiveIntegerField(default=60)
    prerequisites = models.ManyToManyField(
        "self",
        symmetrical=False,
        blank=True,
        related_name="required_by",
        help_text="Topics in the same track that must come first.",
    )

    class Meta:
        ordering = ["track", "order", "title"]

    def __str__(self):
        return f"{self.track.name}: {self.title}"


class Resource(models.Model):
    class Kind(models.TextChoices):
        VIDEO = "video", "Video"
        ARTICLE = "article", "Article"
        INTERACTIVE = "interactive", "Interactive"
        DOCS = "docs", "Docs"

    topic = models.ForeignKey(Topic, on_delete=models.CASCADE, related_name="resources")
    title = models.CharField(max_length=200)
    url = models.URLField(max_length=500)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.ARTICLE)
    estimated_minutes = models.PositiveIntegerField(default=15)
    order = models.PositiveIntegerField(default=0)
    last_checked = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["topic", "order", "title"]

    def __str__(self):
        return self.title


class Tip(models.Model):
    topic = models.ForeignKey(Topic, on_delete=models.CASCADE, related_name="tips")
    text = models.TextField()
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["topic", "order"]

    def __str__(self):
        return self.text[:60]


class SetupStep(models.Model):
    track = models.ForeignKey(Track, on_delete=models.CASCADE, related_name="setup_steps")
    order = models.PositiveIntegerField(default=0)
    title = models.CharField(max_length=200)
    body = models.TextField()
    link = models.URLField(max_length=500, blank=True)

    class Meta:
        ordering = ["track", "order"]

    def __str__(self):
        return self.title


class ProjectIdea(models.Model):
    """A curated project idea for a track. The AI picks one and adapts it to each learner."""

    track = models.ForeignKey(Track, on_delete=models.CASCADE, related_name="project_ideas")
    title = models.CharField(max_length=200)
    brief = models.TextField(help_text="What the learner builds, in 2-3 sentences, for a beginner.")
    done_when = models.JSONField(default=list, help_text="One check per line, e.g. \"It runs without errors.\"")
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["track", "order", "title"]

    def __str__(self):
        return self.title


def _validate_string_list(value, field_name):
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise ValidationError({field_name: "Enter a list of non-empty answers."})


class SkillQuestion(models.Model):
    """Multiple choice. The template adds "I don't know yet"; it is not stored."""

    topic = models.ForeignKey(Topic, on_delete=models.CASCADE, related_name="skill_questions")
    prompt = models.TextField()
    code_snippet = models.TextField(blank=True)
    choices = models.JSONField(default=list, help_text="One choice per line.")
    correct_index = models.PositiveIntegerField(
        help_text="Position of the right choice, counting from 0 (first choice = 0)."
    )
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["topic", "order"]

    def __str__(self):
        return self.prompt[:60]

    def clean(self):
        _validate_string_list(self.choices, "choices")
        if len(self.choices) < 2:
            raise ValidationError({"choices": "Give at least two choices."})
        if self.correct_index is not None and self.correct_index >= len(self.choices):
            raise ValidationError(
                {"correct_index": f"Must be between 0 and {len(self.choices) - 1}."}
            )


class QuizItem(models.Model):
    """Fill in the blank. Write the blank in the prompt as ___."""

    topic = models.ForeignKey(Topic, on_delete=models.CASCADE, related_name="quiz_items")
    prompt = models.TextField(help_text="Write the blank as ___ (three underscores).")
    accepted_answers = models.JSONField(default=list, help_text="One accepted answer per line.")
    hint = models.TextField(blank=True)
    case_sensitive = models.BooleanField(default=False)

    class Meta:
        ordering = ["topic", "id"]

    def __str__(self):
        return self.prompt[:60]

    def clean(self):
        _validate_string_list(self.accepted_answers, "accepted_answers")
        if not self.accepted_answers:
            raise ValidationError({"accepted_answers": "Give at least one accepted answer."})
        if "___" not in self.prompt:
            raise ValidationError({"prompt": "Mark the blank with ___ (three underscores)."})
