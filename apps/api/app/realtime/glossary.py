"""Conservative correction of misrecognized technical terms in STT finals.

The deployed Whisper ignores the ``prompt`` field, so vocabulary biasing
never reaches the model (measured 2026-10-07: identical output with and
without a prompt) and technical names come back as similar English words or
fragments: "Carpenter" for Karpenter, "Ometheus" for Prometheus. This pass
fixes them before the engine sees the answer, against the session vocabulary
(job requirement names, round competencies, the candidate's profile skills and
technologies) and a built-in list of common technical terms:

* only words that look like names are touched - capitalised (Whisper
  capitalises what it takes for a name) and at least ``MIN_FUZZY_CHARS``
  long; inflections ("Terraformed") and ordinary short words never change;
* a word matches a term with rapidfuzz's normalised similarity >= 88 (>= 90
  for built-in terms the session vocabulary does not contain), and the
  lengths may differ by at most 2 characters;
* split terms are joined on an exact match ("Terra form" -> "Terraform");
* a word right after another capitalised word that is neither a term nor a
  common sentence opener is left alone: "John Carpenter" is a person;
* every correction is counted and logged with the terms only, never the
  transcript;
* a few known misrecognitions that fuzzy matching cannot reach (too short,
  spelled out, lower case or split) are fixed by exact aliases first
  (:data:`ALIASES`): "CACD" / "C I C D" / "see icd" -> CI/CD, "L L M" -> LLM,
  "Tech Sara" -> TechSara (live intake 2026-10-10: "we use the CACD pipeline"
  was echoed back by the interviewer). "local error" becomes "local LLM" only
  in an LLM context ("a local error which can answer ..."); elsewhere it is a
  real phrase and stays.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from rapidfuzz import fuzz

from app.core.logging import get_logger

log = get_logger(__name__)

MIN_FUZZY_CHARS = 5
SESSION_THRESHOLD = 88.0
BUILTIN_THRESHOLD = 90.0
MAX_LENGTH_DIFFERENCE = 2
MAX_TERM_WORDS = 4
MAX_TERM_CHARS = 40

BUILTIN_TERMS: tuple[str, ...] = (
    # containers / orchestration
    "Kubernetes", "Karpenter", "kubectl", "Helm", "Kustomize", "Docker", "containerd", "Istio",
    "Linkerd", "Envoy", "Cilium", "Calico", "Velero", "Argo CD", "Argo Rollouts", "Argo Workflows",
    "Flux", "Spinnaker", "Tekton", "OpenShift", "Rancher", "Nomad",
    # cloud
    "AWS", "EKS", "ECS", "EC2", "Fargate", "Lambda", "CloudFront", "CloudWatch", "CloudFormation",
    "CloudTrail", "DynamoDB", "Aurora", "Redshift", "Kinesis", "Route 53", "Azure", "AKS", "GCP",
    "GKE", "BigQuery", "Pub/Sub", "Cloud Run", "Terraform", "Terragrunt", "Pulumi", "Ansible",
    "Packer", "Vault", "Consul", "Crossplane",
    # observability
    "Prometheus", "Grafana", "Loki", "Tempo", "Thanos", "Alertmanager", "OpenTelemetry", "Jaeger",
    "Zipkin", "Datadog", "New Relic", "Splunk", "Elasticsearch", "Kibana", "Logstash", "Fluentd",
    "Fluent Bit", "PagerDuty", "Sentry",
    # CI/CD
    "Jenkins", "GitHub Actions", "GitLab CI", "CircleCI", "Buildkite", "Bamboo", "ArgoCD",
    # data / messaging
    "PostgreSQL", "Postgres", "MySQL", "MariaDB", "MongoDB", "Cassandra", "Redis", "Memcached",
    "Kafka", "RabbitMQ", "Pulsar", "Snowflake", "Databricks", "Airflow", "Spark", "Flink", "dbt",
    "ClickHouse", "Elastic", "OpenSearch", "Neo4j",
    # languages / frameworks
    "Python", "Golang", "TypeScript", "JavaScript", "Kotlin", "Rust", "Scala", "FastAPI", "Django",
    "Flask", "Celery", "SQLAlchemy", "pytest", "asyncio", "Node.js", "Express", "NestJS", "React",
    "Next.js", "Angular", "Vue", "Svelte", "Redux", "GraphQL", "gRPC", "Protobuf", "Spring Boot",
    "Hibernate", "Gradle", "Maven", "Webpack", "Vite",
    # ML
    "PyTorch", "TensorFlow", "scikit-learn", "Hugging Face", "LangChain", "LlamaIndex", "vLLM",
    "Kubeflow", "MLflow", "Ray", "ONNX", "CUDA", "Triton",
    # practices
    "GitOps", "DevOps", "SRE", "SLO", "SLI", "CI/CD", "Backstage", "cert-manager", "Cert Manager",
    "Nginx", "HAProxy", "Traefik", "Linux", "Bash", "systemd",
    # company
    "TechSara",
)  # fmt: skip

_LLM_CONTEXT = (
    r"(?=(?:\s+[\w']+){0,4}?\s+(?:answer|answers|answered|answering|respond|responds|generate|generates|chat|chats|"
    r"chatbot|model|models|prompt|prompts|fine[\s-]?tun\w*|rag|retrieval|trained|inference|tokens?|embeddings?)\b)"
)
#: (pattern, canonical) exact corrections applied before the fuzzy pass (case-insensitive, whole words).
ALIASES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), term)
    for pattern, term in (
        # CI/CD: "CACD", "CICD", "CI CD", "CI-CD", "C I C D", "C.I.C.D.", "see icd", "see I see D"
        (r"\bc\s?a\s?c\s?d\b", "CI/CD"),
        (r"\bci\s?[-/]?\s?cd\b", "CI/CD"),
        (r"\bc\.?\s?i\.?\s?c\.?\s?d\b\.?", "CI/CD"),
        (r"\bsee\s+i\.?\s?c\.?\s?d\b", "CI/CD"),
        (r"\bsee\s+i\s+see\s+d\b", "CI/CD"),
        # LLM spelled out: "L L M", "L.L.M.", "el el em" (+ plural)
        (r"\bl\.?\s+l\.?\s+m\b\.?(?!\w)", "LLM"),
        (r"\bl\.l\.m\b\.?", "LLM"),
        (r"\bel\s+el\s+em\b", "LLM"),
        (r"\bl\s+l\s+ms\b", "LLMs"),
        # "a local error which can answer ...": only in an LLM context
        (r"\blocal\s+errors\b" + _LLM_CONTEXT, "local LLMs"),
        (r"\blocal\s+error\b" + _LLM_CONTEXT, "local LLM"),
        # the company name: "Tech Sara", "Techsara", "Tech-Sarah", "Teksara"
        (r"\bte(?:ch|k)[\s-]?sar(?:a|ah)\b", "TechSara"),
    )
)

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9+#]*")
# Capitalised words that start sentences without being names.
COMMON_OPENERS: frozenset[str] = frozenset(
    {
        "a", "after", "also", "an", "and", "at", "because", "before", "but", "finally", "first", "for",
        "he", "here", "i", "in", "it", "later", "my", "next", "no", "now", "okay", "on", "our", "she",
        "so", "that", "the", "then", "there", "these", "they", "this", "those", "we", "well", "when",
        "while", "with", "yes", "you",
    }
)  # fmt: skip


def normalize(term: str) -> str:
    return re.sub(r"[^a-z0-9+#]", "", term.lower())


@dataclass(slots=True, frozen=True)
class Correction:
    original: str
    term: str
    score: float


def usable_terms(terms: Iterable[str]) -> list[str]:
    """Names worth matching: short (requirement phrases like "5+ years in ..."
    are not terms), deduplicated, order kept."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in terms:
        term = " ".join(str(raw).split())
        key = normalize(term)
        if not key or key in seen or len(term) > MAX_TERM_CHARS or len(term.split()) > MAX_TERM_WORDS:
            continue
        seen.add(key)
        out.append(term)
    return out


