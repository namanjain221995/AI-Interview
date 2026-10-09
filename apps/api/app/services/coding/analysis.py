"""Lightweight structural analysis of candidate code (heuristic, per language).

Used by the code observer to notice *algorithmic* changes worth a question
("You've chosen a nested loop here. What is the current time complexity?",
"You changed the data structure to a hash map. Explain why.") and by the
evaluation prompt to describe how the solution evolved over time.

It is deliberately regex/indentation based: fast (< 1 ms for typical
solutions), dependency free, tolerant of code that does not compile, and
never executes anything. Comments and string literals are blanked first so
keywords inside them never count.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from app.domain.enums import CodeLanguage

BRACE_LANGUAGES = frozenset(
    {CodeLanguage.JAVASCRIPT, CodeLanguage.TYPESCRIPT, CodeLanguage.JAVA, CodeLanguage.GO, CodeLanguage.CPP}
)

_NOT_FUNCTIONS = frozenset(
    {
        "if",
        "for",
        "while",
        "switch",
        "catch",
        "return",
        "new",
        "else",
        "sizeof",
        "do",
        "try",
        "synchronized",
        "function",
        "typeof",
        "await",
        "with",
        "elif",
        "foreach",
        "until",
        "defer",
        "go",
        "select",
        "case",
    }
)


# =============================================================================
# Comment / string stripping (length and line preserving)
# =============================================================================


def _blank(out: list[str], start: int, end: int) -> None:
    for k in range(start, min(end, len(out))):
        if out[k] != "\n":
            out[k] = " "


def strip_c_like(code: str) -> str:
    """Blank // and /* */ comments and string/char/template literal contents."""
    out = list(code)
    i, n = 0, len(code)
    while i < n:
        if code.startswith("//", i):
            end = code.find("\n", i)
            end = n if end < 0 else end
            _blank(out, i, end)
            i = end
            continue
        if code.startswith("/*", i):
            end = code.find("*/", i + 2)
            end = n if end < 0 else end + 2
            _blank(out, i, end)
            i = end
            continue
        ch = code[i]
        if ch in ('"', "'", "`"):
            j = i + 1
            while j < n and code[j] != ch:
                if code[j] == "\\":
                    j += 1
                elif code[j] == "\n" and ch != "`":
                    break
                j += 1
            _blank(out, i + 1, j)
            i = j + 1
            continue
        i += 1
    return "".join(out)


def strip_python(code: str) -> str:
    """Blank # comments and string literal contents (incl. triple quotes)."""
    out = list(code)
    i, n = 0, len(code)
    while i < n:
        ch = code[i]
        if ch == "#":
            end = code.find("\n", i)
            end = n if end < 0 else end
            _blank(out, i, end)
            i = end
            continue
        if ch in ('"', "'"):
            triple = code.startswith(ch * 3, i)
            if triple:
                end = code.find(ch * 3, i + 3)
                end = n if end < 0 else end
                _blank(out, i + 3, end)
                i = end + 3
                continue
            j = i + 1
            while j < n and code[j] != ch and code[j] != "\n":
                if code[j] == "\\":
                    j += 1
                j += 1
            _blank(out, i + 1, j)
            i = j + 1
            continue
        i += 1
    return "".join(out)


def strip_sql(code: str) -> str:
    out = list(code)
    i, n = 0, len(code)
    while i < n:
        if code.startswith("--", i):
            end = code.find("\n", i)
            end = n if end < 0 else end
            _blank(out, i, end)
            i = end
            continue
        if code.startswith("/*", i):
            end = code.find("*/", i + 2)
            end = n if end < 0 else end + 2
            _blank(out, i, end)
            i = end
            continue
        if code[i] == "'":
            j = i + 1
            while j < n and code[j] != "'":
                j += 1
            _blank(out, i + 1, j)
            i = j + 1
            continue
        i += 1
    return "".join(out)


def strip_code(code: str, language: CodeLanguage | str) -> str:
    lang = CodeLanguage(language)
    if lang == CodeLanguage.PYTHON:
        return strip_python(code)
    if lang == CodeLanguage.SQL:
        return strip_sql(code)
    return strip_c_like(code)


