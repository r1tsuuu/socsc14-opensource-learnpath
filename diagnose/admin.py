from django.contrib import admin

from .models import SkillAnswer, SkillCheckAttempt, TopicMastery


class SkillAnswerInline(admin.TabularInline):
    model = SkillAnswer
    extra = 0
    readonly_fields = ["question", "chosen_index"]
    can_delete = False


@admin.register(SkillCheckAttempt)
class SkillCheckAttemptAdmin(admin.ModelAdmin):
    list_display = ["learner", "track", "started_at", "finished_at"]
    list_filter = ["track"]
    inlines = [SkillAnswerInline]


@admin.register(TopicMastery)
class TopicMasteryAdmin(admin.ModelAdmin):
    list_display = ["learner", "topic", "known", "source"]
    list_filter = ["known", "source", "topic__track"]
