"""Tailored plans and culminating projects.

The AI only chooses from the track's existing topics (unknown slugs are dropped)
and writes a one-line reason per topic plus a small project, adapted from one of
the track's curated project ideas when there are any. The rule-based planner still
adds prerequisites, orders topics and splits weeks. Without the AI, the plan holds
every topic the learner doesn't know and a template project (the first curated idea,
or a generic one).
"""

from assistant.service import ask_model_json
from content.models import ProjectIdea, Topic
from diagnose.models import TopicMastery

from .models import PlanProject
from .planner import build_plan

MAX_REASON = 300
MAX_MILESTONES = 6
MAX_CHECKS = 6

SYSTEM_PROMPT = """You plan a learning path for a beginner on LearnPath, a free app that sends
learners to existing free resources and checks their understanding.

Choose the topics this learner needs for their goal and project, ONLY from the topic list
given (use the exact slugs). Prefer fewer topics when the goal is small, but include what
the project needs. Then design one small culminating project (a few hours to a weekend)
that matches their goal and uses only the chosen topics.

Return only a JSON object:
{
  "topics": [{"slug": "exact slug from the list", "reason": "one short line tied to their goal"}],
  "project": {
    "title": "short title",
    "description": "2-3 sentences: what they build and why it fits their goal",
    "milestones": [{"topics": ["slugs this step uses"], "text": "one concrete step"}],
    "done_when": ["3-5 checks that show the project is finished"],
    "idea": 0
  }
}
Rules: 3-5 milestones that grow the project week by week, in the same order as the topics:
each milestone uses only topics that come at or before it in your topic list, starting
with a small working version and adding harder parts (like files) last. No links. No code solutions. Plain English
(or Taglish if the learner used Taglish)."""

IDEAS_PROMPT = """
Project ideas from our curators. Base the project on the one that best fits the learner's
goal and adapt it to them (their interests, their project idea, the chosen topics). Keep a
similar size. Set "idea" to its number. Only if none fits at all, set "idea" to 0.
{ideas}"""


def _known_ids(profile):
    return set(
        TopicMastery.objects.filter(learner=profile, topic__track=profile.track, known=True)
        .values_list("topic_id", flat=True)
    )


def _clean_text(value, limit):
    return " ".join(str(value or "").split())[:limit]


def active_ideas(track):
    return list(ProjectIdea.objects.filter(track=track, is_active=True))


def build_tailor_messages(profile, project_idea, candidates, ideas=()):
    catalog = "\n".join(
        f"- {t.slug}: {t.title} ({t.estimated_minutes} min). {t.summary}" for t in candidates
    )
    learner = (
        f"Track: {profile.track.name}\n"
        f"Experience: {profile.get_experience_display() or 'not given'}\n"
        f"Hours per week: {profile.hours_per_week or 'not given'}\n"
        f"Goal: {profile.goal_text or 'not given'}\n"
        f"Project idea: {project_idea or 'none, suggest one'}\n\n"
        f"Topics they still need to learn:\n{catalog}"
    )
    system = SYSTEM_PROMPT
    if ideas:
        listed = "\n".join(f"{i}. {idea.title}: {idea.brief}" for i, idea in enumerate(ideas, 1))
        system += IDEAS_PROMPT.format(ideas=listed)
    return [{"role": "system", "content": system}, {"role": "user", "content": learner}]


def parse_selection(data, candidates):
    """Keep only topics from the list. Returns (topic ids, reasons by id)."""
    by_slug = {t.slug: t for t in candidates}
    ids, reasons = [], {}
    for entry in data.get("topics") or []:
        if not isinstance(entry, dict):
            continue
        topic = by_slug.get(str(entry.get("slug", "")).strip())
        if topic and topic.pk not in reasons:
            ids.append(topic.pk)
            reasons[topic.pk] = _clean_text(entry.get("reason"), MAX_REASON)
    return ids, reasons


def plan_weeks_by_slug(plan):
    return {item.topic.slug: item.week for item in plan.items.select_related("topic")}


