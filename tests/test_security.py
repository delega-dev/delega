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
        agent = self.models.Agent(name=f"agent-{secrets.token_hex(4)}", api_key=api_key, active=True)
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
                "url": "https://example.com/webhook",
                "events": ["task.created"],
                "secret": "super-secret-value",
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertNotIn("secret", body)
        self.assertEqual(body["url"], "https://example.com/webhook")

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


if __name__ == "__main__":
    unittest.main()
