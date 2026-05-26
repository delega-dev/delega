"""Regression tests for security and data-integrity hardening."""

import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["DELEGA_REQUIRE_AUTH"] = "true"
os.environ["DELEGA_DB_PATH"] = ":memory:"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

import database
import main
import models
from dedup import find_similar_tasks
from main import app, derive_key_hash, derive_key_lookup, get_db, validate_webhook_url


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    test_engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    database.enable_sqlite_foreign_keys(test_engine)
    models.Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    def override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    original_session_local = main.SessionLocal
    main.SessionLocal = TestSession
    main._rate_limiter._hits.clear()

    yield test_engine

    main.SessionLocal = original_session_local
    app.dependency_overrides.clear()


@pytest.fixture()
def client():
    return TestClient(app, base_url="http://localhost")


def auth(key: str):
    return {"X-Agent-Key": key}


def make_agent(engine, name: str, *, is_admin: bool = False):
    import secrets as _secrets

    raw_key = "dlg_" + _secrets.token_urlsafe(32)
    salt = _secrets.token_hex(16)
    Session = sessionmaker(bind=engine)
    db = Session()
    agent = models.Agent(
        name=name,
        api_key=raw_key,
        key_hash=derive_key_hash(raw_key, salt),
        key_salt=salt,
        key_lookup=derive_key_lookup(raw_key),
        is_admin=is_admin,
        permissions=[],
        active=True,
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    agent_id = agent.id
    db.close()
    return agent_id, raw_key


def test_single_candidate_exact_duplicate_is_detected():
    existing = [SimpleNamespace(id=7, content="Research competitor pricing")]

    matches = find_similar_tasks("Research competitor pricing", existing)

    assert matches == [
        {"task_id": 7, "content": "Research competitor pricing", "score": 1.0}
    ]


def test_dedup_threshold_is_bounded(fresh_db, client):
    _, admin_key = make_agent(fresh_db, "admin", is_admin=True)
    created = client.post(
        "/api/tasks",
        json={"content": "Research competitor pricing"},
        headers=auth(admin_key),
    )
    assert created.status_code == 200

    duplicate = client.post(
        "/api/tasks/dedup",
        json={"content": "Research competitor pricing"},
        headers=auth(admin_key),
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["has_duplicates"] is True
    assert duplicate.json()["matches"][0]["score"] == 1.0

    invalid = client.post(
        "/api/tasks/dedup",
        json={"content": "Research competitor pricing", "threshold": 1.01},
        headers=auth(admin_key),
    )
    assert invalid.status_code == 422


@pytest.mark.parametrize("url", [
    "http://100.64.0.1/hook",
    "http://198.18.0.1/hook",
    "http://127.0.0.1/hook",
    "http://example.com:99999/hook",
    "http://example.com:abc/hook",
])
def test_webhook_validation_rejects_non_global_or_malformed_targets(url):
    assert validate_webhook_url(url) is not None


def test_webhook_validation_allows_global_address():
    assert validate_webhook_url("http://1.1.1.1/hook") is None


def test_sqlite_foreign_keys_are_enforced(fresh_db):
    Session = sessionmaker(bind=fresh_db)
    db = Session()
    db.add(models.Comment(task_id=9999, content="orphan"))

    with pytest.raises(IntegrityError):
        db.commit()

    db.rollback()
    db.close()


def test_concurrent_initial_bootstrap_creates_one_admin(fresh_db):
    def create_agent(name: str):
        local_client = TestClient(app, base_url="http://localhost")
        try:
            response = local_client.post("/api/agents", json={"name": name})
            return response.status_code, response.json()
        finally:
            local_client.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create_agent, ["bootstrap-a", "bootstrap-b"]))

    successful = [body for status, body in results if status == 200]
    rejected = [status for status, _body in results if status != 200]

    assert len(successful) == 1
    assert successful[0]["is_admin"] is True
    assert rejected and all(status in (401, 403) for status in rejected)

    Session = sessionmaker(bind=fresh_db)
    db = Session()
    agents = db.query(models.Agent).all()
    assert len(agents) == 1
    assert agents[0].is_admin is True
    db.close()
