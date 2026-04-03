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
    return TestClient(app, base_url="http://localhost")


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

    def test_non_admin_cannot_self_grant_read_all(self, fresh_db, client):
        admin_id, admin_key = make_agent(fresh_db, "admin", is_admin=True)
        user_id, user_key = make_agent(fresh_db, "user")

        client.post("/api/tasks", json={"content": "Admin task"}, headers=auth(admin_key))

        before = client.get("/api/tasks", headers=auth(user_key)).json()
        assert len(before) == 0

        r = client.put(
            f"/api/agents/{user_id}",
            json={"permissions": ["tasks.read_all"]},
            headers=auth(user_key),
        )
        assert r.status_code == 403

        after = client.get("/api/tasks", headers=auth(user_key)).json()
        assert len(after) == 0

    def test_non_admin_cannot_self_toggle_active(self, fresh_db, client):
        user_id, user_key = make_agent(fresh_db, "user")

        r = client.put(
            f"/api/agents/{user_id}",
            json={"active": False},
            headers=auth(user_key),
        )
        assert r.status_code == 403

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

    def test_first_bootstrap_agent_is_admin(self, fresh_db, client):
        r = client.post("/api/agents", json={"name": "bootstrap"})
        assert r.status_code == 200
        body = r.json()
        assert body["is_admin"] is True
        assert body["permissions"] == []

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
                         json={"url": "http://1.1.1.1/hook", "events": ["task.created"]},
                         headers=auth(admin_key))
        assert r.status_code == 200

    def test_admin_can_create_non_admin_reader(self, fresh_db, client):
        admin_id, admin_key = make_agent(fresh_db, "admin", is_admin=True)

        r = client.post(
            "/api/agents",
            json={"name": "reader", "permissions": ["tasks.read_all"]},
            headers=auth(admin_key),
        )
        assert r.status_code == 200
        body = r.json()
        assert body["is_admin"] is False
        assert body["permissions"] == ["tasks.read_all"]

        r2 = client.get(f"/api/agents/{body['id']}", headers=auth(admin_key))
        assert r2.status_code == 200
        assert r2.json()["is_admin"] is False
        assert r2.json()["permissions"] == ["tasks.read_all"]


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
        agent = db.get(models.Agent, admin_id)
        assert has_permission(agent, "tasks.read_all") is True
        assert has_permission(agent, "anything.at_all") is True
        db.close()

    def test_has_permission_checks_list(self, fresh_db, client):
        from main import has_permission
        reader_id, _ = make_agent(fresh_db, "reader", permissions=["tasks.read_all"])
        Session = sessionmaker(bind=fresh_db)
        db = Session()
        agent = db.get(models.Agent, reader_id)
        assert has_permission(agent, "tasks.read_all") is True
        assert has_permission(agent, "tasks.assign_any") is False
        db.close()

    def test_has_permission_none_perms(self, fresh_db, client):
        from main import has_permission
        agent_id, _ = make_agent(fresh_db, "legacy")
        Session = sessionmaker(bind=fresh_db)
        db = Session()
        agent = db.get(models.Agent, agent_id)
        agent.permissions = None
        db.commit()
        db.refresh(agent)
        assert has_permission(agent, "tasks.read_all") is False
        db.close()


# ---------- Test: Task completion status normalization ----------

class TestTaskCompletionStatus:
    """Completion endpoints should keep boolean/timestamp/attribution and status in sync."""

    def test_put_complete_sets_status_completed(self, fresh_db, client):
        agent_id, agent_key = make_agent(fresh_db, "worker")

        created = client.post("/api/tasks", json={"content": "PUT complete me"}, headers=auth(agent_key))
        assert created.status_code == 200
        task_id = created.json()["id"]

        completed = client.put(f"/api/tasks/{task_id}", json={"completed": True}, headers=auth(agent_key))
        assert completed.status_code == 200
        body = completed.json()
        assert body["completed"] is True
        assert body["status"] == "completed"
        assert body["completed_at"] is not None

        Session = sessionmaker(bind=fresh_db)
        db = Session()
        task = db.get(models.Task, task_id)
        assert task.status == "completed"
        assert task.completed_by_agent_id == agent_id
        db.close()

    def test_put_uncomplete_resets_status_open_and_clears_attribution(self, fresh_db, client):
        agent_id, agent_key = make_agent(fresh_db, "worker")

        created = client.post("/api/tasks", json={"content": "PUT reopen me"}, headers=auth(agent_key))
        assert created.status_code == 200
        task_id = created.json()["id"]

        assert client.put(f"/api/tasks/{task_id}", json={"completed": True}, headers=auth(agent_key)).status_code == 200
        reopened = client.put(f"/api/tasks/{task_id}", json={"completed": False}, headers=auth(agent_key))
        assert reopened.status_code == 200
        body = reopened.json()
        assert body["completed"] is False
        assert body["status"] == "open"
        assert body["completed_at"] is None

        Session = sessionmaker(bind=fresh_db)
        db = Session()
        task = db.get(models.Task, task_id)
        assert task.status == "open"
        assert task.completed_by_agent_id is None
        db.close()

    def test_post_complete_and_uncomplete_keep_status_in_sync(self, fresh_db, client):
        agent_id, agent_key = make_agent(fresh_db, "worker")

        created = client.post("/api/tasks", json={"content": "POST complete me"}, headers=auth(agent_key))
        assert created.status_code == 200
        task_id = created.json()["id"]

        completed = client.post(f"/api/tasks/{task_id}/complete", headers=auth(agent_key))
        assert completed.status_code == 200
        completed_body = completed.json()
        assert completed_body["completed"] is True
        assert completed_body["status"] == "completed"
        assert completed_body["completed_at"] is not None

        Session = sessionmaker(bind=fresh_db)
        db = Session()
        task = db.get(models.Task, task_id)
        assert task.status == "completed"
        assert task.completed_by_agent_id == agent_id
        db.close()

        reopened = client.post(f"/api/tasks/{task_id}/uncomplete", headers=auth(agent_key))
        assert reopened.status_code == 200
        reopened_body = reopened.json()
        assert reopened_body["completed"] is False
        assert reopened_body["status"] == "open"
        assert reopened_body["completed_at"] is None

        db = Session()
        task = db.get(models.Task, task_id)
        assert task.status == "open"
        assert task.completed_by_agent_id is None
        db.close()


