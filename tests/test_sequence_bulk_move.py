import os
import shutil
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from main import app
from config.settings import settings
from database.database import SessionLocal, init_db
from database.models import Document, Page, ProcessingDecision, FileMoveAudit


def test_sequence_apply_and_revert(tmp_path):
    init_db()
    client = TestClient(app)

    tmp_docs = tmp_path / "docs"
    tmp_up = tmp_path / "uploads"
    tmp_docs.mkdir()
    tmp_up.mkdir()

    original_docs_dir = settings.documents_dir
    original_uploads_dir = settings.uploads_temp_dir

    doc_id = str(uuid.uuid4())
    page_ids: list[str] = []

    try:
        settings.documents_dir = str(tmp_docs)
        settings.uploads_temp_dir = str(tmp_up)

        session = SessionLocal()
        doc = Document(
            id=doc_id,
            original_filename="seq.pdf",
            total_pages=2,
            status="completed",
            file_size_bytes=1234,
        )
        session.add(doc)

        inbox_folder = Path(settings.documents_dir) / "Banking" / "Inbox"
        inbox_folder.mkdir(parents=True, exist_ok=True)

        for idx in (1, 2):
            page_id = str(uuid.uuid4())
            page_ids.append(page_id)
            filename = f"page{idx}.pdf"
            (inbox_folder / filename).write_text(f"page {idx}")

            page = Page(
                id=page_id,
                document_id=doc_id,
                page_number=idx,
                document_type="bank_statement",
                institution="PNC",
                confidence_score=0.91,
                extracted_metadata={"sequence_id": "seq-1"},
                assigned_folder="Banking/Inbox",
                output_filename=filename,
                processing_status="completed",
                processing_error=None,
                llm_provider_used="ollama",
                llm_model_used="llava",
            )
            session.add(page)

            decision = ProcessingDecision(
                page_id=page_id,
                proposed_folder="Banking/Personal/PNC",
                proposed_filename=f"PNC-Statement-{idx}.pdf",
                proposed_confidence=0.91,
            )
            session.add(decision)

        session.commit()
        session.close()

        resp = client.post(f"/api/v1/documents/{doc_id}/sequences/seq-1/apply_proposed")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["pages_moved"] == 2
        assert payload["missing_proposals"] == []
        assert payload["errors"] == []
        assert len(payload["audit_ids"]) == 2

        target_folder = Path(settings.documents_dir) / "Banking" / "Personal" / "PNC"
        for idx in (1, 2):
            target_file = target_folder / f"PNC-Statement-{idx}.pdf"
            assert target_file.exists()

        first_page_id = page_ids[0]
        revert = client.post(f"/api/v1/documents/pages/{first_page_id}/moves/revert", json={})
        assert revert.status_code == 200
        restored = Path(settings.documents_dir) / "Banking" / "Inbox" / "page1.pdf"
        assert restored.exists()

        session = SessionLocal()
        audit_record = session.query(FileMoveAudit).filter(FileMoveAudit.id == payload["audit_ids"][0]).first()
        assert audit_record is not None and audit_record.reverted is True
        session.close()

    finally:
        session = SessionLocal()
        session.query(FileMoveAudit).filter(FileMoveAudit.page_id.in_(page_ids)).delete(synchronize_session=False)
        session.query(ProcessingDecision).filter(ProcessingDecision.page_id.in_(page_ids)).delete(synchronize_session=False)
        session.query(Page).filter(Page.document_id == doc_id).delete(synchronize_session=False)
        session.query(Document).filter(Document.id == doc_id).delete(synchronize_session=False)
        session.commit()
        session.close()

        shutil.rmtree(tmp_docs, ignore_errors=True)
        shutil.rmtree(tmp_up, ignore_errors=True)
        settings.documents_dir = original_docs_dir
        settings.uploads_temp_dir = original_uploads_dir
