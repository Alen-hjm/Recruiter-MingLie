#!/usr/bin/env python3
"""
明猎 - AI 智能招聘系统
启动入口

用法：
    python run.py            # 默认 127.0.0.1:5000
    python run.py 5001       # 指定端口（向后兼容）
    MINGLIE_HOST=0.0.0.0 MINGLIE_PORT=8000 python run.py

关于监听地址：默认只绑回环地址。重构前写死 0.0.0.0，在公共网络下
同网段任何人都能访问到候选人简历库；要局域网访问请显式设 MINGLIE_HOST。
"""

import os
import sys


def main() -> None:
    # 保证相对路径（data/、browser_data/）按项目根目录解析
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    from app import config
    from app.web import create_app, init_all

    # 命令行端口优先级最高（兼容旧的 python run.py 5001 用法）
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        config.PORT = int(sys.argv[1])
    host = config.HOST
    port = config.PORT

    init_all()
    app = create_app()

    if host not in ("127.0.0.1", "localhost") and not os.environ.get("MINGLIE_SECRET_KEY"):
        print("  ! 正在监听非回环地址，建议同时设置 MINGLIE_SECRET_KEY 与环境变量化的 LLM Key")

    print("\n  ✦ 明猎 AI 招聘系统")
    print("  ✦ 地址: http://%s:%s" % (host, port))
    if not config.ALLOW_LEGACY_KEY_AUTH:
        print("  ✦ 遗留 Key 鉴权已关闭（扩展需使用访问令牌）")
    print("  ✦ 按 Ctrl+C 停止\n")

    app.run(host=host, port=port, debug=config.DEBUG)


if __name__ == "__main__":
    main()
