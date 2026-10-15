from django.contrib import admin

from .models import LearnerProfile


@admin.register(LearnerProfile)
class LearnerProfileAdmin(admin.ModelAdmin):
    list_display = ["user", "track", "experience", "hours_per_week", "onboarded", "email_reminders"]
    list_filter = ["track", "onboarded", "experience"]
    search_fields = ["user__email"]
