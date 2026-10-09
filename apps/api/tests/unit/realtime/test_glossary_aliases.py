"""Exact alias corrections in the realtime glossary (live intake 2026-10-10: "we use the CACD pipeline" was echoed
back by the interviewer as "the CACD pipeline"; "TechSara" / "LLM" came back spelled out)."""

from __future__ import annotations

import pytest

from app.realtime.glossary import Glossary, apply_aliases


@pytest.mark.parametrize(
    ("raw", "fixed"),
    [
        (
            "on the ITplace platform and we use the CACD pipeline for it.",
            "on the ITplace platform and we use the CI/CD pipeline for it.",
        ),
        ("We set up CICD with Jenkins.", "We set up CI/CD with Jenkins."),
        ("our CI CD pipeline", "our CI/CD pipeline"),
        ("a CI-CD workflow", "a CI/CD workflow"),
        ("our C I C D pipeline", "our CI/CD pipeline"),
        ("the see icd pipeline", "the CI/CD pipeline"),
        ("we fine tuned L L Ms on our data", "we fine tuned LLMs on our data"),
        ("an L L M based chatbot", "an LLM based chatbot"),
        ("an el el em based chatbot", "an LLM based chatbot"),
        (
            "so i will take local error which can answer based on the our organization's specific data set",
            "so i will take local LLM which can answer based on the our organization's specific data set",
        ),
        ("I work at Tech Sara as an AI engineer", "I work at TechSara as an AI engineer"),
        ("TechSarah AI", "TechSara AI"),
        ("Techsara", "TechSara"),
    ],
)
def test_known_misrecognitions_are_fixed(raw: str, fixed: str) -> None:
    text, corrections = Glossary([]).correct(raw)
    assert text == fixed
    assert corrections and all(c.score == 100.0 for c in corrections)


@pytest.mark.parametrize(
    "text",
    [
        "we use CI/CD already",
        "there was a local error in the build script",  # a real phrase outside an LLM context
        "the local error log showed a timeout",
        "I like cicada insects",
        "the CAD design was reviewed",
        "we deploy it to tech",  # too ambiguous to guess
        "I used Jenkins and LLM agents",
        "Sarah from tech support helped",
    ],
)
def test_real_words_are_left_alone(text: str) -> None:
    assert apply_aliases(text) == (text, [])
