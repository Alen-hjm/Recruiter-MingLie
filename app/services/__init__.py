# -*- coding: utf-8 -*-
"""后台任务层：把抓取与评分的执行逻辑从路由里剥离出来。

为什么要有这一层：
  重构前 _run_scrape_thread / _run_score_thread 直接写在 server.py 里，
  而 agent_tools.py 为了复用它们不得不 `from .server import _run_score_thread`，
  于是「工具注册表 → HTTP 服务」形成循环依赖：单独 import agent_tools 会连带
  把整个 Flask 应用启动起来，测试无从下手。

  抽出来之后方向是单向的：
      web/*(路由) ──┐
      agent_tools ──┴──▶ services/* ──▶ models, scraper, analyzer
"""
