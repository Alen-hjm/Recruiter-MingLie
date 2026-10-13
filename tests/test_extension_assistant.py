"""Extension and workspace share run state without sharing users' data."""
from app.auth import create_token
from app.models import create_user, update_settings
from app.web import api_agent


def test_extension_recovers_shared_run_and_protects_running_chat(logged_in_client, user_id, monkeypatch):
    token = create_token(user_id, "extension fixture")
    headers = {"X-API-Token": token}
    monkeypatch.setattr(api_agent, "_agent_runs", {user_id: {"id": "fictional", "status": "running"}})
    monkeypatch.setattr(api_agent, "_agent_sessions", {api_agent._session_key(user_id): [{"role": "user", "content": "虚构招聘需求"}]})
    assert logged_in_client.get("/api/extension/chat/run", headers=headers).json["data"]["id"] == "fictional"
    assert logged_in_client.get("/api/extension/chat/history", headers=headers).json["data"][0]["content"] == "虚构招聘需求"
    assert logged_in_client.post("/api/extension/chat/run", headers=headers, json={"message": "重复需求"}).status_code == 409
    assert logged_in_client.post("/api/extension/chat/clear", headers=headers).status_code == 409
    api_agent._agent_runs[user_id]["status"] = "done"
    assert logged_in_client.post("/api/extension/chat/clear", headers=headers).status_code == 200
    assert logged_in_client.get("/api/chat/history").json["data"] == []


def test_extension_run_requires_token_and_isolates_users(app_client, user_id, monkeypatch):
    monkeypatch.setattr(api_agent, "_agent_runs", {user_id: {"id": "private", "status": "running"}})
    monkeypatch.setattr(api_agent, "_agent_sessions", {api_agent._session_key(user_id): [{"role": "user", "content": "private"}]})
    other = create_user("other-extension", "password1234").id
    headers = {"X-API-Token": create_token(other, "other")}
    for path in ["/api/extension/chat/run", "/api/extension/chat/history"]:
        assert app_client.get(path).status_code == 401
    assert app_client.get("/api/extension/chat/run", headers=headers).json["data"] is None
    assert app_client.get("/api/extension/chat/history", headers=headers).json["data"] == []
    assert app_client.post("/api/extension/chat/clear", headers=headers).status_code == 200
    assert user_id in api_agent._agent_runs


def test_extension_starts_background_run_with_authenticated_owner(app_client, user_id, monkeypatch):
    from threading import Event
    finished = Event()
    calls = []
    monkeypatch.setattr(api_agent, "_agent_runs", {})
    monkeypatch.setattr(api_agent, "_agent_sessions", {})
    update_settings(user_id, llm_api_key="fictional-key")
    def runner(app, uid, run_id, message):
        calls.append((uid, message))
        finished.set()
    monkeypatch.setattr(api_agent, "_run_chat_background", runner)
    headers = {"X-API-Token": create_token(user_id, "fixture")}
    response = app_client.post("/api/extension/chat/run", headers=headers, json={"message": "找10位虚构候选人"})
    assert response.status_code == 202
    assert finished.wait(2)
    assert calls == [(user_id, "找10位虚构候选人")]
    assert app_client.post("/api/extension/chat/run", headers=headers, json={"message": ""}).status_code == 400
