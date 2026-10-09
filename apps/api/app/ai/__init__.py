"""AI layer: resilient clients for the local model endpoints.

Domain code imports the protocols and result types from ``app.ai.base`` and
obtains the services with ``app.ai.registry.get_ai()``:

* ``llm`` (``chat`` / ``stream`` / ``structured``), routed by ``ModelTask``
  (``app.ai.router``) with circuit breakers and fallback (``app.ai.http``);
* ``embeddings`` (``embed_documents`` / ``embed_query``, Redis-cached),
  ``reranker`` (Qwen3-Reranker ``/score``), ``stt`` (Whisper), ``ocr``,
  ``tts`` (Kokoro / OpenAI-compatible / browser / mock).

Helpers: ``app.ai.structured`` (schemas for guided decoding, repair, the
streaming field extractor), ``app.ai.prompts`` (fairness rules, rendering),
``app.ai.visemes`` (lip-sync timelines), ``app.ai.mock`` (test doubles).
"""
