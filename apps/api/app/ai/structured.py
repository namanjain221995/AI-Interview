"""Structured outputs: schema preparation, parse/repair/validate, the retry
loop shared by the real and mock LLM clients, and incremental extraction of
one string field from JSON that is still being generated.

Pipeline for one model reply::

    validate JSON as-is
      -> strip <think> blocks, markdown fences and prose around the object
      -> json_repair (trailing commas, quotes, unclosed brackets, ...)
      -> safe schema fixes (truncate over-long strings/lists, drop unknown
         keys, clamp ge/le bounds, normalise enum spelling)
      -> on failure: ask again with the validation errors fed back
      -> fallback() if provided, otherwise AIOutputInvalid
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import json_repair
from pydantic import BaseModel, ValidationError

from app.ai.base import AIError, AIOutputInvalid, ChatMessage

_MAX_FEEDBACK_ERRORS = 8
_MAX_ECHO_CHARS = 6000

# ============================================================================
# Schema preparation
# ============================================================================


@lru_cache(maxsize=256)
def schema_for(model: type[BaseModel]) -> dict[str, Any]:
    """JSON schema for guided decoding. Pydantic emits properties in field
    order, which guided decoding preserves (``TurnDecision.speech`` first)."""
    return model.model_json_schema(mode="validation")


def response_format_for(model: type[BaseModel]) -> dict[str, Any]:
    return response_format({"name": model.__name__, "schema": schema_for(model)})


def response_format(json_schema: Mapping[str, Any]) -> dict[str, Any]:
    """Build the OpenAI ``response_format`` payload from either a wrapper
    ``{"name", "schema"[, "strict"]}`` or a bare JSON schema."""
    if "schema" in json_schema and isinstance(json_schema.get("schema"), Mapping):
        name = str(json_schema.get("name") or "response")
        schema = dict(json_schema["schema"])
        strict = bool(json_schema.get("strict", True))
    else:
        schema = dict(json_schema)
        name = str(schema.get("title") or "response")
        strict = True
    name = re.sub(r"[^A-Za-z0-9_-]", "_", name)[:64] or "response"
    return {"type": "json_schema", "json_schema": {"name": name, "schema": schema, "strict": strict}}


def describe_schema(model: type[BaseModel]) -> str:
    """Compact, human-readable field list for prompts (guided decoding does
    not show the schema to the model, so prompts must describe the fields)."""
    schema = schema_for(model)
    defs = schema.get("$defs", {})
    lines = ["{"]
    required = set(schema.get("required", []))
    for name, prop in schema.get("properties", {}).items():
        desc = _describe_type(prop, defs)
        note = prop.get("description")
        opt = "" if name in required else " (optional)"
        lines.append(f'  "{name}": {desc}{opt}' + (f" -- {note}" if note else ""))
    lines.append("}")
    return "\n".join(lines)


def _describe_type(prop: Mapping[str, Any], defs: Mapping[str, Any]) -> str:
    if "$ref" in prop:
        target = defs.get(prop["$ref"].split("/")[-1], {})
        if "enum" in target:
            return " | ".join(json.dumps(v) for v in target["enum"])
        return "object " + target.get("title", "")
    if "anyOf" in prop:
        parts = [_describe_type(p, defs) for p in prop["anyOf"]]
        return " or ".join(parts)
    if "enum" in prop:
        return " | ".join(json.dumps(v) for v in prop["enum"])
    kind = prop.get("type", "any")
    if kind == "array":
        inner = _describe_type(prop.get("items", {}), defs)
        bound = f", max {prop['maxItems']}" if "maxItems" in prop else ""
        return f"array of ({inner}){bound}"
    if kind == "string" and "maxLength" in prop:
        return f"string (max {prop['maxLength']} chars)"
    if kind in ("number", "integer") and ("minimum" in prop or "maximum" in prop):
        return f"{kind} {prop.get('minimum', '-inf')}..{prop.get('maximum', 'inf')}"
    return str(kind)


# ============================================================================
# Parsing and repair
# ============================================================================


class StructuredParseError(AIOutputInvalid):
    """One reply could not be turned into a valid instance. ``feedback`` is
    the compact error list that is sent back to the model on retry."""

    def __init__(self, message: str, *, feedback: str, raw: str = "") -> None:
        super().__init__(message)
        self.feedback = feedback
        self.raw = raw


@dataclass(slots=True)
class ParsedOutput[T: BaseModel]:
    value: T
    repaired: bool


_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"```(?:json|JSON)?\s*\n?(.*?)\n?\s*```", re.DOTALL)


def strip_wrappers(text: str) -> str:
    """Remove reasoning blocks, markdown fences and prose around the JSON."""
    cleaned = _THINK_BLOCK.sub("", text)
    lowered = cleaned.lower()
    if "<think>" in lowered:  # unterminated reasoning: keep what follows the JSON start, if any
        cleaned = cleaned[lowered.index("<think>") + len("<think>") :]
    if "</think>" in cleaned.lower():
        cleaned = cleaned[cleaned.lower().rindex("</think>") + len("</think>") :]
    fence = _FENCE.search(cleaned)
    if fence:
        cleaned = fence.group(1)
    cleaned = cleaned.strip()
    start = _first_json_start(cleaned)
    if start > 0:
        cleaned = cleaned[start:]
    end = _matching_end(cleaned)
    if end is not None:
        cleaned = cleaned[: end + 1]
    return cleaned.strip()


def _first_json_start(text: str) -> int:
    positions = [p for p in (text.find("{"), text.find("[")) if p >= 0]
    return min(positions) if positions else -1


def _matching_end(text: str) -> int | None:
    """Index of the bracket closing the value that starts at ``text[0]``."""
    if not text or text[0] not in "{[":
        return None
    depth = 0
    in_string = False
    escape = False
    for i, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                return i
    return None


def format_validation_error(error: ValidationError, limit: int = _MAX_FEEDBACK_ERRORS) -> str:
    lines = []
    for item in error.errors()[:limit]:
        loc = ".".join(str(part) for part in item.get("loc", ())) or "(root)"
        lines.append(f"- {loc}: {item.get('msg', 'invalid')}")
    extra = error.error_count() - limit
    if extra > 0:
        lines.append(f"- ... and {extra} more problems")
    return "\n".join(lines)


def parse_structured[T: BaseModel](text: str | None, schema: type[T]) -> ParsedOutput[T]:
    """Turn one model reply into a validated ``schema`` instance or raise
    :class:`StructuredParseError`."""
    raw = text or ""
    if not raw.strip():
        raise StructuredParseError("empty model output", feedback="- (root): the reply was empty", raw=raw)
    try:
        return ParsedOutput(schema.model_validate_json(raw), repaired=False)
    except ValidationError as exc:
        error: ValidationError = exc
    cleaned = strip_wrappers(raw)
    if cleaned and cleaned != raw:
        try:
            return ParsedOutput(schema.model_validate_json(cleaned), repaired=False)
        except ValidationError as exc:
            error = exc
    obj = _repair_json(cleaned or raw)
    if not isinstance(obj, dict):
        raise StructuredParseError(
            "model output is not a JSON object",
            feedback=format_validation_error(error) or "- (root): reply must be a single JSON object",
            raw=raw,
        )
    for _ in range(4):
        try:
            return ParsedOutput(schema.model_validate(obj), repaired=True)
        except ValidationError as exc:
            error = exc
            if not _apply_safe_fixes(obj, exc):
                break
    raise StructuredParseError(
        f"model output failed {schema.__name__} validation ({error.error_count()} errors)",
        feedback=format_validation_error(error),
        raw=raw,
    )


def validate_streamed[T: BaseModel](raw_text: str, schema: type[T]) -> ParsedOutput[T]:
    """End-of-stream validation of an object whose fields were consumed
    incrementally (raises AIOutputInvalid with the validation summary)."""
    return parse_structured(raw_text, schema)


def _repair_json(text: str) -> Any:
    try:
        obj = json_repair.loads(text)
    except Exception:  # noqa: BLE001 - json_repair raises assorted errors on garbage
        return None
    if isinstance(obj, list):
        dicts = [item for item in obj if isinstance(item, dict)]
        if len(dicts) == 1:
            return dicts[0]
        if dicts and len(dicts) == len(obj):
            return dicts[0]
    return obj


def _resolve(obj: Any, loc: Sequence[Any]) -> tuple[Any, Any] | None:
    """(container, key) addressed by a pydantic error location, or None."""
    current = obj
    for part in loc[:-1]:
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and isinstance(part, int) and 0 <= part < len(current):
            current = current[part]
        else:
            return None
    if not loc:
        return None
    key = loc[-1]
    if isinstance(current, dict) and key in current:
        return current, key
    if isinstance(current, list) and isinstance(key, int) and 0 <= key < len(current):
        return current, key
    return None


def _normalise_enum_text(value: str) -> str:
    return re.sub(r"[\s\-]+", "_", value.strip().lower())


def _apply_safe_fixes(obj: dict[str, Any], error: ValidationError) -> bool:
    """Apply loss-minimal fixes for common violations. Returns True when
    something changed (the caller re-validates)."""
    changed = False
    # Deletions shift list indexes: apply them last, deepest first.
    deletions: list[tuple[Any, Any]] = []
    for item in error.errors():
        loc = tuple(item.get("loc", ()))
        kind = item.get("type", "")
        ctx = item.get("ctx") or {}
        target = _resolve(obj, loc)
        if target is None:
            continue
        container, key = target
        value = container[key]
        if kind == "string_too_long" and isinstance(value, str) and "max_length" in ctx:
            container[key] = value[: int(ctx["max_length"])].rstrip()
            changed = True
        elif kind == "too_long" and isinstance(value, list) and "max_length" in ctx:
            container[key] = value[: int(ctx["max_length"])]
            changed = True
        elif kind == "extra_forbidden" and isinstance(container, dict):
            deletions.append((container, key))
        elif kind == "greater_than_equal" and isinstance(value, int | float) and "ge" in ctx:
            container[key] = ctx["ge"]
            changed = True
        elif kind == "less_than_equal" and isinstance(value, int | float) and "le" in ctx:
            container[key] = ctx["le"]
            changed = True
        elif kind == "enum" and isinstance(value, str):
            normalised = _normalise_enum_text(value)
            if normalised != value:
                container[key] = normalised
                changed = True
    for container, key in deletions:
        if key in container:
            del container[key]
            changed = True
    return changed


# ============================================================================
# Retry loop (shared by the real and the mock LLM clients)
# ============================================================================

GenerateFn = Callable[[list[ChatMessage], int], Awaitable[str]]


@dataclass(slots=True)
class StructuredOutcome[T: BaseModel]:
    value: T
    raw_text: str
    repaired: bool
    attempts: int
    used_fallback: bool
    error: str | None = None  # why the fallback was used


def feedback_message(error: StructuredParseError) -> ChatMessage:
    return {
        "role": "user",
        "content": (
            "Your previous reply could not be accepted because it does not match the required JSON schema.\n"
            f"Problems:\n{error.feedback}\n"
            "Reply again with ONLY one JSON object that satisfies the schema: no prose, no markdown fences."
        ),
    }


async def run_structured[T: BaseModel](
    schema: type[T],
    messages: Sequence[ChatMessage],
    generate: GenerateFn,
    *,
    max_attempts: int = 3,
    fallback: Callable[[], T] | None = None,
) -> StructuredOutcome[T]:
    """Generate -> parse/repair -> retry with feedback -> fallback.

    ``generate(conversation, attempt)`` returns the raw reply text. An
    ``AIError`` from it (endpoint down, timeout) ends the loop: with a
    ``fallback`` the fallback value is returned, otherwise the error
    propagates. Cancellation always propagates."""
    base = list(messages)
    conversation = list(base)
    attempts = max(1, max_attempts)
    last_raw = ""
    last_error: StructuredParseError | None = None
    for attempt in range(1, attempts + 1):
        try:
            raw = await generate(conversation, attempt)
        except AIError as exc:
            if fallback is not None:
                return StructuredOutcome(
                    fallback(), last_raw, False, attempt, True, f"{type(exc).__name__}: {exc}"
                )
            raise
        last_raw = raw
        try:
            parsed = parse_structured(raw, schema)
        except StructuredParseError as exc:
            last_error = exc
            conversation = [
                *base,
                {"role": "assistant", "content": raw[:_MAX_ECHO_CHARS]},
                feedback_message(exc),
            ]
            continue
        return StructuredOutcome(parsed.value, raw, parsed.repaired, attempt, False)
    message = f"{schema.__name__}: no valid output after {attempts} attempts"
    if last_error is not None:
        message += f": {last_error.feedback}"
    if fallback is not None:
        return StructuredOutcome(fallback(), last_raw, False, attempts, True, message)
    raise AIOutputInvalid(message)


# ============================================================================
# Incremental extraction of one string field from streaming JSON
# ============================================================================

_WS = " \t\r\n"
_SIMPLE_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
_SPECIAL = re.compile(r'["\\]')


class _S:
    START = 0
    KEY_OR_END = 1
    KEY = 2
    COLON = 3
    VALUE = 4
    TARGET = 5
    SKIP_STRING = 6
    SKIP_NESTED = 7
    SKIP_PRIMITIVE = 8
    AFTER_VALUE = 9
    END = 10


class _StringDecoder:
    """Incremental decoder for the body of a JSON string (after the opening
    quote). Handles escapes and surrogate pairs split across feeds."""

    __slots__ = ("_escape", "_hex", "_pending_high")

    def __init__(self) -> None:
        self._escape = False
        self._hex: str | None = None  # collected hex digits after "\u"
        self._pending_high: int | None = None

    def decode(self, text: str, start: int, out: list[str]) -> tuple[int, bool]:
        """Decode ``text[start:]`` into ``out`` up to the closing quote.
        Returns (next index, closed)."""
        i = start
        n = len(text)
        while i < n:
            if self._hex is not None:
                take = min(4 - len(self._hex), n - i)
                self._hex += text[i : i + take]
                i += take
                if len(self._hex) < 4:
                    return i, False
                digits, self._hex = self._hex, None
                try:
                    code = int(digits, 16)
                except ValueError:
                    self._flush_high(out)
                    out.append("�")
                    continue
                self._code_point(code, out)
                continue
            if self._escape:
                self._escape = False
                ch = text[i]
                i += 1
                if ch == "u":
                    self._hex = ""
                    continue
                self._flush_high(out)
                out.append(_SIMPLE_ESCAPES.get(ch, ch))
                continue
            match = _SPECIAL.search(text, i)
            end = match.start() if match else n
            if end > i:
                self._flush_high(out)
                out.append(text[i:end])
                i = end
                continue
            ch = text[i]
            i += 1
            if ch == "\\":
                self._escape = True
            else:  # closing quote
                self._flush_high(out)
                return i, True
        return i, False

    def _code_point(self, code: int, out: list[str]) -> None:
        if 0xD800 <= code <= 0xDBFF:
            self._flush_high(out)
            self._pending_high = code
        elif 0xDC00 <= code <= 0xDFFF:
            if self._pending_high is not None:
                combined = 0x10000 + ((self._pending_high - 0xD800) << 10) + (code - 0xDC00)
                self._pending_high = None
                out.append(chr(combined))
            else:
                out.append("�")
        else:
            self._flush_high(out)
            out.append(chr(code))

    def _flush_high(self, out: list[str]) -> None:
        if self._pending_high is not None:
            self._pending_high = None
            out.append("�")


class StreamingJsonFieldExtractor:
    """Feed JSON text deltas of an object being generated; get back the
    decoded text of one top-level string field as it streams.

    ``TurnDecision.speech`` is the first property, so the realtime layer can
    start TTS after a handful of tokens::

        extractor = StreamingJsonFieldExtractor("speech")
        async for delta in llm.stream(..., json_schema=schema):
            spoken = extractor.feed(delta)      # newly decoded speech text
            if spoken: send_to_tts(spoken)
            if extractor.done: ...              # closing quote seen
        decision = extractor.finalize(TurnDecision).value

    Only a top-level key matches (a nested ``"speech"`` or the text
    ``"speech"`` inside another string never does); the first occurrence
    wins. Anything before the first ``{`` (fences, a ``<think>`` block) is
    skipped.
    """

    def __init__(self, field: str = "speech") -> None:
        self.field = field
        self._raw: list[str] = []
        self._state = _S.START
        self._prefix: list[str] = []  # text before the object (to skip <think> blocks)
        self._key: list[str] = []
        self._key_decoder = _StringDecoder()
        self._current_key: str | None = None
        self._value_decoder = _StringDecoder()
        self._skip_escape = False
        self._nest_depth = 0
        self._nest_in_string = False
        self._out: list[str] = []
        self._found = False
        self._done = False

    # ---------------------------------------------------------------- state
    @property
    def found(self) -> bool:
        """The target key has been seen and its string value has started."""
        return self._found

    @property
    def done(self) -> bool:
        """The closing quote of the target value has been consumed."""
        return self._done

    @property
    def value(self) -> str:
        return "".join(self._out)

    @property
    def raw_text(self) -> str:
        return "".join(self._raw)

    @property
    def object_closed(self) -> bool:
        return self._state == _S.END

    def finalize[T: BaseModel](self, schema: type[T]) -> ParsedOutput[T]:
        """Validate the complete object (raises AIOutputInvalid)."""
        return validate_streamed(self.raw_text, schema)

    # ----------------------------------------------------------------- feed
    def feed(self, chunk: str) -> str:
        if not chunk:
            return ""
        self._raw.append(chunk)
        before = len(self._out)
        i = 0
        n = len(chunk)
        while i < n:
            state = self._state
            if state == _S.END:
                break
            if state == _S.TARGET:
                i, closed = self._value_decoder.decode(chunk, i, self._out)
                if closed:
                    self._done = True
                    self._state = _S.AFTER_VALUE
                continue
            if state == _S.KEY:
                i, closed = self._key_decoder.decode(chunk, i, self._key)
                if closed:
                    self._current_key = "".join(self._key)
                    self._state = _S.COLON
                continue
            ch = chunk[i]
            i += 1
            if state == _S.START:
                self._start(ch)
            elif state == _S.KEY_OR_END:
                if ch == '"':
                    self._key = []
                    self._key_decoder = _StringDecoder()
                    self._state = _S.KEY
                elif ch == "}":
                    self._state = _S.END
            elif state == _S.COLON:
                if ch == ":":
                    self._state = _S.VALUE
            elif state == _S.VALUE:
                if ch in _WS:
                    continue
                if ch == '"':
                    if self._current_key == self.field and not self._found:
                        self._found = True
                        self._value_decoder = _StringDecoder()
                        self._state = _S.TARGET
                    else:
                        self._skip_escape = False
                        self._state = _S.SKIP_STRING
                elif ch in "{[":
                    self._nest_depth = 1
                    self._nest_in_string = False
                    self._skip_escape = False
                    self._state = _S.SKIP_NESTED
                else:
                    self._state = _S.SKIP_PRIMITIVE
            elif state == _S.SKIP_STRING:
                if self._skip_escape:
                    self._skip_escape = False
                elif ch == "\\":
                    self._skip_escape = True
                elif ch == '"':
                    self._state = _S.AFTER_VALUE
            elif state == _S.SKIP_NESTED:
                self._skip_nested(ch)
            elif state == _S.SKIP_PRIMITIVE:
                if ch == ",":
                    self._state = _S.KEY_OR_END
                elif ch == "}":
                    self._state = _S.END
                elif ch in _WS:
                    self._state = _S.AFTER_VALUE
            elif state == _S.AFTER_VALUE:
                if ch == ",":
                    self._state = _S.KEY_OR_END
                elif ch == "}":
                    self._state = _S.END
        return "".join(self._out[before:]) if len(self._out) > before else ""

    def _start(self, ch: str) -> None:
        if ch != "{":
            self._prefix.append(ch)
            return
        prefix = "".join(self._prefix).lower()
        opened = prefix.rfind("<think>")
        if opened >= 0 and prefix.find("</think>", opened) < 0:
            self._prefix.append(ch)  # "{" inside an unterminated reasoning block
            return
        self._prefix = []
        self._state = _S.KEY_OR_END

    def _skip_nested(self, ch: str) -> None:
        if self._nest_in_string:
            if self._skip_escape:
                self._skip_escape = False
            elif ch == "\\":
                self._skip_escape = True
            elif ch == '"':
                self._nest_in_string = False
            return
        if ch == '"':
            self._nest_in_string = True
        elif ch in "{[":
            self._nest_depth += 1
        elif ch in "}]":
            self._nest_depth -= 1
            if self._nest_depth == 0:
                self._state = _S.AFTER_VALUE
