"""Deterministic starter code from a language-neutral signature.

The implementation lives in :mod:`app.seed.starters` (it depends only on the
enums, so the catalog loader can generate starters for problems that omit
them without importing the coding service package). This module keeps the
public import path used by the coding service and the API.
"""

from __future__ import annotations

from app.seed.starters import FUNCTION_LANGUAGES, SUPPORTED_TYPES, starter_for, starters_for

__all__ = ["FUNCTION_LANGUAGES", "SUPPORTED_TYPES", "starter_for", "starters_for"]