# =============================================================================
# Features
# =============================================================================


@dataclass(frozen=True, slots=True)
class CodeFeatures:
    language: str
    loops: int = 0
    loop_depth: int = 0
    hash_map: bool = False
    hash_set: bool = False
    recursion: bool = False
    sorting: bool = False
    heap: bool = False
    binary_search: bool = False
    stack_or_queue: bool = False
    sql_join: bool = False
    sql_subquery: bool = False
    sql_group_by: bool = False
    sql_window: bool = False
    sql_cte: bool = False
    functions: list[str] = field(default_factory=list)
    non_empty_lines: int = 0

    @property
    def nested_loops(self) -> bool:
        return self.loop_depth >= 2

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["nested_loops"] = self.nested_loops
        return data

    def summary(self) -> str:
        """Short human-readable description for prompts and staff views."""
        parts: list[str] = []
        if self.language == CodeLanguage.SQL.value:
            for flag, label in (
                (self.sql_cte, "CTE"),
                (self.sql_join, "JOIN"),
                (self.sql_subquery, "subquery"),
                (self.sql_group_by, "GROUP BY"),
                (self.sql_window, "window function"),
            ):
                if flag:
                    parts.append(label)
            return ", ".join(parts) or "simple query"
        if self.loop_depth >= 2:
            parts.append(f"nested loops (depth {self.loop_depth})")
        elif self.loops:
            parts.append("single loop" if self.loops == 1 else f"{self.loops} sequential loops")
        for flag, label in (
            (self.hash_map, "hash map"),
            (self.hash_set, "hash set"),
            (self.recursion, "recursion"),
            (self.sorting, "sorting"),
            (self.heap, "heap"),
            (self.binary_search, "binary search"),
            (self.stack_or_queue, "stack/queue"),
        ):
            if flag:
                parts.append(label)
        return ", ".join(parts) or "no loops or special data structures"


