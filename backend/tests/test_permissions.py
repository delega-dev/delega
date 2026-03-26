"""Tests for the Delega roles/permissions system.

Proves that:
- tasks.read_all grants global task visibility without admin
- tasks.read_all does NOT grant agent/project/webhook admin
- tasks.read_all does NOT grant task mutation on others' tasks
- Admin still works as before
- Normal agents remain scoped
"""

import os
import sys
import pytest
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Must set env before importing main
os.environ["DELEGA_REQUIRE_AUTH"] = "true"
os.environ["DELEGA_DB_PATH"] = ":memory:"

import tempfile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

import main
import models
import database
from main import app, get_db, derive_key_lookup, derive_key_hash


# ---------- Fixtures ----------

@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    """Create a fresh temp-file SQLite database for each test.
    
    Uses a real file (not :memory:) so the auth middleware and dependency-
    injected sessions share the same data even when they open separate
    connections. tmp_path is unique per test.
    """
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

    # Patch SessionLocal at the module level — the middleware reads main.SessionLocal at call time
    _orig_session_local = main.SessionLocal
    main.SessionLocal = TestSession

    # Reset in-memory rate limiter to prevent 429s across tests
    main._rate_limiter._hits.clear()

    yield test_engine

    main.SessionLocal = _orig_session_local
    app.dependency_overrides.clear()


@pytest.fixture()
def client():
    """Test client created after fresh_db patches are in place."""
    return TestClient(app)


def make_agent(engine, name: str, *, is_admin: bool = False, permissions: list = None):
    """Create an agent directly in DB. Returns (agent_id, raw_api_key)."""
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
        permissions=permissions or [],
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


# ---------- Test: Normal agent scoping ----------

class TestNormalAgentScoping:
    """Non-admin agent without tasks.read_all sees only own tasks."""

    def test_normal_agent_sees_own_tasks_only(self, fresh_db, client):
        admin_id, admin_key = make_agent(fresh_db, "admin", is_admin=True)
        normal_id, normal_key = make_agent(fresh_db, "normal")

        r1 = client.post("/api/tasks", json={"content": "Admin task"}, headers=auth(admin_key))
        assert r1.status_code == 200

        r2 = client.post("/api/tasks", json={"content": "Normal task"}, headers=auth(normal_key))
        assert r2.status_code == 200

        tasks = client.get("/api/tasks", headers=auth(normal_key)).json()
        assert len(tasks) == 1
        assert tasks[0]["content"] == "Normal task"

    def test_normal_agent_cannot_see_others_task_by_id(self, fresh_db, client):
        admin_id, admin_key = make_agent(fresh_db, "admin", is_admin=True)
        normal_id, normal_key = make_agent(fresh_db, "normal")

        r = client.post("/api/tasks", json={"content": "Admin secret"}, headers=auth(admin_key))
        task_id = r.json()["id"]

        r2 = client.get(f"/api/tasks/{task_id}", headers=auth(normal_key))
        assert r2.status_code == 404

    def test_normal_agent_cannot_filter_by_project(self, fresh_db, client):
        _, normal_key = make_agent(fresh_db, "normal")
        r = client.get("/api/tasks", params={"project_id": 1}, headers=auth(normal_key))
        assert r.status_code == 403

    def test_normal_agent_scoped_stats(self, fresh_db, client):
        admin_id, admin_key = make_agent(fresh_db, "admin", is_admin=True)
        normal_id, normal_key = make_agent(fresh_db, "normal")

        client.post("/api/tasks", json={"content": "Admin task"}, headers=auth(admin_key))
        client.post("/api/tasks", json={"content": "Normal task"}, headers=auth(normal_key))

        stats = client.get("/api/stats", headers=auth(normal_key)).json()
        assert stats["total_tasks"] == 1  # only their own


# ---------- Test: tasks.read_all ----------

