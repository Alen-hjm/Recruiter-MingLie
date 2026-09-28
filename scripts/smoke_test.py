# -*- coding: utf-8 -*-
"""启动冒烟测试：真的把 Flask 应用跑起来，走一遍关键路径。

为什么不能只靠 pytest：pytest 用的是 test_client，不经过真正的网络栈、
也不触发 run.py 的初始化顺序。这里的目的是验证「按 README 敲的那条命令
确实能起来并工作」，以及几条关键安全属性真的生效。

用法：
    python scripts/smoke_test.py

输出：✓ 通过 / ✗ 失败；服务起不来会直接打印进程输出。
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 5077
BASE = "http://127.0.0.1:%d" % PORT


def call(path, data=None, headers=None, method=None):
    url = BASE + path
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method or ("POST" if body else "GET"))
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def main():
    env = dict(os.environ)
    env.update({
        "MINGLIE_DATA_DIR": str(ROOT / ".smoke_data"),
        "MINGLIE_PORT": str(PORT),
        "MINGLIE_HOST": "127.0.0.1",
        "MINGLIE_DEBUG": "0",
    })

    print("启动服务 (端口 %d) ..." % PORT)
    proc = subprocess.Popen(
        [sys.executable, "run.py"],
        cwd=str(ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )

    results = []
    try:
        ready = False
        for _ in range(40):
            time.sleep(0.5)
            try:
                call("/login")
                ready = True
                break
            except Exception:
                if proc.poll() is not None:
                    break
        if not ready:
            out = proc.stdout.read() if proc.stdout else ""
            print("服务未就绪，进程输出：\n%s" % out[:3000])
            return 1
        print("服务已就绪。\n")

        code, _ = call("/login")
        results.append(("GET /login 可访问", code == 200, "HTTP %s" % code))

        # 关键安全属性：没有凭据就不该拿到任何数据
        code, _ = call("/api/extension/jobs")
        results.append(("扩展接口无凭据被拒", code == 401, "HTTP %s" % code))

        code, _ = call("/api/extension/jobs", headers={"X-API-Token": "ml_fake"})
        results.append(("伪造令牌被拒", code == 401, "HTTP %s" % code))

        # 凭据只从请求头读，不接受查询串（否则会进访问日志）
        code, _ = call("/api/extension/jobs?api_key=ml_fake")
        results.append(("查询串凭据被拒", code == 401, "HTTP %s" % code))

        code, _ = call("/static/css/style.css")
        results.append(("静态资源可访问", code == 200, "HTTP %s" % code))

        try:
            with urllib.request.urlopen(BASE + "/", timeout=5) as r:
                code = r.status
        except urllib.error.HTTPError as e:
            code = e.code
        results.append(("首页受登录保护", code in (200, 302), "HTTP %s" % code))

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    print("冒烟测试结果：")
    ok = True
    for name, passed, detail in results:
        print("  %s %-22s %s" % ("✓" if passed else "✗", name, detail))
        ok = ok and passed
    print("\n%s" % ("全部通过" if ok else "存在失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
