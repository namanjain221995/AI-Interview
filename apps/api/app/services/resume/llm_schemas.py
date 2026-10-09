"""Wire schemas for the resume model calls.

Guided JSON decoding on this deployment lets the model *skip optional
properties* (measured: with the contract ``RoleEntry`` every role came back
with ``start``/``end`` missing, so no experience could be computed). The
contract models in :mod:`app.schemas.ai` describe what we store and keep
their defaults; the models sent to the LLM are generated from them with every
field required (nullable fields stay nullable), so the decoder must emit each
one and the model has to decide explicitly. A few low-value fields stay
optional to save tokens, and education dates are dropped entirely: graduation
years are an age proxy (FAIRNESS_RULES) and are never extracted.

Constraints (lengths, ranges, enums) are copied from the contract, so a valid
wire object always converts losslessly with :func:`to_contract`.
"""

from __future__ import annotations

import copy
import functools
import operator
import types
import typing
from typing import Any, get_args, get_origin

from pydantic import BaseModel, ConfigDict, create_model
from pydantic_core import PydanticUndefined

from app.schemas.ai import (
    Achievement,
    CertificationEntry,
    ClaimToVerify,
    EducationEntry,
    Inconsistency,
    JobMatch,
    ProfileAnalysis,
    ProjectEntry,
    RecommendedTopic,
    RequirementMatch,
    ResumeExtraction,
    RoleEntry,
    SkillItem,
)

# Fields that may still be skipped (rarely stated, expensive to emit as null).
_OPTIONAL: dict[type[BaseModel], frozenset[str]] = {
    SkillItem: frozenset({"years", "last_used_year", "evidence"}),
    RoleEntry: frozenset({"location"}),
    ProjectEntry: frozenset({"role", "scale_indicators"}),
}
# Fields never requested from the model.
_DROPPED: dict[type[BaseModel], frozenset[str]] = {
    EducationEntry: frozenset({"start", "end"}),
}
_NESTED: tuple[type[BaseModel], ...] = (
    Achievement,
    RoleEntry,
    ProjectEntry,
    EducationEntry,
    CertificationEntry,
    SkillItem,
    Inconsistency,
    ClaimToVerify,
    RequirementMatch,
    JobMatch,
    RecommendedTopic,
)


def _substitute(annotation: Any, mapping: dict[type[BaseModel], type[BaseModel]]) -> Any:
    if isinstance(annotation, type) and annotation in mapping:
        return mapping[annotation]
    origin = get_origin(annotation)
    if origin is None:
        return annotation
    args = tuple(_substitute(arg, mapping) for arg in get_args(annotation))
    if origin in (types.UnionType, typing.Union):
        return functools.reduce(operator.or_, args)
    if origin is list:
        return list[args[0]]  # type: ignore[valid-type]
    if origin is dict:
        return dict[args[0], args[1]]  # type: ignore[valid-type]
    return origin[args]


def required_variant(
    model: type[BaseModel], mapping: dict[type[BaseModel], type[BaseModel]] | None = None
) -> type[BaseModel]:
    """Copy of ``model`` with every field required (see module docstring)."""
    mapping = mapping or {}
    optional = _OPTIONAL.get(model, frozenset())
    dropped = _DROPPED.get(model, frozenset())
    fields: dict[str, Any] = {}
    for name, info in model.model_fields.items():
        if name in dropped:
            continue
        field = copy.copy(info)
        if name not in optional:
            field.default = PydanticUndefined
            field.default_factory = None
        fields[name] = (_substitute(info.annotation, mapping), field)
    return create_model(  # type: ignore[call-overload,no-any-return]
        model.__name__,  # same name: the JSON schema / prompt reads naturally
        __config__=ConfigDict(extra="ignore", populate_by_name=True),
        __doc__=model.__doc__,
        **fields,
    )


@functools.cache
def _variants() -> dict[type[BaseModel], type[BaseModel]]:
    mapping: dict[type[BaseModel], type[BaseModel]] = {}
    for model in _NESTED:  # dependency order: leaves first
        mapping[model] = required_variant(model, mapping)
    mapping[ResumeExtraction] = required_variant(ResumeExtraction, mapping)
    mapping[ProfileAnalysis] = required_variant(ProfileAnalysis, mapping)
    return mapping


def extraction_wire_model() -> type[BaseModel]:
    return _variants()[ResumeExtraction]


def analysis_wire_model() -> type[BaseModel]:
    return _variants()[ProfileAnalysis]


def wire(model: type[BaseModel]) -> type[BaseModel]:
    """Wire variant of a contract model (nested or top-level)."""
    return _variants()[model]


_NULL_STRINGS = frozenset({"null", "none", "n/a", "na", "unknown"})


def _allows_none(annotation: Any) -> bool:
    return annotation is None or type(None) in get_args(annotation)


def _model_type(annotation: Any) -> type[BaseModel] | None:
    """The pydantic model inside ``X``, ``list[X]``, ``X | None`` ... (if any)."""
    stack = [annotation]
    while stack:
        current = stack.pop()
        if isinstance(current, type) and issubclass(current, BaseModel):
            return current
        stack.extend(get_args(current))
    return None


def _clean_nulls(data: Any, model: type[BaseModel]) -> Any:
    """Guided decoding sometimes emits the string "null" for a nullable field:
    turn such strings into None (only where the contract allows None)."""
    if not isinstance(data, dict):
        return data
    out = dict(data)
    for name, info in model.model_fields.items():
        value = out.get(name)
        if isinstance(value, str):
            if value.strip().lower() in _NULL_STRINGS and _allows_none(info.annotation):
                out[name] = None
            continue
        nested = _model_type(info.annotation)
        if nested is None:
            continue
        if isinstance(value, list):
            out[name] = [_clean_nulls(item, nested) for item in value]
        elif isinstance(value, dict):
            out[name] = _clean_nulls(value, nested)
    return out


def to_contract[T: BaseModel](value: BaseModel, contract: type[T]) -> T:
    return contract.model_validate(_clean_nulls(value.model_dump(mode="python"), contract))
