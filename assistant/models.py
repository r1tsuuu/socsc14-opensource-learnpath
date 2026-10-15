from django.db import models


class AIUsage(models.Model):
    """Messages a learner sent to the AI on one day. Never stores the question text."""

    learner = models.ForeignKey(
        "accounts.LearnerProfile", on_delete=models.CASCADE, related_name="ai_usage"
    )
    date = models.DateField()
    messages_used = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name_plural = "AI usage"
        constraints = [
            models.UniqueConstraint(fields=["learner", "date"], name="one_usage_row_per_day")
        ]

    def __str__(self):
        return f"{self.learner.user} · {self.date} · {self.messages_used}"
