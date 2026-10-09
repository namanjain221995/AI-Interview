"""Language-neutral function signatures (shared convention with the question bank).

``{"function_name": "two_sum", "params": [{"name": "nums", "type": "int[]"}, ...],
"return_type": "int[]", "comparison": "exact" | "unordered" | "float_tolerance"}``

Entry points: Python uses the snake_case name; JavaScript/TypeScript, Java,
C++ and Go use lowerCamelCase (``two_sum`` -> ``twoSum``). An optional
``"tolerance"`` (default 1e-6) applies to ``float_tolerance``; comparison
semantics are described in :mod:`sandbox_service.compare`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

SUPPORTED_TYPES = (
    "int",
    "long",
    "float",
    "bool",
    "string",
    "int[]",
    "long[]",
    "float[]",
    "string[]",
    "bool[]",
    "int[][]",
    "string[][]",
)
COMPARISONS = ("exact", "unordered", "float_tolerance")
DEFAULT_TOLERANCE = 1e-6
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


class SignatureError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Param:
    name: str
    type: str


@dataclass(frozen=True, slots=True)
class Signature:
    function_name: str
    params: tuple[Param, ...]
    return_type: str
    comparison: str
    tolerance: float

    @property
    def camel_name(self) -> str:
        return to_camel(self.function_name)

    @property
    def snake_name(self) -> str:
        return to_snake(self.function_name)

    def entry_candidates(self, primary: str) -> list[str]:
        """Names to try, the convention's name first, then common variants."""
        names = [primary, self.function_name, self.camel_name, self.snake_name, to_pascal(self.function_name)]
        seen: list[str] = []
        for name in names:
            if name not in seen:
                seen.append(name)
        return seen

    def to_payload(self) -> list[dict[str, str]]:
        return [{"name": p.name, "type": p.type} for p in self.params]


def to_camel(name: str) -> str:
    parts = [p for p in name.split("_") if p]
    if not parts:
        return name
    first = parts[0]
    head = first[0].lower() + first[1:]
    return head + "".join(p[0].upper() + p[1:] for p in parts[1:])


def to_snake(name: str) -> str:
    snake = re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", name)
    return re.sub(r"_+", "_", snake).lower().strip("_") or name


def to_pascal(name: str) -> str:
    camel = to_camel(name)
    return camel[:1].upper() + camel[1:]


def parse_signature(raw: dict[str, Any] | None) -> Signature:
    if not isinstance(raw, dict):
        raise SignatureError("tests mode requires a function signature")
    name = raw.get("function_name")
    if not isinstance(name, str) or not _IDENT.match(name):
        raise SignatureError("signature.function_name must be an identifier such as 'two_sum'")
    raw_params = raw.get("params", [])
    if not isinstance(raw_params, list) or len(raw_params) > 16:
        raise SignatureError("signature.params must be a list of at most 16 parameters")
    params = []
    for index, item in enumerate(raw_params):
        if not isinstance(item, dict):
            raise SignatureError(f"signature.params[{index}] must be an object with name and type")
        pname = item.get("name", f"arg{index}")
        ptype = item.get("type")
        if not isinstance(pname, str) or not _IDENT.match(pname):
            raise SignatureError(f"signature.params[{index}].name must be an identifier")
        if ptype not in SUPPORTED_TYPES:
            raise SignatureError(f"signature.params[{index}].type {ptype!r} is not one of {', '.join(SUPPORTED_TYPES)}")
        params.append(Param(pname, ptype))
    return_type = raw.get("return_type")
    if return_type not in SUPPORTED_TYPES:
        raise SignatureError(f"signature.return_type {return_type!r} is not one of {', '.join(SUPPORTED_TYPES)}")
    comparison = raw.get("comparison", "exact") or "exact"
    if comparison not in COMPARISONS:
        raise SignatureError(f"signature.comparison {comparison!r} is not one of {', '.join(COMPARISONS)}")
    tolerance = raw.get("tolerance", DEFAULT_TOLERANCE)
    if not isinstance(tolerance, (int, float)) or isinstance(tolerance, bool) or not 0 <= tolerance <= 1:
        raise SignatureError("signature.tolerance must be a number between 0 and 1")
    return Signature(name, tuple(params), return_type, comparison, float(tolerance))
