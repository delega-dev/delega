"""Tests for the trusted-proxy / Remote-User auth path.

When a request arrives from an IP listed in API_TRUSTED_PROXY_IPS and
carries a Remote-User (or X-Forwarded-User) header naming an active
agent, the auth-gate middleware accepts it without an X-Agent-Key.
This is the contract that lets Authelia (in front of Caddy) hand off
identity to the backend on behalf of browser SSO sessions.

Untrusted source IPs sending the same header must be rejected.
"""

import os
import sys
import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["DELEGA_REQUIRE_AUTH"] = "true"
os.environ["DELEGA_DB_PATH"] = ":memory:"

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

import main
import models
from main import app, get_db, derive_key_hash, derive_key_lookup


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
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
    _orig = main.SessionLocal
    main.SessionLocal = TestSession
    main._rate_limiter._hits.clear()

    yield test_engine

    main.SessionLocal = _orig
    app.dependency_overrides.clear()


def make_agent(engine, name: str):
    import secrets as _secrets
    raw_key = "dlg_" + _secrets.token_urlsafe(32)
    salt = _secrets.token_hex(16)
    Session = sessionmaker(bind=engine)
    db = Session()
    agent = models.Agent(
        name=name,
        api_key=raw_key,
        key_hash=derive_key_hash(raw_key, salt),
        key_salt=salt,
        key_lookup=derive_key_lookup(raw_key),
        is_admin=False,
        permissions=[],
        active=True,
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    db.close()
    return agent.name


class TestTrustedProxyIdentity:
    def test_remote_user_from_trusted_proxy_authenticates(self, fresh_db, monkeypatch):
        agent_name = make_agent(fresh_db, "marty")
        monkeypatch.setenv("API_TRUSTED_PROXY_IPS", "192.168.10.220,127.0.0.1")
        client = TestClient(
            app, base_url="http://localhost", client=("192.168.10.220", 50001)
        )
        try:
            r = client.get("/api/tasks", headers={"Remote-User": agent_name})
            assert r.status_code == 200, r.text
        finally:
            client.close()

    def test_x_forwarded_user_also_accepted(self, fresh_db, monkeypatch):
        agent_name = make_agent(fresh_db, "doc")
        monkeypatch.setenv("API_TRUSTED_PROXY_IPS", "192.168.10.220")
        client = TestClient(
            app, base_url="http://localhost", client=("192.168.10.220", 50002)
        )
        try:
            r = client.get("/api/tasks", headers={"X-Forwarded-User": agent_name})
            assert r.status_code == 200, r.text
        finally:
            client.close()

    def test_email_form_resolves_to_local_part(self, fresh_db, monkeypatch):
        make_agent(fresh_db, "biff")
        monkeypatch.setenv("API_TRUSTED_PROXY_IPS", "192.168.10.220")
        client = TestClient(
            app, base_url="http://localhost", client=("192.168.10.220", 50003)
        )
        try:
            r = client.get("/api/tasks", headers={"Remote-User": "biff@mcmillan.io"})
            assert r.status_code == 200, r.text
        finally:
            client.close()

    def test_untrusted_source_ip_is_rejected(self, fresh_db, monkeypatch):
        make_agent(fresh_db, "george")
        monkeypatch.setenv("API_TRUSTED_PROXY_IPS", "192.168.10.220")
        client = TestClient(
            app, base_url="http://localhost", client=("192.168.10.221", 50004)
        )
        try:
            r = client.get("/api/tasks", headers={"Remote-User": "george"})
            assert r.status_code == 401
        finally:
            client.close()

    def test_unknown_user_from_trusted_proxy_is_rejected(self, fresh_db, monkeypatch):
        monkeypatch.setenv("API_TRUSTED_PROXY_IPS", "192.168.10.220")
        client = TestClient(
            app, base_url="http://localhost", client=("192.168.10.220", 50005)
        )
        try:
            r = client.get("/api/tasks", headers={"Remote-User": "ghost"})
            assert r.status_code == 401
        finally:
            client.close()

    def test_inactive_agent_from_trusted_proxy_is_rejected(self, fresh_db, monkeypatch):
        Session = sessionmaker(bind=fresh_db)
        db = Session()
        agent = models.Agent(
            name="strickland",
            api_key="dlg_xx",
            key_hash="x",
            key_salt="x",
            key_lookup="x",
            is_admin=False,
            permissions=[],
            active=False,
        )
        db.add(agent)
        db.commit()
        db.close()
        monkeypatch.setenv("API_TRUSTED_PROXY_IPS", "192.168.10.220")
        client = TestClient(
            app, base_url="http://localhost", client=("192.168.10.220", 50006)
        )
        try:
            r = client.get("/api/tasks", headers={"Remote-User": "strickland"})
            assert r.status_code == 401
        finally:
            client.close()
