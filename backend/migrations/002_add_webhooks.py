"""
Migration 002: Add webhook tables

Adds:
- webhooks table (id, agent_id, url, events, secret, active, created_at, failure_count)
- webhook_deliveries table (id, webhook_id, event, payload, status_code, response_body, success, created_at)

Safe to run multiple times — checks for table existence first.
"""
import sqlite3
import os


def migrate(db_path: str):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Create webhooks table if not exists
    cur.execute("""
        CREATE TABLE IF NOT EXISTS webhooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
            url TEXT NOT NULL,
            events TEXT DEFAULT '[]',
            secret TEXT,
            active BOOLEAN DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            failure_count INTEGER DEFAULT 0
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS ix_webhooks_agent_id ON webhooks (agent_id)")

    # Create webhook_deliveries table if not exists
    cur.execute("""
        CREATE TABLE IF NOT EXISTS webhook_deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            webhook_id INTEGER NOT NULL REFERENCES webhooks(id) ON DELETE CASCADE,
            event TEXT NOT NULL,
            payload TEXT NOT NULL,
            status_code INTEGER,
            response_body TEXT,
            success BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS ix_webhook_deliveries_webhook_id ON webhook_deliveries (webhook_id)")

    conn.commit()
    conn.close()
    print("Migration 002 complete.")


if __name__ == "__main__":
    default_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "delega.db"
    )
    db_path = os.environ.get("DELEGA_DB_PATH", default_path)
    print(f"Running migration on: {db_path}")
    migrate(db_path)
