"""
Migration 003: Add delegation fields to tasks

Adds columns:
- parent_task_id (FK → tasks.id)
- root_task_id (top of delegation chain)
- delegation_depth (0 = root)
- status (open, in_progress, delegated, blocked, failed, completed)

Sets root_task_id = id for all existing tasks (they're all roots).
Safe to run multiple times.
"""
import sqlite3
import os


def migrate(db_path: str):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Check which columns already exist
    cur.execute("PRAGMA table_info(tasks)")
    existing = {row[1] for row in cur.fetchall()}

    if "parent_task_id" not in existing:
        cur.execute("ALTER TABLE tasks ADD COLUMN parent_task_id INTEGER REFERENCES tasks(id)")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_tasks_parent_task_id ON tasks (parent_task_id)")
        print("  Added parent_task_id")

    if "root_task_id" not in existing:
        cur.execute("ALTER TABLE tasks ADD COLUMN root_task_id INTEGER")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_tasks_root_task_id ON tasks (root_task_id)")
        # Set root_task_id = id for all existing tasks
        cur.execute("UPDATE tasks SET root_task_id = id WHERE root_task_id IS NULL")
        print("  Added root_task_id (set existing tasks as roots)")

    if "delegation_depth" not in existing:
        cur.execute("ALTER TABLE tasks ADD COLUMN delegation_depth INTEGER DEFAULT 0")
        print("  Added delegation_depth")

    if "status" not in existing:
        cur.execute("ALTER TABLE tasks ADD COLUMN status TEXT DEFAULT 'open'")
        # Set completed tasks to 'completed' status
        cur.execute("UPDATE tasks SET status = 'completed' WHERE completed = 1")
        print("  Added status (synced with completed flag)")

    conn.commit()
    conn.close()
    print("Migration 003 complete.")


if __name__ == "__main__":
    default_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "delega.db"
    )
    db_path = os.environ.get("DELEGA_DB_PATH", default_path)
    print(f"Running migration on: {db_path}")
    migrate(db_path)
