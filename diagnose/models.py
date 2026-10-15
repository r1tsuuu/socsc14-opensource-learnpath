from django.db import models


class SkillCheckAttempt(models.Model):
    learner = models.ForeignKey(
        "accounts.LearnerProfile", on_delete=models.CASCADE, related_name="skill_check_attempts"
    )
    track = models.ForeignKey("content.Track", on_delete=models.CASCADE, related_name="+")
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.learner.user} · {self.track} · {self.started_at:%Y-%m-%d}"


class SkillAnswer(models.Model):
    attempt = models.ForeignKey(SkillCheckAttempt, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey("content.SkillQuestion", on_delete=models.CASCADE, related_name="+")
    chosen_index = models.PositiveIntegerField(null=True, blank=True, help_text="Blank means \"I don't know yet\".")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["attempt", "question"], name="one_answer_per_question")
        ]


class TopicMastery(models.Model):
    class Source(models.TextChoices):
        SKILL_CHECK = "skill_check", "Skill check"
        QUIZ = "quiz", "Quiz"
        MANUAL = "manual", "Manual"

    learner = models.ForeignKey(
        "accounts.LearnerProfile", on_delete=models.CASCADE, related_name="masteries"
    )
    topic = models.ForeignKey("content.Topic", on_delete=models.CASCADE, related_name="+")
    known = models.BooleanField(default=False)
    source = models.CharField(max_length=20, choices=Source.choices)

    class Meta:
        verbose_name_plural = "topic masteries"
        constraints = [
            models.UniqueConstraint(fields=["learner", "topic"], name="one_mastery_per_topic")
        ]

    def __str__(self):
        return f"{self.learner.user} · {self.topic} · {'known' if self.known else 'to learn'}"
