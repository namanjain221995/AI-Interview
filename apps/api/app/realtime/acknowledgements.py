"""Pre-synthesized acknowledgement backchannels (REALTIME_DESIGN.md section 5).

A varied set ("Okay.", "I see.", "Right.", ...) is synthesized once per
(provider, voice) in the background - at API startup for the default voice
and when a session with another voice starts - so playing one at the end of
a candidate's answer costs no TTS latency. It masks model latency without
faking content: acknowledgements never carry meaning beyond "I heard you".

Selection never repeats the previous phrase. When a voice is not warmed yet
(or TTS is down) :meth:`AcknowledgementCache.pick` returns ``None`` and the
runtime simply skips the acknowledgement.

:meth:`AcknowledgementCache.warm_openers` pre-synthesizes phrases the
interviewer often starts a reply with ("Thanks, Jane.", "That makes sense.")
before the interview begins, only to fill the TTS service's own cache: when a
reply's first chunk is one of them its audio comes back in ~10 ms instead of
~0.4-0.6 s. Warming stops as soon as the interview starts so it never competes
with the interviewer's real speech for the TTS CPU.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Callable

from app.ai.base import TtsProvider, TtsResult
from app.core.logging import get_logger

log = get_logger(__name__)

# No "Mm-hmm.": the voice (Kokoro's phonemizer) spells it out ("em-em-hum"); so do "Mhm", "Mm hm" and
# "Uh-huh". Only words it pronounces naturally belong here.
ACKNOWLEDGEMENT_PHRASES: tuple[str, ...] = (
    "Okay.",
    "I see.",
    "Got it.",
    "Right.",
    "Thanks, that helps.",
)
# Backchannels that do not claim the floor: used while the answer may still
# continue (the end of turn is only provisional until the transcript is final).
SOFT_ACKNOWLEDGEMENTS: frozenset[str] = frozenset({"Okay.", "I see.", "Right."})
# Spoken by the runtime itself (never sent to the engine) when an answer could
# not be understood.
REPEAT_PROMPT = "Sorry, I didn't catch that. Could you say that again?"
# Said by the runtime when the reply is slow to start (Tier 1: no long silences); never sent to the engine.
FILLER_PHRASES: tuple[str, ...] = ("One moment.", "Let me think about that for a second.")
PROMPT_PHRASES: tuple[str, ...] = (REPEAT_PROMPT, *FILLER_PHRASES)
WARM_TIMEOUT_SECONDS = 20.0
# Frequent first sentences of interviewer replies (live transcripts, 2026-10-07).
OPENER_PHRASES: tuple[str, ...] = (
    "Thanks for walking me through that.",
    "That makes sense.",
    "No worries at all.",
    "Thanks for sharing that.",
    "That's helpful.",
    "Understood.",
    "Interesting.",
    "That gives a clear picture.",
)
NAME_OPENER_TEMPLATES: tuple[str, ...] = (
    "Thanks, {name}.",
    "Got it, {name}.",
    "No worries, {name}.",
    "Understood, {name}.",
    "Great, {name}.",
)


def opener_phrases(first_name: str | None) -> list[str]:
    """Openers to pre-synthesize for one candidate (name variants first)."""
    name = " ".join((first_name or "").split())[:40]
    named = [t.format(name=name) for t in NAME_OPENER_TEMPLATES] if name else []
    return [*named, *OPENER_PHRASES]


class AcknowledgementCache:
    def __init__(
        self, phrases: tuple[str, ...] = ACKNOWLEDGEMENT_PHRASES, prompts: tuple[str, ...] = PROMPT_PHRASES
    ) -> None:
        self.phrases = phrases
        self.prompts = prompts
        self._cache: dict[tuple[str, str], dict[str, TtsResult]] = {}
        self._warming: dict[tuple[str, str], asyncio.Task[int]] = {}
        self._openers_warmed: dict[tuple[str, str], set[str]] = {}
        self._opener_tasks: set[asyncio.Task[int]] = set()

    @staticmethod
    def _key(provider: TtsProvider, voice: str | None) -> tuple[str, str]:
        return (f"{getattr(provider, 'name', 'tts')}:{id(provider)}", voice or "")

    def ready(self, provider: TtsProvider, voice: str | None) -> int:
        return len(self._cache.get(self._key(provider, voice), {}))

    def warm_in_background(self, provider: TtsProvider, voice: str | None) -> asyncio.Task[int] | None:
        if getattr(provider, "name", "") == "browser":
            return None
        key = self._key(provider, voice)
        task = self._warming.get(key)
        if task is not None and not task.done():
            return task
        if len(self._cache.get(key, {})) >= len(self.phrases) + len(self.prompts):
            return None
        task = asyncio.create_task(self.warm(provider, voice), name=f"realtime.ack_warm.{voice}")
        self._warming[key] = task
        return task

    async def warm(self, provider: TtsProvider, voice: str | None) -> int:
        """Synthesize missing phrases sequentially (gentle on the shared TTS)."""
        key = self._key(provider, voice)
        bucket = self._cache.setdefault(key, {})
        for phrase in (*self.phrases, *self.prompts):
            if phrase in bucket:
                continue
            try:
                result = await asyncio.wait_for(
                    provider.synthesize(phrase, voice=voice), timeout=WARM_TIMEOUT_SECONDS
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - TTS down: try again with the next session
                log.info("realtime.ack.warm_failed", voice=voice, error=type(exc).__name__)
                break
            if result.pcm16:
                bucket[phrase] = result
        if bucket:
            log.info("realtime.ack.warmed", voice=voice, phrases=len(bucket))
        return len(bucket)

    def warm_openers(
        self,
        provider: TtsProvider,
        voice: str | None,
        phrases: list[str],
        *,
        stop: Callable[[], bool] | None = None,
    ) -> asyncio.Task[int] | None:
        """Synthesize ``phrases`` once per (provider, voice) in the background,
        one at a time, after the acknowledgements; ``stop()`` ends it early
        (the interview started). Results are not kept here: the point is the
        TTS service's cache."""
        if getattr(provider, "name", "") in ("browser", "mock"):
            return None
        key = self._key(provider, voice)
        done = self._openers_warmed.setdefault(key, set())
        todo = [p for p in dict.fromkeys(phrases) if p and p not in done]
        if not todo:
            return None

        async def run() -> int:
            acks = self._warming.get(key)
            if acks is not None and not acks.done():
                await asyncio.wait({acks})  # acknowledgements first (they are used at every turn)
            warmed = 0
            for phrase in todo:
                if stop is not None and stop():
                    break
                try:
                    await asyncio.wait_for(
                        provider.synthesize(phrase, voice=voice), timeout=WARM_TIMEOUT_SECONDS
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - TTS busy/down: the cache just stays cold
                    log.info("realtime.openers.warm_failed", voice=voice, error=type(exc).__name__)
                    break
                done.add(phrase)
                warmed += 1
            if warmed:
                log.info("realtime.openers.warmed", voice=voice, phrases=warmed)
            return warmed

        task = asyncio.create_task(run(), name=f"realtime.openers_warm.{voice}")
        self._opener_tasks.add(task)
        task.add_done_callback(self._opener_tasks.discard)
        return task

    def pick(
        self,
        provider: TtsProvider,
        voice: str | None,
        *,
        last: str | None = None,
        rng: random.Random | None = None,
        soft_only: bool = False,
    ) -> tuple[str, TtsResult] | None:
        bucket = self._cache.get(self._key(provider, voice))
        if not bucket:
            return None
        available = [p for p in self.phrases if p in bucket and (not soft_only or p in SOFT_ACKNOWLEDGEMENTS)]
        if not available:
            return None
        options = [p for p in available if p != last] or available
        phrase = (rng or random).choice(options)
        return phrase, bucket[phrase]

    def prompt(self, provider: TtsProvider, voice: str | None, phrase: str) -> TtsResult | None:
        """Pre-synthesized audio of one of ``prompts`` (None when not warm yet)."""
        return self._cache.get(self._key(provider, voice), {}).get(phrase)

    def clear(self) -> None:
        for task in [*self._warming.values(), *self._opener_tasks]:
            task.cancel()
        self._warming.clear()
        self._opener_tasks.clear()
        self._openers_warmed.clear()
        self._cache.clear()


_cache = AcknowledgementCache()


def get_acknowledgement_cache() -> AcknowledgementCache:
    return _cache
