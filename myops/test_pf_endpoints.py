"""
Endpoint-shape tests for the Practice Fusion refresh API (myops/pf/).

These mock out pf.runner.run_refresh / get_appointment_row, so they exercise
only the FastAPI layer and the in-memory job store (myops/pf/jobs.py) — no
Playwright, no Chrome, no real Practice Fusion access required. Real
end-to-end browser behavior still needs the VM (see pf-soap-sync/README.md).
"""

import time
from unittest.mock import patch

from fastapi.testclient import TestClient

import pf.jobs as pf_jobs
from server import app

client = TestClient(app)


def setup_function():
    # jobs.py has no reset hook; clear its module-level dict directly between tests.
    pf_jobs._jobs.clear()


def _wait_for_terminal(job_id: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/refresh-jobs/{job_id}").json()
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not reach a terminal status within {timeout}s")


@patch("pf.runner.get_appointment_row")
@patch("pf.runner.run_refresh")
def test_refresh_accepted_and_processed(mock_run_refresh, mock_get_row):
    mock_run_refresh.return_value = {"command": "refresh", "returncode": 0, "stdout": "{}", "stderr": ""}
    mock_get_row.return_value = {"status": "processed", "message": "", "pdf_path": "/tmp/x.pdf"}

    resp = client.post("/appointments/row123/refresh", json={"requested_by": "tester"})
    assert resp.status_code == 202
    body = resp.json()
    assert body["appointment_pk"] == "row123"
    assert body["status"] == "queued"
    job_id = body["job_id"]

    job = _wait_for_terminal(job_id)
    assert job["status"] == "processed"
    assert job["pdf_uri"] == "/tmp/x.pdf"


@patch("pf.runner.get_appointment_row")
@patch("pf.runner.run_refresh")
def test_refresh_no_new_encounter(mock_run_refresh, mock_get_row):
    mock_run_refresh.return_value = {"command": "refresh", "returncode": 0, "stdout": "{}", "stderr": ""}
    mock_get_row.return_value = {
        "status": "ready",
        "status_reason": "waiting_for_encounter",
        "message": "",
    }

    resp = client.post("/appointments/row456/refresh", json={})
    job_id = resp.json()["job_id"]

    job = _wait_for_terminal(job_id)
    assert job["status"] == "no_new_encounter"
    assert job["pdf_uri"] is None


@patch("pf.runner.get_appointment_row")
@patch("pf.runner.run_refresh")
def test_duplicate_refresh_reuses_active_job(mock_run_refresh, mock_get_row):
    # Slow the worker down so the first job is still active when we fire the second request.
    def _slow_refresh(**kwargs):
        time.sleep(0.3)
        return {"command": "refresh", "returncode": 0, "stdout": "{}", "stderr": ""}

    mock_run_refresh.side_effect = _slow_refresh
    mock_get_row.return_value = {"status": "processed", "pdf_path": "/tmp/y.pdf"}

    first = client.post("/appointments/row789/refresh", json={})
    second = client.post("/appointments/row789/refresh", json={})

    assert first.json()["job_id"] == second.json()["job_id"]
    _wait_for_terminal(first.json()["job_id"])


def test_unknown_job_id_returns_404():
    resp = client.get("/refresh-jobs/does-not-exist")
    assert resp.status_code == 404


@patch("pf.runner.get_status")
def test_pf_status_returns_raw_output(mock_status):
    mock_status.return_value = {"command": "status", "returncode": 0, "stdout": "42 ready\n", "stderr": ""}
    resp = client.get("/pf-status")
    assert resp.status_code == 200
    assert "42 ready" in resp.json()["raw_output"]
