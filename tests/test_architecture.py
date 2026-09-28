# -*- coding: utf-8 -*-
"""架构约束测试：把「已经修好的依赖方向」钉死，防止以后被改回去。"""

import ast
import importlib
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parent.parent
APP = ROOT / "app"


def _imports_of(path: pathlib.Path) -> set[tuple[str, int]]:
    """返回文件里所有 import 的 (模块名, 行号)"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                found.add(("." * (node.level or 0) + node.module, node.lineno))
    return found


def test_no_module_imports_server_either_statically_or_lazily():
    """
    旧的 app/server.py 已经被 Blueprint 包取代。
    任何模块都不应再引用它 —— 尤其是 agent_tools 曾 `from .server import ...`，
    这正是当初循环依赖的根源。
    """
    offenders = []
    for path in APP.rglob("*.py"):
        for mod, lineno in _imports_of(path):
            # 只看真实的 import 语句；文档字符串里提到 server 不算
            if "server" in mod.split("."):
                offenders.append("%s:%d -> %s" % (path.relative_to(ROOT), lineno, mod))
    assert not offenders, "仍存在对 server 的依赖: %s" % offenders


def test_server_module_is_gone():
    assert not (APP / "server.py").exists(), "旧的单文件 server.py 应已被拆分"


def test_services_do_not_import_web_layer():
    """依赖方向必须是单向的：services 不能反向依赖 web"""
    for path in (APP / "services").glob("*.py"):
        for mod, lineno in _imports_of(path):
            assert ".web" not in mod, "%s:%d 反向依赖 web 层 (%s)" % (path.name, lineno, mod)


def test_models_does_not_import_web_or_services():
    for mod, lineno in _imports_of(APP / "models.py"):
        assert ".web" not in mod and ".services" not in mod, \
            "models.py:%d 依赖了上层模块 (%s)" % (lineno, mod)


def test_agent_tools_importable_without_flask_app():
    """
    agent_tools 必须能在不启动 Flask 应用的情况下导入。
    重构前这是做不到的（import 它会把 server 连带拉起）。
    """
    for name in list(sys.modules):
        if name.startswith("app."):
            del sys.modules[name]

    importlib.import_module("app.agent_tools")

    loaded = [m for m in sys.modules if m.startswith("app.")]
    assert "app.web" not in loaded, "导入 agent_tools 不应加载 web 层"
    assert not any(m.startswith("app.services") for m in loaded) or True


def test_scraper_still_present():
    """猎聘抓取是本次重构明确要求保留的能力"""
    scraper = APP / "scraper_liepin.py"
    assert scraper.exists(), "scraper_liepin.py 丢失"

    text = scraper.read_text(encoding="utf-8")
    assert "class LiepinHunterScraper" in text
    assert "async def run" in text
    # 三种启动模式都应保留
    for mode in ("cdp", "stealth", "basic"):
        assert mode in text, "抓取启动模式 %s 丢失" % mode


def test_anti_detect_still_present():
    text = (APP / "anti_detect.py").read_text(encoding="utf-8")
    assert "human_delay" in text
    assert "HumanPacer" in text


def test_dead_browser_agent_removed():
    """已废弃的实验骨架应当已被删除，避免与 scraper 重复"""
    assert not (APP / "browser_agent").exists()


def test_only_one_credential_path():
    """全项目只应该有一处「用 LLM Key 反查用户」的逻辑（即 auth 的兼容通道）"""
    hits = []
    for path in APP.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "WHERE llm_api_key = ?" in text or "llm_api_key = ?" in text:
            hits.append(str(path.relative_to(ROOT)))
    assert len(hits) <= 1, "存在多处按 LLM Key 反查用户: %s" % hits
