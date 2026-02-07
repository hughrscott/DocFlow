"""Integration tests for the proposal accept/modify/reject workflow."""

import io
import os
import tempfile
import uuid
import yaml

from fastapi.testclient import TestClient
from database.database import init_db, SessionLocal
from database.models import (
    Document, SubDocument, FilingProposal, LearningCorrection, Page,
)
from config.settings import settings
from main import app


def _setup_temp_dirs():
    """Create temp directories for test."""
    tmpdir = tempfile.mkdtemp()
    docs_dir = os.path.join(tmpdir, "documents")
    uploads_dir = os.path.join(tmpdir, "uploads")
    os.makedirs(docs_dir, exist_ok=True)
    os.makedirs(uploads_dir, exist_ok=True)
    return tmpdir, docs_dir, uploads_dir


def _create_test_data(db, docs_dir):
    """Insert a document with sub-documents and proposals for testing."""
    doc_id = str(uuid.uuid4())
    doc = Document(
        id=doc_id,
        original_filename="test.pdf",
        total_pages=3,
        status="awaiting_review",
        file_size_bytes=1000,
        pipeline_version=2,
        sub_document_count=2,
    )
    db.add(doc)
    db.flush()

    # Sub-document 1: pages 1-2
    sd1_id = str(uuid.uuid4())
    sd1 = SubDocument(
        id=sd1_id,
        document_id=doc_id,
        start_page=1,
        end_page=2,
        page_count=2,
        document_type="bank_statement",
        document_category="Banking",
        institution="PNC",
        confidence_score=0.9,
        proposed_folder="Banking/PNC",
        proposed_filename="PNC-Statement-2025-01.pdf",
        status="proposed",
    )
    db.add(sd1)

    # Sub-document 2: page 3
    sd2_id = str(uuid.uuid4())
    sd2 = SubDocument(
        id=sd2_id,
        document_id=doc_id,
        start_page=3,
        end_page=3,
        page_count=1,
        document_type="letter",
        document_category="Correspondence",
        institution="IRS",
        confidence_score=0.85,
        proposed_folder="Correspondence/IRS",
        proposed_filename="IRS-Letter-2025-01.pdf",
        status="proposed",
    )
    db.add(sd2)
    db.flush()

    # Create page records with actual files on disk
    for page_num in range(1, 4):
        folder = "Banking/PNC" if page_num <= 2 else "Correspondence/IRS"
        filename = f"page-{page_num}.pdf"
        sub_doc_id = sd1_id if page_num <= 2 else sd2_id

        # Create the actual PDF file
        full_dir = os.path.join(docs_dir, folder)
        os.makedirs(full_dir, exist_ok=True)
        # Write a minimal PDF
        from PyPDF2 import PdfWriter
        writer = PdfWriter()
        writer.add_blank_page(72, 72)
        with open(os.path.join(full_dir, filename), "wb") as f:
            writer.write(f)

        page = Page(
            document_id=doc_id,
            page_number=page_num,
            document_type="bank_statement" if page_num <= 2 else "letter",
            confidence_score=0.9,
            extracted_metadata={},
            assigned_folder=folder,
            output_filename=filename,
            llm_provider_used="test",
            llm_model_used="test",
            sub_document_id=sub_doc_id,
        )
        db.add(page)

    # Filing proposals
    fp1 = FilingProposal(
        sub_document_id=sd1_id,
        proposed_folder="Banking/PNC",
        proposed_filename="PNC-Statement-2025-01.pdf",
        is_new_folder=False,
        rationale="Existing PNC folder",
        confidence=0.9,
    )
    fp2 = FilingProposal(
        sub_document_id=sd2_id,
        proposed_folder="Correspondence/IRS",
        proposed_filename="IRS-Letter-2025-01.pdf",
        is_new_folder=True,
        rationale="New folder for IRS correspondence",
        confidence=0.85,
    )
    db.add(fp1)
    db.add(fp2)
    db.commit()

    return doc_id, sd1_id, sd2_id