class TestTasksReadAll:
    """Agent with tasks.read_all sees all tasks but cannot admin."""

    def test_read_all_sees_all_tasks(self, fresh_db, client):
        admin_id, admin_key = make_agent(fresh_db, "admin", is_admin=True)
        reader_id, reader_key = make_agent(fresh_db, "reader", permissions=["tasks.read_all"])
        other_id, other_key = make_agent(fresh_db, "other")

        client.post("/api/tasks", json={"content": "Admin task"}, headers=auth(admin_key))
        client.post("/api/tasks", json={"content": "Other task"}, headers=auth(other_key))
        client.post("/api/tasks", json={"content": "Reader task"}, headers=auth(reader_key))

        tasks = client.get("/api/tasks", headers=auth(reader_key)).json()
        assert len(tasks) == 3

    def test_read_all_sees_task_by_id(self, fresh_db, client):
        admin_id, admin_key = make_agent(fresh_db, "admin", is_admin=True)
        reader_id, reader_key = make_agent(fresh_db, "reader", permissions=["tasks.read_all"])

        r = client.post("/api/tasks", json={"content": "Admin secret"}, headers=auth(admin_key))
        task_id = r.json()["id"]

        r2 = client.get(f"/api/tasks/{task_id}", headers=auth(reader_key))
        assert r2.status_code == 200
        assert r2.json()["content"] == "Admin secret"

    def test_read_all_can_filter_by_project(self, fresh_db, client):
        admin_id, admin_key = make_agent(fresh_db, "admin", is_admin=True)
        reader_id, reader_key = make_agent(fresh_db, "reader", permissions=["tasks.read_all"])

        pr = client.post("/api/projects", json={"name": "Proj"}, headers=auth(admin_key))
        project_id = pr.json()["id"]
        client.post("/api/tasks", json={"content": "In project", "project_id": project_id}, headers=auth(admin_key))
        client.post("/api/tasks", json={"content": "No project"}, headers=auth(admin_key))

        tasks = client.get("/api/tasks", params={"project_id": project_id}, headers=auth(reader_key)).json()
        assert len(tasks) == 1
        assert tasks[0]["content"] == "In project"

    def test_read_all_sees_global_stats(self, fresh_db, client):
        admin_id, admin_key = make_agent(fresh_db, "admin", is_admin=True)
        reader_id, reader_key = make_agent(fresh_db, "reader", permissions=["tasks.read_all"])

        client.post("/api/tasks", json={"content": "Task A"}, headers=auth(admin_key))
        client.post("/api/tasks", json={"content": "Task B"}, headers=auth(admin_key))

        stats = client.get("/api/stats", headers=auth(reader_key)).json()
        assert stats["total_tasks"] == 2

    def test_read_all_sees_delegation_chain(self, fresh_db, client):
        admin_id, admin_key = make_agent(fresh_db, "admin", is_admin=True)
        reader_id, reader_key = make_agent(fresh_db, "reader", permissions=["tasks.read_all"])

        r = client.post("/api/tasks", json={"content": "Parent"}, headers=auth(admin_key))
        task_id = r.json()["id"]
        client.post(f"/api/tasks/{task_id}/delegate",
                     json={"content": "Child"}, headers=auth(admin_key))

        chain = client.get(f"/api/tasks/{task_id}/chain", headers=auth(reader_key)).json()
        assert len(chain["chain"]) >= 2

    def test_read_all_cannot_mutate_others_tasks(self, fresh_db, client):
        admin_id, admin_key = make_agent(fresh_db, "admin", is_admin=True)
        reader_id, reader_key = make_agent(fresh_db, "reader", permissions=["tasks.read_all"])

        r = client.post("/api/tasks", json={"content": "Admin task"}, headers=auth(admin_key))
        task_id = r.json()["id"]

        # Can read it
        assert client.get(f"/api/tasks/{task_id}", headers=auth(reader_key)).status_code == 200

        # Cannot update it
        r2 = client.put(f"/api/tasks/{task_id}", json={"content": "Hacked"}, headers=auth(reader_key))
        assert r2.status_code == 403

        # Cannot complete it
        r3 = client.post(f"/api/tasks/{task_id}/complete", headers=auth(reader_key))
        assert r3.status_code == 403

        # Cannot delete it
        r4 = client.delete(f"/api/tasks/{task_id}", headers=auth(reader_key))
        assert r4.status_code == 403

    def test_read_all_cannot_manage_agents(self, fresh_db, client):
        reader_id, reader_key = make_agent(fresh_db, "reader", permissions=["tasks.read_all"])

        assert client.get("/api/agents", headers=auth(reader_key)).status_code == 403
        assert client.post("/api/agents", json={"name": "evil"}, headers=auth(reader_key)).status_code == 403

    def test_read_all_cannot_manage_projects(self, fresh_db, client):
        reader_id, reader_key = make_agent(fresh_db, "reader", permissions=["tasks.read_all"])

        assert client.get("/api/projects", headers=auth(reader_key)).status_code == 403
        assert client.post("/api/projects", json={"name": "evil"}, headers=auth(reader_key)).status_code == 403

    def test_read_all_cannot_manage_webhooks(self, fresh_db, client):
        reader_id, reader_key = make_agent(fresh_db, "reader", permissions=["tasks.read_all"])

        assert client.get("/api/webhooks", headers=auth(reader_key)).status_code == 403
        assert client.post("/api/webhooks",
                            json={"url": "http://evil.com", "events": ["task.created"]},
                            headers=auth(reader_key)).status_code == 403

    def test_read_all_can_read_task_context(self, fresh_db, client):
        admin_id, admin_key = make_agent(fresh_db, "admin", is_admin=True)
        reader_id, reader_key = make_agent(fresh_db, "reader", permissions=["tasks.read_all"])

        r = client.post("/api/tasks", json={"content": "Task with context"}, headers=auth(admin_key))
        task_id = r.json()["id"]
        client.patch(f"/api/tasks/{task_id}/context",
                      json={"data": {"key": "value"}}, headers=auth(admin_key))

        r2 = client.get(f"/api/tasks/{task_id}/context", headers=auth(reader_key))
        assert r2.status_code == 200

    def test_read_all_can_list_subtasks(self, fresh_db, client):
        admin_id, admin_key = make_agent(fresh_db, "admin", is_admin=True)
        reader_id, reader_key = make_agent(fresh_db, "reader", permissions=["tasks.read_all"])

        r = client.post("/api/tasks", json={"content": "Parent"}, headers=auth(admin_key))
        task_id = r.json()["id"]

        r2 = client.get(f"/api/tasks/{task_id}/subtasks", headers=auth(reader_key))
        assert r2.status_code == 200