class Glossary:
    def __init__(self, session_terms: Sequence[str] = (), *, builtin: Sequence[str] = BUILTIN_TERMS) -> None:
        self.session = usable_terms(session_terms)
        session_keys = {normalize(t) for t in self.session}
        # normalized key -> (canonical spelling, from the session vocabulary?)
        self._terms: dict[str, tuple[str, bool]] = {}
        for term in self.session:
            self._terms.setdefault(normalize(term), (term, True))
        for term in usable_terms(builtin):
            key = normalize(term)
            self._terms.setdefault(key, (term, key in session_keys))
        self._fuzzy = [
            (key, term, own) for key, (term, own) in self._terms.items() if len(key) >= MIN_FUZZY_CHARS
        ]

    def correct(self, text: str) -> tuple[str, list[Correction]]:
        """``text`` with misrecognized terms replaced, and the corrections made."""
        text, corrections = apply_aliases(text)
        tokens = list(_WORD.finditer(text))
        if not tokens or not self._fuzzy:
            return text, corrections
        pieces: list[str] = []
        cursor = 0
        i = 0
        while i < len(tokens):
            token = tokens[i]
            joined = self._joined_term(text, tokens, i)
            if joined is not None:
                term, end_index = joined
                start, end = token.start(), tokens[end_index].end()
                pieces.append(text[cursor:start])
                pieces.append(term)
                corrections.append(Correction(text[start:end], term, 100.0))
                cursor = end
                i = end_index + 1
                continue
            match = None if self._after_name(text, tokens, i) else self._fuzzy_match(token.group(0))
            if match is not None:
                term, score = match
                pieces.append(text[cursor : token.start()])
                pieces.append(term)
                corrections.append(Correction(token.group(0), term, score))
                cursor = token.end()
            i += 1
        pieces.append(text[cursor:])
        return "".join(pieces), corrections

    def _joined_term(self, text: str, tokens: list[re.Match[str]], i: int) -> tuple[str, int] | None:
        """ "Terra form" -> "Terraform": 2-3 words separated by single spaces whose
        concatenation is exactly a known one-word term."""
        for n in (3, 2):
            j = i + n - 1
            if j >= len(tokens):
                continue
            gaps = [text[tokens[k].end() : tokens[k + 1].start()] for k in range(i, j)]
            if any(gap != " " for gap in gaps):
                continue
            key = normalize("".join(t.group(0) for t in tokens[i : j + 1]))
            entry = self._terms.get(key)
            if entry is not None and " " not in entry[0] and len(key) >= MIN_FUZZY_CHARS:
                return entry[0], j
        return None

    def _after_name(self, text: str, tokens: list[re.Match[str]], i: int) -> bool:
        """The previous word is capitalised, adjacent, not a known term and not a
        common sentence opener ("John Carpenter"): probably a person's name."""
        if i == 0:
            return False
        previous = tokens[i - 1]
        word = previous.group(0)
        if not word[0].isupper() or normalize(word) in self._terms or word.lower() in COMMON_OPENERS:
            return False
        return text[previous.end() : tokens[i].start()] == " "

    def _fuzzy_match(self, word: str) -> tuple[str, float] | None:
        key = normalize(word)
        if len(key) < MIN_FUZZY_CHARS or not word[0].isupper() or key in self._terms:
            return None
        best: tuple[str, float] | None = None
        for term_key, term, own in self._fuzzy:
            if abs(len(term_key) - len(key)) > MAX_LENGTH_DIFFERENCE:
                continue
            if term_key.startswith(key) or key.startswith(term_key):
                continue  # an inflection or a prefix of the term, not a misrecognition
            score = fuzz.ratio(key, term_key)
            if score >= (SESSION_THRESHOLD if own else BUILTIN_THRESHOLD) and (
                best is None or score > best[1]
            ):
                best = (term, score)
        return best


def apply_aliases(text: str) -> tuple[str, list[Correction]]:
    """The exact alias corrections (:data:`ALIASES`) of ``text``."""
    corrections: list[Correction] = []
    for pattern, term in ALIASES:

        def repl(match: re.Match[str], term: str = term) -> str:
            if match.group(0) == term:
                return term
            corrections.append(Correction(match.group(0), term, 100.0))
            return term

        text = pattern.sub(repl, text)
    return text, corrections


def correct_transcript(text: str, session_terms: Sequence[str] = ()) -> str:
    """Convenience wrapper (harnesses): the corrected text only."""
    return Glossary(session_terms).correct(text)[0]


def log_corrections(corrections: list[Correction], *, session_id: str) -> None:
    if corrections:
        log.info(
            "realtime.stt.glossary_corrections",
            session_id=session_id,
            count=len(corrections),
            terms=sorted({c.term for c in corrections}),
        )
        log.debug(
            "realtime.stt.glossary_detail",
            session_id=session_id,
            pairs=[(c.original, c.term, round(c.score)) for c in corrections],
        )
