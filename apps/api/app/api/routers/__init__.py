"""REST routers, one module per resource.

``app.main`` imports every module of this package and includes its ``router``
(an ``APIRouter`` declaring its own sub-prefix and tags) under ``/api/v1``.
Modules named ``test_hooks``, ``test_hooks_*`` or ``*_test_hooks`` (or that set
``TEST_HOOKS = True``) are only included when ``ENABLE_TEST_HOOKS=true``.
Modules whose name starts with ``_`` are ignored.
"""
