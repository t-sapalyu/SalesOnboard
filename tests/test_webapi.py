"""Tests for the web API (login and upload), through FastAPI's TestClient.
TestClient sends requests to the app in memory, no server needed, and keeps
cookies between requests like a browser. Needs Postgres running.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from salesonboard.config import get_settings
from salesonboard.db import connect
from salesonboard.db.control import get_batch
from salesonboard.webapi.app import create_app
from salesonboard.webapi.auth import check_login
from scripts.seed_tenants import DEMO_TENANTS
from tests.conftest import TEST_PASSWORD, TEST_USERNAME

COMPANY1_FILE = Path("demo_data/company1/sales.csv")


@pytest.fixture
def client(registered_tenant, tmp_path, monkeypatch):
    """A browser-like client. Uploaded files go to a temporary folder."""
    monkeypatch.setattr(get_settings(), "upload_dir", tmp_path)
    return TestClient(create_app())


@pytest.fixture
def logged_in(client):
    response = client.post(
        "/api/login", json={"username": TEST_USERNAME, "password": TEST_PASSWORD}
    )
    assert response.status_code == 200
    return client


def upload(client, name, content):
    return client.post("/api/upload", files={"file": (name, content, "text/csv")})


# --- Login (A6.1) -----------------------------------------------------------


def test_login_and_me(client):
    response = client.post(
        "/api/login", json={"username": TEST_USERNAME, "password": TEST_PASSWORD}
    )
    assert response.status_code == 200
    assert response.json() == {"tenant_id": "pytest_tenant", "name": "Pytest tenant"}

    # The session cookie is sent back on the next request.
    assert client.get("/api/me").json()["tenant_id"] == "pytest_tenant"


def test_wrong_password_is_refused(client):
    response = client.post("/api/login", json={"username": TEST_USERNAME, "password": "nope"})
    assert response.status_code == 401
    assert client.get("/api/me").status_code == 401


def test_unknown_user_gets_the_same_message(client):
    wrong_password = client.post("/api/login", json={"username": TEST_USERNAME, "password": "x"})
    unknown_user = client.post("/api/login", json={"username": "nobody", "password": "x"})
    assert unknown_user.status_code == 401
    assert unknown_user.json() == wrong_password.json()


def test_logout(logged_in):
    logged_in.post("/api/logout")
    assert logged_in.get("/api/me").status_code == 401


def test_pages_need_a_login(client):
    assert client.get("/api/me").status_code == 401
    assert upload(client, "sales.csv", b"a,b\n1,2\n").status_code == 401


def test_demo_accounts_can_log_in(db_conn):
    with connect() as conn:
        seeded = conn.execute("SELECT count(*) FROM tenants WHERE tenant_id = 'company1'")
        if seeded.fetchone()[0] == 0:
            pytest.skip("Demo tenants not seeded: uv run python -m scripts.seed_tenants")
    for tenant in DEMO_TENANTS:
        assert check_login(tenant["tenant_id"], tenant["password"]) is not None


# --- Upload (A6.2) ----------------------------------------------------------


def test_upload_saves_file_and_shows_preview(logged_in, tmp_path):
    response = upload(logged_in, "sales.csv", COMPANY1_FILE.read_bytes())
    assert response.status_code == 200
    result = response.json()

    assert result["file_name"] == "sales.csv"
    assert result["row_count"] == 721
    assert "OrderNo" in result["columns"]
    assert len(result["preview"]) == 5

    # Saved as <tenant_id>/<batch_id>.csv, and the batch remembers both names.
    saved = tmp_path / "pytest_tenant" / f"{result['batch_id']}.csv"
    assert saved.read_bytes() == COMPANY1_FILE.read_bytes()
    with connect() as conn:
        batch = get_batch(conn, result["batch_id"])
    assert batch["status"] == "mapping"
    assert batch["file_name"] == "sales.csv"
    assert batch["file_path"] == str(saved)
    assert len(batch["file_hash"]) == 64


def test_second_upload_gets_a_clear_error(logged_in, tmp_path):
    assert upload(logged_in, "sales.csv", COMPANY1_FILE.read_bytes()).status_code == 200

    response = upload(logged_in, "sales.csv", COMPANY1_FILE.read_bytes())
    assert response.status_code == 409
    assert "already have an upload in progress" in response.json()["detail"]
    assert len(list((tmp_path / "pytest_tenant").iterdir())) == 1  # second file removed


def test_only_csv_files(logged_in):
    response = upload(logged_in, "sales.xlsx", b"whatever")
    assert response.status_code == 400
    assert ".csv" in response.json()["detail"]


def test_unreadable_file_creates_no_batch(logged_in, tmp_path):
    response = upload(logged_in, "empty.csv", b"")
    assert response.status_code == 400
    assert "Could not read" in response.json()["detail"]
    assert list((tmp_path / "pytest_tenant").iterdir()) == []

    # No batch was created, so a good file can still be uploaded.
    assert upload(logged_in, "sales.csv", COMPANY1_FILE.read_bytes()).status_code == 200
