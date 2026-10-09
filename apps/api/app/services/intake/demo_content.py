"""Deterministic content of the intake demo interview (used by ``demo.py``, the intake tests and the staff UI work).

A fictional candidate, Alex Rivera (data engineer, three years of hands-on experience, Python 7/10), goes through all
seven stages: passage B (Gradient Descent) is read aloud, passage C (Overfitting) is relayed, topic card 2 (time
management) is spoken. Every answer is written so the canned evaluator outputs in :func:`demo_evaluator_outputs` cite
verbatim quotes, which lets the pipeline run without any model.

Nothing here is real data; nothing here touches the database.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

CANDIDATE_NAME = "Alex Rivera"
INTERVIEWER_NAME = "Sam"
PROGRAM_NAME = "AI/ML Training Program"
PASSAGE_DRILL_A = "B"
PASSAGE_DRILL_B = "C"
TOPIC_CARD = "2"
ELIGIBILITY_COUNTRY = "the United States"

ONBOARDING: dict[str, Any] = {
    "version": 1,
    "full_name": CANDIDATE_NAME,
    "target_role": "ai_ml",
    "target_role_other": None,
    "open_to_ai_ml": "yes",
    "technical_background": (
        "Data engineer at a logistics company for three years: Python and SQL pipelines in Airflow, data quality "
        "checks and reporting tables. Evening courses in machine learning over the last year."
    ),
    "primary_language": "python",
    "primary_language_other": None,
    "self_rating": 7,
    "scripting": "regular",
    "scripting_details": "Daily Python and SQL for ETL pipelines",
    "data_ml_libraries": "pandas, scikit-learn (courses)",
    "years_hands_on": 3.0,
    "experience_level_comfort": "yes",
    "training_commitment": "full",
    "weekly_hours": 40,
    "availability": "within_1_month",
    "available_from": None,
    "accuracy_confirmed": True,
}

ELIGIBILITY: dict[str, Any] = {
    "version": 1,
    "country": ELIGIBILITY_COUNTRY,
    "authorized": True,
    "needs_sponsorship": False,
}

CLAIMS: list[dict[str, Any]] = [
    {
        "ref": "C1",
        "text": "Built and scheduled ETL pipelines in Python and Apache Airflow at Northwind Logistics",
        "probe": "tool_or_framework",
        "source": "resume",
        "source_quote": "Built and scheduled 6 ETL pipelines (Python, Apache Airflow) feeding the reporting database",
        "profile_claim_id": None,
    },
    {
        "ref": "C2",
        "text": "Reduced reporting database query times by 40%",
        "probe": "metric_improvement",
        "source": "resume",
        "source_quote": "Reduced reporting database query times by 40%",
        "profile_claim_id": None,
    },
]

DRILL_A_TRANSCRIPT = (
    "Gradient descent is an optimization algorithm used to train machine learning models. The goal is to minimize a "
    "cost function, which measures how wrong the model's predictions are. Imagine standing on a foggy hill and "
    "wanting to reach the lowest point. You can't see far, so you feel the slope beneath your feet and take a step "
    "in the the steepest downhill direction, and you repeat this until the ground is flat. In the same way, gradient "
    "descent measures the slope of the error and adjusts the model's parameters step by step. The size of each step "
    "is called the learning rate: too large and you overshoot the valley, too small and training takes forever."
)

RELAY_TRANSCRIPT = (
    "So the passage was about overfitting. Overfitting is when a machine learning model learns the training data too "
    "well, it memorizes the noise and the random details instead of the real pattern. So it does really well on the "
    "data it has already seen but it fails on new data. They compared it to a student who memorizes the answers from "
    "last year's exam without understanding the concepts. And to prevent it you can get more training data or make "
    "the model simpler. There was one more technique but I don't remember what it was called."
)

TOPIC_TRANSCRIPT = (
    "Okay, so time management. For me good time management means knowing what your priorities are for the day and "
    "actually spending your best hours on them, not just being busy. One common mistake people make is saying yes to "
    "everything, so their day gets filled with small requests and the important work gets pushed to the evening. A "
    "technique that helps me is time blocking. In the morning I block two hours in my calendar for focused work, and "
    "I turn off notifications during that block. For an example from my own experience, last quarter we had a "
    "migration deadline and I was also getting a lot of ad hoc data requests. I started grouping the requests into "
    "one slot in the afternoon and protecting the mornings for the migration, and we delivered it on time. So I "
    "think the main thing is being clear about priorities and protecting time for them."
)

OPENING = (
    f"Hi Alex, I'm {INTERVIEWER_NAME}. Thanks for joining this intake interview for the {PROGRAM_NAME}. It takes "
    "about 45 minutes and has seven short parts: an introduction, your experience, a few standard questions, a "
    "reading exercise, a short speaking exercise, how you learn, and your questions. If you don't know something, "
    "it's fine to say so."
)


@dataclass(frozen=True, slots=True)
class Exchange:
    stage: str
    item_key: str
    item_kind: str
    question: str
    answer: str | None
    answer_seconds: float = 0.0
    mandatory: bool = True
    template: bool = True
    meta: dict[str, Any] = field(default_factory=dict)
    activity: str | None = None  # reading_aloud | read_relay | topic_card


EXCHANGES: tuple[Exchange, ...] = (
    # ---------------------------------------------------------------- stage 1 greeting
    Exchange(
        "greeting",
        "greeting.intro",
        "prompt",
        OPENING + " To start, please introduce yourself in about 90 seconds: who you are and what you do.",
        "Sure. My name is Alex and I've been working as a data engineer for about three years at a logistics company "
        "called Northwind Logistics. Most of my day is building and maintaining data pipelines in Python and SQL, so I "
        "pull data from our warehouse management system, clean it and load it into our reporting database. Before "
        "that I did a bootcamp in data analytics and I studied mechanical engineering at university. Over the last "
        "year I've been doing online courses in machine learning in the evenings, because I want to move from moving "
        "data around to actually building models with it. That's why I applied to this program.",
        62.0,
        meta={"answer_mode": "long", "soft_limit_s": 90},
    ),
    Exchange(
        "greeting",
        "greeting.project",
        "prompt",
        "Now walk me through the one project or role you're most proud of, and why.",
        "The project I'm most proud of is the delivery delay report. Our operations team used to build a daily report "
        "by hand in Excel, it took them about two hours every morning. I built a pipeline in Python with Airflow that "
        "pulls the shipment events every night, joins them with the carrier data and flags deliveries that are likely "
        "to be late. I wrote the transformation logic, the data quality checks and the scheduling. Now the report is "
        "ready at six in the morning and the team uses that time to call customers instead. I'm proud of it because "
        "it was the first thing I built end to end, and people actually use it every day.",
        58.0,
        meta={"answer_mode": "long"},
    ),
    Exchange(
        "greeting",
        "greeting.followup",
        "followup",
        "What was the hardest technical problem in that pipeline, and how did you solve it?",
        "The hardest part was duplicate shipment events. The carrier API sometimes sent the same event twice with "
        "slightly different timestamps, so our late counts were too high. I added a deduplication step that groups "
        "events by shipment ID and event type and keeps the first one within a ten minute window, and I wrote a test "
        "with real examples from the API. After that the numbers matched what the operations team saw manually.",
        38.0,
        mandatory=False,
        template=False,
        meta={"move": "follow_up", "followup_index": 1},
    ),
    # ---------------------------------------------------------------- stage 2 résumé
    Exchange(
        "resume",
        "resume.C1.ownership",
        "ladder",
        "Thanks. Next, a few questions about your experience. Your résumé says you built and scheduled ETL pipelines "
        "in Python and Airflow at Northwind Logistics. What did you personally build there, versus the team?",
        "I personally built four of the six pipelines we run in Airflow, including the delivery delay one and the "
        "carrier invoices pipeline. The Airflow server itself was set up by our platform team, so I didn't install or "
        "manage Airflow, but I wrote the DAGs, the Python operators and the SQL transformations, and I'm on call for "
        "those pipelines.",
        30.0,
        template=False,
        meta={"claim_ref": "C1", "rung": "ownership", "answer_mode": "long", "move": "ask_item"},
    ),
    Exchange(
        "resume",
        "resume.C1.mechanics",
        "ladder",
        "Walk me through how one of those DAGs actually works, from the trigger to the final table.",
        "Take the invoices one. It's scheduled at two a.m. The first task uses a sensor to wait until the carrier drops "
        "the invoice files into our S3 bucket. Then a Python task reads the CSV files with pandas, validates the "
        "columns and the totals, and writes them to a staging table. After that a SQL task merges staging into the "
        "invoices table using the invoice number as the key, so reruns don't create duplicates. The last task runs a "
        "few data quality checks, like row counts and negative amounts, and sends a Slack message if something fails.",
        52.0,
        template=False,
        meta={"claim_ref": "C1", "rung": "mechanics", "answer_mode": "long", "move": "ask_item"},
    ),
    Exchange(
        "resume",
        "resume.C1.stress",
        "ladder",
        "What would break if the invoice volume doubled, or if you had to redo that pipeline today?",
        "If the volume doubled, the pandas step would probably be the first problem because it loads the whole file "
        "in memory, so I would read it in chunks or move the transformation into SQL. The merge should be fine "
        "because it's indexed. If I redid it today, I would add proper retries with alerting earlier, because at the "
        "start we only found failures when someone complained.",
        34.0,
        template=False,
        meta={"claim_ref": "C1", "rung": "stress", "move": "ask_item"},
    ),
    Exchange(
        "resume",
        "resume.C2.ownership",
        "ladder",
        "You also mention reducing query times on the reporting database by 40 percent. What was your part in that?",
        "That one was more of a team effort. We had a performance push last year where we went through the slowest "
        "dashboards. I was part of that group, and I helped find the slow queries from the logs.",
        17.0,
        template=False,
        meta={"claim_ref": "C2", "rung": "ownership", "answer_mode": "long", "move": "ask_item"},
    ),
    Exchange(
        "resume",
        "resume.C2.mechanics",
        "ladder",
        "How did you measure the 40 percent, and what was the baseline?",
        "We compared the average dashboard load times before and after, I think from the monitoring tool. To be "
        "honest I don't remember exactly how the baseline was calculated, my colleague set up the measurement. We "
        "added indexes and rewrote a couple of the heavy queries.",
        24.0,
        template=False,
        meta={"claim_ref": "C2", "rung": "mechanics", "answer_mode": "long", "move": "ask_item"},
    ),
    # ---------------------------------------------------------------- stage 3 mandatory
    Exchange(
        "mandatory",
        "mandatory.mq2",
        "mq",
        "Thanks. Next, a few standard questions. On the form you said you're aiming for the AI/ML track. What draws "
        "you to it, and are you open to related roles?",
        "Mainly I like the idea of using data to predict things instead of only reporting what happened. In my job I "
        "already see a lot of questions like which shipments will be late, and I'd love to build models for that. "
        "I'm open to data analyst or ML engineering roles, but AI/ML is my first choice.",
        26.0,
        meta={"mq": "mq2"},
    ),
    Exchange(
        "mandatory",
        "mandatory.mq3",
        "mq",
        "You told us Python is your main language, that you script regularly, and you rated yourself 7 out of 10. Is "
        "that still right?",
        "Yes, that's right. Python every day, plus SQL. Seven feels fair, I'm comfortable with the standard library "
        "and pandas but I'm still learning things like packaging and testing properly.",
        16.0,
        meta={"mq": "mq3", "language": "python"},
    ),
    Exchange(
        "mandatory",
        "mandatory.mq3.verification",
        "verification",
        "Difference between a list and a dictionary, and when you'd use each?",
        "A list is an ordered collection, you access items by their position, like index zero, one, two. A dictionary "
        "stores key value pairs, so you look things up by a key, and the lookup is fast because it's a hash table. I'd "
        "use a list when the order matters or I just need to loop over items, and a dictionary when I need to find "
        "something by an ID, like shipments by shipment number.",
        33.0,
        meta={"mq": "mq3", "language": "python"},
    ),
    Exchange(
        "mandatory",
        "mandatory.mq3.rating_probe",
        "mq",
        "You said 7/10 — what would someone at 9/10 know or do that you don't yet?",
        "They would write cleaner, tested code, understand things like generators and async properly, and know how to "
        "profile and optimize code. I usually make things work first and I'm still learning to structure bigger "
        "projects well.",
        20.0,
        meta={"mq": "mq3"},
    ),
    Exchange(
        "mandatory",
        "mandatory.mq4",
        "mq",
        "You mentioned about 3 years of hands-on experience. This program prepares people for roles that usually expect "
        "around five years of hands-on experience. Are you comfortable with the responsibilities and skills expected at "
        "that level, and can you commit to the training needed?",
        "Yes. I know I'm at three years, not five, so I expect the training to be intense. I'm comfortable with that, "
        "and I can commit full time. I've already arranged to finish my current job at the end of the month.",
        19.0,
        meta={"mq": "mq4"},
    ),
    Exchange(
        "mandatory",
        "mandatory.mq4.followup",
        "followup",
        "Where's the gap between your hands-on experience and that level, and how will you close it?",
        "The biggest gap is modelling itself. I've done courses but I haven't deployed a model at work. I also need "
        "more depth in statistics. I plan to close it by doing projects on real data every week and asking for code "
        "reviews, which is how I learned data engineering.",
        23.0,
        mandatory=False,
        meta={"mq": "mq4"},
    ),
    # ---------------------------------------------------------------- stage 4 reading
    Exchange(
        "reading",
        "reading.drill_a",
        "activity",
        "Thanks. Next is a reading exercise. A short technical passage will appear on your screen. Please read it aloud "
        "at a natural pace, then press Finish reading.",
        DRILL_A_TRANSCRIPT,
        46.0,
        activity="reading_aloud",
    ),
    Exchange(
        "reading",
        "reading.drill_b",
        "activity",
        "Now another passage. You'll have 30 seconds to read it silently. Then it will disappear and I'll ask you to "
        "explain it in your own words.",
        RELAY_TRANSCRIPT,
        41.0,
        activity="read_relay",
    ),
    Exchange(
        "reading",
        "reading.followup.least_sure",
        "followup",
        "What part were you least sure about?",
        "The last part about the techniques to prevent overfitting. I remember more data and a simpler model, but "
        "there was a third one I couldn't remember, so I wasn't sure if I explained the defences fully.",
        16.0,
    ),
    Exchange(
        "reading",
        "reading.followup.non_technical",
        "followup",
        "Now explain the same idea to a non-technical person in two sentences.",
        "Overfitting is when a computer program memorizes its practice examples so closely that it gets confused by "
        "anything new. It's like a student who memorized last year's test answers and then can't handle different "
        "questions on the real exam.",
        17.0,
    ),
    Exchange(
        "reading",
        "reading.followup.interviewer_question",
        "followup",
        "What might an interviewer ask about this, and how would you answer?",
        "They might ask how you can tell a model is overfitting. I'd say you compare the performance on the training "
        "data with a separate validation set, and if the training accuracy is much higher than the validation "
        "accuracy, it's probably overfitting.",
        18.0,
    ),
    # ---------------------------------------------------------------- stage 5 topic
    Exchange(
        "topic",
        "topic.card",
        "activity",
        "Thanks. Now a short speaking exercise. A topic card with four points will appear. You'll have 60 seconds to "
        "prepare, then about two minutes to speak, connecting all four points.",
        TOPIC_TRANSCRIPT,
        88.0,
        activity="topic_card",
    ),
    Exchange(
        "topic",
        "topic.weakest",
        "followup",
        "Of the four points, which one do you think you developed least, and what would you add?",
        "Probably the common mistake. I only gave one example. I'd add that people also underestimate how long tasks "
        "take, so they plan too much in one day, and then everything feels late.",
        15.0,
        template=False,
        meta={"move": "ask_item"},
    ),
    # ---------------------------------------------------------------- stage 6 learning
    Exchange(
        "learning",
        "learning.three_weeks",
        "prompt",
        "Thanks. Next, a couple of questions about how you learn. Imagine you had three weeks to learn the basics of "
        "AI/ML for logistics data. How would you approach it?",
        "In the first week I'd refresh the fundamentals: linear and logistic regression, train test splits and "
        "evaluation metrics, with a short course and the scikit-learn documentation. In the second week I'd pick one "
        "real problem from my work, like predicting late deliveries, and build a simple model end to end, starting "
        "with a baseline. In the third week I'd improve it, try a tree-based model, look at which features matter, "
        "and write up what worked. Every few days I'd ask someone more experienced to review my notebook, because I "
        "learn fastest from feedback.",
        49.0,
        template=False,
        meta={"answer_mode": "long", "move": "ask_item"},
    ),
    Exchange(
        "learning",
        "learning.teach",
        "prompt",
        "Teach me something technical you learned recently — anything — in two minutes.",
        "Sure, I recently learned about data leakage. Data leakage is when information from the future, or from the "
        "answer you're trying to predict, sneaks into the features you train on. For example, if you predict whether "
        "a delivery will be late and one of your features is the actual delivery date, the model looks amazing in "
        "testing but it's useless in real life, because you don't know the delivery date in advance. The way to "
        "avoid it is to ask, for every feature, would I really know this at the moment I make the prediction. You "
        "also split your data by time, so you train on the past and test on later data.",
        57.0,
        meta={"answer_mode": "long"},
    ),
    Exchange(
        "learning",
        "learning.followup",
        "followup",
        "How would you check that you really understood a new concept like that, rather than just reading about it?",
        "I try to explain it to someone else or write a short note without looking at the source. Then I apply it to "
        "a small dataset. With leakage, I actually reproduced it on purpose with an old project, and saw the accuracy "
        "drop when I removed the leaky feature.",
        22.0,
        mandatory=False,
        template=False,
        meta={"move": "follow_up", "followup_index": 1},
    ),
    # ---------------------------------------------------------------- stage 7 close
    Exchange(
        "close",
        "close.questions",
        "prompt",
        "That's the end of my questions. What questions do you have for me about the program?",
        "What does a typical week in the training look like, and how much of it is hands-on project work?",
        8.0,
    ),
    Exchange(
        "close",
        "close.answer",
        "answer_question",
        "Each week combines guided study with a hands-on project and a review with a mentor. The hiring team can give "
        "you the detailed schedule. Is there anything else you'd like to ask?",
        "No, that's all. Thank you.",
        3.0,
        mandatory=False,
        template=False,
        meta={"move": "answer_question"},
    ),
    Exchange(
        "close",
        "close.closing",
        "closing",
        "Thank you, Alex. That's everything from my side. The hiring team will review the interview and contact you "
        "about next steps.",
        None,
    ),
)

STAGE_NUMBERS = {
    "greeting": 1,
    "resume": 2,
    "mandatory": 3,
    "reading": 4,
    "topic": 5,
    "learning": 6,
    "close": 7,
}
STAGE_BUDGETS = {
    "greeting": 314,
    "resume": 566,
    "mandatory": 251,
    "reading": 471,
    "topic": 471,
    "learning": 314,
    "close": 251,
}
#: Stage items that did not produce a question row (prefilled / designed skips), appended to the stage results.
EXTRA_ITEMS: dict[str, list[dict[str, Any]]] = {
    "resume": [
        {"item_key": "resume.C2.stress", "mandatory": True, "compressible": True, "status": "skipped",
         "skip_reason": "not_hands_on"},
    ],
    "mandatory": [
        {"item_key": "mandatory.mq1", "mandatory": True, "compressible": False, "status": "prefilled",
         "skip_reason": "prefilled"},
        {"item_key": "mandatory.mq2.pm", "mandatory": False, "compressible": False, "status": "not_applicable",
         "skip_reason": None},
    ],
}  # fmt: skip

INCOMPLETE_LAST_STAGE = "reading"  # the incomplete variant ends after the reading stage (time_up)


@dataclass(slots=True)
class DemoRows:
    """Row-like objects (attribute access, like ORM rows) of one demo session."""

    session: SimpleNamespace
    questions: list[SimpleNamespace]
    answers: list[SimpleNamespace]
    transcripts: list[SimpleNamespace]
    activities: list[SimpleNamespace]
    stages: list[SimpleNamespace]
    onboarding: dict[str, Any]
    eligibility: dict[str, Any] | None


def _words(text: str) -> int:
    return len(text.split())


def _segments(text: str, start_ms: int, duration_ms: int, parts: int = 5) -> list[dict[str, Any]]:
    words = text.split()
    size = max(1, len(words) // parts)
    chunks = [" ".join(words[i : i + size]) for i in range(0, len(words), size)]
    step = duration_ms // max(1, len(chunks))
    out = []
    for n, chunk in enumerate(chunks):
        seg_start = start_ms + n * step
        seg_end = seg_start + int(step * 0.9)
        out.append(
            {
                "start_ms": seg_start,
                "end_ms": seg_end,
                "speech_ms": int(step * 0.85),
                "vad_prob": 0.93,
                "no_speech_prob": 0.02,
                "text": chunk,
            }
        )
    return out


def _pauses(segments: list[dict[str, Any]], long_pause_after: int | None = None) -> list[dict[str, Any]]:
    pauses = []
    for n in range(len(segments) - 1):
        start = segments[n]["end_ms"]
        end = segments[n + 1]["start_ms"]
        if n == long_pause_after:
            end = start + 2100
        if end - start >= 300:
            pauses.append({"start_ms": start, "end_ms": end, "duration_ms": end - start})
    return pauses


def build_demo_rows(
    *,
    session_id: uuid.UUID | None = None,
    organization_id: uuid.UUID | None = None,
    intake_id: uuid.UUID | None = None,
    interview_id: uuid.UUID | None = None,
    candidate_id: uuid.UUID | None = None,
    started_at: datetime | None = None,
    variant: str = "complete",
    with_eligibility: bool = True,
) -> DemoRows:
    """Rows of the demo session (``variant``: ``complete`` or ``incomplete`` — ends after the reading stage)."""
    session_id = session_id or uuid.uuid4()
    organization_id = organization_id or uuid.uuid4()
    intake_id = intake_id or uuid.uuid4()
    started_at = started_at or datetime(2026, 10, 8, 14, 0, tzinfo=UTC)
    stage_order = list(STAGE_NUMBERS)
    cut = stage_order.index(INCOMPLETE_LAST_STAGE) if variant == "incomplete" else len(stage_order) - 1
    included = [ex for ex in EXCHANGES if stage_order.index(ex.stage) <= cut]
    questions: list[SimpleNamespace] = []
    answers: list[SimpleNamespace] = []
    transcripts: list[SimpleNamespace] = []
    activities: list[SimpleNamespace] = []
    stage_items: dict[str, list[dict[str, Any]]] = {key: [] for key in stage_order}
    stage_times: dict[str, list[float]] = {}
    clock_ms = 4000
    answer_seq = 0
    transcript_seq = 0
    for q_seq, ex in enumerate(included, start=1):
        question_id = uuid.uuid5(session_id, f"q:{ex.item_key}")
        activity_id = uuid.uuid5(session_id, f"activity:{ex.activity}") if ex.activity else None
        meta: dict[str, Any] = {
            "stage": ex.stage,
            "item_key": ex.item_key,
            "item_kind": ex.item_kind,
            "mandatory": ex.mandatory,
            "template": ex.template,
            "claim_ref": None,
            "rung": None,
            "mq": None,
            "language": None,
            "activity_id": str(activity_id) if activity_id else None,
            "followup_index": 0,
            "answer_mode": "standard",
            "soft_limit_s": None,
            "move": None,
            "deviations": [],
        }
        meta.update(ex.meta)
        asked_ms = clock_ms
        speech_ms = int(_words(ex.question) / 2.6 * 1000)
        questions.append(
            SimpleNamespace(
                id=question_id,
                session_id=session_id,
                sequence=q_seq,
                segment=f"intake:{ex.stage}",
                text=ex.question,
                spoken_text=ex.question,
                kind="intro"
                if ex.stage == "greeting"
                else "followup"
                if ex.item_kind == "followup"
                else "technical",
                action="ask_question",
                source="generated" if not ex.template else "curated",
                topic=ex.item_key,
                competency_keys=[],
                difficulty="medium",
                decision={"intake": meta},
                elapsed_seconds=asked_ms / 1000.0,
                asked_at=started_at + timedelta(milliseconds=asked_ms),
                claim_id=meta.get("claim_ref"),
                interrupted=False,
                question_focus=None,
                reason=None,
            )
        )
        transcript_seq += 1
        transcripts.append(
            SimpleNamespace(
                session_id=session_id,
                seq=transcript_seq,
                speaker="interviewer",
                kind="final",
                text=ex.question,
                start_ms=asked_ms,
                end_ms=asked_ms + speech_ms,
                confidence=None,
                question_id=question_id,
                answer_id=None,
            )
        )
        stage_times.setdefault(ex.stage, [asked_ms / 1000.0, asked_ms / 1000.0])
        item: dict[str, Any] = {
            "item_key": ex.item_key,
            "title": ex.item_key,
            "mandatory": ex.mandatory,
            "compressible": False,
            "status": "answered" if ex.answer else "asked",
            "question_ids": [str(question_id)],
            "answer_ids": [],
            "followups_used": 0,
            "followup_limit": 0,
            "clarifications": 0,
            "skip_reason": None,
            "started_s": asked_ms / 1000.0,
            "ended_s": None,
        }
        clock_ms = asked_ms + speech_ms + 900
        if ex.activity:
            clock_ms += {"reading_aloud": 1500, "read_relay": 31500, "topic_card": 61000}[ex.activity]
        if ex.answer is not None:
            answer_seq += 1
            answer_id = uuid.uuid5(session_id, f"a:{ex.item_key}")
            duration_ms = int(ex.answer_seconds * 1000)
            start_ms, end_ms = clock_ms, clock_ms + duration_ms
            answers.append(
                SimpleNamespace(
                    id=answer_id,
                    session_id=session_id,
                    question_id=question_id,
                    sequence=answer_seq,
                    transcript_text=ex.answer,
                    typed=False,
                    started_ms=start_ms,
                    ended_ms=end_ms,
                    duration_ms=duration_ms,
                    word_count=_words(ex.answer),
                    stt_confidence=0.88,
                    audio_storage_key=None,
                    status="received",
                )
            )
            transcript_seq += 1
            transcripts.append(
                SimpleNamespace(
                    session_id=session_id,
                    seq=transcript_seq,
                    speaker="candidate",
                    kind="final",
                    text=ex.answer,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    confidence=0.88,
                    question_id=question_id,
                    answer_id=answer_id,
                )
            )
            item["answer_ids"] = [str(answer_id)]
            item["ended_s"] = end_ms / 1000.0
            if ex.activity:
                segments = _segments(ex.answer, start_ms, duration_ms)
                pauses = _pauses(segments, long_pause_after=2 if ex.activity == "reading_aloud" else None)
                phase = {"reading_aloud": "read", "read_relay": "relay", "topic_card": "speak"}[ex.activity]
                material = {
                    "reading_aloud": PASSAGE_DRILL_A,
                    "read_relay": PASSAGE_DRILL_B,
                    "topic_card": TOPIC_CARD,
                }[ex.activity]
                phases: list[dict[str, Any]] = []
                if ex.activity == "read_relay":
                    phases.append(
                        {"phase": "read", "status": "ended", "planned_ms": 30000,
                         "started_elapsed_s": (start_ms - 31000) / 1000.0, "ended_elapsed_s": (start_ms - 1000) / 1000.0,
                         "end_reason": "deadline"}
                    )  # fmt: skip
                if ex.activity == "topic_card":
                    phases.append(
                        {"phase": "prepare", "status": "ended", "planned_ms": 60000,
                         "started_elapsed_s": (start_ms - 61000) / 1000.0, "ended_elapsed_s": (start_ms - 1000) / 1000.0,
                         "end_reason": "deadline"}
                    )  # fmt: skip
                planned = {"read": 180000, "relay": 150000, "speak": 120000}[phase]
                phases.append(
                    {"phase": phase, "status": "ended", "planned_ms": planned, "started_elapsed_s": start_ms / 1000.0,
                     "ended_elapsed_s": end_ms / 1000.0, "end_reason": "finish_button"}
                )  # fmt: skip
                activities.append(
                    SimpleNamespace(
                        id=activity_id,
                        organization_id=organization_id,
                        intake_id=intake_id,
                        session_id=session_id,
                        kind=ex.activity,
                        stage_key=ex.stage,
                        status="completed",
                        attempt=1,
                        material_key=material,
                        kit_version="v1",
                        question_id=question_id,
                        answer_id=answer_id,
                        phases=phases,
                        reveal=(
                            {
                                "shown_ms": 30000,
                                "windows": [
                                    {
                                        "from_elapsed_s": (start_ms - 31000) / 1000.0,
                                        "to_elapsed_s": (start_ms - 1000) / 1000.0,
                                    }
                                ],
                                "interrupted": False,
                            }
                            if ex.activity == "read_relay"
                            else {}
                        ),  # fmt: skip
                        capture={
                            "attempt": 1,
                            "phase": phase,
                            "started_ms": start_ms - 600,
                            "ended_ms": end_ms + 400,
                            "voiced_ms": int(duration_ms * 0.82),
                            "end_reason": "finish_button",
                            "segments": segments,
                            "pauses": pauses,
                            "stt_confidence": 0.87,
                            "stt_degraded": False,
                            "typed": False,
                            "windows": [{"start_ms": start_ms, "end_ms": end_ms, "text": ex.answer}],
                            "audio_stored": False,
                        },
                        attempts_log=[],
                        transcript_live=ex.answer,
                        transcript_final=ex.answer,
                        transcript_status="final",
                        transcript_source="final_pass_live",
                        audio_media_id=None,
                        ended_reason="finish_button",
                    )
                )
            clock_ms = end_ms + 1800
        else:
            item["ended_s"] = (asked_ms + speech_ms) / 1000.0
            clock_ms = asked_ms + speech_ms + 500
        stage_items[ex.stage].append(item)
        stage_times[ex.stage][1] = clock_ms / 1000.0
    stages: list[SimpleNamespace] = []
    for key in stage_order:
        number = STAGE_NUMBERS[key]
        items = stage_items[key] + [dict(entry) for entry in EXTRA_ITEMS.get(key, []) if key in stage_times]
        reached = key in stage_times
        if not reached:
            status = "incomplete"
        elif variant == "incomplete" and stage_order.index(key) == cut:
            status = "completed"
        else:
            status = "completed"
        if variant == "incomplete" and not reached:
            items = [
                {"item_key": f"{key}.pending", "title": f"{key} items", "mandatory": True, "compressible": False,
                 "status": "not_reached", "question_ids": [], "answer_ids": [], "followups_used": 0,
                 "followup_limit": 0, "clarifications": 0, "skip_reason": "time_up", "started_s": None,
                 "ended_s": None}
            ]  # fmt: skip
        times = stage_times.get(key)
        stages.append(
            SimpleNamespace(
                organization_id=organization_id,
                intake_id=intake_id,
                session_id=session_id,
                stage_key=key,
                stage_number=number,
                status=status,
                started_elapsed_s=times[0] if times else None,
                ended_elapsed_s=times[1] if times else None,
                budget_s=float(STAGE_BUDGETS[key]),
                items=items,
                extra=_stage_extra(key),
            )
        )
    elapsed = clock_ms / 1000.0
    session = SimpleNamespace(
        id=session_id,
        organization_id=organization_id,
        interview_id=interview_id or uuid.uuid4(),
        candidate_id=candidate_id or uuid.uuid4(),
        status="completing",
        end_reason="stopping_condition" if variant == "complete" else "time_limit",
        elapsed_seconds=elapsed,
        started_at=started_at,
        ended_at=started_at + timedelta(seconds=elapsed),
    )
    onboarding = dict(ONBOARDING, submitted_at=(started_at - timedelta(hours=2)).isoformat())
    eligibility = (
        dict(ELIGIBILITY, answered_at=(started_at - timedelta(hours=2)).isoformat())
        if with_eligibility
        else None
    )
    return DemoRows(
        session=session,
        questions=questions,
        answers=answers,
        transcripts=transcripts,
        activities=activities,
        stages=stages,
        onboarding=onboarding,
        eligibility=eligibility,
    )


def _stage_extra(key: str) -> dict[str, Any]:
    if key == "resume":
        return {"claims": [dict(c) for c in CLAIMS]}
    if key == "mandatory":
        return {"language": "python", "level": "intermediate", "self_rating": 7}
    if key == "reading":
        return {"passages": {"drill_a": PASSAGE_DRILL_A, "drill_b": PASSAGE_DRILL_B}}
    if key == "topic":
        return {"card": TOPIC_CARD}
    return {}


# ============================================================================================== evaluator outputs


def _alias(inputs: Any, item_key: str) -> str:
    answers = inputs.answers_for_item(item_key)
    if not answers:
        raise KeyError(f"demo answer for {item_key} not found")
    return answers[0].alias


def _activity_alias(inputs: Any, kind: str) -> str:
    answer = inputs.activity_answer(kind)
    if answer is None:
        raise KeyError(f"demo capture for {kind} not found")
    return answer.alias


def _cite(alias: str, quote: str, supports: str = "positive") -> dict[str, Any]:
    return {"answer": alias, "quote": quote, "supports": supports}


def _judgement(
    competency: str,
    score: int | None,
    reasoning: str,
    evidence: list[dict[str, Any]],
    *,
    missing: list[str] | None = None,
    strengths: list[str] | None = None,
    improvements: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "competency": competency,
        "sufficient": score is not None,
        "score": score,
        "reasoning": reasoning,
        "evidence": evidence,
        "missing_evidence": missing or [],
        "strengths": strengths or [],
        "improvements": improvements or [],
    }


def demo_evaluator_outputs(inputs: Any) -> dict[str, dict[str, Any]]:
    """Canned evaluator outputs for the demo session (aliases resolved from ``inputs`` after ``assign_aliases``).
    Outputs of evaluators whose stages were not reached are left out (the pipeline then falls back)."""
    outputs: dict[str, dict[str, Any]] = {}
    intro, project, followup = (
        _alias(inputs, k) for k in ("greeting.intro", "greeting.project", "greeting.followup")
    )
    c1_own, c1_mech, c1_stress = (
        _alias(inputs, f"resume.C1.{r}") for r in ("ownership", "mechanics", "stress")
    )
    c2_own, c2_mech = (_alias(inputs, f"resume.C2.{r}") for r in ("ownership", "mechanics"))
    mq2, mq3, verification, probe, mq4 = (
        _alias(inputs, k)
        for k in (
            "mandatory.mq2",
            "mandatory.mq3",
            "mandatory.mq3.verification",
            "mandatory.mq3.rating_probe",
            "mandatory.mq4",
        )
    )
    outputs["resume_evidence"] = {
        "claims": [
            {
                "claim": "C1",
                "verdict": "hands_on",
                "rung_answers": {"ownership": c1_own, "mechanics": c1_mech, "stress": c1_stress},
                "evidence": [
                    _cite(c1_own, "I personally built four of the six pipelines we run in Airflow"),
                    _cite(
                        c1_mech,
                        "a SQL task merges staging into the invoices table using the invoice number as the key",
                    ),
                    _cite(
                        c1_stress,
                        "the pandas step would probably be the first problem because it loads the whole file",
                    ),
                ],
                "note": "Described their own DAGs step by step and named a concrete scaling limit and fix.",
                "unresolved": [],
            },
            {
                "claim": "C2",
                "verdict": "team_level",
                "rung_answers": {"ownership": c2_own, "mechanics": c2_mech, "stress": None},
                "evidence": [
                    _cite(c2_own, "That one was more of a team effort.", "context"),
                    _cite(c2_mech, "I don't remember exactly how the baseline was calculated", "negative"),
                ],
                "note": "Described a team performance effort; the measurement was set up by a colleague.",
                "unresolved": ["Which queries they rewrote themselves and the measured before/after numbers"],
            },
        ],
        "review_items": [],
    }
    outputs["communication"] = {
        "judgement": _judgement(
            "communication",
            4,
            "Answers address the question directly and follow a clear order (context, what they built, result). "
            "The project answer is structured from problem to outcome; some résumé answers are brief.",
            [
                _cite(project, "Our operations team used to build a daily report by hand in Excel"),
                _cite(followup, "The hardest part was duplicate shipment events."),
                _cite(
                    c2_own,
                    "I was part of that group, and I helped find the slow queries from the logs.",
                    "context",
                ),
            ],
            strengths=["Answers follow problem, action, result", "Stays on the question asked"],
            improvements=["Add the measured outcome when describing team results"],
        ),
        "review_items": [],
    }
    outputs["technical"] = {
        "judgement": _judgement(
            "technical",
            4,
            "Explains their own pipelines in mechanical detail (sensor, staging table, idempotent merge, quality "
            "checks) and answered the language check correctly; the second résumé claim stayed at team level.",
            [
                _cite(c1_mech, "validates the columns and the totals, and writes them to a staging table"),
                _cite(verification, "a dictionary when I need to find something by an ID"),
                _cite(c2_mech, "my colleague set up the measurement", "negative"),
            ],
            strengths=["Idempotent pipeline design", "Correct Python data-structure answer"],
            improvements=["Hands-on modelling experience", "Measuring performance work independently"],
        ),
        "verification": {
            "answer": verification,
            "quality": "correct",
            "matched_key_points": [1, 2, 3],
            "note": "Covered ordered access by position, key lookup in a hash table and when to use each.",
        },
        "rating_supported": "supported",
        "review_items": [],
    }
    outputs["composure"] = {
        "judgement": _judgement(
            "composure",
            4,
            "Kept going on the stress question and answered the follow-ups; said plainly when a detail was not known "
            "and offered what they did know.",
            [
                _cite(
                    c1_stress, "If the volume doubled, the pandas step would probably be the first problem"
                ),
                _cite(
                    c2_mech,
                    "To be honest I don't remember exactly how the baseline was calculated",
                    "context",
                ),
                _cite(probe, "I usually make things work first", "context"),
            ],
            strengths=["Answers challenge questions with a concrete next step"],
            improvements=["Offer a way to find out when a detail is not known"],
        ),
        "observations": [
            {
                "behaviour": "handled_followup",
                "answer": c1_stress,
                "note": "Named a scaling limit and two fixes.",
            },
            {
                "behaviour": "kept_going_when_unsure",
                "answer": c2_mech,
                "note": "Shared what they knew about the measurement.",
            },
            {
                "behaviour": "handled_followup",
                "answer": probe,
                "note": "Described concrete skills for the next level.",
            },
        ],
    }
    outputs["trainability"] = {
        "trainability": _judgement(
            "trainability",
            4,
            "Describes a concrete three-week plan with fundamentals, a real project and feedback loops, and taught "
            "data leakage clearly with an example and a prevention rule.",
            [
                _cite(
                    _alias(inputs, "learning.three_weeks"),
                    "build a simple model end to end, starting with a baseline",
                )
                if _has(inputs, "learning.three_weeks")
                else _cite(mq4, "I expect the training to be intense"),
                _cite(
                    _alias(inputs, "learning.teach"),
                    "would I really know this at the moment I make the prediction",
                )
                if _has(inputs, "learning.teach")
                else _cite(mq4, "I can commit full time"),
            ],
            strengths=["Self-directed learning plan", "Clear teach-back with an example"],
            improvements=["Statistics depth for model evaluation"],
        )
        if _has(inputs, "learning.teach")
        else _judgement(
            "trainability", None, "The learning stage was not reached.", [], missing=["learning stage"]
        ),
        "adaptability": _judgement(
            "adaptability",
            5,
            "Actively wants to move into AI/ML, links it to problems in their current work, and is open to related "
            "roles.",
            [_cite(mq2, "I'd love to build models for that"), _cite(mq2, "AI/ML is my first choice")],
            strengths=["Clear motivation for AI/ML"],
        ),
        "learning_strategy": "Fundamentals first, then one real problem end to end, then iteration with reviews.",
        "teach_back": "Explained data leakage with a delivery-date example and a rule to prevent it."
        if _has(inputs, "learning.teach")
        else "Not reached.",
        "ai_ml_openness": "eager",
        "review_items": [],
    }
    if inputs.activity_answer("read_relay") is not None:
        relay = _activity_alias(inputs, "read_relay")
        followups = [q for q in inputs.questions if (q.item_key or "").startswith("reading.followup.")]
        outputs["relay"] = {
            "concepts": [
                {
                    "key": "definition",
                    "status": "covered",
                    "answer": relay,
                    "quote": "learns the training data too well",
                },
                {
                    "key": "noise",
                    "status": "covered",
                    "answer": relay,
                    "quote": "it memorizes the noise and the random details instead of the real pattern",
                },
                {"key": "consequence", "status": "covered", "answer": relay, "quote": "it fails on new data"},
                {
                    "key": "analogy",
                    "status": "covered",
                    "answer": relay,
                    "quote": "a student who memorizes the answers from last year's exam",
                },
                {"key": "analogy_outcome", "status": "missing", "answer": None, "quote": None},
                {
                    "key": "more_data",
                    "status": "covered",
                    "answer": relay,
                    "quote": "you can get more training data",
                },
                {
                    "key": "simpler_model",
                    "status": "covered",
                    "answer": relay,
                    "quote": "make the model simpler",
                },
                {"key": "regularisation", "status": "missing", "answer": None, "quote": None},
            ],
            "paraphrase": "own_words",
            "misstatements": [],
            "followups": [
                {
                    "question": q.alias,
                    "answer": (inputs.answers_for(q.id)[0].alias if inputs.answers_for(q.id) else None),
                    "note": note,
                }
                for q, note in zip(
                    followups,
                    (
                        "Named the defences section and what they did not remember.",
                        "Gave a two-sentence plain-language version with the exam analogy.",
                        "Proposed comparing training and validation performance.",
                    ),
                    strict=False,
                )
            ],
            "notes": "Relayed the definition, cause, effect, analogy and two defences in own words.",
        }
    if inputs.activity_answer("topic_card") is not None:
        speech = _activity_alias(inputs, "topic_card")
        weakest = _alias(inputs, "topic.weakest")
        outputs["structured"] = {
            "judgement": _judgement(
                "structured_answer",
                4,
                "Covered all four points in order with a personal example; the transitions are simple but the answer "
                "ties back to priorities at the end.",
                [
                    _cite(speech, "A technique that helps me is time blocking."),
                    _cite(speech, "last quarter we had a migration deadline"),
                    _cite(weakest, "I only gave one example.", "context"),
                ],
                strengths=["All four points covered", "Concrete personal example"],
                improvements=["Connect the points with transitions rather than listing them"],
            ),
            "bullets": [
                {
                    "index": 1,
                    "status": "covered",
                    "answer": speech,
                    "quote": "knowing what your priorities are for the day",
                },
                {
                    "index": 2,
                    "status": "covered",
                    "answer": speech,
                    "quote": "One common mistake people make is saying yes to everything",
                },
                {
                    "index": 3,
                    "status": "covered",
                    "answer": speech,
                    "quote": "A technique that helps me is time blocking.",
                },
                {
                    "index": 4,
                    "status": "covered",
                    "answer": speech,
                    "quote": "last quarter we had a migration deadline",
                },
            ],
            "flow": "natural",
            "examples_used": True,
            "gap_handling": "not_needed",
            "followup_analysis": "Identified the least developed point and added a second common mistake.",
        }
    return outputs


def _has(inputs: Any, item_key: str) -> bool:
    return bool(inputs.answers_for_item(item_key))


__all__ = [
    "CANDIDATE_NAME",
    "CLAIMS",
    "DRILL_A_TRANSCRIPT",
    "ELIGIBILITY",
    "ELIGIBILITY_COUNTRY",
    "EXCHANGES",
    "ONBOARDING",
    "PASSAGE_DRILL_A",
    "PASSAGE_DRILL_B",
    "PROGRAM_NAME",
    "RELAY_TRANSCRIPT",
    "TOPIC_CARD",
    "TOPIC_TRANSCRIPT",
    "DemoRows",
    "build_demo_rows",
    "demo_evaluator_outputs",
]