# ---------- Test: Admin still works ----------

class TestAdminStillWorks:
    """Admin powers unchanged by the permission system."""

    def test_admin_sees_all_tasks(self, fresh_db, client):
        admin_id, admin_key = make_agent(fresh_db, "admin", is_admin=True)
        other_id, other_key = make_agent(fresh_db, "other")

        client.post("/api/tasks", json={"content": "Other task"}, headers=auth(other_key))

        tasks = client.get("/api/tasks", headers=auth(admin_key)).json()
        assert len(tasks) == 1

    def test_admin_can_manage_agents(self, fresh_db, client):
        admin_id, admin_key = make_agent(fresh_db, "admin", is_admin=True)
        assert client.get("/api/agents", headers=auth(admin_key)).status_code == 200

    def test_admin_can_manage_projects(self, fresh_db, client):
        admin_id, admin_key = make_agent(fresh_db, "admin", is_admin=True)
        assert client.post("/api/projects", json={"name": "Test"}, headers=auth(admin_key)).status_code == 200

    def test_admin_can_manage_webhooks(self, fresh_db, client):
        admin_id, admin_key = make_agent(fresh_db, "admin", is_admin=True)
        r = client.post("/api/webhooks",
                         json={"url": "http://example.com/hook", "events": ["task.created"]},
                         headers=auth(admin_key))
        assert r.status_code == 200


# ---------- Test: Backward compatibility ----------

class TestBackwardCompatibility:
    """Agents with empty permissions behave exactly as before."""

    def test_agent_with_no_permissions_is_scoped(self, fresh_db, client):
        admin_id, admin_key = make_agent(fresh_db, "admin", is_admin=True)
        agent_id, agent_key = make_agent(fresh_db, "agent", permissions=[])

        r1 = client.post("/api/tasks", json={"content": "Admin task"}, headers=auth(admin_key))
        assert r1.status_code == 200, f"Admin create failed: {r1.status_code} {r1.text}"
        r2 = client.post("/api/tasks", json={"content": "Agent task"}, headers=auth(agent_key))
        assert r2.status_code == 200, f"Agent create failed: {r2.status_code} {r2.text}"

        tasks = client.get("/api/tasks", headers=auth(agent_key)).json()
        assert len(tasks) == 1, f"Expected 1 task, got {len(tasks)}: {tasks}"
        assert tasks[0]["content"] == "Agent task"

    def test_agent_with_null_permissions_is_scoped(self, fresh_db, client):
        """Covers agents created before the permissions field existed."""
        admin_id, admin_key = make_agent(fresh_db, "admin", is_admin=True)

        # Create agent with None permissions (simulating pre-migration)
        import secrets as _secrets
        raw_key = "dlg_" + _secrets.token_urlsafe(32)
        salt = _secrets.token_hex(16)

        Session = sessionmaker(bind=fresh_db)
        db = Session()
        agent = models.Agent(
            name="legacy",
            api_key=raw_key,
            key_hash=derive_key_hash(raw_key, salt),
            key_salt=salt,
            key_lookup=derive_key_lookup(raw_key),
            is_admin=False,
            permissions=None,
            active=True,
        )
        db.add(agent)
        db.commit()
        db.close()

        client.post("/api/tasks", json={"content": "Admin task"}, headers=auth(admin_key))

        tasks = client.get("/api/tasks", headers=auth(raw_key)).json()
        assert len(tasks) == 0


# ---------- Test: Permission helper ----------

class TestPermissionHelper:
    """Unit tests for has_permission and require_permission."""

    def test_has_permission_admin_always_true(self, fresh_db, client):
        from main import has_permission
        admin_id, _ = make_agent(fresh_db, "admin", is_admin=True)
        Session = sessionmaker(bind=fresh_db)
        db = Session()
        agent = db.query(models.Agent).get(admin_id)
        assert has_permission(agent, "tasks.read_all") is True
        assert has_permission(agent, "anything.at_all") is True
        db.close()

    def test_has_permission_checks_list(self, fresh_db, client):
        from main import has_permission
        reader_id, _ = make_agent(fresh_db, "reader", permissions=["tasks.read_all"])
        Session = sessionmaker(bind=fresh_db)
        db = Session()
        agent = db.query(models.Agent).get(reader_id)
        assert has_permission(agent, "tasks.read_all") is True
        assert has_permission(agent, "tasks.assign_any") is False
        db.close()

    def test_has_permission_none_perms(self, fresh_db, client):
        from main import has_permission
        agent_id, _ = make_agent(fresh_db, "legacy")
        Session = sessionmaker(bind=fresh_db)
        db = Session()
        agent = db.query(models.Agent).get(agent_id)
        agent.permissions = None
        db.commit()
        db.refresh(agent)
        assert has_permission(agent, "tasks.read_all") is False
        db.close()