_PATTERNS: dict[CodeLanguage, dict[str, list[str]]] = {
    CodeLanguage.PYTHON: {
        "hash_map": [
            r"\bdict\s*\(",
            r"\bdefaultdict\b",
            r"\bCounter\s*\(",
            r"\bOrderedDict\b",
            r"=\s*\{\s*\}",
            r"\{[^{}\n]*:[^{}\n]*\}",
        ],
        "hash_set": [r"\bset\s*\(", r"\bfrozenset\s*\(", r"=\s*\{[^:{}\n]+\}"],
        "sorting": [r"\bsorted\s*\(", r"\.sort\s*\("],
        "heap": [r"\bheapq\b", r"\bheappush\b", r"\bheappop\b"],
        "binary_search": [r"\bbisect\w*\b"],
        "stack_or_queue": [r"\bdeque\s*\(", r"\b(?:queue\.)?(?:Lifo|Simple|Priority)?Queue\s*\("],
    },
    CodeLanguage.JAVASCRIPT: {
        "hash_map": [r"\bnew\s+(?:Weak)?Map\b", r"=\s*\{\s*\}", r"\bObject\.create\s*\(\s*null\s*\)"],
        "hash_set": [r"\bnew\s+(?:Weak)?Set\b"],
        "sorting": [r"\.sort\s*\(", r"\.toSorted\s*\("],
        "heap": [r"\b(?:Min|Max)?Heap\b", r"\bPriorityQueue\b"],
        "binary_search": [],
        "stack_or_queue": [r"\.shift\s*\(\s*\)"],
    },
    CodeLanguage.JAVA: {
        "hash_map": [
            r"\b(?:HashMap|TreeMap|LinkedHashMap|ConcurrentHashMap|Map)\s*<",
            r"\bnew\s+(?:HashMap|TreeMap|LinkedHashMap)\b",
        ],
        "hash_set": [
            r"\b(?:HashSet|TreeSet|LinkedHashSet|Set)\s*<",
            r"\bnew\s+(?:HashSet|TreeSet|LinkedHashSet)\b",
        ],
        "sorting": [r"\b(?:Arrays|Collections)\.sort\s*\(", r"\.sort\s*\(", r"\.sorted\s*\("],
        "heap": [r"\bPriorityQueue\b"],
        "binary_search": [r"\bbinarySearch\s*\("],
        "stack_or_queue": [r"\b(?:ArrayDeque|Deque|Stack|Queue|LinkedList)\b"],
    },
    CodeLanguage.GO: {
        "hash_map": [r"\bmap\s*\[[^\]]+\]\s*(?!bool\b|struct\s*\{\s*\})[\w\[\]*]"],
        "hash_set": [r"\bmap\s*\[[^\]]+\]\s*(?:bool\b|struct\s*\{\s*\})"],
        "sorting": [r"\bsort\.\w+\s*\(", r"\bslices\.Sort\w*\s*\("],
        "heap": [r"\bheap\.(?:Init|Push|Pop|Fix)\b", r"\"container/heap\""],
        "binary_search": [r"\bsort\.Search\w*\s*\(", r"\bslices\.BinarySearch\w*\s*\("],
        "stack_or_queue": [r"\blist\.New\s*\("],
    },
    CodeLanguage.CPP: {
        "hash_map": [r"\b(?:std::)?(?:unordered_map|map|multimap)\s*<"],
        "hash_set": [r"\b(?:std::)?(?:unordered_set|set|multiset)\s*<"],
        "sorting": [r"\b(?:std::)?(?:sort|stable_sort|partial_sort|nth_element)\s*\("],
        "heap": [r"\b(?:std::)?priority_queue\s*<", r"\b(?:std::)?(?:make_heap|push_heap|pop_heap)\s*\("],
        "binary_search": [r"\b(?:std::)?(?:lower_bound|upper_bound|binary_search|equal_range)\s*\("],
        "stack_or_queue": [r"\b(?:std::)?(?:stack|queue|deque)\s*<"],
    },
}
_PATTERNS[CodeLanguage.TYPESCRIPT] = {
    **_PATTERNS[CodeLanguage.JAVASCRIPT],
    "hash_map": [*_PATTERNS[CodeLanguage.JAVASCRIPT]["hash_map"], r"\bRecord\s*<"],
}

_MANUAL_BINARY_SEARCH = re.compile(r"\bmid\w*\s*:?=\s*[^\n;]*(?://\s*2|>>\s*1|/\s*2)")
_PY_LOOP = re.compile(r"^\s*(?:async\s+)?(?:for|while)\b")
_PY_FOR = re.compile(r"\bfor\b")
_PY_DEF = re.compile(r"^(\s*)(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(")
_C_LOOP = re.compile(r"\b(?:for|while|do)\b|\.forEach\s*\(")
_GO_FUNC = re.compile(r"\bfunc\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*\(")
_JS_FUNC = re.compile(
    r"\bfunction\s*\*?\s*([A-Za-z_$][\w$]*)\s*\("
    r"|\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(?:async\s*)?(?:function\b|\([^)]*\)\s*(?::[^=]+)?=>|[A-Za-z_$][\w$]*\s*=>)"
)
_C_FUNC = re.compile(
    r"\b([A-Za-z_]\w*)\s*\([^;{}()]*(?:\([^;{}()]*\)[^;{}()]*)*\)\s*(?:const\s*)?(?:noexcept\s*)?(?:throws\s+[\w\s,.]+)?\{"
)
_SQL = {
    "sql_join": re.compile(r"\bjoin\b", re.IGNORECASE),
    "sql_subquery": re.compile(r"\(\s*select\b", re.IGNORECASE),
    "sql_group_by": re.compile(r"\bgroup\s+by\b", re.IGNORECASE),
    "sql_window": re.compile(r"\bover\s*\(", re.IGNORECASE),
    "sql_cte": re.compile(r"^\s*with\b", re.IGNORECASE | re.MULTILINE),
}


def _any(patterns: list[str], text: str) -> bool:
    return any(re.search(p, text, re.MULTILINE) for p in patterns)


