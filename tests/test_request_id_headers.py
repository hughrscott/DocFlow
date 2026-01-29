from fastapi.testclient import TestClient
from main import app


def test_request_id_headers_present():
    client = TestClient(app)
    r = client.get('/api/v1/health')
    assert r.status_code == 200
    rid = r.headers.get('X-Request-ID')
    dur = r.headers.get('X-Response-Time-ms')
    assert rid and isinstance(rid, str) and len(rid) >= 8
    # duration should be a float-ish string
    assert dur and dur.replace('.', '', 1).isdigit()

