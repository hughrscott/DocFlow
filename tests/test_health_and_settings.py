import io
import os
import shutil
import tempfile
import yaml

from fastapi.testclient import TestClient
from main import app
from database.database import init_db
from config import settings as cfg_module


def test_health_and_list():
    client = TestClient(app)
    # Health
    r = client.get('/api/v1/health')
    assert r.status_code == 200
    data = r.json()
    assert 'database_ok' in data and 'active_providers' in data

    # List
    r = client.get('/api/v1/documents?page=1&page_size=5')
    assert r.status_code == 200
    data = r.json()
    assert 'total' in data and 'items' in data


def test_settings_api_basic_auth():
    # Prepare temp yaml
    tmpdir = tempfile.mkdtemp()
    try:
        temp_yaml = os.path.join(tmpdir, 'llm.yaml')
        with open(temp_yaml, 'w') as f:
            yaml.safe_dump({
                'llm': {'vision_provider': 'ollama', 'text_provider': 'ollama'},
                'providers': {'ollama': {'enabled': True, 'base_url': 'http://localhost:11434'}}
            }, f)

        # Monkeypatch config path and enable auth for this test
        from config.settings import settings
        settings.llm_config_path = temp_yaml
        settings.auth_enabled = True
        settings.default_username = 'admin'
        settings.default_password = 'changeme'

        client = TestClient(app)

        # GET should not require auth
        r = client.get('/api/v1/settings')
        assert r.status_code == 200

        # POST without auth should fail
        r = client.post('/api/v1/settings', json={'vision_provider': 'claude'})
        assert r.status_code == 401

        # POST with basic auth
        r = client.post(
            '/api/v1/settings',
            json={'vision_provider': 'claude'},
            auth=('admin', 'changeme')
        )
        assert r.status_code == 200
        out = r.json()
        assert out['settings']['llm']['vision_provider'] == 'claude'
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

