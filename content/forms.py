"""Admin forms that let the content team edit JSON lists as one item per line."""

from django import forms

from .models import QuizItem, SkillQuestion, Topic


class LinesField(forms.CharField):
    """A textarea where each non-empty line becomes one item of a list."""

    widget = forms.Textarea(attrs={"rows": 4})

    def prepare_value(self, value):
        if isinstance(value, list):
            return "\n".join(value)
        return value

    def to_python(self, value):
        text = super().to_python(value)
        return [line.strip() for line in text.splitlines() if line.strip()]


class SkillQuestionForm(forms.ModelForm):
    choices = LinesField(help_text="One choice per line. Don't add \"I don't know yet\".")

    class Meta:
        model = SkillQuestion
        fields = "__all__"


class QuizItemForm(forms.ModelForm):
    accepted_answers = LinesField(help_text="One accepted answer per line.")

    class Meta:
        model = QuizItem
        fields = "__all__"


class TopicForm(forms.ModelForm):
    class Meta:
        model = Topic
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        track = cleaned.get("track")
        prerequisites = cleaned.get("prerequisites") or []
        if self.instance.pk and any(p.pk == self.instance.pk for p in prerequisites):
            self.add_error("prerequisites", "A topic can't be its own prerequisite.")
        if track and any(p.track_id != track.pk for p in prerequisites):
            self.add_error("prerequisites", "Prerequisites must be in the same track.")
        return cleaned
