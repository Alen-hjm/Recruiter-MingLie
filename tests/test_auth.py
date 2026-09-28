# -*- coding: utf-8 -*-
"""鉴权测试：重点覆盖「令牌与 LLM Key 解耦」这一安全修复。"""

import pytest


def test_token_is_not_llm_key(app_client, user_id, temp_db):
    """核心回归：扩展凭据不应等于 LLM API Key"""
    from app.auth import create_token
    from app.models import get_db, update_settings

    update_settings(user_id, llm_api_key="sk-super-secret-llm-key")

    token = create_token(user_id, "test")

    assert token.startswith("ml_")
    assert token != "sk-super-secret-llm-key"
    assert "sk-super-secret-llm-key" not in token

    # 数据库里不应该出现明文令牌
    conn = get_db()
    try:
        rows = conn.execute("SELECT token_hash, token_hint FROM api_tokens").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    stored = rows[0]["token_hash"]
    assert stored != token
    assert len(stored) == 64  # sha256 hex
    assert token not in rows[0]["token_hint"]


def test_token_grants_access(app_client, user_id, temp_db):
    from app.auth import create_token
    token = create_token(user_id, "test")

    resp = app_client.get("/api/extension/jobs", headers={"X-API-Token": token})
    assert resp.status_code == 200
    assert resp.get_json()["success"] is True


def test_no_credential_is_rejected(app_client, user_id, temp_db):
    resp = app_client.get("/api/extension/jobs")
    assert resp.status_code == 401
    assert resp.get_json()["code"] == "unauthorized"


def test_bogus_token_is_rejected(app_client, user_id, temp_db):
    resp = app_client.get("/api/extension/jobs", headers={"X-API-Token": "ml_not-a-real-token"})
    assert resp.status_code == 401


def test_revoked_token_is_rejected(app_client, user_id, temp_db):
    from app.auth import create_token, list_tokens, revoke_token
    token = create_token(user_id, "test")
    assert app_client.get("/api/extension/jobs", headers={"X-API-Token": token}).status_code == 200

    tid = list_tokens(user_id)[0]["id"]
    assert revoke_token(user_id, tid) is True

    assert app_client.get("/api/extension/jobs", headers={"X-API-Token": token}).status_code == 401


def test_cannot_revoke_other_users_token(app_client, temp_db):
    from app.auth import create_token, revoke_token
    from app.models import create_user

    u1 = create_user("alice", "password1234").id
    u2 = create_user("bob", "password1234").id
    create_token(u1, "alice-token")
    tid = create_token(u2, "bob-token")  # noqa: F841

    from app.auth import list_tokens
    alice_tid = list_tokens(u1)[0]["id"]
    # bob 去撤销 alice 的令牌必须失败
    assert revoke_token(u2, alice_tid) is False
    # alice 的令牌仍然有效
    assert list_tokens(u1)[0]["revoked_at"] is None


def test_legacy_key_auth_works_when_enabled(app_client, user_id, temp_db, monkeypatch):
    """兼容期：旧扩展发 X-API-Key（LLM Key）仍可用"""
    from app import config
    from app.models import update_settings

    monkeypatch.setattr(config, "ALLOW_LEGACY_KEY_AUTH", True)
    update_settings(user_id, llm_api_key="sk-legacy-key")

    resp = app_client.get("/api/extension/jobs", headers={"X-API-Key": "sk-legacy-key"})
    assert resp.status_code == 200


def test_legacy_key_auth_can_be_disabled(app_client, user_id, temp_db, monkeypatch):
    """关掉兼容开关后，LLM Key 不再能当凭据"""
    from app import config
    from app.models import update_settings

    monkeypatch.setattr(config, "ALLOW_LEGACY_KEY_AUTH", False)
    update_settings(user_id, llm_api_key="sk-legacy-key")

    resp = app_client.get("/api/extension/jobs", headers={"X-API-Key": "sk-legacy-key"})
    assert resp.status_code == 401


def test_query_string_credential_is_not_accepted(app_client, user_id, temp_db):
    """凭据只从请求头读 —— 避免密钥进入访问日志"""
    from app.auth import create_token
    token = create_token(user_id, "test")

    resp = app_client.get("/api/extension/jobs?api_key=%s" % token)
    assert resp.status_code == 401


def test_token_listing_hides_plaintext(app_client, user_id, temp_db):
    from app.auth import create_token
    secret = create_token(user_id, "test")

    # 未登录时被重定向到登录页（login_required 的默认行为）
    resp = app_client.get("/api/tokens")
    assert resp.status_code in (302, 401)

    # 用会话登录后再查
    app_client.post("/login", data={"username": "tester", "password": "password1234"})
    resp = app_client.get("/api/tokens")
    body = resp.get_data(as_text=True)
    assert secret not in body


def test_token_creation_returns_plaintext_once(app_client, user_id, temp_db):
    app_client.post("/login", data={"username": "tester", "password": "password1234"})
    resp = app_client.post("/api/tokens", json={"label": "my-ext"})
    data = resp.get_json()

    assert data["success"] is True
    assert data["token"].startswith("ml_")
    assert "warning" in data

    # 再次列表时不再出现明文
    listing = app_client.get("/api/tokens").get_data(as_text=True)
    assert data["token"] not in listing
