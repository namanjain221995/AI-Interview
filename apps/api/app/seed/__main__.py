"""Seed the database: ``uv run python -m app.seed [--core-only | --demo] [--print-credentials]``.

Steps (each in its own transaction, so a failing optional step never undoes
the core data):

1. ``core``    - demo organization + one account per staff role (always).
2. ``catalog`` - ``app.seed.catalog.seed_catalog`` when that module exists.
3. extra steps registered with ``app.seed.core.seed_step`` (any ``app.seed.*``
   module is imported so its decorators run).
4. ``demo``    - ``app.seed.demo.seed_demo`` and ``demo_only`` steps, with ``--demo``.

Every step is idempotent. Credentials are printed only with
``--print-credentials``.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from typing import Any

from app.core.config import get_settings
from app.core.lifecycle import import_submodules, optional_import
from app.core.logging import configure_logging, get_logger
from app.db.session import dispose_engine, session_scope
from app.seed.core import call_seed_fn, registered_steps, seed_core

log = get_logger("app.seed")

_CONVENTIONAL = (("catalog", "seed_catalog", False, 20), ("demo", "seed_demo", True, 90))


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.seed", description="Seed the interview platform database."
    )
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--core-only", action="store_true", help="only the organization and staff accounts")
    scope.add_argument("--demo", action="store_true", help="also load demo jobs and demo candidates with live interview invite links")
    parser.add_argument(
        "--print-credentials", action="store_true", help="print the seeded logins and password"
    )
    parser.add_argument(
        "--reset-passwords", action="store_true", help="reset seeded accounts to SEED_PASSWORD"
    )
    return parser.parse_args(argv)


def _collect_steps(*, demo: bool, strict: bool) -> list[tuple[str, Any]]:
    import_submodules("app.seed", strict=strict, skip=lambda name: name in ("core", "loaders"))
    steps: dict[str, tuple[int, Any]] = {}
    for module_name, fn_name, demo_only, order in _CONVENTIONAL:
        module = optional_import(f"app.seed.{module_name}", strict=strict)
        fn = getattr(module, fn_name, None) if module is not None else None
        if fn is not None and (demo or not demo_only):
            steps[module_name] = (order, fn)
    for step in registered_steps():
        if step.demo_only and not demo:
            continue
        if step.name in steps and steps[step.name][1] is step.fn:
            continue
        steps[step.name] = (step.order, step.fn)
    return [(name, fn) for name, (_, fn) in sorted(steps.items(), key=lambda item: (item[1][0], item[0]))]


def _summarize(value: Any) -> str:
    if isinstance(value, dict):
        counts = {k: (len(v) if isinstance(v, list | dict) else v) for k, v in value.items()}
        return ", ".join(f"{k}={v}" for k, v in list(counts.items())[:12])
    return "" if value is None else str(value)[:200]


async def run(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level, json_logs=False)
    strict = settings.app_env in ("production", "staging")
    try:
        async with session_scope() as db:
            context: dict[str, Any] = await seed_core(db, reset_passwords=args.reset_passwords)
        print(
            f"[core] organization {context['organization_name']} ({context['organization_slug']}) id={context['organization_id']}"
        )
        for role, email in context["user_emails"].items():
            print(f"[core]   {role:<12} {email}")
        if args.print_credentials:
            secret = settings.seed_password.get_secret_value() if settings.seed_password else ""
            print(f"[core]   password (all accounts): {secret}")
        else:
            print("[core]   passwords hidden (use --print-credentials)")

        if not args.core_only:
            for name, fn in _collect_steps(demo=args.demo, strict=strict):
                try:
                    async with session_scope() as db:
                        result = await call_seed_fn(fn, db, dict(context))
                except Exception as exc:
                    log.error(
                        "seed.step_failed", step=name, error=f"{type(exc).__name__}: {exc}", exc_info=exc
                    )
                    print(f"[{name}] FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
                    return 1
                if isinstance(result, dict):
                    context.update({k: v for k, v in result.items() if k not in context})
                print(f"[{name}] done {_summarize(result)}".rstrip())
        return 0
    finally:
        await dispose_engine()


def main(argv: Sequence[str] | None = None) -> None:
    sys.exit(asyncio.run(run(argv)))


if __name__ == "__main__":
    main()
