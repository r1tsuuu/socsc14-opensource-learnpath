from django import forms

from accounts.models import LearnerProfile
from content.models import Track

DONT_KNOW = "dont_know"


class AboutYouForm(forms.ModelForm):
    track = forms.ModelChoiceField(
        queryset=Track.objects.filter(is_active=True),
        empty_label=None,
        widget=forms.RadioSelect,
        label="What do you want to learn?",
    )
    experience = forms.ChoiceField(
        choices=LearnerProfile.Experience.choices,
        widget=forms.RadioSelect,
        label="How much have you coded before?",
    )
    hours_per_week = forms.IntegerField(
        min_value=1, max_value=40, label="How many hours a week can you study?"
    )
    goal_text = forms.CharField(
        required=False, max_length=300, label="What's your goal? (optional)",
        widget=forms.TextInput(attrs={"placeholder": "e.g. pass CS 11, build a small website"}),
    )

    class Meta:
        model = LearnerProfile
        fields = ["track", "experience", "hours_per_week", "goal_text"]


class SkillCheckForm(forms.Form):
    """One required radio group per question, plus "I don't know yet"."""

    def __init__(self, *args, questions, **kwargs):
        super().__init__(*args, **kwargs)
        self.questions = questions
        for question in questions:
            choices = [(str(i), text) for i, text in enumerate(question.choices)]
            choices.append((DONT_KNOW, "I don't know yet"))
            self.fields[self.field_name(question)] = forms.ChoiceField(
                label=question.prompt,
                choices=choices,
                widget=forms.RadioSelect,
                error_messages={"required": "Pick an answer, or \"I don't know yet\"."},
            )

    @staticmethod
    def field_name(question):
        return f"question_{question.pk}"

    def question_fields(self):
        return [(q, self[self.field_name(q)]) for q in self.questions]

    def answers(self):
        """Map question id to chosen index, or None for "I don't know yet"."""
        result = {}
        for question in self.questions:
            value = self.cleaned_data[self.field_name(question)]
            result[question.pk] = None if value == DONT_KNOW else int(value)
        return result
