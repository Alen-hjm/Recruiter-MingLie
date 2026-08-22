#!/usr/bin/env python3
"""
明猎 - AI 智能招聘系统
启动入口
"""
import sys
import os

# 确保工作目录正确
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from app.models import init_db
from app.knowledge_engine import init_knowledge_db

# 延迟导入 server，确保 models 先初始化
from app.server import app

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    init_db()
    init_knowledge_db()
    print(f"\n  ✦ 明猎 AI 招聘系统")
    print(f"  ✦ 地址: http://localhost:{port}")
    print(f"  ✦ 按 Ctrl+C 停止\n")
    app.run(host="0.0.0.0", port=port, debug=False)
