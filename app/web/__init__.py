# -*- coding: utf-8 -*-
"""
Web 层：Flask 应用工厂 + Blueprint 分包。

重构前 app/server.py 是单文件约 89KB、71 个 @app.route 混在一起。现在按职责分包：

    pages.py          13 个页面路由（渲染模板）
    api_core.py       统计、设置、岗位、搜索策略、简历编辑/删除
    api_tasks.py      抓取 / 评分 / 报告
    api_agent.py      Agent 对话、记忆、候选人管道、知识库
    api_extension.py  给 Chrome 扩展用的 18 个接口（令牌鉴权）
    api_tokens.py     扩展访问令牌的管理接口

每个 blueprint 只依赖 app/services 与 app/models，不再互相 import。
"""

from flask import Flask
from flask_login import LoginManager

from .. import config
from ..auth import init_auth_db
from ..models import get_user_by_id, init_db


login_manager = LoginManager()


@login_manager.user_loader
def load_user(user_id):
    return get_user_by_id(int(user_id))


def create_app() -> Flask:
    """应用工厂。测试里可以直接调用它拿到一个干净实例。"""
    app = Flask(
        __name__,
        template_folder=str(config.TEMPLATE_DIR),
        static_folder=str(config.STATIC_DIR),
    )

    # 会话密钥：来自环境变量或 data/.secret_key（不再每次重启随机）
    app.secret_key = config.SECRET_KEY
    app.config["MAX_CONTENT_LENGTH"] = config.MAX_CONTENT_LENGTH_MB * 1024 * 1024
    app.config["JSON_AS_ASCII"] = False

    # 跨域：只对扩展接口放开，且白名单默认限定到扩展协议与本地回环
    from flask_cors import CORS
    CORS(app, resources={
        r"/api/extension/*": {
            "origins": config.EXTENSION_ORIGINS or [
                "chrome-extension://*",
                "http://localhost:*",
                "http://127.0.0.1:*",
            ],
            "allow_headers": ["Content-Type", "X-API-Token", "X-API-Key"],
        }
    })

    login_manager.init_app(app)
    login_manager.login_view = "pages.login"
    login_manager.login_message = "请先登录"

    # 注册 blueprint
    from .api_agent import bp as agent_bp
    from .api_core import bp as core_bp
    from .api_extension import bp as extension_bp
    from .api_tasks import bp as tasks_bp
    from .api_tokens import bp as tokens_bp
    from .pages import bp as pages_bp

    app.register_blueprint(pages_bp)
    app.register_blueprint(core_bp)
    app.register_blueprint(tasks_bp)
    app.register_blueprint(agent_bp)
    app.register_blueprint(extension_bp)
    app.register_blueprint(tokens_bp)

    return app


def init_all() -> None:
    """初始化数据库（业务表 + 令牌表）。启动时调用一次。"""
    config.ensure_dirs()
    init_db()
    init_auth_db()
    try:
        from ..knowledge_engine import init_knowledge_db
        init_knowledge_db()
    except Exception as e:  # 知识库是可选增强，失败不应阻塞启动
        print("[启动] 知识库初始化跳过: %s" % e)
