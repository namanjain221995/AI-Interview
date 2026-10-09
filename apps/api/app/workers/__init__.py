"""Background job handlers, one module per domain.

``python -m app.worker`` imports every module of this package; handlers
register themselves with ``@job_handler("<type>")`` from ``app.core.queue``.
Handlers must be idempotent (a job can run more than once after a crash).
Modules may also register worker lifecycle hooks with
``app.core.lifecycle.on_startup(..., processes=("worker",))``.
"""
