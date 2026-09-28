# -*- coding: utf-8 -*-
"""Web 层冒烟测试：路由是否注册、鉴权是否生效、跨用户访问是否被挡住。"""


def test_all_pages_require_login(app_client):
    for path in ("/", "/scrape", "/score", "/jobs", "/candidates", "/agent", "/settings"):
        resp = app_client.get(path)
        assert resp.status_code == 302, "%s 未要求登录" % path
        assert "/login" in resp.headers["Location"]


def test_login_then_dashboard(logged_in_client):
    assert logged_in_client.get("/").status_code == 200


def test_route_inventory(app_client):
    """路由清单：确认 Blueprint 拆分后 71 个端点一个没丢"""
    rules = {r.rule for r in app_client.application.url_map.iter_rules()}
    expected = {
        "/login", "/register", "/logout",
        "/", "/scrape", "/score", "/report", "/jobs", "/settings",
        "/interview", "/candidates", "/agent", "/logs",
        "/api/stats", "/api/settings",
        "/api/jobs", "/api/jobs/<int:job_id>", "/api/jobs/<int:job_id>/resumes",
        "/api/jobs/<int:job_id>/strategies",
        "/api/strategies/<int:strategy_id>",
        "/api/resumes/edit", "/api/resumes/delete",
        "/api/scrape", "/api/scrape/<int:task_id>", "/api/scrape/list",
        "/api/score", "/api/score/<int:task_id>", "/api/score/list",
        "/api/report/<int:score_task_id>", "/api/report/candidate",
        "/api/report/candidate/download", "/api/report/templates",
        "/api/feedback", "/api/feedback/list",
        "/api/chat", "/api/chat/clear", "/api/chat/history",
        "/api/memory/stats", "/api/memory/list",
        "/api/candidates/<int:job_id>", "/api/candidate/action",
        "/api/pipeline/<int:job_id>", "/api/pipeline/update",
        "/api/jd/analyze",
        "/api/knowledge/status", "/api/knowledge/stats",
        "/api/knowledge/refresh", "/api/knowledge/query",
        "/api/tokens", "/api/tokens/<int:token_id>", "/api/tokens/revoke-all",
        "/api/extension/jobs", "/api/extension/score",
        "/api/extension/submit_batch", "/api/extension/submit_webpage",
        "/api/extension/resumes/<int:job_id>",
        "/api/extension/filter_rules/<int:job_id>",
        "/api/extension/strategies/<int:job_id>",
        "/api/extension/pipeline/<int:job_id>",
        "/api/extension/pipeline/<int:job_id>/add",
        "/api/extension/pipeline/<int:job_id>/delete",
        "/api/extension/chat", "/api/extension/chat/clear",
        "/api/extension/knowledge/stats", "/api/extension/knowledge/refresh",
        "/api/extension/knowledge/list", "/api/extension/knowledge/delete",
    }
    missing = expected - rules
    assert not missing, "缺失路由: %s" % sorted(missing)


def test_cross_user_job_access_blocked(app_client, temp_db):
    """越权防护：不能读别人的岗位简历"""
    from app.models import create_job, create_user

    alice = create_user("alice", "password1234").id
    bob = create_user("bob", "password1234").id
    alice_job = create_job(alice, "alice 的岗位", "kw", "jd")

    # bob 登录后访问 alice 的岗位
    app_client.post("/login", data={"username": "bob", "password": "password1234"})
    resp = app_client.get("/api/jobs/%d/resumes" % alice_job)
    assert resp.status_code == 404

    resp = app_client.post("/api/jobs/%d" % alice_job, method="DELETE")
    # Flask 用 POST 覆盖 DELETE 的场景这里不适用，直接测 DELETE
    assert resp.status_code in (404, 405)

    resp = app_client.delete("/api/jobs/%d" % alice_job)
    assert resp.status_code == 404

    # 确认岗位还在
    from app.models import get_job
    assert get_job(alice_job) is not None


def test_settings_whitelist(app_client, user_id, temp_db):
    """设置接口只允许白名单字段，防止写入任意列"""
    app_client.post("/login", data={"username": "tester", "password": "password1234"})
    resp = app_client.post("/api/settings", json={
        "llm_model": "deepseek-chat",
        "id": 9999,              # 不在白名单
        "user_id": 12345,        # 不在白名单
    })
    assert resp.status_code == 200

    from app.models import get_settings
    settings = get_settings(user_id)
    assert settings["llm_model"] == "deepseek-chat"
    assert settings["user_id"] == user_id  # 未被篡改


def test_api_key_not_leaked_in_settings_get(app_client, user_id, temp_db):
    """设置 GET 返回的是脱敏值，不是明文 Key"""
    from app.models import update_settings
    update_settings(user_id, llm_api_key="sk-abcdefghijklmnop")

    app_client.post("/login", data={"username": "tester", "password": "password1234"})
    body = app_client.get("/api/settings").get_data(as_text=True)

    assert "sk-abcdefghijklmnop" not in body
    assert "***" in body


def test_register_rejects_short_password(app_client, temp_db):
    resp = app_client.post("/register", data={
        "username": "newbie", "password": "123", "display_name": "x",
    }, follow_redirects=True)
    assert "至少 8 位".encode("utf-8") in resp.data