class TestProposalWorkflow:
    def test_get_proposals(self):
        """GET /proposals/{doc_id} returns sub-documents and proposals."""
        init_db()
        db = SessionLocal()
        tmpdir, docs_dir, uploads_dir = _setup_temp_dirs()
        orig_docs = settings.documents_dir
        settings.documents_dir = docs_dir
        try:
            doc_id, sd1_id, sd2_id = _create_test_data(db, docs_dir)
            db.close()

            client = TestClient(app)
            r = client.get(f"/api/v1/proposals/{doc_id}")
            assert r.status_code == 200
            data = r.json()
            assert data["document_id"] == doc_id
            assert len(data["sub_documents"]) == 2
            assert len(data["proposals"]) == 2
        finally:
            settings.documents_dir = orig_docs
            db.close()

    def test_get_proposals_not_found(self):
        init_db()
        client = TestClient(app)
        r = client.get("/api/v1/proposals/nonexistent-id")
        assert r.status_code == 404

    def test_accept_proposal(self):
        """Accept moves the file and updates status."""
        init_db()
        db = SessionLocal()
        tmpdir, docs_dir, uploads_dir = _setup_temp_dirs()
        orig_docs = settings.documents_dir
        orig_uploads = settings.uploads_temp_dir
        settings.documents_dir = docs_dir
        settings.uploads_temp_dir = uploads_dir
        try:
            doc_id, sd1_id, sd2_id = _create_test_data(db, docs_dir)
            db.close()

            client = TestClient(app)
            # Accept the single-page sub-document (page 3)
            r = client.post(f"/api/v1/proposals/sub_documents/{sd2_id}/accept")
            assert r.status_code == 200
            data = r.json()
            assert data["success"] is True

            # Verify status updated
            db2 = SessionLocal()
            sd = db2.query(SubDocument).filter(SubDocument.id == sd2_id).first()
            assert sd.status == "filed"
            assert sd.final_folder == "Correspondence/IRS"
            db2.close()
        finally:
            settings.documents_dir = orig_docs
            settings.uploads_temp_dir = orig_uploads

    def test_modify_proposal(self):
        """Modify changes folder/filename and records learning correction."""
        init_db()
        db = SessionLocal()
        tmpdir, docs_dir, uploads_dir = _setup_temp_dirs()
        orig_docs = settings.documents_dir
        orig_uploads = settings.uploads_temp_dir
        settings.documents_dir = docs_dir
        settings.uploads_temp_dir = uploads_dir
        try:
            doc_id, sd1_id, sd2_id = _create_test_data(db, docs_dir)
            db.close()

            client = TestClient(app)
            r = client.post(
                f"/api/v1/proposals/sub_documents/{sd2_id}/modify",
                json={
                    "folder": "Taxes/IRS",
                    "filename": "IRS-Notice-2025.pdf",
                    "rationale": "This is actually a tax notice",
                },
            )
            assert r.status_code == 200

            # Verify learning correction created
            db2 = SessionLocal()
            corrections = db2.query(LearningCorrection).filter(
                LearningCorrection.sub_document_id == sd2_id
            ).all()
            assert len(corrections) == 1
            assert corrections[0].actual_folder == "Taxes/IRS"
            assert corrections[0].user_rationale == "This is actually a tax notice"
            db2.close()
        finally:
            settings.documents_dir = orig_docs
            settings.uploads_temp_dir = orig_uploads

    def test_reject_proposal(self):
        """Reject records reason and doesn't move the file."""
        init_db()
        db = SessionLocal()
        tmpdir, docs_dir, uploads_dir = _setup_temp_dirs()
        orig_docs = settings.documents_dir
        settings.documents_dir = docs_dir
        try:
            doc_id, sd1_id, sd2_id = _create_test_data(db, docs_dir)
            db.close()

            client = TestClient(app)
            r = client.post(
                f"/api/v1/proposals/sub_documents/{sd2_id}/reject",
                json={"reason": "Wrong document, need to rescan"},
            )
            assert r.status_code == 200

            db2 = SessionLocal()
            sd = db2.query(SubDocument).filter(SubDocument.id == sd2_id).first()
            assert sd.status == "rejected"

            corrections = db2.query(LearningCorrection).filter(
                LearningCorrection.sub_document_id == sd2_id
            ).all()
            assert len(corrections) == 1
            db2.close()
        finally:
            settings.documents_dir = orig_docs
