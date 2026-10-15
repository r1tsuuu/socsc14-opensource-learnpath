from django import forms


class QuizForm(forms.Form):
    """One text box per fill-in-the-blank item. Blank answers are allowed and count as wrong."""

    def __init__(self, *args, items, **kwargs):
        super().__init__(*args, **kwargs)
        self.items = items
        for item in items:
            self.fields[self.field_name(item)] = forms.CharField(
                label=item.prompt,
                required=False,
                max_length=500,
                widget=forms.TextInput(attrs={"autocomplete": "off", "spellcheck": "false"}),
            )

    @staticmethod
    def field_name(item):
        return f"item_{item.pk}"

    def item_fields(self):
        return [(item, self[self.field_name(item)]) for item in self.items]

    def answers(self):
        return {item.pk: self.cleaned_data[self.field_name(item)] for item in self.items}
