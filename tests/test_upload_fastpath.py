import os
import tempfile
import shutil
from fastapi.testclient import TestClient
from main import app
from config.settings import settings


def test_upload_fastpath_background():
    client = TestClient(app)
    tmp_docs = tempfile.mkdtemp()
    tmp_up = tempfile.mkdtemp()
    try:
        # Point settings to temp dirs
        settings.documents_dir = tmp_docs
        settings.uploads_temp_dir = tmp_up

        # Use bundled test PDF
        pdf_path = os.path.join(os.path.dirname(__file__), '..', 'test_document.pdf')
        pdf_path = os.path.abspath(pdf_path)
        assert os.path.exists(pdf_path)

        with open(pdf_path, 'rb') as f:
            files = {'file': ('test_document.pdf', f, 'application/pdf')}
            r = client.post('/api/v1/documents/upload?analyze=false&dpi=120&background=true', files=files)
        assert r.status_code == 200
        doc_id = r.json()['document_id']
        # Poll a few times
        for _ in range(25):
            g = client.get(f'/api/v1/documents/{doc_id}')
            if g.status_code == 200 and g.json()['status'] in ('completed', 'failed'):
                break
        g = client.get(f'/api/v1/documents/{doc_id}')
        assert g.status_code == 200
        data = g.json()
        assert data['pages_done'] >= 1
        # Files should be created under temp docs dir
        assert os.path.isdir(tmp_docs)

        listing = client.get('/api/v1/documents?page=1&page_size=25')
        assert listing.status_code == 200
        items = listing.json().get('items', [])
        match = next((item for item in items if item['id'] == doc_id), None)
        assert match is not None, "Uploaded document missing from list response"
        assert match['pages_done'] == data['pages_done']
        assert 'pages_failed' in match
        assert 'last_error' in match
    finally:
        shutil.rmtree(tmp_docs, ignore_errors=True)
        shutil.rmtree(tmp_up, ignore_errors=True)
