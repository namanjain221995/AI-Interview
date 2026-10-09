"""Mock AI services for the browser end-to-end stack (no model is ever called).

``install()`` puts :class:`app.ai.mock.MockAIServices` into the AI registry with responders that keep an intake
interview realistic enough to drive from the browser:

* intake turns (``TURN_DECISION`` prompts with an ``OFFERED MOVES`` block): ``ask_item`` when offered, else the first
  offered move; the speech is the kit question quoted in that move's instruction (``Kit: '...'``), else a neutral
  question about the item;
* the stage 3 language grade (``VerificationGrade``): ``adequate``;
* the seven intake evaluators (``ANSWER_EVALUATION``): sufficient judgements that cite verbatim words of the answers
  listed in the prompt (so the deterministic validator keeps them), relay concepts and topic-card points marked
  covered with quotes from the candidate's own answer;
* every other structured call: the schema's minimal valid payload (the ``MockLLM`` default);
* speech recognition: one fixed sentence per segment (``E2E_MOCK_STT_TEXT`` overrides it);
* speech synthesis: the mock tone at ``E2E_MOCK_TTS_MS_PER_CHAR`` milliseconds per character (default 20).

Used by ``serve.py`` for both the API and the worker process. Test infrastructure only: never imported by the app.
"""

from __future__ import annotations

import os
import re
from typing import Any

DEFAULT_STT_TEXT = (
    "I have worked with Python and SQL for about three years, mostly writing scripts that clean data and build "
    "small reports, and I explain what I did step by step to my team."
)

_OFFER = re.compile(
    r"^- (follow_up|ask_item|clarify|answer_question|redirect) \(item_key ([\w.]+)\): ?(.*)$", re.M
)
# The first quoted kit question of an instruction: 'What did you personally build ...?' (apostrophes inside allowed).
_KIT_QUOTE = re.compile(r"(?:(?<=\s)|(?<=:))'(.{8,300}?[?.])'(?=[\s,.;)/]|$)")
_QUESTION_LINE = re.compile(r"^(Q\d+) \[([^\]]*)\] Interviewer: (.*)$")
_ANSWER_LINE = re.compile(r"^\s+(A\d+) \[([^\]]*)\] Candidate: (.*)$")
_CONCEPT_LINE = re.compile(r"^([A-Za-z][\w-]{0,39}): \S")
_RELAY_ALIAS = re.compile(r"the relay answer (A\d+)")


# ============================================================================================== parsing helpers


def _prompt(call: Any) -> str:
    return "\n".join(str(m.get("content", "")) for m in call.messages)


def _answers(text: str) -> list[dict[str, Any]]:
    """Every candidate answer listed in an evaluator prompt, with the item of the question before it."""
    out: list[dict[str, Any]] = []
    item = stage = ""
    for line in text.splitlines():
        question = _QUESTION_LINE.match(line)
        if question:
            meta = question.group(2)
            item = next((p[5:] for p in meta.split("; ") if p.startswith("item ")), "")
            stage = next((p[6:] for p in meta.split("; ") if p.startswith("stage ")), "")
            continue
        answer = _ANSWER_LINE.match(line)
        if not answer:
            continue
        body = answer.group(3).strip()
        if not body or body.startswith("(silence"):
            continue
        out.append(
            {"alias": answer.group(1), "meta": answer.group(2), "text": body, "item": item, "stage": stage}
        )
    return out


def _quote(text: str, *, start: int = 0, words: int = 10) -> str:
    """A verbatim run of words from ``text`` (the validator matches quotes against the answer)."""
    tokens = text.split()
    if not tokens:
        return text[:300]
    start = min(start, max(0, len(tokens) - 3))
    quote = " ".join(tokens[start : start + words]).strip()
    if len(quote) < 3:
        quote = text.strip()[:300]
    return quote[:300]


def _cite(answer: dict[str, Any], supports: str = "positive", *, start: int = 0) -> dict[str, Any]:
    return {"answer": answer["alias"], "quote": _quote(answer["text"], start=start), "supports": supports}


