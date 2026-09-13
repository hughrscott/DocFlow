"""P02/P08: full pipelines share one gateway per job; local-only completes with zero calls."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import yaml
from pypdf import PdfWriter

from docflow.cli import _run_pipeline
from docflow.llm.gateway import Feature
from tests.privacy import fakes
from tests.privacy import sentinels as s

CLUSTER = json.dumps({"documents": [{"pages": [1, 2], "confidence": 0.9}]})
SUGGEST = json.dumps({"rule_id": None, "confidence": 0.4, "reasoning": "synthetic",
                      "suggested_directory": "Review/Synthetic",
                      "suggested_filename": "SyntheticLetter.pdf"})


def _pdf(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    for _ in range(2):
        writer.add_blank_page(width=612, height=792)
    with open(path, "wb") as fh:
        writer.write(fh)
    return path


@pytest.fixture()
def setup(tmp_path: Path, monkeypatch):
    from docflow.ingestion import loader
    from docflow.ocr import analyzer

    def local_pages(images):
        records = fakes.pages(len(images))
        for record in records:
            record.institution = record.account_hint = None
        return records

    monkeypatch.setattr(loader, "load_pdf", lambda path: ["page-image-1", "page-image-2"])
    monkeypatch.setattr(analyzer, "analyze_pages", local_pages)
    archive, inbox = tmp_path / "archive", tmp_path / "inbox"
    archive.mkdir()
    rules = tmp_path / "rules.md"
    rules.write_text(f"## {s.RULES_MD_NAME}\n- **Institution**: pnc\n"
                     f"- **File to**: {s.RULE_FILE_TO}\n- **Filename**: {s.RULE_TEMPLATE}\n")

    def write_config(**overrides) -> Path:
        config = {**s.synthetic_config(archive_root=str(archive)), "scan_watch_folder": str(inbox),
                  "rules_file": str(rules), "filing_rules": [], **overrides}
        path = tmp_path / "config.yaml"
        path.write_text(yaml.safe_dump(config))
        return path

    return archive, inbox, write_config


def _user_token(request) -> str:
    return fakes.data(request)["context"]["user"]["name"]


def test_cli_pipeline_shares_one_gateway_per_job(setup, intercept, network_attempts) -> None:
    _archive, inbox, write_config = setup
    config_path = write_config()
    jobs = []
    for name in ("first.pdf", "second.pdf"):
        intercept.requests.clear()
        intercept.responses = [CLUSTER, SUGGEST]
        _run_pipeline(_pdf(inbox / name), config_path)
        cluster, classify = intercept.requests
        assert [cluster.feature, classify.feature] == [Feature.CLUSTERING, Feature.CLASSIFICATION]
        assert cluster.job_id == classify.job_id
        for request in (cluster, classify):
            s.assert_no_sentinels(request.body)
        # the user's name gets the same token in both calls of one job
        assert _user_token(classify) in fakes.data(cluster)["pages"][0]["text"]
        jobs.append((cluster.job_id, _user_token(classify)))
    (first_job, first_token), (second_job, second_token) = jobs
    assert first_job != second_job and first_token != second_token
    assert network_attempts == []


def test_web_pipeline_shares_one_gateway_per_job(setup, intercept, monkeypatch) -> None:
    from docflow.web import app as web_app

    _archive, inbox, write_config = setup
    config = yaml.safe_load(write_config().read_text())
    monkeypatch.setattr(web_app, "_config", config)
    monkeypatch.setattr(web_app, "_processing_state", {"job": {}})
    intercept.responses = [CLUSTER, SUGGEST]
    asyncio.run(web_app._run_pipeline_async("job", _pdf(inbox / "web.pdf")))
    assert web_app._processing_state["job"]["status"] == "completed"
    cluster, classify = intercept.requests
    assert cluster.job_id == classify.job_id
    assert _user_token(classify) in fakes.data(cluster)["pages"][0]["text"]


def test_local_only_pipeline_completes_locally_with_zero_calls(
    setup, intercept, network_attempts, model_transport_calls
) -> None:
    archive, inbox, write_config = setup
    scan = _pdf(inbox / "private.pdf")
    _run_pipeline(scan, write_config(privacy_mode="local_only"))
    assert intercept.requests == [] and network_attempts == [] and model_transport_calls == []
    items = json.loads((archive / "review_queue.json").read_text())["items"]
    assert sorted(page for item in items for page in item["pages"]) == [1, 2]
    assert all(item["status"] == "pending" for item in items)
    assert not scan.exists() and list(inbox.glob("BeenOrganized*/private.pdf"))
