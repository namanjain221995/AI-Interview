"""Normalisation, section detection and chunking."""

from __future__ import annotations

import pytest

from app.services.resume.extraction import docx_text, pdf_text_pages
from app.services.resume.normalize import (
    chunk_sections,
    detect_sections,
    heading_section,
    meaningful_chars,
    normalize_text,
)
from tests.unit.resume import corpus


def test_normalize_bullets_hyphens_furniture_and_whitespace() -> None:
    raw = (
        "Jane Doe\r\n"
        "•  Built   infra-\nstructure for 3 teams\n"
        "(cid:127) Led migration\n"
        "▪ Reduced costs by 40%\n"
        "Page 2 of 3\n"
        "-\t Wrote Terraform\n\n\n\n"
        "ﬁnal ­touch\x07"
    )
    text = normalize_text(raw)
    lines = text.split("\n")
    assert lines[0] == "Jane Doe"
    assert "- Built infrastructure for 3 teams" in lines
    assert "- Led migration" in lines
    assert "- Reduced costs by 40%" in lines
    assert "- Wrote Terraform" in lines
    assert "Page 2 of 3" not in text
    assert "\n\n\n" not in text
    assert text.endswith("final touch")  # NFKC ligature, soft hyphen and BEL removed


def test_negative_numbers_are_not_bullets() -> None:
    assert normalize_text("-5 degrees") == "-5 degrees"


@pytest.mark.parametrize(
    ("line", "key"),
    [
        ("EXPERIENCE", "experience"),
        ("Work Experience:", "experience"),
        ("Professional Summary", "summary"),
        ("Technical Skills", "skills"),
        ("Licenses & Certifications", "certifications"),
        ("EDUCATION", "education"),
        ("Personal Projects", "projects"),
        ("Awards", "other"),
        ("Languages: Python, Go, Bash", None),
        ("Senior DevOps Engineer at Bluepeak Analytics", None),
        ("- Experience with Kubernetes", None),
    ],
)
def test_heading_detection(line: str, key: str | None) -> None:
    assert heading_section(line) == key


@pytest.mark.parametrize("name", ["jane_doe_devops.txt", "jane_doe_devops.pdf", "jane_doe_devops.docx"])
def test_sections_of_sample_resumes(name: str) -> None:
    data = corpus.sample(name)
    if name.endswith(".pdf"):
        raw = "\n\n".join(pdf_text_pages(data))
    elif name.endswith(".docx"):
        raw = docx_text(data)
    else:
        raw = data.decode()
    text = normalize_text(raw)
    sections = detect_sections(text)
    keys = [s.key for s in sections]
    assert keys == ["other", "summary", "experience", "projects", "skills", "certifications", "education"]
    experience = next(s for s in sections if s.key == "experience").text
    assert "Bluepeak Analytics" in experience and "Cobalt Retail Group" in experience
    assert "Kubernetes" in next(s for s in sections if s.key == "skills").text
    assert sections[0].heading is None and "jane.doe@example.com" in sections[0].text


def test_preamble_with_prose_is_a_summary() -> None:
    text = (
        "Senior engineer with a decade of experience building data platforms. Led teams of five. "
        "Designed streaming pipelines processing billions of events per day across three regions with strict SLOs.\n\n"
        "SKILLS\nPython, Kafka"
    )
    sections = detect_sections(text)
    assert [s.key for s in sections] == ["summary", "skills"]


def test_chunking_size_overlap_and_sections() -> None:
    text = normalize_text(corpus.sample("jane_doe_devops.txt").decode())
    sections = detect_sections(text)
    chunks = chunk_sections(sections, target=800, overlap=120)
    assert all(len(c.text) <= 800 for c in chunks)
    assert [c.index for c in chunks] == list(range(len(chunks)))
    assert {c.section for c in chunks} >= {"summary", "experience", "skills"}
    experience = [c for c in chunks if c.section == "experience"]
    assert len(experience) >= 2
    # Overlap: the next chunk starts with text from the end of the previous one.
    tail = experience[0].text[-60:]
    assert any(part.strip() and part.strip() in experience[1].text for part in tail.split("\n"))
    # Every meaningful character of the experience section is covered.
    joined = "\n".join(c.text for c in experience)
    for line in next(s for s in sections if s.key == "experience").text.split("\n"):
        if line.strip():
            assert line.strip() in joined


def test_giant_paragraph_is_split() -> None:
    text = ("word " * 2000).strip()
    chunks = chunk_sections(detect_sections(text), target=800, overlap=100)
    assert len(chunks) >= 10
    assert all(len(c.text) <= 800 for c in chunks)


def test_meaningful_chars() -> None:
    assert meaningful_chars("a1 -- ..") == 2