def _python_loops(stripped: str) -> tuple[int, int]:
    stack: list[int] = []
    loops = depth = 0
    for raw in stripped.splitlines():
        line = raw.expandtabs(4)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        while stack and indent <= stack[-1]:
            stack.pop()
        fors = len(_PY_FOR.findall(line))
        if _PY_LOOP.match(line):
            loops += 1
            stack.append(indent)
            depth = max(depth, len(stack))
            extra = fors - (1 if line.lstrip().startswith(("for", "async for")) else 0)
        else:
            extra = fors
        if extra > 0:  # comprehension / generator loops
            loops += extra
            depth = max(depth, len(stack) + extra)
    return loops, depth


def _brace_loops(stripped: str, language: CodeLanguage) -> tuple[int, int]:
    starts = {m.start(): m.group(0) for m in _C_LOOP.finditer(stripped)}
    depth = paren = loops = max_depth = 0
    loop_stack: list[int] = []
    pending = False
    pending_paren = 0
    i, n = 0, len(stripped)
    while i < n:
        keyword = starts.get(i)
        if keyword is not None:
            if keyword == "while" and _is_do_while_tail(stripped, i):
                i += len(keyword)
                continue
            loops += 1
            current = len(loop_stack) + (1 if pending else 0) + 1
            max_depth = max(max_depth, current)
            pending = True
            pending_paren = paren
            i += len(keyword)
            continue
        ch = stripped[i]
        if ch == "(":
            paren += 1
        elif ch == ")":
            paren = max(0, paren - 1)
        elif ch == "{":
            depth += 1
            if pending:
                loop_stack.append(depth)
                pending = False
        elif ch == "}":
            while loop_stack and loop_stack[-1] >= depth:
                loop_stack.pop()
            depth = max(0, depth - 1)
        elif ch == ";" and pending and paren == pending_paren and language != CodeLanguage.GO:
            pending = False
        i += 1
    return loops, max_depth


def _is_do_while_tail(text: str, pos: int) -> bool:
    """``} while (cond);`` closing a do-loop is not a new loop."""
    before = text[:pos].rstrip()
    return before.endswith("}") and bool(re.match(r"while\s*\([^{}]*\)\s*;", text[pos:]))


def _python_functions(stripped: str) -> tuple[list[str], bool]:
    lines = stripped.splitlines()
    names: list[str] = []
    recursive = False
    for idx, line in enumerate(lines):
        match = _PY_DEF.match(line.expandtabs(4))
        if not match:
            continue
        indent, name = len(match.group(1)), match.group(2)
        names.append(name)
        call = re.compile(rf"(?<![\w.])(?:self\.|cls\.)?{re.escape(name)}\s*\(")
        for body_line in lines[idx + 1 :]:
            expanded = body_line.expandtabs(4)
            if expanded.strip() and len(expanded) - len(expanded.lstrip()) <= indent:
                break
            if call.search(body_line):
                recursive = True
                break
    return names, recursive


def _matching_brace(text: str, open_pos: int) -> int:
    depth = 0
    for k in range(open_pos, len(text)):
        if text[k] == "{":
            depth += 1
        elif text[k] == "}":
            depth -= 1
            if depth == 0:
                return k
    return len(text)


def _brace_functions(stripped: str, language: CodeLanguage) -> tuple[list[str], bool]:
    found: list[tuple[str, int]] = []  # (name, index of body "{")
    if language == CodeLanguage.GO:
        for m in _GO_FUNC.finditer(stripped):
            brace = stripped.find("{", m.end())
            if brace >= 0:
                found.append((m.group(1), brace))
    else:
        if language in (CodeLanguage.JAVASCRIPT, CodeLanguage.TYPESCRIPT):
            for m in _JS_FUNC.finditer(stripped):
                name = m.group(1) or m.group(2)
                brace = stripped.find("{", m.end() - 1 if stripped[m.end() - 1] == "{" else m.end())
                arrow_expr = stripped.find("\n", m.end())
                if brace >= 0 and (arrow_expr < 0 or brace <= arrow_expr or "function" in m.group(0)):
                    found.append((name, brace))
        for m in _C_FUNC.finditer(stripped):
            name = m.group(1)
            if name in _NOT_FUNCTIONS or any(name == f for f, _ in found):
                continue
            found.append((name, m.end() - 1))
    names: list[str] = []
    recursive = False
    for name, brace in found:
        if name in names:
            continue
        names.append(name)
        end = _matching_brace(stripped, brace)
        body = stripped[brace + 1 : end]
        if re.search(rf"(?<![\w$]){re.escape(name)}\s*\(", body):
            recursive = True
    return names, recursive


