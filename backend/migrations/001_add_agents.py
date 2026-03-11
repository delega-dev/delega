"""
Migration 001: Add agent identity model

Adds:
- agents table (id, name, display_name, api_key, description, permissions, active, created_at, last_seen_at)
- tasks.created_by_agent_id (FK → agents.id)
- tasks.assigned_to_agent_id (FK → agents.id)
- tasks.completed_by_agent_id (FK → agents.id)

Safe to run multiple times — checks for table/column existence first.
"""
import sqlite3
import sys
import os


def migrate(db_path: str):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Create agents table if not exists
    cur.execute("""
        CREATE TABLE IF NOT EXISTS agents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            display_name TEXT,
            api_key TEXT NOT NULL UNIQUE,
            description TEXT,
            permissions TEXT DEFAULT '[]',
            active BOOLEAN DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TIMESTAMP
        )
    """)
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_agents_name ON agents (name)")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_agents_api_key ON agents (api_key)")

    # Add agent columns to tasks table
    existing_columns = {row[1] for row in cur.execute("PRAGMA table_info(tasks)").fetchall()}

    for col in ["created_by_agent_id", "assigned_to_agent_id", "completed_by_agent_id"]:
        if col not in existing_columns:
            cur.execute(f"ALTER TABLE tasks ADD COLUMN {col} INTEGER REFERENCES agents(id)")
            print(f"  Added tasks.{col}")
        else:
            print(f"  tasks.{col} already exists, skipping")

    conn.commit()
    conn.close()
    print("Migration 001 complete.")


if __name__ == "__main__":
    # Default to the same path resolution as database.py
    default_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "delega.db"
    )
    db_path = os.environ.get("DELEGA_DB_PATH", default_path)
    print(f"Running migration on: {db_path}")
    migrate(db_path)
