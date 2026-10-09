"""Intake Interview module (``round_type: intake``): screening before the training program.

Pure foundations (no I/O beyond reading the kit YAML)::

    from app.services.intake.kit import load_intake_kit        # IntakeKit from samples/intake/intake_kit_v1.yaml
    from app.services.intake.reading import align_reading      # Drill A transcript-to-passage alignment
    from app.services.intake.scoring import weighted_total, decision_band, technical_reading_score

Submodules are imported explicitly (this package keeps no eager imports, so importing one part never
loads the others).
"""
