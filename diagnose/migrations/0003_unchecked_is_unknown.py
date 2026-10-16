"""Older checks saved every unchecked topic as "not known". Under the evidence rules a
topic is only a skill to work on if the learner actually answered a question on it and
got it wrong; otherwise there is no evidence yet (unknown)."""

from django.db import migrations


def fix(apps, schema_editor):
    TopicMastery = apps.get_model("diagnose", "TopicMastery")
    SkillAnswer = apps.get_model("diagnose", "SkillAnswer")
    for mastery in TopicMastery.objects.filter(status="not_yet", source="skill_check"):
        answered = SkillAnswer.objects.filter(
            attempt__learner=mastery.learner_id,
            question__topic=mastery.topic_id,
            chosen_index__isnull=False,
        ).exists()
        if not answered:
            mastery.status = "unknown"
            mastery.evidence = ""
            mastery.save(update_fields=["status", "evidence"])


class Migration(migrations.Migration):
    dependencies = [("diagnose", "0002_mastery_status_evidence")]

    operations = [migrations.RunPython(fix, migrations.RunPython.noop)]
