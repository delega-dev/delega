"""
Migration 005: Harden agent auth storage and add admin flag

Adds:
- agents.key_hash
- agents.key_lookup
- agents.key_salt
- agents.key_prefix
- agents.is_admin

Marks the earliest agent as admin if none are marked.
Backfills recoverable plaintext agent keys into split storage.
Safe to run multiple times.
"""
import os
import sqlite3
import hashlib
import secrets


KEY_DERIVE_ITERATIONS = int(os.environ.get("DELEGA_KEY_DERIVE_ITERATIONS", "100000"))


def key_prefix(api_key: str) -> str:
    return api_key[:12] + "..."


def derive_key_lookup(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:32]


def derive_key_hash(api_key: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256",
        api_key.encode("utf-8"),
        salt.encode("utf-8"),
        KEY_DERIVE_ITERATIONS,
    ).hex()


def backfill_agent_keys(cur: sqlite3.Cursor) -> None:
    rows = cur.execute(
        """
        SELECT id, api_key
        FROM agents
        WHERE COALESCE(key_hash, '') = ''
           OR COALESCE(key_lookup, '') = ''
           OR COALESCE(key_salt, '') = ''
           OR COALESCE(key_prefix, '') = ''
        """
    ).fetchall()

    backfilled = 0
    skipped = 0
    for agent_id, api_key in rows:
        if not api_key or api_key.startswith("migrated_"):
            skipped += 1
            continue

        salt = secrets.token_hex(16)
        cur.execute(
            """
            UPDATE agents
            SET key_hash = ?,
                key_lookup = ?,
                key_salt = ?,
                key_prefix = ?,
                api_key = ?
            WHERE id = ?
            """,
            (
                derive_key_hash(api_key, salt),
                derive_key_lookup(api_key),
                salt,
                key_prefix(api_key),
                f"migrated_{agent_id}",
                agent_id,
            ),
        )
        backfilled += 1

    if backfilled:
        print(f"  Backfilled {backfilled} plaintext agent key(s)")
    if skipped:
        print(f"  Skipped {skipped} agent(s) without recoverable plaintext keys")


def migrate(db_path: str):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("PRAGMA table_info(agents)")
    existing = {row[1] for row in cur.fetchall()}

    if "key_hash" not in existing:
        cur.execute("ALTER TABLE agents ADD COLUMN key_hash TEXT")
        print("  Added agents.key_hash")
    if "key_lookup" not in existing:
        cur.execute("ALTER TABLE agents ADD COLUMN key_lookup TEXT")
        print("  Added agents.key_lookup")
    if "key_salt" not in existing:
        cur.execute("ALTER TABLE agents ADD COLUMN key_salt TEXT")
        print("  Added agents.key_salt")
    if "key_prefix" not in existing:
        cur.execute("ALTER TABLE agents ADD COLUMN key_prefix TEXT")
        print("  Added agents.key_prefix")
    if "is_admin" not in existing:
        cur.execute("ALTER TABLE agents ADD COLUMN is_admin BOOLEAN DEFAULT 0")
        print("  Added agents.is_admin")

    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_agents_key_lookup ON agents (key_lookup)")

    admin_count = cur.execute(
        "SELECT COUNT(*) FROM agents WHERE COALESCE(is_admin, 0) = 1"
    ).fetchone()[0]
    if admin_count == 0:
        cur.execute(
            """
            UPDATE agents
            SET is_admin = 1
            WHERE id = (
                SELECT id
                FROM agents
                ORDER BY created_at ASC, id ASC
                LIMIT 1
            )
            """
        )
        print("  Seeded first agent as admin")

    backfill_agent_keys(cur)

    conn.commit()
    conn.close()
    print("Migration 005 complete.")


if __name__ == "__main__":
    default_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "delega.db"
    )
    db_path = os.environ.get("DELEGA_DB_PATH", default_path)
    print(f"Running migration on: {db_path}")
    migrate(db_path)