def parse_project(data, plan, ideas=()):
    """Validate the AI's project. Returns field values for PlanProject, or None if unusable."""
    project = data.get("project")
    if not isinstance(project, dict):
        return None
    title = _clean_text(project.get("title"), 200)
    description = str(project.get("description") or "").strip()[:2000]
    if not title or not description:
        return None

    weeks = plan_weeks_by_slug(plan)
    last_week = max(weeks.values(), default=1)
    milestones = []
    for step in (project.get("milestones") or [])[:MAX_MILESTONES]:
        if not isinstance(step, dict) or not str(step.get("text") or "").strip():
            continue
        step_weeks = [weeks[s] for s in step.get("topics") or [] if isinstance(s, str) and s in weeks]
        milestones.append({
            "week": max(step_weeks) if step_weeks else last_week,
            "text": _clean_text(step.get("text"), 300),
        })
    # Keep the AI's step order, but never let a later step fall in an earlier week.
    for previous, current in zip(milestones, milestones[1:]):
        current["week"] = max(current["week"], previous["week"])
    idea = None
    try:
        number = int(project.get("idea") or 0)
    except (TypeError, ValueError):
        number = 0
    if 1 <= number <= len(ideas):
        idea = ideas[number - 1]
    done_when = [_clean_text(c, 200) for c in (project.get("done_when") or [])[:MAX_CHECKS] if str(c).strip()]
    if not done_when and idea is not None:
        done_when = list(idea.done_when)
    if not milestones or not done_when:
        return None
    return {"title": title, "description": description, "milestones": milestones,
            "done_when": done_when, "idea": idea}


def template_project(plan, profile):
    """A project for when the AI is off or its answer was unusable.

    Uses the track's first curated idea when there is one, otherwise a generic project.
    """
    goal = f" toward your goal: {profile.goal_text}" if profile.goal_text else ""
    by_week = {}
    for item in plan.items.select_related("topic").order_by("order"):
        by_week.setdefault(item.week, []).append(item.topic.title)
    milestones = [
        {"week": week, "text": f"Add something to your project that uses {', '.join(titles)}."}
        for week, titles in sorted(by_week.items())
    ]
    ideas = active_ideas(plan.track)
    if ideas:
        idea = ideas[0]
        return {
            "title": idea.title,
            "description": idea.brief,
            "milestones": milestones,
            "done_when": list(idea.done_when) or ["It runs without errors.", "You can explain each part to a friend."],
            "idea": idea,
        }
    return {
        "title": f"Your {plan.track.name} mini project",
        "description": (
            f"Build one small {plan.track.name} project{goal}. Grow it a little each week "
            "using what you just learned, so by the end you have something you made yourself."
        ),
        "milestones": milestones,
        "done_when": [
            "It runs without errors.",
            "It uses something from every week of your plan.",
            "You can explain each part to a friend.",
        ],
        "idea": None,
    }


def save_project(plan, fields, source):
    PlanProject.objects.update_or_create(plan=plan, defaults={**fields, "source": source})


def build_plan_with_template(profile):
    """The form path: every topic the learner doesn't know, plus a template project."""
    plan = build_plan(profile)
    save_project(plan, template_project(plan, profile), PlanProject.Source.TEMPLATE)
    return plan


def tailor_plan(profile, project_idea=""):
    """Ask the AI to pick topics and design a project; fall back to the template plan.

    Returns (plan, used_ai).
    """
    known = _known_ids(profile)
    candidates = list(
        Topic.objects.filter(track=profile.track).exclude(pk__in=known).order_by("order", "title")
    )
    if not candidates:
        return build_plan_with_template(profile), False

    ideas = active_ideas(profile.track)
    data = ask_model_json(build_tailor_messages(profile, project_idea, candidates, ideas), max_tokens=2500)
    if data is None:
        return build_plan_with_template(profile), False

    ids, reasons = parse_selection(data, candidates)
    plan = build_plan(profile, selected_topic_ids=ids or None, reasons=reasons)
    fields = parse_project(data, plan, ideas)
    if fields is None:
        save_project(plan, template_project(plan, profile), PlanProject.Source.TEMPLATE)
        return plan, bool(ids)
    save_project(plan, fields, PlanProject.Source.AI)
    return plan, True
