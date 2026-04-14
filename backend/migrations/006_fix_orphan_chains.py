"""
Migration 006: Fix orphan chain rows.

Parity with the delega-api coherence fix (hosted: delega-dev/delega-api#28).
Zeroes delegation_depth and re-opens status for any rows where
delegation_depth > 0 but parent_task_id / root_task_id are NULL — those
rows could not have been created via the server's /tasks or
/tasks/:id/delegate handlers in their current form, but may exist from
direct SQL writes, prior buggy API versions, or imports.

Idempotent: reports "no orphans" and exits cleanly if the DB is already
coherent, which is the expected case for installs minted after the
handler-level guards landed in the same PR as this migration.

Usage:
    python backend/migrations/006_fix_orphan_chains.py
    DELEGA_DB_PATH=/path/to/custom.db python backend/migrations/006_fix_orphan_chains.py
"""
import sqlite3
import os


def migrate(db_path: str):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute(
        """
        SELECT COUNT(*) FROM tasks
         WHERE parent_task_id IS NULL
           AND root_task_id IS NULL
           AND delegation_depth > 0
        """
    )
    orphans = cur.fetchone()[0]

    if orphans == 0:
        print("  No orphan rows found. DB is coherent; nothing to do.")
        conn.close()
        return

    cur.execute(
        """
        UPDATE tasks
           SET delegation_depth = 0,
               status = CASE
                 WHEN status = 'delegated' THEN 'open'
                 ELSE status
               END,
               updated_at = datetime('now')
         WHERE parent_task_id IS NULL
           AND root_task_id IS NULL
           AND delegation_depth > 0
        """
    )
    conn.commit()
    conn.close()
    print(f"  Normalized {orphans} orphan row(s): delegation_depth → 0, status 'delegated' → 'open'.")
    print("Migration 006 complete.")


if __name__ == "__main__":
    default_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "delega.db",
    )
    db_path = os.environ.get("DELEGA_DB_PATH", default_path)
    print(f"Running migration on: {db_path}")
    migrate(db_path)
