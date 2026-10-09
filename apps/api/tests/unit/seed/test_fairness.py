"""Fairness lint: protected/prohibited topics are caught, technical vocabulary is not."""

from __future__ import annotations

import pytest

from app.seed.fairness import fairness_hits, lint_fields, strip_code

PROBING = [
    ("How old are you?", "age"),
    ("What is your age?", "age"),
    ("We prefer younger candidates for this team.", "age"),
    ("What year did you graduate?", "age"),
    ("What is your date of birth?", "date of birth"),
    ("When were you born?", "date of birth"),
    ("Are you married?", "marital or family status"),
    ("How do you balance work with your family commitments?", "marital or family status"),
    ("Does your spouse support the relocation?", "marital or family status"),
    ("Do you have any children?", "children"),
    ("Who looks after your kids during on-call weeks?", "children"),
    ("Are you pregnant or planning parental leave?", "pregnancy"),
    ("Do you have any health conditions we should know about?", "health"),
    ("How is your mental health?", "health"),
    ("Do you have a disability that affects your work?", "disability"),
    ("What religion do you practice?", "religion"),
    ("Which holidays do you observe?", "religion"),
    ("What is your race?", "race or ethnicity"),
    ("What is your ethnicity?", "race or ethnicity"),
    ("Where are you originally from?", "national origin"),
    ("What is your nationality?", "national origin"),
    ("Is English your mother tongue?", "national origin"),
    ("Are you a US citizen?", "citizenship or visa status"),
    ("Do you require visa sponsorship?", "citizenship or visa status"),
    ("What is your sexual orientation?", "sexual orientation"),
    ("What is your gender?", "gender identity"),
    ("How many women are on your team?", "gender identity"),
    ("What is your current salary?", "salary history"),
    ("What was your salary at your previous job?", "salary history"),
    ("How much do you currently make?", "salary history"),
    ("Describe your physical appearance.", "appearance"),
    ("Do you have any tattoos?", "appearance"),
    ("You have a strong accent; can customers understand you?", "accent"),
    ("Describe your personality.", "emotion or personality"),
    ("Are you an introvert or an extrovert?", "emotion or personality"),
    ("How do you control your emotions under pressure?", "emotion or personality"),
    ("Keep good eye contact while answering.", "emotion or personality"),
]

TECHNICAL = [
    "Explain how a race condition can corrupt a counter and how a data race differs.",
    "There is a race between the cache invalidation and the write; how do you close it?",
    "Design the health check endpoint and readiness probes for this service.",
    "How do you check your health endpoint latency?",
    "Each node has up to two children; return the number of children of the root.",
    "How does the children prop work in React?",
    "Your child process exits with status 1; how do you reap child processes?",
    "Set Cache-Control max-age and explain the Age header.",
    "What is the age of the oldest message in the queue?",
    "The cache entry is too old; evict it.",
    "Use the accent color from the theme and strip accent marks from names.",
    "Which EC2 instance family would you choose, and what about the font family?",
    "Functions are first-class citizens in JavaScript.",
    "Compute a 95% confidence interval for the conversion rate.",
    "What happens on a kernel panic, and how does panic/recover work in Go?",
    "Make the error messages user-friendly and cache-friendly.",
    "Implement the stable marriage problem (Gale-Shapley).",
    "Explain the birthday attack and the birthday paradox for hash collisions.",
    "Walk me through your mental model of the event loop.",
    "How did you migrate your physical servers to the cloud?",
    "Use eager loading to avoid N+1 queries; the pipeline is composed of stages.",
    "How do you secure executive sponsorship for a platform migration?",
    "Design a payments system that supports Visa and Mastercard.",
    "Describe the saga pattern and your compensation logic for failed steps.",
    "How do you initialize your weight matrix and choose your weight decay?",
    "Explain artificial intelligence versus business intelligence.",
    "Track the work status of every ticket.",
    "Which man pages document man-in-the-middle mitigations?",
    "Render a page with storage usage, image averages and package management.",
    "Give the second highest salary per department from the employee table.",
    "What are your salary expectations for this role?",
    "Use smart pointers and a smart retry policy.",
    "How do you stress test the API under peak load?",
    "Write tests that keep the suite fast and trustworthy.",
    "Explain JWT key rotation with the kid header.",
    "Describe the widows and orphans CSS properties.",
]


@pytest.mark.parametrize(("text", "topic"), PROBING, ids=[t for t, _ in PROBING])
def test_protected_topics_are_flagged(text: str, topic: str) -> None:
    assert topic in {hit.topic for hit in fairness_hits(text)}


@pytest.mark.parametrize("text", TECHNICAL)
def test_technical_vocabulary_is_not_flagged(text: str) -> None:
    assert [h.describe() for h in fairness_hits(text, evaluation=True)] == []


@pytest.mark.parametrize(
    "text",
    [
        "Answers confidently and calmly.",
        "Shows enthusiasm and passion for the role.",
        "Is honest about weaknesses.",
        "Has a positive attitude and a good demeanour.",
        "Would be a good culture fit.",
    ],
)
def test_demeanour_judgements_are_flagged_in_evaluator_text_only(text: str) -> None:
    assert fairness_hits(text, evaluation=True)
    assert fairness_hits(text, evaluation=False) == []


def test_hits_name_the_field_and_quote_the_original_text() -> None:
    hits = lint_fields(
        [("text", "What is your Nationality?", False), ("rubric.good", "Answers CONFIDENTLY.", True)]
    )
    assert [h.field for h in hits] == ["text", "rubric.good"]
    assert hits[0].match == "Nationality" and "What is your Nationality?" in hits[0].context
    assert (
        hits[1].describe().startswith("rubric.good: mentions emotion or personality judgement ('CONFIDENTLY'")
    )


def test_code_is_not_prose() -> None:
    markdown = "Count rows.\n\n```sql\nSELECT gender, age FROM people;\n```\n\nUse the `gender_id` column."
    assert fairness_hits(strip_code(markdown)) == []
    assert fairness_hits(markdown)
