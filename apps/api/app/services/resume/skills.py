"""Skill normalisation: canonical names, de-duplication and category grouping.

Two mentions are the same skill when their canonical keys match (case,
punctuation and common aliases such as ``k8s`` -> ``kubernetes`` or
``postgres`` -> ``postgresql`` are folded). Merging keeps the highest claimed
level, the largest stated years, the latest last-used year and the first
evidence quote. Unknown or invalid categories are filled from a small lookup
of common technologies, otherwise ``other``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from app.schemas.ai import ProjectEntry, RoleEntry, SkillItem

CATEGORIES: tuple[str, ...] = (
    "programming_language",
    "framework",
    "database",
    "cloud",
    "devops",
    "ai_ml",
    "tool",
    "architecture",
    "soft",
    "other",
)
LEVEL_ORDER = {"unspecified": 0, "beginner": 1, "intermediate": 2, "advanced": 3, "expert": 4}

ALIASES: dict[str, str] = {
    "k8s": "kubernetes",
    "kube": "kubernetes",
    "postgres": "postgresql",
    "psql": "postgresql",
    "pg": "postgresql",
    "js": "javascript",
    "ecmascript": "javascript",
    "ts": "typescript",
    "golang": "go",
    "py": "python",
    "python3": "python",
    "amazon web services": "aws",
    "google cloud": "gcp",
    "google cloud platform": "gcp",
    "microsoft azure": "azure",
    "reactjs": "react",
    "react js": "react",
    "nodejs": "node js",
    "node": "node js",
    "vuejs": "vue",
    "nextjs": "next js",
    "c plus plus": "c++",
    "cpp": "c++",
    "csharp": "c#",
    "c sharp": "c#",
    "dotnet": ".net",
    "net": ".net",
    "net core": ".net",
    "tf": "terraform",
    "gha": "github actions",
    "ci cd": "ci/cd",
    "cicd": "ci/cd",
    "ml": "machine learning",
    "llms": "llm",
    "large language models": "llm",
    "amazon eks": "eks",
    "aws eks": "eks",
    "elastic kubernetes service": "eks",
    "mongo": "mongodb",
    "es": "elasticsearch",
    "rabbit mq": "rabbitmq",
    "sql server": "mssql",
    "ms sql": "mssql",
}

_CATEGORY_LOOKUP: dict[str, str] = {}
for _category, _names in {
    "programming_language": (
        "python",
        "go",
        "java",
        "javascript",
        "typescript",
        "c++",
        "c",
        "c#",
        "rust",
        "ruby",
        "php",
        "scala",
        "kotlin",
        "swift",
        "bash",
        "shell",
        "sql",
        "r",
        "elixir",
        "haskell",
        "perl",
        "lua",
        "dart",
    ),
    "framework": (
        "react",
        "vue",
        "angular",
        "svelte",
        "next js",
        "node js",
        "express",
        "django",
        "flask",
        "fastapi",
        "spring",
        "spring boot",
        "rails",
        ".net",
        "pytorch",
        "tensorflow",
        "celery",
        "sqlalchemy",
        "pydantic",
        "redux",
        "tailwind css",
        "jest",
        "playwright",
        "react testing library",
        "msw",
        "langchain",
    ),
    "database": (
        "postgresql",
        "mysql",
        "mssql",
        "oracle",
        "sqlite",
        "redis",
        "mongodb",
        "cassandra",
        "dynamodb",
        "elasticsearch",
        "opensearch",
        "clickhouse",
        "snowflake",
        "bigquery",
        "pgvector",
        "kafka",
    ),
    "cloud": (
        "aws",
        "gcp",
        "azure",
        "eks",
        "ec2",
        "ecs",
        "s3",
        "rds",
        "lambda",
        "cloudfront",
        "vpc",
        "iam",
        "gke",
        "aks",
        "cloudflare",
        "organizations",
    ),
    "devops": (
        "kubernetes",
        "docker",
        "helm",
        "terraform",
        "terragrunt",
        "ansible",
        "argo cd",
        "argo rollouts",
        "karpenter",
        "github actions",
        "gitlab ci",
        "jenkins",
        "ci/cd",
        "prometheus",
        "grafana",
        "loki",
        "opentelemetry",
        "pagerduty",
        "datadog",
        "istio",
        "linux",
        "nginx",
        "packer",
        "vault",
        "pulumi",
    ),
    "ai_ml": (
        "machine learning",
        "deep learning",
        "llm",
        "rag",
        "nlp",
        "vllm",
        "mlflow",
        "airflow",
        "scikit learn",
        "xgboost",
        "lightgbm",
        "hugging face",
        "transformers",
        "computer vision",
        "embeddings",
    ),
    "tool": ("git", "jira", "figma", "backstage", "postman", "vs code", "webpack", "vite", "storybook"),
    "architecture": (
        "microservices",
        "system design",
        "distributed systems",
        "event driven architecture",
        "rest",
        "graphql",
        "grpc",
        "domain driven design",
        "serverless",
    ),
    "soft": ("leadership", "mentoring", "communication", "stakeholder management", "agile", "scrum"),
}.items():
    for _name in _names:
        _CATEGORY_LOOKUP[_name] = _category

_NON_WORD = re.compile(r"[^a-z0-9+#./ ]+")
_SPACE = re.compile(r"\s+")
_PAREN = re.compile(r"\s*\([^)]*\)")


def canonical_key(name: str) -> str:
    """Comparison key: lower case, punctuation folded, aliases resolved."""
    text = name.strip().lower()
    text = _PAREN.sub("", text) if not text.startswith("(") else text
    text = text.replace("&", " and ").replace("_", " ").replace("-", " ")
    text = _NON_WORD.sub(" ", text)
    text = _SPACE.sub(" ", text).strip(" ./")
    text = re.sub(r"(?<=[a-z])\.(?=js\b)", " ", text)  # node.js -> node js
    if text.endswith(" js") and text[:-3] in ("react", "vue"):
        text = text[:-3]
    return ALIASES.get(text, text)


def category_for(name: str, declared: str | None = None) -> str:
    if declared in CATEGORIES and declared != "other":
        return declared  # type: ignore[return-value]
    return _CATEGORY_LOOKUP.get(canonical_key(name), declared if declared in CATEGORIES else "other")


def _level(value: str | None) -> str:
    value = (value or "unspecified").strip().lower()
    return value if value in LEVEL_ORDER else "unspecified"


def merge_skills(items: Iterable[SkillItem]) -> list[SkillItem]:
    """De-duplicate by canonical key, preserving first-seen order."""
    merged: dict[str, SkillItem] = {}
    for item in items:
        name = item.name.strip()
        if not name:
            continue
        key = canonical_key(name)
        if not key:
            continue
        category = category_for(name, item.category)
        level = _level(item.claimed_level)
        current = merged.get(key)
        if current is None:
            merged[key] = item.model_copy(
                update={"name": name[:80], "category": category, "claimed_level": level}
            )
            continue
        updates: dict[str, object] = {}
        if LEVEL_ORDER[level] > LEVEL_ORDER[_level(current.claimed_level)]:
            updates["claimed_level"] = level
        if item.years is not None and (current.years is None or item.years > current.years):
            updates["years"] = item.years
        if item.last_used_year is not None and (
            current.last_used_year is None or item.last_used_year > current.last_used_year
        ):
            updates["last_used_year"] = item.last_used_year
        if current.evidence is None and item.evidence:
            updates["evidence"] = item.evidence
        if current.category == "other" and category != "other":
            updates["category"] = category
        if updates:
            merged[key] = current.model_copy(update=updates)
    return list(merged.values())


def skills_from_roles(roles: Iterable[RoleEntry]) -> list[SkillItem]:
    """Technologies listed under roles, as skills with the role as evidence."""
    out: list[SkillItem] = []
    for role in roles:
        for tech in role.technologies:
            tech = tech.strip()
            if not tech or len(tech) > 80:
                continue
            out.append(
                SkillItem(
                    name=tech,
                    category=category_for(tech),
                    evidence=f"Listed for {role.title} at {role.company}"[:300],
                )
            )
    return out


def skills_from_projects(projects: Iterable[ProjectEntry]) -> list[SkillItem]:
    """Technologies listed under projects, as skills with the project as evidence."""
    out: list[SkillItem] = []
    for project in projects:
        for tech in project.technologies:
            tech = tech.strip()
            if not tech or len(tech) > 80:
                continue
            out.append(
                SkillItem(
                    name=tech,
                    category=category_for(tech),
                    evidence=f"Used in the project {project.name}"[:300],
                )
            )
    return out


# Vocabulary terms that are also ordinary words (or too short to find safely in
# prose): matched in free text only with this exact capitalisation, or never.
_PROSE_CASE_SENSITIVE: dict[str, str] = {
    "react": "React",
    "rust": "Rust",
    "swift": "Swift",
    "spring": "Spring",
    "express": "Express",
    "helm": "Helm",
    "vault": "Vault",
    "lambda": "Lambda",
    "rails": "Rails",
    "ruby": "Ruby",
    "dart": "Dart",
    "lua": "Lua",
    "celery": "Celery",
    "flask": "Flask",
    "airflow": "Airflow",
    "packer": "Packer",
    "jest": "Jest",
    "loki": "Loki",
    "scala": "Scala",
    "rag": "RAG",
}
_PROSE_NEVER = frozenset(
    {
        "go",
        "c",
        "r",
        "rest",
        "shell",
        "organizations",
        ".net",
        "net",
        "transformers",
        "embeddings",
        "iam",
        "vpc",
    }
)
_DISPLAY: dict[str, str] = {
    "aws": "AWS",
    "gcp": "GCP",
    "eks": "EKS",
    "ec2": "EC2",
    "ecs": "ECS",
    "s3": "S3",
    "rds": "RDS",
    "gke": "GKE",
    "aks": "AKS",
    "sql": "SQL",
    "llm": "LLM",
    "nlp": "NLP",
    "ci/cd": "CI/CD",
    "grpc": "gRPC",
    "graphql": "GraphQL",
    "postgresql": "PostgreSQL",
    "mysql": "MySQL",
    "mssql": "SQL Server",
    "mongodb": "MongoDB",
    "dynamodb": "DynamoDB",
    "clickhouse": "ClickHouse",
    "bigquery": "BigQuery",
    "opensearch": "OpenSearch",
    "github actions": "GitHub Actions",
    "gitlab ci": "GitLab CI",
    "opentelemetry": "OpenTelemetry",
    "pagerduty": "PagerDuty",
    "fastapi": "FastAPI",
    "sqlalchemy": "SQLAlchemy",
    "pytorch": "PyTorch",
    "tensorflow": "TensorFlow",
    "javascript": "JavaScript",
    "typescript": "TypeScript",
    "node js": "Node.js",
    "next js": "Next.js",
    "vllm": "vLLM",
    "mlflow": "MLflow",
    "xgboost": "XGBoost",
    "lightgbm": "LightGBM",
    "scikit learn": "scikit-learn",
    "hugging face": "Hugging Face",
    "argo cd": "Argo CD",
    "vs code": "VS Code",
    "tailwind css": "Tailwind CSS",
    "msw": "MSW",
    "pgvector": "pgvector",
    "c++": "C++",
    "c#": "C#",
    "php": "PHP",
}
_VOCAB_PATTERN: re.Pattern[str] | None = None


def _vocabulary_pattern() -> re.Pattern[str]:
    """One alternation over the skill vocabulary (longest first); spaces in a
    term also match ``.``/``-``/nothing (``node js`` finds ``Node.js``)."""
    global _VOCAB_PATTERN
    if _VOCAB_PATTERN is None:
        terms = {term for term in _CATEGORY_LOOKUP if _CATEGORY_LOOKUP[term] != "soft"}
        terms |= {alias for alias, target in ALIASES.items() if target in terms and len(alias) > 2}
        terms -= _PROSE_NEVER
        parts = []
        for term in sorted(terms, key=len, reverse=True):
            escaped = r"[\s.\-]?".join(re.escape(word) for word in term.split(" "))
            parts.append(escaped)
        _VOCAB_PATTERN = re.compile(r"(?<![\w+#.])(" + "|".join(parts) + r")(?![\w+#])", re.IGNORECASE)
    return _VOCAB_PATTERN


def find_vocabulary_skills(text: str) -> list[SkillItem]:
    """Technologies of the skill vocabulary named anywhere in ``text`` (prose:
    role bullets, summary, projects), de-duplicated by canonical key."""
    found: dict[str, SkillItem] = {}
    for match in _vocabulary_pattern().finditer(text):
        written = match.group(1)
        key = canonical_key(written)
        if not key or key in found:
            continue
        required_case = _PROSE_CASE_SENSITIVE.get(key)
        if required_case is not None and written != required_case:
            continue
        name = _DISPLAY.get(key) or (written if not written.islower() else written.title())
        found[key] = SkillItem(name=name[:80], category=category_for(name))
    return list(found.values())


def group_skills(items: Iterable[SkillItem]) -> dict[str, list[SkillItem]]:
    """Category -> skills, categories in ``CATEGORIES`` order, empty ones dropped."""
    grouped: dict[str, list[SkillItem]] = {category: [] for category in CATEGORIES}
    for item in items:
        grouped.setdefault(item.category if item.category in grouped else "other", []).append(item)
    return {category: skills for category, skills in grouped.items() if skills}


def flat_skill_names(grouped: dict[str, list[SkillItem]]) -> list[str]:
    return [skill.name for skills in grouped.values() for skill in skills]


def top_skills(
    grouped: dict[str, list[SkillItem]], limit: int = 12, *, context: str | None = None
) -> list[str]:
    """Most prominent skills: claimed level, stated years, how often ``context``
    (the summary and role text) names the skill, then listing order (categories in
    ``CATEGORIES`` order - stored JSONB does not keep the key order); soft skills last."""
    mentions = _mention_counter(context) if context else None
    ranked: list[tuple[tuple[int, float, int, int], str]] = []
    order = 0
    for category in sorted(
        grouped, key=lambda c: CATEGORIES.index(c) if c in CATEGORIES else len(CATEGORIES)
    ):
        for skill in grouped[category]:
            order += 1
            weight = (
                -LEVEL_ORDER.get(_level(skill.claimed_level), 0) - (0 if category != "soft" else -10),
                -(skill.years or 0.0),
                -(mentions(skill.name) if mentions else 0),
                order,
            )
            ranked.append((weight, skill.name))
    ranked.sort()
    seen: set[str] = set()
    out: list[str] = []
    for _, name in ranked:
        key = canonical_key(name)
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
        if len(out) >= limit:
            break
    return out


def _mention_counter(context: str) -> Any:
    lowered = context.lower()

    def count(name: str) -> int:
        term = name.strip().lower()
        if len(term) < 2:
            return 0
        return min(5, len(re.findall(rf"(?<![\w+#]){re.escape(term)}(?![\w+#])", lowered)))

    return count
