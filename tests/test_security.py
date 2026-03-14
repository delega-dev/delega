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


class SecurityHardeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.TemporaryDirectory()
        db_path = Path(cls.tempdir.name) / "delega-test.db"
        cls.db_path = db_path
        os.environ["DELEGA_DATABASE_URL"] = f"sqlite:///{db_path}"
        os.environ["DELEGA_REQUIRE_AUTH"] = "true"
        os.environ["DELEGA_MAX_BODY_BYTES"] = "1024"

        for module_name in ["database", "models", "schemas", "main"]:
            sys.modules.pop(module_name, None)

        cls.database = importlib.import_module("database")
        cls.models = importlib.import_module("models")
        cls.main = importlib.import_module("main")
        cls.client = TestClient(cls.main.app)

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
        db.query(self.models.Task).delete()
        db.query(self.models.Project).delete()
        db.query(self.models.Agent).delete()

        api_key = f"dlg_{secrets.token_hex(16)}"
        key_material = self.main.create_agent_key_material(api_key)
        agent = self.models.Agent(
            name=f"agent-{secrets.token_hex(4)}",
            api_key=f"migrated_seed_{secrets.token_hex(4)}",
            active=True,
            is_admin=True,
            **key_material,
        )
        db.add(agent)
        db.commit()
        db.refresh(agent)
        db.close()

        self.api_key = api_key
        self.agent_id = agent.id

    def test_require_auth_applies_to_all_api_routes(self):
        response = self.client.get("/api/projects")
        self.assertEqual(response.status_code, 401)
        self.assertIn("X-Agent-Key", response.text)

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

    def test_non_admin_agents_cannot_use_admin_routes(self):
        db = self.database.SessionLocal()
        worker_key = f"dlg_{secrets.token_hex(16)}"
        worker_material = self.main.create_agent_key_material(worker_key)
        worker = self.models.Agent(
            name=f"worker-{secrets.token_hex(4)}",
            api_key=f"migrated_seed_{secrets.token_hex(4)}",
            active=True,
            is_admin=False,
            **worker_material,
        )
        db.add(worker)
        db.commit()
        db.refresh(worker)
        db.close()

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
            f"/api/agents/{worker.id}/rotate-key",
            headers={"X-Agent-Key": worker_key},
        )
        self.assertEqual(rotate_res.status_code, 200)
        self.assertIn("api_key", rotate_res.json())

    def test_non_admin_agents_only_see_their_tasks(self):
        db = self.database.SessionLocal()
        worker_key = f"dlg_{secrets.token_hex(16)}"
        worker_material = self.main.create_agent_key_material(worker_key)
        worker = self.models.Agent(
            name=f"worker-{secrets.token_hex(4)}",
            api_key=f"migrated_seed_{secrets.token_hex(4)}",
            active=True,
            is_admin=False,
            **worker_material,
        )
        db.add(worker)
        db.commit()
        db.refresh(worker)
        worker_id = worker.id
        db.close()

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
        db = self.database.SessionLocal()
        legacy_key = f"dlg_{secrets.token_hex(16)}"
        legacy_agent = self.models.Agent(
            name=f"legacy-{secrets.token_hex(4)}",
            api_key=legacy_key,
            active=True,
            is_admin=False,
        )
        db.add(legacy_agent)
        db.commit()
        db.close()

        response = self.client.get("/api/tasks", headers={"X-Agent-Key": legacy_key})
        self.assertEqual(response.status_code, 401)

    def test_auth_migration_backfills_plaintext_agent_keys(self):
        db = self.database.SessionLocal()
        legacy_key = f"dlg_{secrets.token_hex(16)}"
        legacy_agent = self.models.Agent(
            name=f"legacy-backfill-{secrets.token_hex(4)}",
            api_key=legacy_key,
            active=True,
            is_admin=False,
        )
        db.add(legacy_agent)
        db.commit()
        legacy_id = legacy_agent.id
        db.close()

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


if __name__ == "__main__":
    unittest.main()
