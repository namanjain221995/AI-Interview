"""Matching of expected-evidence topics against evidence (pure).

Topics come from three places with slightly different vocabularies:
question ``expected_evidence`` ("node count", "autoscaling"), round/plan focus
areas (``state_management``) and evidence dimensions written by the evaluator
(``availability``, ``cost_measurement``). Matching therefore works on
normalised content tokens with light stemming instead of exact strings.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from rapidfuzz import fuzz

_TOKEN = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    "a an and are as at be by for from how in into is it of on or the their to via vs was what when where which "
    "with without about across any each per use used using".split()
)
# Fuzzy similarity (0-100) at which a topic and a dimension are considered the same.
_FUZZY_MATCH = 88


def _stem(token: str) -> str:
    for suffix in ("ations", "ation", "ments", "ment", "ings", "ing", "ies", "es", "s"):
        if len(token) > len(suffix) + 2 and token.endswith(suffix):
            if suffix == "ies":
                return token[: -len(suffix)] + "y"
            return token[: -len(suffix)]
    return token


def content_tokens(text: str | None) -> frozenset[str]:
    """Lower-cased, stop-word-free, lightly stemmed tokens."""
    if not text:
        return frozenset()
    words = _TOKEN.findall(text.lower().replace("_", " "))
    return frozenset(_stem(w) for w in words if w not in _STOPWORDS)


def normalize_topic(text: str) -> str:
    return " ".join(sorted(content_tokens(text)))


def topic_matches(topic: str, dimension: str, text: str = "") -> bool:
    """Whether ``topic`` is covered by an evidence item with ``dimension``
    (and, optionally, its evidence ``text``)."""
    topic_tokens = content_tokens(topic)
    if not topic_tokens:
        return False
    dim_tokens = content_tokens(dimension)
    if dim_tokens and (topic_tokens <= dim_tokens or dim_tokens <= topic_tokens):
        return True
    if len(topic_tokens) == 1 and dim_tokens:
        # "scaling" is covered by "autoscaling", "monitor" by "monitoring_stack".
        (token,) = topic_tokens
        if len(token) >= 4 and any(token in d for d in dim_tokens):
            return True
    if dim_tokens and fuzz.ratio(normalize_topic(topic), normalize_topic(dimension)) >= _FUZZY_MATCH:
        return True
    return bool(text) and topic_tokens <= content_tokens(text)


def topic_covered(topic: str, items: Iterable[tuple[str, str]]) -> bool:
    """``items`` are ``(dimension, evidence_text)`` pairs."""
    return any(topic_matches(topic, dimension, text) for dimension, text in items)


def dedupe_topics(topics: Iterable[str]) -> list[str]:
    """Order-preserving de-duplication on the normalised form."""
    seen: set[str] = set()
    out: list[str] = []
    for topic in topics:
        if not topic or not topic.strip():
            continue
        key = normalize_topic(topic) or topic.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(topic.strip())
    return out


ACRONYMS = frozenset(
    "api apis aws gcp iam vpc sql nosql slo slos sla slas sli rpo rto ci cd dns tls ssl http https tcp udp "
    "cpu gpu ram k8s eks gke aks ecs ec2 s3 rds hpa vpa pdb oom ttl sre devops iac json yaml grpc rest ui ux "
    "ml ai llm etl cdn waf ddos jwt sso oauth rbac mfa kpi okr".split()
)
_SPECIAL = {"ci_cd": "CI/CD", "ci/cd": "CI/CD", "devops": "DevOps", "k8s": "K8s"}


def humanize_topic(topic: str | None) -> str:
    """Readable topic text ("change_safety" -> "change safety", "iam" -> "IAM")."""
    text = (topic or "").strip()
    if not text:
        return ""
    if text.lower() in _SPECIAL:
        return _SPECIAL[text.lower()]
    words = text.replace("_", " ").split()
    return " ".join(_SPECIAL.get(w.lower(), w.upper() if w.lower() in ACRONYMS else w) for w in words)
