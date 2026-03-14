import asyncio
import importlib
import os
import secrets
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def load_test_stack(db_path: Path, require_auth):
    os.environ["DELEGA_DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ["DELEGA_MAX_BODY_BYTES"] = "1024"
    if require_auth is True:
        os.environ["DELEGA_REQUIRE_AUTH"] = "true"
    elif require_auth is False:
        os.environ["DELEGA_REQUIRE_AUTH"] = "false"
    else:
        os.environ.pop("DELEGA_REQUIRE_AUTH", None)

    for module_name in ["database", "models", "schemas", "main"]:
        sys.modules.pop(module_name, None)

    database = importlib.import_module("database")
    models = importlib.import_module("models")
    main = importlib.import_module("main")
    client = TestClient(main.app, base_url="http://localhost", client=("127.0.0.1", 50000))
    return database, models, main, client


class BaseSecurityTestCase(unittest.TestCase):
    require_auth = True

    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.tempdir.name) / "delega-test.db"
        cls.database, cls.models, cls.main, cls.client = load_test_stack(cls.db_path, cls.require_auth)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        try:
            cls.main.scheduler.shutdown(wait=False)
        except Exception:
            pass
        cls.tempdir.cleanup()

    def setUp(self):
        db = self.database.SessionLocal()
        db.query(self.models.WebhookDelivery).delete()
        db.query(self.models.Webhook).delete()
        db.query(self.models.Comment).delete()
        db.query(self.models.SubTask).delete()
        db.query(self.models.Task).delete()
        db.query(self.models.Project).delete()
        db.query(self.models.PushSubscription).delete()
        db.query(self.models.Agent).delete()
        db.commit()
        db.close()

        self.api_key, self.agent_id = self.create_agent(is_admin=True)

    def create_agent(self, *, is_admin: bool, plaintext_only: bool = False):
        api_key = f"dlg_{secrets.token_hex(16)}"
        fields = {}
        stored_api_key = api_key
        if not plaintext_only:
            fields.update(self.main.create_agent_key_material(api_key))
            stored_api_key = f"migrated_seed_{secrets.token_hex(4)}"

        db = self.database.SessionLocal()
        agent = self.models.Agent(
            name=f"agent-{secrets.token_hex(4)}",
            api_key=stored_api_key,
            active=True,
            is_admin=is_admin,
            **fields,
        )
        db.add(agent)
        db.commit()
        db.refresh(agent)
        agent_id = agent.id
        db.close()
        return api_key, agent_id

    def create_project(self, name: str = "Operations"):
        db = self.database.SessionLocal()
        project = self.models.Project(name=name, sort_order=1)
        db.add(project)
        db.commit()
        db.refresh(project)
        project_id = project.id
        db.close()
        return project_id

    def create_task(self, content: str = "Inbox task", *, project_id=None):
        db = self.database.SessionLocal()
        task = self.models.Task(
            content=content,
            project_id=project_id,
            created_by_agent_id=self.agent_id,
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        task_id = task.id
        db.close()
        return task_id


class SecurityHardeningTests(BaseSecurityTestCase):
    require_auth = True

    def test_require_auth_applies_to_frontend_dashboard_routes(self):
        for path in ["/api/projects", "/api/tasks", "/api/stats"]:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 401, path)
            self.assertIn("X-Agent-Key", response.text)

    def test_loopback_can_bootstrap_first_agent_without_key(self):
        db = self.database.SessionLocal()
        db.query(self.models.Agent).delete()
        db.commit()
        db.close()

        response = self.client.post(
            "/api/agents",
            json={"name": "bootstrap-admin", "display_name": "Bootstrap Admin"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["is_admin"])
        self.assertTrue(body["api_key"].startswith("dlg_"))

    def test_rejects_internal_webhook_targets(self):
        response = self.client.post(
            "/api/webhooks",
            headers={"X-Agent-Key": self.api_key},
            json={
                "url": "http://127.0.0.1:8080/hook",
                "events": ["task.created"],
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("internal addresses", response.text)

    def test_webhook_secret_is_redacted_from_api_responses(self):
        response = self.client.post(
            "/api/webhooks",
            headers={"X-Agent-Key": self.api_key},
            json={
                "url": "https://93.184.216.34/webhook",
                "events": ["task.created"],
                "secret": "super-secret-value",
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertNotIn("secret", body)
        self.assertEqual(body["url"], "https://93.184.216.34/webhook")

    def test_rejects_oversized_write_bodies(self):
        response = self.client.post(
            "/api/tasks",
            headers={"X-Agent-Key": self.api_key},
            json={
                "content": "x" * 1500,
                "description": "y" * 1500,
            },
        )

        self.assertEqual(response.status_code, 413)

    def test_rejects_streamed_write_bodies_without_content_length(self):
        chunks = [
            b'{"content":"',
            b'x' * 1500,
            b'","description":"',
            b'y' * 1500,
            b'"}',
        ]
        messages = [
            {"type": "http.request", "body": chunk, "more_body": i < len(chunks) - 1}
            for i, chunk in enumerate(chunks)
        ]
        sent_messages = []

        async def receive():
            if messages:
                return messages.pop(0)
            return {"type": "http.disconnect"}

        async def send(message):
            sent_messages.append(message)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/tasks",
            "raw_path": b"/api/tasks",
            "query_string": b"",
            "headers": [
                (b"host", b"localhost"),
                (b"x-agent-key", self.api_key.encode()),
                (b"content-type", b"application/json"),
            ],
            "client": ("127.0.0.1", 50000),
            "server": ("localhost", 80),
            "root_path": "",
        }

        asyncio.run(self.main.app(scope, receive, send))

        start = next(message for message in sent_messages if message["type"] == "http.response.start")
        body_chunks = [
            message.get("body", b"")
            for message in sent_messages
            if message["type"] == "http.response.body"
        ]

        self.assertEqual(start["status"], 413)
        self.assertIn(b"Request body too large", b"".join(body_chunks))

    def test_non_admin_agents_cannot_use_admin_routes(self):
        worker_key, worker_id = self.create_agent(is_admin=False)

        list_res = self.client.get("/api/agents", headers={"X-Agent-Key": worker_key})
        self.assertEqual(list_res.status_code, 403)

        webhook_res = self.client.post(
            "/api/webhooks",
            headers={"X-Agent-Key": worker_key},
            json={"url": "https://example.com/hook", "events": ["task.created"]},
        )
        self.assertEqual(webhook_res.status_code, 403)

        push_res = self.client.get("/api/push/subscriptions", headers={"X-Agent-Key": worker_key})
        self.assertEqual(push_res.status_code, 403)

        rotate_res = self.client.post(
            f"/api/agents/{worker_id}/rotate-key",
            headers={"X-Agent-Key": worker_key},
        )
        self.assertEqual(rotate_res.status_code, 200)
        self.assertIn("api_key", rotate_res.json())

    def test_non_admin_agents_only_see_their_tasks(self):
        worker_key, worker_id = self.create_agent(is_admin=False)

        admin_task_res = self.client.post(
            "/api/tasks",
            headers={"X-Agent-Key": self.api_key},
            json={"content": "admin-only task"},
        )
        self.assertEqual(admin_task_res.status_code, 200)

        worker_list_res = self.client.get("/api/tasks", headers={"X-Agent-Key": worker_key})
        self.assertEqual(worker_list_res.status_code, 200)
        self.assertEqual(worker_list_res.json(), [])

        shared_task_res = self.client.post(
            "/api/tasks",
            headers={"X-Agent-Key": self.api_key},
            json={"content": "shared task", "assigned_to_agent_id": worker_id},
        )
        self.assertEqual(shared_task_res.status_code, 200)

        worker_list_res = self.client.get("/api/tasks", headers={"X-Agent-Key": worker_key})
        self.assertEqual(worker_list_res.status_code, 200)
        self.assertEqual(len(worker_list_res.json()), 1)
        self.assertEqual(worker_list_res.json()[0]["content"], "shared task")

    def test_rejects_legacy_plaintext_only_agent_keys(self):
        legacy_key, _legacy_id = self.create_agent(is_admin=False, plaintext_only=True)

        response = self.client.get("/api/tasks", headers={"X-Agent-Key": legacy_key})
        self.assertEqual(response.status_code, 401)

    def test_auth_migration_backfills_plaintext_agent_keys(self):
        legacy_key, legacy_id = self.create_agent(is_admin=False, plaintext_only=True)

        migration_path = BACKEND_DIR / "migrations" / "005_harden_agent_auth.py"
        spec = importlib.util.spec_from_file_location("migration_005_harden_agent_auth", migration_path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        module.migrate(str(self.db_path))

        db = self.database.SessionLocal()
        migrated_agent = db.query(self.models.Agent).filter(self.models.Agent.id == legacy_id).first()
        self.assertIsNotNone(migrated_agent)
        self.assertTrue(migrated_agent.key_hash)
        self.assertTrue(migrated_agent.key_lookup)
        self.assertTrue(migrated_agent.key_salt)
        self.assertTrue(migrated_agent.key_prefix)
        self.assertEqual(migrated_agent.api_key, f"migrated_{legacy_id}")
        db.close()

        response = self.client.get("/api/tasks", headers={"X-Agent-Key": legacy_key})
        self.assertEqual(response.status_code, 200)


class OpenModeCompatibilityTests(BaseSecurityTestCase):
    require_auth = False

    def test_open_mode_dashboard_routes_work_without_auth(self):
        project_id = self.create_project()
        self.create_task("Ship docs update", project_id=project_id)

        projects_res = self.client.get("/api/projects")
        self.assertEqual(projects_res.status_code, 200)
        self.assertEqual(len(projects_res.json()), 1)
        self.assertEqual(projects_res.json()[0]["name"], "Operations")

        tasks_res = self.client.get("/api/tasks")
        self.assertEqual(tasks_res.status_code, 200)
        self.assertEqual(len(tasks_res.json()), 1)
        self.assertEqual(tasks_res.json()[0]["content"], "Ship docs update")

        stats_res = self.client.get("/api/stats")
        self.assertEqual(stats_res.status_code, 200)
        body = stats_res.json()
        self.assertEqual(body["total_tasks"], 1)
        self.assertIn("Operations", body["by_project"])


class DefaultAuthModeTests(BaseSecurityTestCase):
    require_auth = None

    def test_auth_is_required_when_env_is_unset(self):
        response = self.client.get("/api/tasks")
        self.assertEqual(response.status_code, 401)
        self.assertIn("X-Agent-Key", response.text)


if __name__ == "__main__":
    unittest.main()
