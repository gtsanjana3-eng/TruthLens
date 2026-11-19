"""Authentication, authorization, input validation and security controls."""
from app import audit
from app.database import SessionLocal
from app.models import AuditLog
from app.security import hash_password, verify_password

from .conftest import PASSWORD, register


def test_password_hashing_roundtrip():
    h = hash_password("Abcdefghij1")
    assert h != "Abcdefghij1" and h.startswith("$2")
    assert verify_password("Abcdefghij1", h)
    assert not verify_password("wrong", h)
    assert not verify_password("x", "not-a-hash")


def test_register_login_and_me(client):
    headers, user = register(client)
    assert user["role"] == "admin"  # first user bootstraps admin
    _, second = register(client, "bob@example.com", "Bob")
    assert second["role"] == "investigator"
    r = client.post("/api/auth/login", json={"email": "ALICE@example.com", "password": PASSWORD})
    assert r.status_code == 200
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"})
    assert me.json()["email"] == "alice@example.com"
    assert "password_hash" not in me.text


def test_weak_password_and_duplicate_rejected(client):
    r = client.post("/api/auth/register", json={"email": "c@example.com", "full_name": "C", "password": "short"})
    assert r.status_code == 422
    r = client.post("/api/auth/register", json={"email": "c@example.com", "full_name": "Carol", "password": "alllowercase123"})
    assert r.status_code == 422
    register(client, "c@example.com", "Carol")
    r = client.post("/api/auth/register", json={"email": "c@example.com", "full_name": "Carol", "password": PASSWORD})
    assert r.status_code == 409


def test_login_failures_generic_and_lockout(client):
    register(client)
    r = client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "x"})
    assert r.status_code == 401 and r.json()["detail"] == "Invalid email or password"
    for _ in range(5):
        assert client.post("/api/auth/login", json={"email": "alice@example.com", "password": "Wrong-pass1"}).status_code == 401
    r = client.post("/api/auth/login", json={"email": "alice@example.com", "password": PASSWORD})
    assert r.status_code == 423


def test_protected_endpoints_require_token(client):
    for path in ("/api/cases", "/api/auth/me", "/api/history", "/api/cases/dashboard/stats"):
        assert client.get(path).status_code == 401
    assert client.get("/api/cases", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_tampered_token_rejected(client, auth):
    token = auth["Authorization"].split()[1]
    bad = token[:-2] + ("AA" if token[-2:] != "AA" else "BB")
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {bad}"}).status_code == 401


def test_case_isolation_between_users(client, auth, case_id):
    other, _ = register(client, "eve@example.com", "Eve")
    assert client.get(f"/api/cases/{case_id}", headers=other).status_code == 404
    assert client.get(f"/api/cases/{case_id}/evidence", headers=other).status_code == 404
    assert client.post(f"/api/cases/{case_id}/analyze", headers=other).status_code == 404
    assert client.get("/api/cases", headers=other).json()["total"] == 0
    assert client.patch(f"/api/cases/{case_id}", json={"title": "hijack"}, headers=other).status_code == 404
    assert client.post(f"/api/cases/{case_id}/members", json={"email": "eve@example.com"}, headers=auth).status_code in (404, 405)  # sharing removed


def test_human_review_feature_removed(client, auth, case_id):
    assert client.post(f"/api/cases/{case_id}/notes", json={"body": "x", "verdict": "accepted"}, headers=auth).status_code in (404, 405)
    assert client.get(f"/api/cases/{case_id}/notes", headers=auth).status_code in (404, 405)


def test_admin_only_endpoints(client, auth):
    other, _ = register(client, "eve@example.com", "Eve")
    assert client.get("/api/auth/users", headers=other).status_code == 403
    assert client.get("/api/audit/verify", headers=other).status_code == 403
    assert client.get("/api/auth/users", headers=auth).status_code == 200


def test_input_validation_messages(client, auth):
    r = client.post("/api/cases", json={"title": "x"}, headers=auth)
    assert r.status_code == 422
    body = r.json()
    assert body["detail"] == "Validation failed" and body["errors"][0]["field"] == "title"
    assert client.post("/api/cases", json={"title": "ok title", "priority": "nuclear"}, headers=auth).status_code == 422


def test_security_headers_and_rate_limit(client, monkeypatch):
    r = client.get("/api/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["X-Frame-Options"] == "DENY"
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "rate_limit_auth_per_minute", 3)
    codes = [client.post("/api/auth/login", json={"email": "a@example.com", "password": "x"}).status_code for _ in range(5)]
    assert 429 in codes and "Retry-After" in client.post("/api/auth/login", json={"email": "a@example.com", "password": "x"}).headers


def test_audit_chain_detects_tampering(client, auth, case_id):
    db = SessionLocal()
    try:
        assert audit.verify_chain(db)["valid"]
        row = db.query(AuditLog).order_by(AuditLog.id).first()
        row.detail = {"tampered": True}
        db.commit()
        res = audit.verify_chain(db)
        assert not res["valid"] and res["broken_at"] == row.id
    finally:
        db.close()


def test_profile_and_password_change(client, auth):
    r = client.patch("/api/auth/me", json={"full_name": "Alice A.", "preferences": {"reduced_motion": True, "evil": 1}}, headers=auth)
    assert r.json()["full_name"] == "Alice A." and r.json()["preferences"] == {"reduced_motion": True}
    assert client.post("/api/auth/me/password", json={"current_password": "nope", "new_password": "N3wPassword!!"}, headers=auth).status_code == 400
    assert client.post("/api/auth/me/password", json={"current_password": PASSWORD, "new_password": "N3wPassword!!"}, headers=auth).status_code == 204
    assert client.post("/api/auth/login", json={"email": "alice@example.com", "password": "N3wPassword!!"}).status_code == 200


def test_production_requires_strong_secret(monkeypatch):
    import pytest
    from pydantic import ValidationError

    from app.config import Settings

    with pytest.raises(ValidationError):
        Settings(app_env="production", jwt_secret="dev-only-insecure-secret-change-me")
    assert Settings(app_env="production", jwt_secret="x" * 40).app_env == "production"
