from django.db import models
from django.db.models import Q


class LearningPlan(models.Model):
    learner = models.ForeignKey(
        "accounts.LearnerProfile", on_delete=models.CASCADE, related_name="plans"
    )
    track = models.ForeignKey("content.Track", on_delete=models.CASCADE, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["learner", "track"],
                condition=Q(is_active=True),
                name="one_active_plan_per_track",
            )
        ]

    def __str__(self):
        return f"{self.learner.user} · {self.track}{'' if self.is_active else ' (old)'}"

    def progress_percent(self):
        """Share of non-skipped items that are done. Computed, never stored."""
        statuses = list(self.items.values_list("status", flat=True))
        counted = [s for s in statuses if s != PlanItem.Status.SKIPPED]
        if not counted:
            return 100 if statuses else 0
        done = sum(1 for s in counted if s == PlanItem.Status.DONE)
        return round(done * 100 / len(counted))


class PlanItem(models.Model):
    class Status(models.TextChoices):
        TODO = "todo", "To do"
        IN_PROGRESS = "in_progress", "In progress"
        DONE = "done", "Done"
        SKIPPED = "skipped", "Skipped"

    plan = models.ForeignKey(LearningPlan, on_delete=models.CASCADE, related_name="items")
    topic = models.ForeignKey("content.Topic", on_delete=models.CASCADE, related_name="+")
    order = models.PositiveIntegerField()
    week = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.TODO)
    completed_at = models.DateTimeField(null=True, blank=True)
    reason = models.CharField(
        max_length=300, blank=True, help_text="Why this topic is in the plan (from the AI intake)."
    )

    class Meta:
        ordering = ["plan", "order"]

    def __str__(self):
        return f"Week {self.week}: {self.topic.title} ({self.get_status_display()})"


class PlanProject(models.Model):
    """A small culminating project tied to the learner's goal and plan."""

    class Source(models.TextChoices):
        AI = "ai", "AI"
        TEMPLATE = "template", "Template"

    plan = models.OneToOneField(LearningPlan, on_delete=models.CASCADE, related_name="project")
    title = models.CharField(max_length=200)
    description = models.TextField()
    milestones = models.JSONField(default=list, help_text='List of {"week": n, "text": "..."}.')
    done_when = models.JSONField(default=list, help_text="Checklist of strings.")
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.TEMPLATE)

    def __str__(self):
        return self.title


class QuizAttempt(models.Model):
    learner = models.ForeignKey(
        "accounts.LearnerProfile", on_delete=models.CASCADE, related_name="quiz_attempts"
    )
    quiz_item = models.ForeignKey("content.QuizItem", on_delete=models.CASCADE, related_name="+")
    answer = models.CharField(max_length=500, blank=True)
    is_correct = models.BooleanField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return f"{self.learner.user} · {self.quiz_item} · {'right' if self.is_correct else 'wrong'}"
