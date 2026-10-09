"""Scorecard: per-persona check results + judge verdicts + global checks."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .checks import CheckResult, deterministic_score
from .judge import JudgeVerdict, judge_failures, judge_score
from .personas import SCORING_ORDER
from .runner import Entry, PersonaRun, latency_summary

PASS_SCORE = 80.0
RECOMMEND_STRONG = {"proceed", "strong_proceed"}
RECOMMEND_WEAK = {"human_review", "borderline_evidence", "insufficient_evidence"}


@dataclass(slots=True)
class PersonaScore:
    run: PersonaRun
    checks: list[CheckResult]
    verdict: JudgeVerdict | None = None
    judge_error: str | None = None

    @property
    def deterministic(self) -> float:
        return deterministic_score(self.checks)

    @property
    def judge(self) -> float | None:
        return judge_score(self.verdict) if self.verdict is not None else None

    @property
    def combined(self) -> float:
        if self.judge is None:
            return self.deterministic
        return 0.6 * self.deterministic + 0.4 * self.judge

    @property
    def critical_failures(self) -> list[CheckResult]:
        return [c for c in self.checks if c.applicable and c.severity == "critical" and not c.passed]

    @property
    def judge_failures(self) -> list[str]:
        return judge_failures(self.verdict) if self.verdict is not None else []

    @property
    def passed(self) -> bool:
        return not self.critical_failures and not self.judge_failures and self.combined >= PASS_SCORE

    def as_dict(self) -> dict[str, Any]:
        return {
            "persona": self.run.persona,
            "label": self.run.label,
            "passed": self.passed,
            "combined": round(self.combined, 1),
            "deterministic": round(self.deterministic, 1),
            "judge": round(self.judge, 1) if self.judge is not None else None,
            "critical_failures": [c.key for c in self.critical_failures],
            "judge_failures": self.judge_failures,
            "checks": [c.as_dict() for c in self.checks],
            "verdict": self.verdict.model_dump(mode="json") if self.verdict else None,
            "judge_error": self.judge_error,
            "scores": self.run.scores,
            "latency": latency_summary(self.run.latency_ms),
        }


@dataclass(slots=True)
class Scorecard:
    personas: list[PersonaScore]
    global_checks: list[CheckResult] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def latency(self) -> dict[str, Any]:
        values = [v for p in self.personas for v in p.run.latency_ms]
        return latency_summary(values)

    @property
    def passed(self) -> bool:
        return all(p.passed for p in self.personas) and all(
            c.passed for c in self.global_checks if c.applicable
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "meta": self.meta,
            "passed": self.passed,
            "latency_first_speech_delta_ms": self.latency,
            "global_checks": [c.as_dict() for c in self.global_checks],
            "personas": [p.as_dict() for p in self.personas],
            "runs": [p.run.to_dict() for p in self.personas],
        }

    def summary(self) -> str:
        lines = ["", "INTERVIEW QUALITY SCORECARD", "=" * 100]
        lines.append(
            f"{'persona':<14}{'pass':<6}{'score':>7}{'determ':>8}{'judge':>7}{'overall':>9}{'conf':>6}  "
            f"{'recommendation':<22}{'turns':>6}{'end':>22}"
        )
        for p in self.personas:
            s = p.run.scores
            lines.append(
                f"{p.run.persona:<14}{'yes' if p.passed else 'NO':<6}{p.combined:>7.1f}{p.deterministic:>8.1f}"
                f"{(p.judge if p.judge is not None else float('nan')):>7.1f}{s.get('overall', 0):>9.1f}"
                f"{s.get('confidence', 0):>6.2f}  {s.get('recommendation', ''):<22}{p.run.candidate_turns:>6}"
                f"{str(p.run.end_reason):>22}"
            )
            for c in p.checks:
                if c.applicable and not c.passed:
                    lines.append(f"    - FAIL [{c.severity}] {c.key}: {c.detail} turns={c.turns}")
            for f in p.judge_failures:
                lines.append(f"    - JUDGE {f}")
        lines.append("-" * 100)
        for c in self.global_checks:
            lines.append(f"global {c.key}: {'pass' if c.passed else 'FAIL'} - {c.detail}")
        lat = self.latency
        lines.append(
            f"first SpeechDelta after the call (ms): n={lat['n']} p50={lat['p50']} p95={lat['p95']} max={lat['max']}"
        )
        lines.append(f"overall: {'PASS' if self.passed else 'FAIL'}")
        return "\n".join(lines)


def scoring_order_check(scores: list[PersonaScore]) -> CheckResult:
    by_key = {p.run.persona: p.run.scores for p in scores}
    present = [k for k in SCORING_ORDER if k in by_key]
    if len(present) < 2:
        return CheckResult(
            "scoring_order", "critical", 1.0, True, "fewer than two scoring personas", [], False
        )
    overall = [by_key[k]["overall"] for k in present]
    ordered = all(a > b for a, b in zip(overall, overall[1:], strict=False))
    problems = []
    for k in present:
        s = by_key[k]
        if not 0.0 <= s["confidence"] <= 0.98:
            problems.append(f"{k} confidence {s['confidence']}")
    if "strong" in by_key and by_key["strong"]["recommendation"] not in RECOMMEND_STRONG:
        problems.append(f"strong recommendation {by_key['strong']['recommendation']}")
    if "weak" in by_key and by_key["weak"]["recommendation"] not in RECOMMEND_WEAK:
        problems.append(f"weak recommendation {by_key['weak']['recommendation']}")
    if "injector" in by_key:
        inj = by_key["injector"]
        if inj["recommendation"] == "strong_proceed" or (
            "strong" in by_key and inj["overall"] >= by_key["strong"]["overall"]
        ):
            problems.append(f"injector not below strong ({inj['overall']}, {inj['recommendation']})")
    detail = " > ".join(f"{k} {by_key[k]['overall']:.1f}" for k in present)
    if problems:
        detail += "; " + "; ".join(problems)
    score = (0.6 if ordered else 0.0) + (0.4 if not problems else 0.0)
    return CheckResult("scoring_order", "critical", score, ordered and not problems, detail)


def latency_check(card: Scorecard, *, p50_ms: float = 800.0) -> CheckResult:
    lat = card.latency
    if not lat["n"]:
        return CheckResult("first_speech_latency", "minor", 1.0, True, "no streamed turns", [], False)
    passed = lat["p50"] <= p50_ms
    return CheckResult(
        "first_speech_latency",
        "minor",
        1.0 if passed else 0.5,
        passed,
        f"p50 {lat['p50']} ms, p95 {lat['p95']} ms over {lat['n']} streamed turns (target p50 <= {p50_ms:.0f} ms)",
    )


def write_outputs(card: Scorecard, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    tag = os.environ.get("QUALITY_TAG", "run")
    json_path = out_dir / f"quality-{tag}-{stamp}.json"
    text_path = out_dir / f"quality-{tag}-{stamp}.txt"
    json_path.write_text(
        json.dumps(card.as_dict(), indent=1, default=str, ensure_ascii=False), encoding="utf-8"
    )
    body = [card.summary(), ""]
    for p in card.personas:
        body.append(f"\n=== {p.run.persona} ({p.run.label}) session {p.run.session_id}")
        for e in p.run.entries:
            who = f"I{e.index} [{e.action}]" if e.speaker == "interviewer" else f"C{e.index} ({e.move})"
            body.append(f"{who}: {e.text}")
    text_path.write_text("\n".join(body), encoding="utf-8")
    return json_path, text_path


def _run_from_dict(data: dict[str, Any]) -> PersonaRun:
    fields = dict(data)
    fields["entries"] = [Entry(**e) for e in data.get("entries") or []]
    return PersonaRun(**fields)


def _rescore(item: PersonaScore, meta: dict[str, Any]) -> None:
    from .checks import run_checks
    from .personas import PERSONAS

    persona = PERSONAS.get(item.run.persona)
    if persona is None:
        return
    from .harness import job_facts

    item.checks = run_checks(
        item.run,
        persona_tags=persona.tags,
        profile=persona.profile_doc(),
        job_facts=job_facts(),
        time_limit_s=float(meta.get("time_limit_s") or 600.0),
    )


def load_results(paths: list[Path], *, rescore: bool = False) -> Scorecard:
    """Merge result files of separate harness invocations (one persona each,
    typically) into one scorecard and recompute the global checks. The last
    file wins when a persona appears more than once."""
    by_persona: dict[str, PersonaScore] = {}
    meta: dict[str, Any] = {"merged_from": [str(p) for p in paths]}
    for path in paths:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        runs = {r["persona"]: r for r in doc.get("runs") or []}
        for item in doc.get("personas") or []:
            run = runs.get(item["persona"])
            if run is None:
                continue
            try:
                verdict = JudgeVerdict.model_validate(item["verdict"]) if item.get("verdict") else None
            except ValueError:  # produced by an older judge schema: re-judge with --rejudge
                verdict = None
            score = PersonaScore(
                run=_run_from_dict(run),
                checks=[CheckResult(**c) for c in item.get("checks") or []],
                verdict=verdict,
                judge_error=item.get("judge_error"),
            )
            if rescore:
                _rescore(score, doc.get("meta") or {})
            by_persona[item["persona"]] = score
        meta.setdefault("round", (doc.get("meta") or {}).get("round"))
    order = {key: i for i, key in enumerate(SCORING_ORDER)}
    personas = sorted(by_persona.values(), key=lambda p: (order.get(p.run.persona, 99), p.run.persona))
    card = Scorecard(personas=personas, meta=meta)
    card.global_checks.append(scoring_order_check(personas))
    card.global_checks.append(latency_check(card))
    return card


__all__ = [
    "PersonaScore",
    "Scorecard",
    "latency_check",
    "load_results",
    "scoring_order_check",
    "write_outputs",
]
