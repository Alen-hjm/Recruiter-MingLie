from __future__ import annotations

from flask import Flask, jsonify

from app.config import Config
from app.db import init_database
from app.integrations import build_search_provider
from app.orchestration import WorkflowService
from app.repositories import Store


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, instance_path=str(Config.INSTANCE_PATH), template_folder="templates", static_folder="static")
    app.config.from_mapping({key: getattr(Config, key) for key in ("DATABASE", "SEARCH_PROVIDER", "SECRET_KEY", "AI_BASE_URL", "AI_API_KEY", "AI_MODEL", "BROWSER_DATA_DIR", "PLAYWRIGHT_CHANNEL", "SCRAPE_MAX_PAGES")})
    if test_config: app.config.update(test_config)
    init_database(app.config["DATABASE"])
    store = Store(app.config["DATABASE"])
    app.extensions["store"] = store
    from app.services.scraping import ScrapeService
    app.extensions["scrapes"] = ScrapeService(store, app.config["BROWSER_DATA_DIR"], app.config["PLAYWRIGHT_CHANNEL"], app.config["SCRAPE_MAX_PAGES"])
    app.extensions["workflows"] = WorkflowService(store, build_search_provider(app.config["SEARCH_PROVIDER"]), app.extensions["scrapes"])
    from app.api import api
    from app.web import web
    app.register_blueprint(api); app.register_blueprint(web)
    @app.errorhandler(404)
    def missing(_):
        if __import__('flask').request.path.startswith('/api/'): return jsonify({"error":{"code":"not_found","message":"resource not found","details":{}}}),404
        return "Not found",404
    return app
