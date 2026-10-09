"""Language drivers: request -> runner job, runner report -> response."""

from __future__ import annotations

from ..config import Settings
from ..models import CodeLanguage
from .base import ContainerOutcome, Driver, Plan, Rejected
from .cpp import CppDriver
from .go import GoDriver
from .java import JavaDriver
from .node import JavaScriptDriver, TypeScriptDriver
from .python import PythonDriver, SqlDriver

DRIVER_CLASSES: dict[CodeLanguage, type[Driver]] = {
    CodeLanguage.PYTHON: PythonDriver,
    CodeLanguage.SQL: SqlDriver,
    CodeLanguage.JAVASCRIPT: JavaScriptDriver,
    CodeLanguage.TYPESCRIPT: TypeScriptDriver,
    CodeLanguage.JAVA: JavaDriver,
    CodeLanguage.GO: GoDriver,
    CodeLanguage.CPP: CppDriver,
}

ENTRY_CONVENTIONS: dict[CodeLanguage, str] = {
    CodeLanguage.PYTHON: "top-level function in snake_case (two_sum); a Solution class method is also accepted",
    CodeLanguage.JAVASCRIPT: (
        "top-level function in lowerCamelCase (twoSum); async functions and Solution class methods also work"
    ),
    CodeLanguage.TYPESCRIPT: "top-level function in lowerCamelCase (twoSum); types are stripped, not checked",
    CodeLanguage.JAVA: "public method in class Solution, lowerCamelCase (twoSum); arrays, boxed types and List/Set accepted",
    CodeLanguage.GO: "top-level func in package main, lowerCamelCase (twoSum)",
    CodeLanguage.CPP: "public method in class Solution, lowerCamelCase (twoSum); vectors may be taken by reference",
    CodeLanguage.SQL: "a single SELECT; each test runs it on a fresh in-memory SQLite database built from sql_setup + setup_sql",
}


def build_drivers(settings: Settings) -> dict[CodeLanguage, Driver]:
    return {language: cls(settings) for language, cls in DRIVER_CLASSES.items()}


__all__ = ["DRIVER_CLASSES", "ENTRY_CONVENTIONS", "ContainerOutcome", "Driver", "Plan", "Rejected", "build_drivers"]
