"""Tests for the learning loop: corrections flow back into prompts."""

import uuid
from datetime import datetime

from database.database import init_db, SessionLocal
from database.models import SubDocument, Document, LearningCorrection
from services.rich_classifier import RichClassifier
from services.smart_filer import SmartFiler
from unittest.mock import MagicMock


def _mock_llm():
    mgr = MagicMock()
    mgr.get_provider_name.return_value = "claude"
    return mgr


class TestLearningLoop:
    def test_corrections_stored_and_loaded(self):
        """Corrections are persisted and can be queried."""
        init_db()
        db = SessionLocal()
        try:
            # Create parent records
            doc = Document(
                id=str(uuid.uuid4()),
                original_filename="test.pdf",
                total_pages=1,
                status="completed",
                file_size_bytes=100,
                pipeline_version=2,
            )
            db.add(doc)
            db.flush()

            sd = SubDocument(
                id=str(uuid.uuid4()),
                document_id=doc.id,
                start_page=1,
                end_page=1,
                page_count=1,
                document_type="bank_statement",
                institution="PNC",
                status="filed",
            )
            db.add(sd)
            db.flush()

            # Store a correction
            correction = LearningCorrection(
                sub_document_id=sd.id,
                proposed_document_type="letter",
                actual_document_type="bank_statement",
                proposed_folder="Correspondence/PNC",
                actual_folder="Banking/Personal/PNC",
                user_rationale="This is a bank statement, not a letter",
                correction_type="classification",
                key_signals={"institution": "PNC"},
            )
            db.add(correction)
            db.commit()

            # Query it back
            corrections = (
                db.query(LearningCorrection)
                .order_by(LearningCorrection.created_at.desc())
                .limit(5)
                .all()
            )
            assert len(corrections) >= 1
            latest = corrections[0]
            assert latest.actual_document_type == "bank_statement"
            assert latest.user_rationale == "This is a bank statement, not a letter"
        finally:
            db.close()

    def test_classifier_injects_corrections(self):
        """RichClassifier incorporates corrections into prompts."""
        classifier = RichClassifier(_mock_llm())
        corrections = [
            {
                "correction_type": "classification",
                "institution": "PNC",
                "proposed_document_type": "letter",
                "actual_document_type": "bank_statement",
                "user_rationale": "PNC statements look like letters but aren't",
            },
            {
                "correction_type": "classification",
                "institution": "Chase",
                "proposed_document_type": "generic",
                "actual_document_type": "bank_statement",
                "user_rationale": "Chase monthly statement",
            },
        ]
        context = classifier._inject_learning_context(corrections, "classification")
        assert "PAST CORRECTIONS" in context
        assert "PNC" in context
        assert "Chase" in context
        assert "bank_statement" in context

    def test_filer_injects_corrections(self):
        """SmartFiler incorporates corrections into filing prompts."""
        filer = SmartFiler(_mock_llm())
        corrections = [
            {
                "correction_type": "filing",
                "proposed_folder": "Banking/PNC",
                "actual_folder": "Banking/Personal/PNC-Checking",
                "user_rationale": "separate folders for checking vs savings",
                "proposed_document_type": "bank_statement",
            },
        ]
        context = filer._inject_filing_corrections(corrections, {})
        assert "PAST FILING CORRECTIONS" in context
        assert "PNC-Checking" in context
        assert "separate folders" in context

    def test_correction_used_count_can_increment(self):
        """used_in_prompts counter can be updated."""
        init_db()
        db = SessionLocal()
        try:
            doc = Document(
                id=str(uuid.uuid4()),
                original_filename="test.pdf",
                total_pages=1,
                status="completed",
                file_size_bytes=100,
                pipeline_version=2,
            )
            db.add(doc)
            db.flush()

            sd = SubDocument(
                id=str(uuid.uuid4()),
                document_id=doc.id,
                start_page=1,
                end_page=1,
                page_count=1,
                status="filed",
            )
            db.add(sd)
            db.flush()

            correction = LearningCorrection(
                sub_document_id=sd.id,
                correction_type="filing",
                proposed_folder="A",
                actual_folder="B",
                used_in_prompts=0,
            )
            db.add(correction)
            db.commit()

            # Simulate incrementing
            correction.used_in_prompts += 1
            db.commit()

            refreshed = db.query(LearningCorrection).filter(
                LearningCorrection.id == correction.id
            ).first()
            assert refreshed.used_in_prompts == 1
        finally:
            db.close()

    def test_multiple_correction_types(self):
        """Different correction types (classification, filing, splitting) stored correctly."""
        init_db()
        db = SessionLocal()
        try:
            doc = Document(
                id=str(uuid.uuid4()),
                original_filename="test.pdf",
                total_pages=1,
                status="completed",
                file_size_bytes=100,
                pipeline_version=2,
            )
            db.add(doc)
            db.flush()

            sd = SubDocument(
                id=str(uuid.uuid4()),
                document_id=doc.id,
                start_page=1,
                end_page=1,
                page_count=1,
                status="filed",
            )
            db.add(sd)
            db.flush()

            for ctype in ("classification", "filing", "splitting", "filename"):
                c = LearningCorrection(
                    sub_document_id=sd.id,
                    correction_type=ctype,
                )
                db.add(c)
            db.commit()

            all_corrections = db.query(LearningCorrection).filter(
                LearningCorrection.sub_document_id == sd.id
            ).all()
            types = {c.correction_type for c in all_corrections}
            assert types == {"classification", "filing", "splitting", "filename"}
        finally:
            db.close()
