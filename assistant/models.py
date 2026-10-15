from django.db import models


class AIUsage(models.Model):
    """A learner's AI use on one day. Never stores question or chat text."""

    learner = models.ForeignKey(
        "accounts.LearnerProfile", on_delete=models.CASCADE, related_name="ai_usage"
    )
    date = models.DateField()
    messages_used = models.PositiveIntegerField(default=0)
    intakes_used = models.PositiveIntegerField(
        default=0, help_text="Plan conversations started (the AI intake on /start/)."
    )

    class Meta:
        verbose_name_plural = "AI usage"
        constraints = [
            models.UniqueConstraint(fields=["learner", "date"], name="one_usage_row_per_day")
        ]

    def __str__(self):
        return f"{self.learner.user} · {self.date} · {self.messages_used}"
