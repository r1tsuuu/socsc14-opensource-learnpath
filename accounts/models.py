from django.conf import settings
from django.db import models


class LearnerProfile(models.Model):
    """Created on first visit to `home`, so everything but the user starts blank."""

    class Experience(models.TextChoices):
        NONE = "none", "I've never coded"
        A_LITTLE = "a_little", "I've tried a little"
        COMFORTABLE = "comfortable", "I'm fairly comfortable"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="learner_profile"
    )
    experience = models.CharField(max_length=20, choices=Experience.choices, blank=True)
    hours_per_week = models.PositiveIntegerField(null=True, blank=True)
    goal_text = models.CharField(max_length=300, blank=True)
    track = models.ForeignKey(
        "content.Track", on_delete=models.SET_NULL, null=True, blank=True, related_name="learners"
    )
    email_reminders = models.BooleanField(default=True)
    onboarded = models.BooleanField(default=False)

    def __str__(self):
        return f"Profile of {self.user}"
