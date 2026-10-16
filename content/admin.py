from django.contrib import admin

from .forms import ProjectIdeaForm, QuizItemForm, SkillQuestionForm, TopicForm
from .models import Path, PathTrack, ProjectIdea, QuizItem, Resource, SetupStep, SkillQuestion, Tip, Topic, Track


class SetupStepInline(admin.StackedInline):
    model = SetupStep
    extra = 0


class ProjectIdeaInline(admin.StackedInline):
    model = ProjectIdea
    form = ProjectIdeaForm
    extra = 0


class TopicInline(admin.TabularInline):
    model = Topic
    fields = ["title", "order", "estimated_minutes"]
    show_change_link = True
    extra = 0


@admin.register(Track)
class TrackAdmin(admin.ModelAdmin):
    list_display = ["name", "order", "is_active"]
    list_editable = ["order", "is_active"]
    prepopulated_fields = {"slug": ["name"]}
    inlines = [TopicInline, SetupStepInline, ProjectIdeaInline]


class ResourceInline(admin.TabularInline):
    model = Resource
    fields = ["title", "url", "kind", "estimated_minutes", "order", "is_active", "last_checked"]
    readonly_fields = ["last_checked"]
    extra = 0


class TipInline(admin.TabularInline):
    model = Tip
    extra = 0


class SkillQuestionInline(admin.StackedInline):
    model = SkillQuestion
    form = SkillQuestionForm
    extra = 0


class QuizItemInline(admin.StackedInline):
    model = QuizItem
    form = QuizItemForm
    extra = 0


@admin.register(Topic)
class TopicAdmin(admin.ModelAdmin):
    form = TopicForm
    list_display = ["title", "track", "order", "estimated_minutes"]
    list_editable = ["order", "estimated_minutes"]
    list_filter = ["track"]
    search_fields = ["title", "summary"]
    prepopulated_fields = {"slug": ["title"]}
    filter_horizontal = ["prerequisites"]
    inlines = [ResourceInline, TipInline, SkillQuestionInline, QuizItemInline]


@admin.register(Resource)
class ResourceAdmin(admin.ModelAdmin):
    list_display = ["title", "topic", "kind", "is_active", "last_checked"]
    list_filter = ["kind", "is_active", "topic__track"]
    search_fields = ["title", "url"]


@admin.register(SkillQuestion)
class SkillQuestionAdmin(admin.ModelAdmin):
    form = SkillQuestionForm
    list_display = ["__str__", "topic", "order"]
    list_filter = ["topic__track"]


@admin.register(QuizItem)
class QuizItemAdmin(admin.ModelAdmin):
    form = QuizItemForm
    list_display = ["__str__", "topic"]
    list_filter = ["topic__track"]


@admin.register(SetupStep)
class SetupStepAdmin(admin.ModelAdmin):
    list_display = ["title", "track", "order"]
    list_filter = ["track"]


class PathTrackInline(admin.TabularInline):
    model = PathTrack
    extra = 0


@admin.register(Path)
class PathAdmin(admin.ModelAdmin):
    list_display = ["title", "tagline", "order", "is_active"]
    list_editable = ["order", "is_active"]
    prepopulated_fields = {"slug": ["title"]}
    inlines = [PathTrackInline]


@admin.register(ProjectIdea)
class ProjectIdeaAdmin(admin.ModelAdmin):
    form = ProjectIdeaForm
    list_display = ["title", "track", "order", "is_active"]
    list_filter = ["track", "is_active"]


admin.site.register(Tip)
admin.site.site_header = "LearnPath content admin"
admin.site.site_title = "LearnPath admin"
