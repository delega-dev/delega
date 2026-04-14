"""Tests for delegation_depth coherence guards on the tasks endpoints.

Parity with the hosted API's coherence fix (delega-dev/delega-api#28):
chain fields (parent_task_id, root_task_id, delegation_depth) must be
managed atomically by the server. Clients cannot set them directly on
POST /tasks or mutate them via PUT /tasks/:id. Chains are built only
via POST /tasks/:id/delegate, which sets all three atomically.
"""

import os
import sys
import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["DELEGA_REQUIRE_AUTH"] = "true"
os.environ["DELEGA_DB_PATH"] = ":memory:"

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

import main
import models
from main import app, get_db, derive_key_lookup, derive_key_hash


# ---------- Fixtures (mirrors test_permissions.py pattern) ----------

@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    test_engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    models.Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    def override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    _orig_session_local = main.SessionLocal
    main.SessionLocal = TestSession
    main._rate_limiter._hits.clear()

    yield test_engine

    main.SessionLocal = _orig_session_local
    app.dependency_overrides.clear()


@pytest.fixture()
def client():
    return TestClient(app, base_url="http://localhost")


def make_agent(engine, name: str, *, is_admin: bool = False):
    import secrets as _secrets
    raw_key = "dlg_" + _secrets.token_urlsafe(32)
    salt = _secrets.token_hex(16)
    key_hash = derive_key_hash(raw_key, salt)
    key_lookup = derive_key_lookup(raw_key)

    Session = sessionmaker(bind=engine)
    db = Session()
    agent = models.Agent(
        name=name,
        api_key=raw_key,
        key_hash=key_hash,
        key_salt=salt,
        key_lookup=key_lookup,
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


def auth(key: str):
    return {"X-Agent-Key": key}


# ---------- POST /tasks coherence ----------

class TestCreateTaskCoherence:
    def test_bare_create_defaults_to_depth_zero_root_self(self, fresh_db, client):
        _, key = make_agent(fresh_db, "a", is_admin=True)
        r = client.post("/api/tasks", json={"content": "hello"}, headers=auth(key))
        assert r.status_code == 200
        body = r.json()
        assert body["delegation_depth"] == 0
        assert body["parent_task_id"] is None
        assert body["root_task_id"] == body["id"]

    def test_explicit_zero_depth_accepted(self, fresh_db, client):
        _, key = make_agent(fresh_db, "a", is_admin=True)
        r = client.post(
            "/api/tasks",
            json={"content": "explicit zero", "delegation_depth": 0},
            headers=auth(key),
        )
        assert r.status_code == 200
        assert r.json()["delegation_depth"] == 0

    def test_non_zero_depth_rejected_400(self, fresh_db, client):
        _, key = make_agent(fresh_db, "a", is_admin=True)
        r = client.post(
            "/api/tasks",
            json={"content": "orphan attempt", "delegation_depth": 3},
            headers=auth(key),
        )
        assert r.status_code == 400
        assert "delegation_depth" in r.json()["detail"]

    def test_root_task_id_in_body_rejected_400(self, fresh_db, client):
        _, key = make_agent(fresh_db, "a", is_admin=True)
        r = client.post(
            "/api/tasks",
            json={"content": "x", "root_task_id": 99},
            headers=auth(key),
        )
        assert r.status_code == 400
        assert "root_task_id" in r.json()["detail"]

    def test_parent_task_id_still_accepted(self, fresh_db, client):
        _, key = make_agent(fresh_db, "a", is_admin=True)
        parent = client.post("/api/tasks", json={"content": "parent"}, headers=auth(key)).json()
        r = client.post(
            "/api/tasks",
            json={"content": "child", "parent_task_id": parent["id"]},
            headers=auth(key),
        )
        assert r.status_code == 200
        child = r.json()
        assert child["parent_task_id"] == parent["id"]
        assert child["root_task_id"] == parent["id"]
        assert child["delegation_depth"] == 1


# ---------- PUT /tasks/:id coherence ----------

class TestUpdateTaskCoherence:
    def test_delegation_depth_in_put_rejected_400(self, fresh_db, client):
        _, key = make_agent(fresh_db, "a", is_admin=True)
        task = client.post("/api/tasks", json={"content": "x"}, headers=auth(key)).json()
        r = client.put(
            f"/api/tasks/{task['id']}",
            json={"delegation_depth": 9},
            headers=auth(key),
        )
        assert r.status_code == 400
        assert "delegation_depth" in r.json()["detail"]

    def test_parent_task_id_in_put_rejected_400(self, fresh_db, client):
        _, key = make_agent(fresh_db, "a", is_admin=True)
        task = client.post("/api/tasks", json={"content": "x"}, headers=auth(key)).json()
        r = client.put(
            f"/api/tasks/{task['id']}",
            json={"parent_task_id": 42},
            headers=auth(key),
        )
        assert r.status_code == 400
        assert "parent_task_id" in r.json()["detail"]

    def test_root_task_id_in_put_rejected_400(self, fresh_db, client):
        _, key = make_agent(fresh_db, "a", is_admin=True)
        task = client.post("/api/tasks", json={"content": "x"}, headers=auth(key)).json()
        r = client.put(
            f"/api/tasks/{task['id']}",
            json={"root_task_id": 7},
            headers=auth(key),
        )
        assert r.status_code == 400
        assert "root_task_id" in r.json()["detail"]

    def test_regular_content_update_still_works(self, fresh_db, client):
        _, key = make_agent(fresh_db, "a", is_admin=True)
        task = client.post("/api/tasks", json={"content": "old"}, headers=auth(key)).json()
        r = client.put(
            f"/api/tasks/{task['id']}",
            json={"content": "new"},
            headers=auth(key),
        )
        assert r.status_code == 200
        assert r.json()["content"] == "new"


# ---------- POST /tasks/:id/delegate atomicity ----------

class TestDelegateAtomicity:
    def test_delegate_sets_chain_fields_atomically(self, fresh_db, client):
        _, key = make_agent(fresh_db, "a", is_admin=True)
        parent = client.post("/api/tasks", json={"content": "root"}, headers=auth(key)).json()
        r = client.post(
            f"/api/tasks/{parent['id']}/delegate",
            json={"content": "delegated"},
            headers=auth(key),
        )
        assert r.status_code == 200
        child = r.json()
        assert child["parent_task_id"] == parent["id"]
        assert child["root_task_id"] == parent["id"]
        assert child["delegation_depth"] == 1
