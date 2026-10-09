"""Resume pipeline: upload validation, extraction/OCR, chunks + embeddings,
profile generation and job match.

Entry point for other modules: ``app.services.resume.service.get_resume_service()``
(implements :class:`app.services.resume.contracts.ResumeService`). Background
jobs: ``resume.process`` and ``profile.generate`` (``app/workers/resume.py``).
"""
