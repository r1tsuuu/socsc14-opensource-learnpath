from django.contrib import admin

from .models import LearningPlan, PlanItem


class PlanItemInline(admin.TabularInline):
    model = PlanItem
    extra = 0
    fields = ["order", "week", "topic", "status", "completed_at"]


@admin.register(LearningPlan)
class LearningPlanAdmin(admin.ModelAdmin):
    list_display = ["learner", "track", "created_at", "is_active"]
    list_filter = ["track", "is_active"]
    inlines = [PlanItemInline]