class TestTaskContentValidation:
    """Task content must contain non-whitespace text."""

    @pytest.mark.parametrize("content", ["", "   ", "\n\t"])
    def test_create_task_rejects_blank_content(self, fresh_db, client, content):
        _, agent_key = make_agent(fresh_db, "worker")

        response = client.post("/api/tasks", json={"content": content}, headers=auth(agent_key))

        assert response.status_code == 422
        assert "content" in response.text.lower()

    @pytest.mark.parametrize("content", ["", "   ", "\n\t"])
    def test_update_task_rejects_blank_content(self, fresh_db, client, content):
        _, agent_key = make_agent(fresh_db, "worker")

        created = client.post("/api/tasks", json={"content": "valid"}, headers=auth(agent_key))
        assert created.status_code == 200
        task_id = created.json()["id"]

        response = client.put(f"/api/tasks/{task_id}", json={"content": content}, headers=auth(agent_key))

        assert response.status_code == 422
        assert "content" in response.text.lower()


# ---------- Test: open_task_count on agent endpoints ----------

class TestOpenTaskCount:
    """open_task_count is returned correctly on list and detail endpoints."""

    def test_list_agents_open_task_counts(self, fresh_db, client):
        """Agents with open, completed, and zero tasks get correct counts."""
        admin_id, admin_key = make_agent(fresh_db, "admin", is_admin=True)
        worker_id, worker_key = make_agent(fresh_db, "worker")
        idle_id, idle_key = make_agent(fresh_db, "idle")

        # Create 2 open tasks assigned to worker
        for i in range(2):
            r = client.post(
                "/api/tasks",
                json={"content": f"open task {i}", "assigned_to_agent_id": worker_id},
                headers=auth(admin_key),
            )
            assert r.status_code == 200

        # Create 1 completed task assigned to worker
        r = client.post(
            "/api/tasks",
            json={"content": "done task", "assigned_to_agent_id": worker_id},
            headers=auth(admin_key),
        )
        assert r.status_code == 200
        done_id = r.json()["id"]
        r = client.post(f"/api/tasks/{done_id}/complete", headers=auth(admin_key))
        assert r.status_code == 200

        agents = client.get("/api/agents", headers=auth(admin_key)).json()
        counts = {a["name"]: a["open_task_count"] for a in agents}
        assert counts["worker"] == 2
        assert counts["idle"] == 0
        assert counts["admin"] == 0

    def test_get_agent_open_task_count(self, fresh_db, client):
        """Detail endpoint returns correct open_task_count."""
        admin_id, admin_key = make_agent(fresh_db, "admin", is_admin=True)
        worker_id, worker_key = make_agent(fresh_db, "worker")

        r = client.post(
            "/api/tasks",
            json={"content": "assigned", "assigned_to_agent_id": worker_id},
            headers=auth(admin_key),
        )
        assert r.status_code == 200

        detail = client.get(f"/api/agents/{worker_id}", headers=auth(admin_key)).json()
        assert detail["open_task_count"] == 1

    def test_get_agent_only_completed_tasks(self, fresh_db, client):
        """Agent with only completed tasks has open_task_count == 0."""
        admin_id, admin_key = make_agent(fresh_db, "admin", is_admin=True)
        worker_id, worker_key = make_agent(fresh_db, "worker")

        r = client.post(
            "/api/tasks",
            json={"content": "will complete", "assigned_to_agent_id": worker_id},
            headers=auth(admin_key),
        )
        assert r.status_code == 200
        task_id = r.json()["id"]
        client.post(f"/api/tasks/{task_id}/complete", headers=auth(admin_key))

        detail = client.get(f"/api/agents/{worker_id}", headers=auth(admin_key)).json()
        assert detail["open_task_count"] == 0