def analyze(code: str | None, language: CodeLanguage | str) -> CodeFeatures:
    """Structural features of ``code`` (never raises on malformed code)."""
    lang = CodeLanguage(language)
    text = code or ""
    non_empty = sum(1 for line in text.splitlines() if line.strip())
    try:
        stripped = strip_code(text, lang)
    except Exception:  # pragma: no cover - defensive: analysis must never break a session
        stripped = text
    if lang == CodeLanguage.SQL:
        sql_flags: dict[str, Any] = {key: bool(rx.search(stripped)) for key, rx in _SQL.items()}
        return CodeFeatures(language=lang.value, non_empty_lines=non_empty, **sql_flags)
    patterns = _PATTERNS[lang]
    if lang == CodeLanguage.PYTHON:
        loops, depth = _python_loops(stripped)
        functions, recursive = _python_functions(stripped)
    else:
        loops, depth = _brace_loops(stripped, lang)
        functions, recursive = _brace_functions(stripped, lang)
    flags = {key: _any(pats, stripped) for key, pats in patterns.items()}
    if not flags.get("binary_search") and _MANUAL_BINARY_SEARCH.search(stripped):
        flags["binary_search"] = True
    return CodeFeatures(
        language=lang.value,
        loops=loops,
        loop_depth=depth,
        recursion=recursive,
        functions=functions,
        non_empty_lines=non_empty,
        **flags,
    )


# =============================================================================
# Changes between two versions
# =============================================================================


@dataclass(frozen=True, slots=True)
class StructuralChange:
    key: str
    introduced: bool
    description: str
    focus: str = "approach"


_CHANGE_LABELS: dict[str, tuple[str, str, str]] = {
    # key: (introduced text, removed text, observer focus)
    "nested_loops": ("introduced a nested loop", "removed the nested loop", "complexity"),
    "hash_map": ("introduced a hash map", "stopped using a hash map", "approach"),
    "hash_set": ("introduced a hash set", "stopped using a hash set", "approach"),
    "recursion": ("switched to a recursive approach", "removed the recursion", "approach"),
    "sorting": ("added a sort", "removed the sort", "complexity"),
    "heap": ("introduced a heap / priority queue", "stopped using a heap", "approach"),
    "binary_search": ("introduced a binary search", "removed the binary search", "approach"),
    "stack_or_queue": ("introduced a stack or queue", "stopped using a stack or queue", "approach"),
    "sql_join": ("added a JOIN", "removed the JOIN", "approach"),
    "sql_subquery": ("added a subquery", "removed the subquery", "approach"),
    "sql_group_by": ("added a GROUP BY", "removed the GROUP BY", "approach"),
    "sql_window": ("added a window function", "removed the window function", "approach"),
    "sql_cte": ("added a CTE", "removed the CTE", "readability"),
}


def describe_changes(previous: CodeFeatures | None, current: CodeFeatures) -> list[StructuralChange]:
    """Algorithmic pattern changes from ``previous`` to ``current``."""
    if previous is None or previous.language != current.language:
        return []
    changes: list[StructuralChange] = []
    for key, (on, off, focus) in _CHANGE_LABELS.items():
        before = bool(getattr(previous, key))
        after = bool(getattr(current, key))
        if before != after:
            changes.append(
                StructuralChange(key=key, introduced=after, description=on if after else off, focus=focus)
            )
    return changes


def features_from_dict(data: dict[str, Any] | None) -> CodeFeatures | None:
    if not data:
        return None
    allowed = {f for f in CodeFeatures.__dataclass_fields__}
    try:
        return CodeFeatures(**{k: v for k, v in data.items() if k in allowed})
    except TypeError:
        return None
