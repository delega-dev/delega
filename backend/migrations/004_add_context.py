"""
Migration 004: Add context blob to tasks

Adds:
- context column (JSON, nullable) for persistent agent state

Safe to run multiple times.
"""
import sqlite3
import os


def migrate(db_path: str):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("PRAGMA table_info(tasks)")
    existing = {row[1] for row in cur.fetchall()}

    if "context" not in existing:
        cur.execute("ALTER TABLE tasks ADD COLUMN context TEXT")  # JSON stored as TEXT in SQLite
        print("  Added context column")

    conn.commit()
    conn.close()
    print("Migration 004 complete.")


if __name__ == "__main__":
    default_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "delega.db"
    )
    db_path = os.environ.get("DELEGA_DB_PATH", default_path)
    print(f"Running migration on: {db_path}")
    migrate(db_path)
