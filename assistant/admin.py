from django.contrib import admin

from .models import AIUsage


@admin.register(AIUsage)
class AIUsageAdmin(admin.ModelAdmin):
    list_display = ["learner", "date", "messages_used"]
    list_filter = ["date"]