def _judgement(competency: str, answers: list[dict[str, Any]], score: int = 4) -> dict[str, Any]:
    if not answers:
        return {
            "competency": competency,
            "sufficient": False,
            "score": None,
            "reasoning": "No answers in scope.",
            "evidence": [],
            "missing_evidence": ["No answers in scope."],
            "strengths": [],
            "improvements": [],
        }
    return {
        "competency": competency,
        "sufficient": True,
        "score": score,
        "reasoning": "The answers address the questions with concrete steps (end-to-end mock evaluation).",
        "evidence": [_cite(a, "positive", start=0) for a in answers[:2]],
        "missing_evidence": [],
        "strengths": ["Gave concrete steps."],
        "improvements": ["Add measured results."],
    }


def _by_item(answers: list[dict[str, Any]], *prefixes: str) -> list[dict[str, Any]]:
    return [a for a in answers if any(a["item"].startswith(p) for p in prefixes)]


def _capture(answers: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [a for a in answers if f"{kind} capture" in a["meta"]]


# ============================================================================================== responders


def evaluator_output(evaluator: str, text: str) -> dict[str, Any] | None:
    """A plausible, validator-proof output for one intake evaluator, or ``None`` for an unknown evaluator."""
    answers = _answers(text)
    if evaluator == "communication":
        return {"judgement": _judgement("communication", answers), "review_items": []}
    if evaluator == "resume_evidence":
        claims = []
        for ref in ("C1", "C2", "C3"):
            mine = _by_item(answers, f"resume.{ref}.")
            if f"{ref}" not in text:
                continue
            rungs: dict[str, str | None] = {"ownership": None, "mechanics": None, "stress": None}
            for answer in mine:
                rung = answer["item"].split(".")[2] if answer["item"].count(".") >= 2 else ""
                if rung in rungs and rungs[rung] is None:
                    rungs[rung] = answer["alias"]
            claims.append(
                {
                    "claim": ref,
                    "verdict": "hands_on" if mine else "uncertain",
                    "rung_answers": rungs,
                    "evidence": [_cite(mine[0])] if mine else [],
                    "note": "Described their own part step by step."
                    if mine
                    else "Too little was said to tell.",
                    "unresolved": [],
                }
            )
        return {"claims": claims[:3], "review_items": []}
    if evaluator == "technical":
        verification = next(
            (
                a
                for a in answers
                if a["item"] in ("mandatory.mq3.verification", "mandatory.mq3.verification_retry")
            ),
            None,
        )
        return {
            "judgement": _judgement("technical", answers, score=3),
            "verification": {
                "answer": verification["alias"] if verification else None,
                "quality": "correct" if verification else "not_asked",
                "matched_key_points": [1, 2] if verification else [],
                "note": "Covered the main key points." if verification else "Not asked.",
            },
            "rating_supported": "supported" if verification else "unknown",
            "review_items": [],
        }
    if evaluator == "relay":
        alias_match = _RELAY_ALIAS.search(text)
        relay = next((a for a in answers if alias_match and a["alias"] == alias_match.group(1)), None)
        relay = relay or next(iter(_capture(answers, "read_relay")), None)
        concepts: list[dict[str, Any]] = []
        lines = text.splitlines()
        if "Key concepts (key: concept):" in lines:
            for line in lines[lines.index("Key concepts (key: concept):") + 1 :]:
                if not line.strip():
                    break
                match = _CONCEPT_LINE.match(line)
                if match:
                    concepts.append(
                        {
                            "key": match.group(1),
                            "status": "covered" if relay else "missing",
                            "answer": relay["alias"] if relay else None,
                            "quote": _quote(relay["text"], start=len(concepts) * 3, words=6)
                            if relay
                            else None,
                        }
                    )
        return {
            "concepts": concepts[:8],
            "paraphrase": "own_words" if relay else "insufficient",
            "misstatements": [],
            "followups": [],
            "notes": "Explained the passage in their own words." if relay else "Nothing was said.",
        }
    if evaluator == "structured":
        speak = next(iter(_capture(answers, "topic_card")), None) or next(
            iter(_by_item(answers, "topic.")), None
        )
        return {
            "judgement": _judgement("structured_answer", [speak] if speak else []),
            "bullets": [
                {
                    "index": n,
                    "status": "covered" if speak else "missing",
                    "answer": speak["alias"] if speak else None,
                    "quote": _quote(speak["text"], start=(n - 1) * 4, words=6) if speak else None,
                }
                for n in (1, 2, 3, 4)
            ],
            "flow": "natural",
            "examples_used": bool(speak),
            "gap_handling": "not_needed",
            "followup_analysis": "",
        }
    if evaluator == "trainability":
        learning = _by_item(answers, "learning.") or answers
        return {
            "trainability": _judgement("trainability", learning),
            "adaptability": _judgement("adaptability", answers),
            "learning_strategy": "Plans to learn by building small projects and asking for reviews.",
            "teach_back": "Explained a recently learned topic in plain steps.",
            "ai_ml_openness": "open",
            "review_items": [],
        }
    if evaluator == "composure":
        return {
            "judgement": _judgement("composure", answers),
            "observations": [
                {
                    "behaviour": "handled_followup",
                    "answer": a["alias"],
                    "note": "Answered the follow-up directly.",
                }
                for a in answers[:2]
            ],
        }
    return None


def _evaluator_key(call: Any) -> str | None:
    version = getattr(call.context, "prompt_version", None) or ""
    match = re.match(r"intake-eval-([a-z_]+)@", version)
    return match.group(1) if match else None


def _kit_question(instruction: str) -> str | None:
    match = _KIT_QUOTE.search(instruction)
    if not match:
        return None
    question = match.group(1).strip()
    # Drop a sentence with a personalisation slot ("You've mostly done [their field].") and fill any other slot.
    question = re.sub(r"[^.?]*\[their field\][^.?]*[.?]\s*", "", question).strip() or question
    question = re.sub(r"\[[^\]]*\]", "that point", question)
    return question


def intake_turn(call: Any) -> dict[str, Any] | None:
    """An ``IntakeTurn`` for an intake turn prompt, or ``None`` when the prompt is not one."""
    offers = _OFFER.findall(str(call.messages[-1].get("content", ""))) if call.messages else []
    if not offers:
        return None
    moves = {move: (key, instruction) for move, key, instruction in offers}
    move = "ask_item" if "ask_item" in moves else offers[0][0]
    key, instruction = moves[move]
    if move == "answer_question":
        speech = "The hiring team is best placed to answer that; they'll follow up with you on it. Any other questions?"
    else:
        speech = (
            _kit_question(instruction)
            or f"Could you tell me more about {key.split('.')[-1].replace('_', ' ')}?"
        )
    return {
        "speech": speech,
        "move": move,
        "item_key": key,
        "answer_quality": "adequate",
        "claim_signal": "hands_on" if key.startswith("resume.") else None,
        "target_bullet": 2 if key == "topic.weakest" else None,
        "missing_specifics": [],
        "reason": f"e2e mock {move}",
    }


def _minimal(call: Any) -> Any:
    from app.ai.mock import minimal_payload, minimal_payload_from_json_schema
    from app.ai.structured import response_format

    if call.schema is not None:
        return minimal_payload(call.schema)
    if call.json_schema is not None:
        return minimal_payload_from_json_schema(response_format(call.json_schema)["json_schema"]["schema"])
    return f"Mock response for {call.task.value}."


def respond(call: Any) -> Any:
    """Default responder for every model task (see the module docstring)."""
    name = getattr(call.schema, "__name__", "") if call.schema is not None else ""
    json_name = (call.json_schema or {}).get("name", "") if isinstance(call.json_schema, dict) else ""
    if json_name == "IntakeTurn" or name == "IntakeTurn":
        return intake_turn(call) or _minimal(call)
    if name == "VerificationGrade":
        return {"quality": "adequate", "matched_key_points": [1, 2]}
    evaluator = _evaluator_key(call)
    if evaluator:
        output = evaluator_output(evaluator, _prompt(call))
        if output is not None:
            return output
    return _minimal(call)


def install() -> Any:
    """Build the mock services, wire the responders and install them in the AI registry."""
    from app.ai.mock import MockAIServices
    from app.ai.registry import set_ai
    from app.core.config import get_settings

    services = MockAIServices(dimensions=get_settings().embedding_dimensions)
    services.llm.set_default(None, respond)
    services.stt.default_text = os.environ.get("E2E_MOCK_STT_TEXT", DEFAULT_STT_TEXT)
    services.tts.ms_per_char = float(os.environ.get("E2E_MOCK_TTS_MS_PER_CHAR", "20"))
    set_ai(services)
    return services
