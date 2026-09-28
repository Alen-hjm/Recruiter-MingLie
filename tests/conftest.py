# -*- coding: utf-8 -*-
"""
测试共用夹具。

关键点：测试绝不碰真的 data/minglie.db —— 那里面是真实候选人简历。
conftest 在每个测试前把 config.DB_PATH 指向 tmp_path 下的临时库。
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """把数据库切到临时目录，并完成建表"""
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)

    # models.DB_PATH 在 import 时就绑定了 config.DB_PATH，需要显式覆盖
    from app import models
    monkeypatch.setattr(models, "DB_PATH", db_path)

    from app import auth
    monkeypatch.setattr(auth, "config", config)

    models.init_db()
    auth.init_auth_db()
    return db_path


@pytest.fixture
def app_client(temp_db):
    """一个干净的 Flask 测试客户端"""
    from app.web import create_app
    application = create_app()
    application.config.update(TESTING=True)
    with application.test_client() as client:
        yield client


@pytest.fixture
def user_id(temp_db):
    """建一个测试用户，返回其 id"""
    from app.models import create_user
    user = create_user("tester", "password1234", "测试用户")
    return user.id


@pytest.fixture
def logged_in_client(app_client, user_id):
    """已登录的测试客户端"""
    resp = app_client.post(
        "/login",
        data={"username": "tester", "password": "password1234"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 200), "登录失败"
    return app_client
