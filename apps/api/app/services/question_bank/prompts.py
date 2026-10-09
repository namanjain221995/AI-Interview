"""Prompt for AI question generation (``POST /question-bank/generate``)."""

from __future__ import annotations

from app.ai.prompts import PromptTemplate

GENERATION_SYSTEM = PromptTemplate(
    "question_generation",
    """You write interview questions for structured, fair, evidence-based technical interviews.

{{ FAIRNESS_RULES }}

Rules for every question:
- It assesses the competency given, at the difficulty given, for the role given; it is answerable in a
  spoken interview in two to four minutes.
- One clear, open-ended question per item (no multi-part lists, no yes/no questions, no trivia that can be
  looked up in seconds). Prefer realistic scenarios and experience-based prompts.
- Never ask about personal life, age, family, health, nationality, religion or any protected trait.
- expected_concepts: 3 to 8 concrete concepts a strong answer covers.
- rubric_excellent / rubric_weak: what an excellent and a weak answer look like, concretely.
- followups: 2 or 3 natural follow-up questions; common_errors: typical misconceptions.
- Vary topics within the competency and do not repeat or paraphrase the existing questions listed.

Reply with one JSON object with these fields:
{{ schema }}""",
)

GENERATION_USER = PromptTemplate(
    "question_generation_user",
    """Write exactly {{ count }} distinct question(s) in the "questions" array.
Role: {{ role }}
Competency: {{ competency.name }} (key "{{ competency.key }}", category {{ competency.category }})
{% if competency.description %}Description: {{ competency.description }}
{% endif %}{% if levels %}Proficiency anchors: {{ levels }}
{% endif %}Difficulty: {{ difficulty }}
{% if topic %}Focus topic: {{ topic }}
{% endif %}{% if kind %}Question kind: {{ kind }}
{% endif %}Use competency_key "{{ competency.key }}" and difficulty "{{ difficulty }}" for every question.
{% if existing %}
Existing questions (do not repeat):
{% for text in existing %}- {{ text }}
{% endfor %}{% endif %}""",
)


def generation_messages(
    *,
    schema: str,
    count: int,
    role: str,
    competency: dict[str, str | None],
    levels: str,
    difficulty: str,
    topic: str | None,
    kind: str | None,
    existing: list[str],
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": GENERATION_SYSTEM.render(schema=schema)},
        {
            "role": "user",
            "content": GENERATION_USER.render(
                count=count,
                role=role,
                competency=competency,
                levels=levels,
                difficulty=difficulty,
                topic=topic,
                kind=kind,
                existing=existing,
            ),
        },
    ]
