"""
猎聘猎头端简历抓取器 (h.liepin.com)
- 猎头账号登录后，搜索候选人简历
- 自动翻页 + 提取简历卡片 + 进详情页抓完整信息
"""

import asyncio
import json
import os
import random
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path
from datetime import datetime

import httpx
from playwright.async_api import async_playwright, Page, BrowserContext
from rich.console import Console

from .anti_detect import (
    human_delay, human_click, human_scroll,
    reading_delay, page_load_delay, between_cards_delay,
    HumanPacer, STEALTH_ARGS, STEALTH_JS,
)

console = Console()


def _find_free_port() -> int:
    """找一个空闲端口"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _wait_cdp_ready(port: int, timeout: int = 30) -> bool:
    """通过 HTTP 检查 CDP 是否就绪"""
    url = f"http://127.0.0.1:{port}/json/version"
    start = time.time()
    last_err = ""
    while time.time() - start < timeout:
        try:
            async with httpx.AsyncClient(timeout=2) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    console.print(f"[dim]CDP 就绪: {data.get('Browser', 'unknown')}[/]")
                    return True
                else:
                    last_err = f"HTTP {resp.status_code}"
        except httpx.ConnectError:
            last_err = "连接被拒绝"
        except httpx.ReadTimeout:
            last_err = "读取超时"
        except Exception as e:
            last_err = str(e)[:60]
        
        elapsed = int(time.time() - start)
        if elapsed > 0 and elapsed % 5 == 0 and elapsed != getattr(_wait_cdp_ready, '_last_log', 0):
            console.print(f"[dim]等待 CDP... {elapsed}秒 ({last_err})[/]")
            _wait_cdp_ready._last_log = elapsed
        
        await asyncio.sleep(0.5)
    return False


async def _get_ws_url(port: int) -> str | None:
    """获取 Chrome 页面的 WebSocket 调试 URL（优先选猎聘页面）"""
    url = f"http://127.0.0.1:{port}/json/list"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(url)
            pages = resp.json()
            console.print(f"[dim]发现 {len(pages)} 个标签页:[/]")
            
            best_ws = None
            for page_info in pages:
                title = page_info.get("title", "")[:50]
                ptype = page_info.get("type", "")
                page_url = page_info.get("url", "")[:60]
                ws = page_info.get("webSocketDebuggerUrl", "")
                console.print(f"  [{ptype}] {title} | {page_url}")
                
                if ptype == "page" and ws:
                    # 优先选猎聘页面
                    if "liepin" in page_info.get("url", "").lower():
                        console.print(f"[green]  → 选中猎聘页面[/]")
                        return ws
                    if not best_ws:
                        best_ws = ws
            
            if best_ws:
                console.print(f"[yellow]  → 未找到猎聘页面，使用第一个页面[/]")
            return best_ws
    except Exception as e:
        console.print(f"[red]获取标签页列表失败: {e}[/]")
    return None


async def _cdp_listen(port: int) -> list[dict]:
    """直接通过 WebSocket 连接 CDP 监听网络事件（不注入任何 JS）
    
    关键：不用 Playwright！Playwright 连接时会自动注入 JS 修改 navigator.webdriver，
    猎聘检测到这个变化就跳转空白页。WebSocket 直连是纯被动监听，零注入。
    """
    import websockets

    ws_url = await _get_ws_url(port)
    if not ws_url:
        console.print("[red]未找到 Chrome 页面的 WebSocket URL[/]")
        return []

    console.print(f"[green]✓ WebSocket 直连 Chrome（零注入，不可检测）[/]")
    console.print("")
    console.print("[bold cyan]═══════════════════════════════════════════[/]")
    console.print("[bold cyan]  请在 Chrome 中手动操作：[/]")
    console.print("[bold cyan]    1. 登录猎聘（如果需要）[/]")
    console.print("[bold cyan]    2. 搜索候选人[/]")
    console.print("[bold cyan]    3. 搜索结果出来后，回来按 Enter[/]")
    console.print("[bold cyan]═══════════════════════════════════════════[/]")
    console.print("")

    captured_resumes = []
    msg_id = 0
    pending_bodies = {}

    try:
        async with websockets.connect(ws_url, max_size=10 * 1024 * 1024) as ws:
            # 用 Fetch 拦截搜索 API（暂停响应，确保能拿到 body）
            msg_id += 1
            await ws.send(json.dumps({
                "id": msg_id,
                "method": "Fetch.enable",
                "params": {
                    "patterns": [{
                        "urlPattern": "*search*resume*",
                        "requestStage": "Response"
                    }]
                }
            }))
            console.print("[dim]✓ Fetch 拦截已启动[/]")
            console.print("[bold]请在 Chrome 中搜索候选人，搜索结果出来后按 Enter...[/]")

            user_ready = asyncio.Event()
            event_count = 0

            async def wait_input():
                await asyncio.get_event_loop().run_in_executor(None, input)
                user_ready.set()

            async def listen_network():
                nonlocal captured_resumes, msg_id, event_count
                try:
                    async for message in ws:
                        if user_ready.is_set():
                            break
                        try:
                            data = json.loads(message)
                            method = data.get("method", "")

                            if method == "Fetch.requestPaused":
                                event_count += 1
                                params = data.get("params", {})
                                req_id = params.get("requestId", "")
                                status = params.get("responseStatusCode", 0)
                                
                                console.print(f"[green]  ✓ 拦截到搜索 API (status={status})[/]")

                                # 获取响应体（此时请求还在暂停中，一定能拿到）
                                msg_id += 1
                                await ws.send(json.dumps({
                                    "id": msg_id,
                                    "method": "Fetch.getResponseBody",
                                    "params": {"requestId": req_id}
                                }))
                                
                                # 等待响应体
                                body_text = None
                                for _ in range(50):
                                    try:
                                        body_msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
                                        body_parsed = json.loads(body_msg)
                                        if body_parsed.get("id") == msg_id:
                                            result = body_parsed.get("result", {})
                                            body_text = result.get("body", "")
                                            if result.get("base64Encoded"):
                                                import base64
                                                body_text = base64.b64decode(body_text).decode("utf-8", errors="ignore")
                                            break
                                    except asyncio.TimeoutError:
                                        break

                                # 放行请求（原样继续，不修改任何内容）
                                msg_id += 1
                                await ws.send(json.dumps({
                                    "id": msg_id,
                                    "method": "Fetch.continueResponse",
                                    "params": {"requestId": req_id}
                                }))

                                # 解析数据
                                if body_text:
                                    try:
                                        parsed = json.loads(body_text)
                                        if parsed.get("flag") == 1 and parsed.get("data", {}).get("resList"):
                                            resumes = parsed["data"]["resList"]
                                            captured_resumes.extend(resumes)
                                            console.print(f"[green]  ✓ 成功: {len(resumes)} 条简历（累计 {len(captured_resumes)}）[/]")
                                        else:
                                            console.print(f"[yellow]  flag={parsed.get('flag')}，无 resList[/]")
                                    except json.JSONDecodeError:
                                        console.print(f"[yellow]  JSON 解析失败[/]")
                                else:
                                    console.print(f"[yellow]  响应体为空[/]")

                        except Exception:
                            pass
                except asyncio.CancelledError:
                    pass

            listen_task = asyncio.create_task(listen_network())
            input_task = asyncio.create_task(wait_input())

            await input_task
            if event_count == 0:
                console.print("[yellow]⚠ 未拦截到请求，请确认已在 Chrome 中完成搜索[/]")
            await asyncio.sleep(2)
            listen_task.cancel()
            try:
                await listen_task
            except (asyncio.CancelledError, Exception):
                pass

    except Exception as e:
        console.print(f"[red]WebSocket 连接失败: {e}[/]")
        return []

    console.print(f"\n[green]监听结束，共捕获 {len(captured_resumes)} 条原始简历数据[/]")
    return captured_resumes


def _find_real_browser() -> tuple[str, str] | None:
    """查找系统上真正的 Chrome 或 Edge 浏览器（非 Playwright 内置的 Chrome for Testing）
    
    Returns:
        (browser_path, browser_name) 或 None
    """
    candidates = [
        # Chrome（优先，因为猎聘对 Chrome 兼容最好）
        (r"C:\Program Files\Google\Chrome\Application\chrome.exe", "chrome"),
        (r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe", "chrome"),
        (Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "Application" / "chrome.exe", "chrome"),
        # Edge
        (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", "edge"),
        (r"C:\Program Files\Microsoft\Edge\Application\msedge.exe", "edge"),
        (Path.home() / "AppData" / "Local" / "Microsoft" / "Edge" / "Application" / "msedge.exe", "edge"),
    ]
    
    for path, name in candidates:
        p = Path(path) if not isinstance(path, Path) else path
        if p.exists():
            # 排除 Playwright 内置的 Chrome for Testing
            # 它通常在 playwright 包目录下
            path_str = str(p).lower()
            if "playwright" in path_str or "chromium" in path_str:
                continue
            return (str(p), name)
    
    # Windows 上用 where 命令兜底
    for cmd in ["chrome.exe", "msedge.exe"]:
        try:
            result = subprocess.run(
                ["where", cmd], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                found = result.stdout.strip().split("\n")[0].strip()
                if "playwright" not in found.lower() and "chromium" not in found.lower():
                    name = "chrome" if "chrome" in found.lower() else "edge"
                    return (found, name)
        except Exception:
            pass
    
    return None


def _get_browser_profile(browser_name: str) -> str | None:
    """获取真实浏览器的用户数据目录（含登录态 Cookie）"""
    if browser_name == "chrome":
        candidates = [
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data",
            Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "User Data",
        ]
    else:  # edge
        candidates = [
            Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Edge" / "User Data",
            Path.home() / "AppData" / "Local" / "Microsoft" / "Edge" / "User Data",
        ]
    
    for path in candidates:
        if path.exists() and ((path / "Default").is_dir() or any(path.glob("Profile*"))):
            return str(path)
    return None


def _prepare_browser_profile(real_profile: str, browser_name: str) -> str:
    """复制浏览器关键 Cookie 文件到临时目录，避免与已运行的浏览器冲突
    
    Chrome 不允许两个实例使用同一个 user-data-dir，
    所以我们复制 Cookie 相关文件到一个临时目录，用它来启动新实例。
    """
    import tempfile
    
    temp_dir = Path(tempfile.mkdtemp(prefix=f"minglie_{browser_name}_"))
    default_dir = temp_dir / "Default"
    default_dir.mkdir(parents=True, exist_ok=True)
    
    src_default = Path(real_profile) / "Default"
    
    # 需要复制的关键文件（登录态 + Cookie）
    key_files = [
        "Cookies",           # 核心 Cookie
        "Cookies-journal",   # Cookie 日志
        "Login Data",        # 保存的登录信息
        "Login Data-journal",
        "Web Data",          # 自动填充等
        "Web Data-journal",
        "Local State",       # 浏览器状态（加密密钥等）
        "Preferences",       # 用户偏好
        "Secure Preferences",  # 安全偏好
    ]
    
    copied = 0
    for fname in key_files:
        src = src_default / fname
        if not src.exists():
            # 有些文件可能在根目录
            src = Path(real_profile) / fname
        if src.exists():
            try:
                shutil.copy2(src, default_dir / fname)
                copied += 1
            except Exception:
                pass  # 文件被占用时忽略
    
    # 复制 Local State（在根目录，包含加密密钥）
    local_state = Path(real_profile) / "Local State"
    if local_state.exists():
        try:
            shutil.copy2(local_state, temp_dir / "Local State")
        except Exception:
            pass
    
    console.print(f"[dim]已复制 {copied} 个 Cookie 文件到临时目录[/]")
    return str(temp_dir)


class LiepinHunterScraper:
    """猎聘猎头端简历抓取"""

    BASE_URL = "https://h.liepin.com"
    SEARCH_URL = "https://h.liepin.com/search/getConditionItem"

    # 两阶段抓取：快速筛选阈值（基于卡片上实际可见的字段）
    # 猎聘搜索页卡片展示：姓名、年龄、工作年限、学历、所在地、活跃状态、
    #   求职城市、求职职位、技能标签、工作经历摘要、教育经历
    QUICK_FILTER_RULES = {
        "min_experience_years": 0,  # 最低工作年限，0 表示不限（如 3 → 跳过不足3年的）
        "skip_education": [],       # 跳过的学历（如 ["大专"]），空表示不限
        "max_age": 0,               # 最大年龄，0 表示不限
        "min_age": 0,               # 最小年龄，0 表示不限
    }

    def __init__(self, config: dict):
        self.config = config
        self.scraper_cfg = config.get("scraper", {})
        self.search_cfg = config.get("search", {})
        self.results = []
        self.data_dir = Path("data")
        self.data_dir.mkdir(exist_ok=True)
        self.screenshot_dir = Path("screenshots")
        self.screenshot_dir.mkdir(exist_ok=True)
        self._chrome_process = None  # CDP 模式启动的 Chrome 进程

        # 合并用户自定义的快速筛选规则
        user_rules = config.get("quick_filter", {})
        if user_rules:
            self.QUICK_FILTER_RULES = {**self.QUICK_FILTER_RULES, **user_rules}

    async def run(self) -> list[dict]:
        """主流程（异常时也返回已抓取的数据）"""
        async with async_playwright() as p:
            # ── 浏览器启动模式 ──
            # cdp: 自动启动真实 Chrome/Edge + CDP 连接（推荐，最难被检测）
            # stealth: 用反检测插件启动 + 真实 Edge 登录数据
            # basic: 纯 Playwright 启动（最弱，容易被检测）
            launch_mode = self.scraper_cfg.get("launch_mode", "stealth")

            if launch_mode == "cdp":
                # ── CDP 纯监听模式：WebSocket 直连，零注入 ──
                cdp_port = self.scraper_cfg.get("cdp_port", 9222)
                
                console.print(f"[bold]CDP 模式：WebSocket 直连 Chrome（端口 {cdp_port}）[/]")
                
                cdp_ready = await _wait_cdp_ready(cdp_port, timeout=5)
                if not cdp_ready:
                    console.print(f"[yellow]端口 {cdp_port} 未响应[/]")
                    console.print("[yellow]请先双击 start_chrome_debug.bat 启动 Chrome，然后按 Enter[/]")
                    await asyncio.get_event_loop().run_in_executor(None, input)
                    cdp_ready = await _wait_cdp_ready(cdp_port, timeout=10)
                
                if not cdp_ready:
                    console.print(f"[red]端口 {cdp_port} 无响应，请检查 Chrome 是否已启动[/]")
                    return self.results
                
                # 直接用 WebSocket 监听（不用 Playwright！）
                raw_resumes = await _cdp_listen(cdp_port)
                
                # 解析数据
                if raw_resumes:
                    console.print(f"[bold]解析 {len(raw_resumes)} 条简历数据...[/]")
                    for raw in raw_resumes:
                        try:
                            info = self._parse_api_resume(raw)
                            if not info:
                                continue
                            if info.get("is_viewed"):
                                continue
                            is_promising, reason = self.quick_evaluate_card(info)
                            if not is_promising:
                                continue
                            self.results.append(info)
                        except Exception:
                            continue
                    console.print(f"[green]✓ 提取 {len(self.results)} 条有效简历[/]")
                else:
                    console.print("[yellow]未捕获到简历数据[/]")

            elif launch_mode == "stealth":
                # ── Stealth 模式：反检测插件 + 真实 Edge 登录数据 ──
                from playwright_stealth import Stealth

                edge_profile = self._find_edge_profile()
                if edge_profile:
                    console.print(f"[green]✓ 找到 Edge 用户数据目录: {edge_profile}[/]")
                    user_data_dir = edge_profile
                else:
                    user_data_dir = self.scraper_cfg.get("user_data_dir", "./browser_data")
                    console.print(f"[yellow]未找到 Edge 数据，使用: {user_data_dir}[/]")

                stealth_obj = Stealth(
                    navigator_languages_override=["zh-CN", "zh"],
                    navigator_vendor_override="Google Inc.",
                )

                context = await p.chromium.launch_persistent_context(
                    user_data_dir=user_data_dir,
                    headless=self.scraper_cfg.get("headless", False),
                    slow_mo=self.scraper_cfg.get("slow_mo", 300),
                    viewport={"width": 1440, "height": 900},
                    locale="zh-CN",
                    channel="chrome",  # 使用系统安装的 Chrome
                    args=STEALTH_ARGS,
                    ignore_default_args=["--enable-automation"],
                )
                page = context.pages[0] if context.pages else await context.new_page()
                await stealth_obj.apply_stealth_async(page)
                console.print("[green]✓ Stealth 模式已启动（反检测 + Edge 登录态）[/]")

            else:
                # ── Basic 模式：纯 Playwright（备用）──
                user_data_dir = self.scraper_cfg.get("user_data_dir", "./browser_data")
                context = await p.chromium.launch_persistent_context(
                    user_data_dir=user_data_dir,
                    headless=self.scraper_cfg.get("headless", False),
                    slow_mo=self.scraper_cfg.get("slow_mo", 300),
                    viewport={"width": 1440, "height": 900},
                    locale="zh-CN",
                    args=STEALTH_ARGS,
                    ignore_default_args=["--enable-automation"],
                )
                page = context.pages[0] if context.pages else await context.new_page()
                await page.add_init_script(STEALTH_JS)

            # CDP 模式已在上面处理完毕，不需要 Playwright
            if launch_mode == "cdp":
                self._save_results()
                console.print(f"\n[bold green]完成！共提取 {len(self.results)} 份简历[/]")
                return self.results

            # ── 以下为非 CDP 模式（stealth/basic），需要 Playwright ──
            self.pacer = HumanPacer()

            try:
                await self._ensure_login(page)
                await self._setup_search(page)

                max_pages = self.search_cfg.get("max_pages", 5)
                if max_pages > 5:
                    console.print("[yellow]⚠ 为避免风控，单次最多抓取5页[/]")
                    max_pages = 5

                for page_num in range(1, max_pages + 1):
                    if await self.pacer.should_stop():
                        break
                    console.print(f"\n[bold cyan]=== 第 {page_num}/{max_pages} 页 ===[/]")
                    resumes = await self._scrape_page(page, page_num)
                    self.results.extend(resumes)
                    console.print(f"本页抓取: {len(resumes)} 条，累计: {len(self.results)} 条")
                    if self.results:
                        self._save_results()
                    if len(self.results) >= 100:
                        break
                    if page_num < max_pages:
                        has_next = await self._go_next_page(page)
                        if not has_next:
                            break
                        await page_load_delay()

                self._save_results()
                console.print(f"\n[bold green]完成！共抓取 {len(self.results)} 份简历[/]")

            except Exception as e:
                error_msg = str(e)
                console.print(f"[bold red]出错: {error_msg}[/]")
                try:
                    await page.screenshot(
                        path=str(self.screenshot_dir / f"error_{datetime.now():%Y%m%d_%H%M%S}.png")
                    )
                except Exception:
                    pass
                # 出错时也保存已抓取的数据
                if self.results:
                    self._save_results()
                    console.print(f"[yellow]已保存已抓取的 {len(self.results)} 条数据[/]")
                # 注意：不再 raise，让 finally 返回 self.results
            finally:
                if self.results:
                    self._save_results()
                try:
                    await context.close()
                except Exception:
                    pass
                except Exception:
                    pass

        return self.results

    @staticmethod
    def _find_edge_profile() -> str | None:
        """查找系统上真实的浏览器用户数据目录（复用 Cookie 和登录态）"""
        import os
        candidates = [
            # Chrome
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data",
            Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "User Data",
            Path.home() / ".config" / "google-chrome",
            # Edge
            Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Edge" / "User Data",
            Path.home() / "AppData" / "Local" / "Microsoft" / "Edge" / "User Data",
            Path.home() / "Library" / "Application Support" / "Microsoft Edge",
        ]
        for path in candidates:
            if path.exists() and ((path / "Default").is_dir() or any(path.glob("Profile*"))):
                return str(path)
        return None

    @staticmethod
    def _check_chrome_not_running(browser_name: str) -> bool:
        """检查 Chrome/Edge 是否没有在运行"""
        try:
            if browser_name == "chrome":
                result = subprocess.run(
                    ["tasklist", "/FI", "IMAGENAME eq chrome.exe"],
                    capture_output=True, text=True, timeout=5
                )
            else:
                result = subprocess.run(
                    ["tasklist", "/FI", "IMAGENAME eq msedge.exe"],
                    capture_output=True, text=True, timeout=5
                )
            # 如果没有找到进程，返回 True（没有在运行）
            return "No tasks" in result.stdout or browser_name not in result.stdout.lower()
        except Exception:
            return True  # 假设没运行

    # ── 网络拦截：直接从 API 获取数据，绕过 DOM 检测 ──

    def _setup_api_interception(self, page: Page):
        """设置网络请求拦截，捕获猎聘搜索 API 响应"""
        self._api_resumes = []  # 存储拦截到的简历数据
        self._api_page_count = 0

        async def on_response(response):
            try:
                url = response.url
                # 猎聘搜索 API
                if "search-resumes" in url or "searchResumes" in url:
                    if response.status == 200:
                        try:
                            data = await response.json()
                            if data.get("flag") == 1 and data.get("data", {}).get("resList"):
                                resumes = data["data"]["resList"]
                                self._api_resumes.extend(resumes)
                                self._api_page_count += 1
                                console.print(f"[green]✓ API 拦截: 获取 {len(resumes)} 条简历 (第{self._api_page_count}页)[/]")
                        except Exception:
                            pass
                # 猎聘简历详情 API
                elif "resumeDetail" in url or "resume-detail" in url or "showresumedetail" in url:
                    if response.status == 200:
                        try:
                            data = await response.json()
                            if data.get("data"):
                                console.print(f"[dim]✓ 拦截到简历详情 API[/]")
                                # 存储详情数据供后续使用
                                if not hasattr(self, '_api_details'):
                                    self._api_details = {}
                                detail_id = url.split("/")[-1].split("?")[0]
                                self._api_details[detail_id] = data["data"]
                        except Exception:
                            pass
            except Exception:
                pass

        page.on("response", on_response)

    def _parse_api_resume(self, raw: dict) -> dict | None:
        """将 API 返回的原始简历数据解析为标准格式"""
        try:
            simple = raw.get("simpleResumeForm", {})
            if not simple:
                return None

            name = simple.get("resName", "未知候选人")
            age = simple.get("resBirthYearAge", 0)
            education = simple.get("resEdulevelName", "")
            experience_years = simple.get("resWorkyearAge", 0)
            location = simple.get("resDqName", "")
            company = simple.get("resCompany", "")
            title = simple.get("resTitle", "")

            # 活跃状态
            active = raw.get("activeStatus", {})
            activity = active.get("name", "") if active else ""

            # 技能标签
            skill_tags = raw.get("skillTag", [])

            # 期望
            expect_city = raw.get("wantDq", "")
            expect_position = raw.get("wantJobTitle", "")

            # 是否已阅
            is_viewed = raw.get("viewed", False)

            # 详情页 URL
            detail_url = raw.get("detailUrl", "")
            if detail_url and not detail_url.startswith("http"):
                detail_url = f"https://h.liepin.com{detail_url}"

            return {
                "name": name,
                "age": f"{age}岁" if age else "",
                "activity": activity,
                "salary": "",  # API 卡片数据中通常不含薪资
                "experience": f"{experience_years}年经验" if experience_years else "",
                "education": education,
                "company": company,
                "title": title,
                "location": location,
                "expect_city": expect_city,
                "expect_position": expect_position,
                "skill_tags": skill_tags,
                "work_summary": "",
                "is_viewed": is_viewed,
                "detail_url": detail_url,
                "user_id_encode": raw.get("usercIdEncode", ""),
                "res_id_encode": simple.get("resIdEncode", ""),
                "sign": raw.get("sign", ""),
            }
        except Exception as e:
            console.print(f"[yellow]解析 API 简历失败: {e}[/]")
            return None

    async def _wait_for_api_data(self, page: Page, timeout: int = 120) -> bool:
        """等待 API 拦截到数据（用户在浏览器中搜索后自动捕获）"""
        console.print("[cyan]⏳ 等待搜索 API 响应...[/]")
        console.print("[cyan]   请在浏览器中输入关键词并搜索[/]")

        start = time.time()
        last_count = 0
        while time.time() - start < timeout:
            current_count = len(self._api_resumes)
            if current_count > last_count:
                console.print(f"[green]✓ 已拦截 {current_count} 条简历数据[/]")
                return True
            # 检查页面是否已经跳转到搜索结果页
            try:
                url = page.url
                if "search" in url and ("keyword" in url or "query" in url or "resList" in url):
                    await asyncio.sleep(2)
                    if len(self._api_resumes) > 0:
                        return True
            except Exception:
                pass
            await asyncio.sleep(1)

            if int(time.time() - start) % 15 == 0 and int(time.time() - start) > 0:
                console.print(f"[dim]已等待 {int(time.time() - start)} 秒，请在浏览器中完成搜索...[/]")

        console.print("[yellow]⚠ 等待超时[/]")
        return len(self._api_resumes) > 0

    async def _scrape_page_from_api(self, page: Page, page_num: int) -> list[dict]:
        """从拦截到的 API 数据中提取简历（不依赖 DOM）"""
        resumes = []
        # 计算当前页的数据范围（每页约30条）
        page_size = 30
        start_idx = (page_num - 1) * page_size
        end_idx = start_idx + page_size

        page_data = self._api_resumes[start_idx:end_idx]
        if not page_data:
            return resumes

        console.print(f"[bold]处理第 {page_num} 页 API 数据: {len(page_data)} 条[/]")

        for i, raw in enumerate(page_data):
            try:
                info = self._parse_api_resume(raw)
                if not info:
                    continue

                # 跳过已阅
                if info.get("is_viewed"):
                    console.print(f"  [{i+1}] {info.get('name', '?')} - [dim]已阅，跳过[/]")
                    continue

                # 快速筛选
                is_promising, reason = self.quick_evaluate_card(info)
                if not is_promising:
                    console.print(f"  [{i+1}] {info.get('name', '?')} - [dim]跳过: {reason}[/]")
                    continue

                console.print(f"  [{i+1}] {info.get('name', '?')} - {info.get('title', '')} [{info.get('education', '')}]")
                resumes.append(info)

            except Exception as e:
                console.print(f"  [{i+1}] [yellow]解析失败: {e}[/]")
                continue

        return resumes

    async def _ensure_login(self, page: Page):
        """确保已登录"""
        console.print("[bold]检查登录状态...[/]")
        await page.goto(self.BASE_URL, wait_until="domcontentloaded")
        await human_delay(2.0, 4.0)

        # 检查是否被风控（空白页）
        current_url = page.url
        page_content = await page.content()
        if "about:blank" in current_url or len(page_content) < 200:
            console.print("[yellow]⚠ 页面空白，可能被风控[/]")
            console.print("[yellow]   等待 5 秒后重试...[/]")
            await asyncio.sleep(5)
            await page.goto(self.BASE_URL, wait_until="domcontentloaded")
            await human_delay(2.0, 4.0)

        # 猎头端登录后通常有用户头像或"退出"按钮
        logged_indicators = [
            '[class*="user-info"]',
            '[class*="avatar"]',
            '[class*="header-user"]',
            'text="退出"',
            '[class*="logout"]',
            '[class*="header"] img',
        ]

        for indicator in logged_indicators:
            el = await page.query_selector(indicator)
            if el:
                console.print("[green]✓ 已登录[/]")
                return

        # 检查是否在登录页
        if "login" in page.url or "passport" in page.url:
            console.print("[yellow]跳转到登录页，请在浏览器中登录...[/]")
        else:
            console.print("[yellow]未登录，请在浏览器中登录猎头端...[/]")
        
        console.print("[yellow]（支持扫码/手机号登录，登录后脚本自动继续）[/]")

        # 等待登录成功
        for indicator in logged_indicators:
            try:
                await page.wait_for_selector(indicator, timeout=180000)
                console.print("[green]✓ 登录成功！[/]")
                return
            except Exception:
                continue

        console.print("[yellow]超时，请确认已登录后按Enter...[/]")
        input()

    async def _setup_search(self, page: Page):
        """进入搜索页，填写搜索条件（仅非 CDP 模式使用）"""
        console.print("[bold]进入简历搜索...[/]")
        await page.goto(self.SEARCH_URL, wait_until="domcontentloaded")
        await human_delay(1.5, 3.0)
        await page.screenshot(path=str(self.screenshot_dir / "search_page.png"))

        try:
            await page.wait_for_load_state('networkidle', timeout=15000)
        except Exception:
            pass
        await human_delay(1.0, 2.0)

        page_content = await page.content()
        if len(page_content) < 500 or "about:blank" in page.url:
            console.print("[yellow]⚠ 页面内容过少，可能被风控拦截[/]")
            await page.reload(wait_until="domcontentloaded")
            await human_delay(2.0, 4.0)

        keyword = self.search_cfg.get("keyword", "")
        search_mode = self.scraper_cfg.get("search_mode", "manual")
        is_cdp = self.scraper_cfg.get("launch_mode") == "cdp"

        # ===== 模式A：手动搜索 =====
        if search_mode == "manual":
            console.print("")
            console.print("=" * 50)
            console.print("  手动搜索模式")
            console.print("  请在浏览器中：")
            console.print("    1. 输入搜索关键词")
            console.print("    2. 按回车或点击搜索")
            console.print("    3. 搜索结果出来后，脚本自动开始抓取")
            console.print("=" * 50)
            console.print("")
            if is_cdp:
                await self._wait_for_api_data(page)
            else:
                await self._wait_for_search_results(page)
            return

        # ===== 模式B：全自动 =====
        if not keyword:
            console.print("[yellow]未设置关键词，切换到手动搜索模式[/]")
            console.print("[yellow]请在浏览器中手动输入关键词并搜索[/]")
            if is_cdp:
                await self._wait_for_api_data(page)
            else:
                await self._wait_for_search_results(page)
            return

        console.print(f"[bold cyan]全自动模式：正在输入关键词「{keyword}」...[/]")

        filled = await self._fill_keyword(page, keyword)
        if not filled:
            console.print("[yellow]自动填入失败，切换到手动搜索模式[/]")
            console.print("[yellow]请在浏览器中手动输入关键词并搜索[/]")
            if is_cdp:
                await self._wait_for_api_data(page)
            else:
                await self._wait_for_search_results(page)
            return

        # 自动按回车触发搜索
        await human_delay(0.5, 1.0)
        console.print("[bold]按回车触发搜索...[/]")

        search_input = page.locator('.auto-input-wrap-v3 input[role="combobox"]').first
        if await search_input.count() > 0:
            await search_input.press('Enter')
        else:
            await page.keyboard.press('Enter')

        await page_load_delay()
        console.print("[green]✓ 搜索已触发[/]")

        # 等待搜索结果
        if is_cdp:
            await self._wait_for_api_data(page)
        else:
            await self._wait_for_search_results(page)

    async def _fill_keyword(self, page: Page, keyword: str) -> bool:
        """往猎聘搜索框填入关键词（针对 Ant Design AutoComplete 优化）"""
        # 直接用最精准的选择器（基于用户提供的实际DOM）
        el = page.locator('.auto-input-wrap-v3 input[role="combobox"]').first

        # 快速检查：最多等3秒
        try:
            await el.wait_for(state='visible', timeout=3000)
        except Exception:
            console.print("[yellow]搜索框未找到，尝试备用选择器...[/]")
            # 备用
            el = page.locator('input.ant-select-selection-search-input[type="search"]').first
            try:
                await el.wait_for(state='visible', timeout=2000)
            except Exception:
                console.print("[red]✗ 所有选择器都未找到搜索框[/]")
                return False

        try:
            # 1. 点击聚焦
            await el.click()
            await human_delay(0.2, 0.4)

            # 2. 全选清空
            await page.keyboard.press('Control+a')
            await asyncio.sleep(0.05)
            await page.keyboard.press('Delete')
            await asyncio.sleep(0.05)
            # Backspace兜底
            for _ in range(10):
                await page.keyboard.press('Backspace')
            await human_delay(0.1, 0.2)

            # 3. 逐字符输入（触发Ant Design的onChange事件）
            await page.keyboard.type(keyword, delay=30)
            await human_delay(0.2, 0.4)

            # 4. 验证
            value = await el.input_value()
            if value.strip():
                console.print(f"[green]✓ 关键词已填入: {value.strip()}[/]")
                return True

            # 5. 兜底：JS直接设值
            result = await page.evaluate("""
                (kw) => {
                    const input = document.querySelector('.auto-input-wrap-v3 input[role="combobox"]')
                        || document.querySelector('input.ant-select-selection-search-input[type="search"]');
                    if (!input) return false;
                    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                    setter.call(input, kw);
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                    return input.value;
                }
            """, keyword)
            if result:
                console.print(f"[green]✓ 关键词已填入(JS): {result}[/]")
                return True

        except Exception as e:
            console.print(f"[red]填入失败: {e}[/]")

        return False

    async def _select_city(self, page: Page, city: str):
        """尝试在搜索页选择城市"""
        try:
            console.print(f"[bold]尝试选择城市: {city}[/]")

            # 方式1: 点击城市选择器（常见选择器）
            city_selectors = [
                '[class*="city"] .ant-select',
                '[class*="city-select"]',
                '[class*="area-select"]',
                '[class*="region"] .ant-select',
                'div[class*="city"]',
            ]

            for sel in city_selectors:
                try:
                    el = page.locator(sel).first
                    if await el.count() > 0 and await el.is_visible():
                        await el.click()
                        await human_delay(0.5, 1.0)

                        # 在弹出的下拉框中搜索城市
                        input_el = page.locator('.ant-select-dropdown input, .ant-select-search__field').first
                        if await input_el.count() > 0:
                            await input_el.fill(city)
                            await human_delay(0.5, 1.0)

                            # 点击匹配项
                            option = page.locator(f'.ant-select-dropdown .ant-select-item:has-text("{city}")').first
                            if await option.count() > 0:
                                await option.click()
                                console.print(f"[green]✓ 已选择城市: {city}[/]")
                                await human_delay(0.5, 1.0)
                                return
                except Exception:
                    continue

            # 方式2: 用JavaScript直接设置（有些页面支持URL参数）
            current_url = page.url
            if '?' in current_url:
                await page.goto(f"{current_url}&dq={city}", wait_until="domcontentloaded")
            else:
                await page.goto(f"{current_url}?dq={city}", wait_until="domcontentloaded")
            await human_delay(2.0, 3.0)
            console.print(f"[dim]已尝试通过URL参数设置城市: {city}[/]")

        except Exception as e:
            console.print(f"[yellow]城市选择失败({e})，使用默认[/]")

    async def _wait_for_search_results(self, page: Page):
        """等待搜索结果出现（支持手动和自动两种模式）"""
        console.print("[cyan]⏳ 等待搜索结果...[/]")

        # 猎头端简历卡片的特征选择器
        result_selectors = [
            '[class*="resume-card"]',
            '[class*="result-item"]',
            '[class*="talent-card"]',
            '[class*="candidate-card"]',
            '[class*="search-result"] li',
            '[class*="card-item"]',
        ]

        # 方式1: 轮询检测简历卡片是否出现
        max_wait = 300  # 最多等5分钟
        check_interval = 2  # 每2秒检查一次
        elapsed = 0

        while elapsed < max_wait:
            for sel in result_selectors:
                try:
                    cards = await page.query_selector_all(sel)
                    visible_cards = [c for c in cards if await c.is_visible()]
                    if len(visible_cards) >= 2:
                        console.print(f"[green]✓ 检测到 {len(visible_cards)} 个简历卡片，开始抓取！[/]")
                        return
                except Exception:
                    continue

            # 也检查 URL 是否变化了（搜索可能会跳转）
            current_url = page.url
            if 'search' in current_url and ('keyword' in current_url or 'query' in current_url):
                # URL 里有搜索参数，可能已经搜索了，再等等结果加载
                await human_delay(1.0, 2.0)

            await asyncio.sleep(check_interval)
            elapsed += check_interval

            # 每30秒提示一次
            if elapsed % 30 == 0:
                console.print(f"[dim]已等待 {elapsed} 秒，请在浏览器中完成搜索...[/]")

        console.print("[yellow]⚠ 等待超时，尝试继续抓取当前页面...[/]")

    async def _extract_basic_info(self, card) -> dict | None:
        """从简历卡片提取基础信息（修复：区分姓名和活跃度 + 检测已阅标记）"""
        try:
            info = await card.evaluate("""
                (card) => {
                    const getText = (el) => el ? el.textContent.trim() : '';

                    // === 检测「阅」标记（猎聘标记已查看的简历） ===
                    let isViewed = false;
                    // 方式1：检查卡片内部是否有「阅」字的标记元素
                    const allEls = card.querySelectorAll('*');
                    for (const el of allEls) {
                        const text = getText(el);
                        if (text === '阅' || text === '已阅') {
                            isViewed = true;
                            break;
                        }
                    }
                    // 方式2：检查 class 包含 viewed/read/已读 的元素
                    if (!isViewed) {
                        const viewedEl = card.querySelector('[class*="viewed"], [class*="read"], [class*="已读"], [class*="已看"]');
                        if (viewedEl) isViewed = true;
                    }

                    // === 提取姓名 ===
                    let name = '';
                    let activity = '';

                    // 策略1：找 em 标签（通常是姓名加粗显示）
                    const emEl = card.querySelector('em');
                    if (emEl) {
                        const emText = getText(emEl);
                        if (emText && !emText.includes('活跃') && !emText.includes('天内') && !emText.includes('小时内')) {
                            name = emText;
                        }
                    }

                    // 策略2：找 class 包含 name 的元素
                    if (!name) {
                        const nameEl = card.querySelector('[class*="name"]') || card.querySelector('[class*="title"]');
                        if (nameEl) {
                            const nameText = getText(nameEl);
                            if (nameText && !nameText.includes('活跃') && !nameText.includes('天内') && !nameText.includes('小时内')) {
                                name = nameText;
                            }
                        }
                    }

                    // 策略3：从 personal-detail-age 区域的兄弟元素中找姓名
                    if (!name) {
                        const ageEl = card.querySelector('.personal-detail-age, [class*="personal-detail"]');
                        if (ageEl) {
                            let prev = ageEl.previousElementSibling;
                            while (prev) {
                                const text = getText(prev);
                                if (text && !text.includes('活跃') && !text.includes('天内') && !text.includes('小时内') && text.length < 10) {
                                    name = text;
                                    break;
                                }
                                prev = prev.previousElementSibling;
                            }
                        }
                    }

                    // 提取活跃度
                    const activityEls = card.querySelectorAll('[class*="active"], [class*="活跃"], span, div');
                    for (const el of activityEls) {
                        const text = getText(el);
                        if (text && /\\d+\\s*(天|小时)内活跃/.test(text)) {
                            activity = text;
                            break;
                        }
                    }

                    // 其他字段
                    const salaryEl = card.querySelector('[class*="salary"], [class*="薪资"]');
                    const salary = salaryEl ? getText(salaryEl) : '';

                    let experience = '';
                    const expEls = card.querySelectorAll('[class*="exp"], [class*="经验"], [class*="year"]');
                    for (const el of expEls) {
                        const text = getText(el);
                        if (text && /\\d+\\s*年/.test(text)) { experience = text; break; }
                    }

                    let education = '';
                    const eduEls = card.querySelectorAll('[class*="edu"], [class*="学历"]');
                    for (const el of eduEls) {
                        const text = getText(el);
                        if (text && (text.includes('本科') || text.includes('硕士') || text.includes('博士') || text.includes('大专') || text.includes('MBA'))) {
                            education = text; break;
                        }
                    }

                    const companyEl = card.querySelector('[class*="company"], [class*="公司"]');
                    const company = companyEl ? getText(companyEl) : '';

                    const titleEl = card.querySelector('[class*="position"], [class*="职位"], [class*="job-title"]');
                    const title = titleEl ? getText(titleEl) : '';

                    // 所在地
                    let location = '';
                    const locEls = card.querySelectorAll('[class*="area"], [class*="city"], [class*="location"], [class*="地点"], [class*="城市"]');
                    for (const el of locEls) {
                        const text = getText(el);
                        if (text && text.length < 20 && !text.includes('活跃')) {
                            location = text; break;
                        }
                    }

                    // 求职期望（期望城市、期望职位）
                    let expectCity = '';
                    let expectPosition = '';
                    const expectEls = card.querySelectorAll('[class*="expect"], [class*="意向"], [class*="求职"]');
                    for (const el of expectEls) {
                        const text = getText(el);
                        // 包含城市关键词
                        if (text && /北京|上海|广州|深圳|杭州|成都|武汉|南京|西安|苏州|天津|重庆|长沙|郑州|合肥|青岛|厦门|济南|福州|昆明|大连|沈阳|哈尔滨|长春|石家庄|太原|南昌|贵阳|南宁|兰州|银川|西宁|拉萨|乌鲁木齐|呼和浩特|海口|三亚|珠海|佛山|东莞|无锡|常州|宁波|温州|嘉兴|绍兴|金华|台州|泉州|漳州|龙岩|三明|南平|宁德|莆田|晋江|石狮/.test(text)) {
                            expectCity = text; break;
                        }
                    }
                    // 从所有文本中提取期望职位
                    const allTexts = card.querySelectorAll('span, div, a');
                    for (const el of allTexts) {
                        const text = getText(el);
                        if (text && text.includes('求职职位')) {
                            // 取冒号后面的内容
                            const m = text.match(/求职职位[：:]\s*(.+)/);
                            if (m) expectPosition = m[1].trim();
                            break;
                        }
                    }
                    // 备选：找 class 包含 expect-position 的元素
                    if (!expectPosition) {
                        const posEl = card.querySelector('[class*="expect-position"], [class*="意向职位"]');
                        if (posEl) expectPosition = getText(posEl);
                    }

                    // 技能标签
                    let skillTags = [];
                    const tagEls = card.querySelectorAll('[class*="tag"], [class*="skill"], [class*="标签"], [class*="badge"]');
                    for (const el of tagEls) {
                        const text = getText(el);
                        if (text && text.length < 20 && text.length > 1 && !text.includes('活跃') && !text.includes('立即')) {
                            skillTags.push(text);
                        }
                    }
                    // 去重
                    skillTags = [...new Set(skillTags)];

                    // 工作经历摘要（卡片上可见的最近工作）
                    let workSummary = '';
                    const workEls = card.querySelectorAll('[class*="work"], [class*="experience"], [class*="经历"]');
                    for (const el of workEls) {
                        const text = getText(el);
                        if (text && text.length > 10 && text.length < 200) {
                            workSummary = text; break;
                        }
                    }

                    return { name, activity, salary, experience, education, company, title, location, expectCity, expectPosition, skillTags, workSummary, isViewed };
                }
            """)

            if not info:
                return None

            name = info.get("name", "").strip()
            activity = info.get("activity", "").strip()

            if not name and activity:
                name = activity
            elif not name and not activity:
                name = "未知候选人"

            return {
                "name": name,
                "activity": activity,
                "salary": info.get("salary", ""),
                "experience": info.get("experience", ""),
                "education": info.get("education", ""),
                "company": info.get("company", ""),
                "title": info.get("title", ""),
                "location": info.get("location", ""),
                "expect_city": info.get("expectCity", ""),
                "expect_position": info.get("expectPosition", ""),
                "skill_tags": info.get("skillTags", []),
                "work_summary": info.get("workSummary", ""),
                "is_viewed": info.get("isViewed", False),
            }
        except Exception as e:
            console.print(f"    [yellow]提取基础信息失败: {e}[/]")
            return None

    def quick_evaluate_card(self, card_info: dict) -> tuple[bool, str]:
        """第一阶段：用规则快速评估简历卡片，决定是否值得进详情页
        
        筛选维度（仅三个）：学历、工作年限、年龄
        
        Returns:
            (is_promising, reason): is_promising=True 表示值得查看详情
        """
        rules = self.QUICK_FILTER_RULES
        education = card_info.get("education", "")
        experience = card_info.get("experience", "")

        # 1. 学历过滤
        skip_edu = rules.get("skip_education", [])
        if skip_edu and education:
            for edu in skip_edu:
                if edu in education:
                    return False, f"学历不达标：{education}"

        # 2. 工作年限过滤
        min_exp = rules.get("min_experience_years", 0)
        if min_exp > 0 and experience:
            exp_years = self._parse_experience_years(experience)
            if exp_years > 0 and exp_years < min_exp:
                return False, f"经验不足：{experience}（低于{min_exp}年）"

        # 3. 年龄过滤
        min_age = rules.get("min_age", 0)
        max_age = rules.get("max_age", 0)
        if (min_age > 0 or max_age > 0) and card_info.get("age"):
            age = self._parse_age(card_info.get("age", ""))
            if age > 0:
                if min_age > 0 and age < min_age:
                    return False, f"年龄过小：{age}岁（要求≥{min_age}岁）"
                if max_age > 0 and age > max_age:
                    return False, f"年龄过大：{age}岁（要求≤{max_age}岁）"

        return True, "通过快速筛选"

    @staticmethod
    def _parse_age(age_str: str) -> int:
        """解析年龄字符串，如 '25岁' → 25"""
        import re
        m = re.search(r'(\d+)', age_str)
        if m:
            return int(m.group(1))
        return 0

    @staticmethod
    def _parse_experience_years(exp_str: str) -> int:
        """解析经验字符串，返回年数
        支持格式：'5年经验'、'8年以上'、'3-5年'
        """
        import re
        m = re.search(r'(\d+)', exp_str)
        if m:
            return int(m.group(1))
        return 0

    async def _scrape_page(self, page: Page, page_num: int) -> list[dict]:
        """两阶段抓取当前页简历
        
        阶段1：提取所有卡片基础信息 + 规则快速筛选
        阶段2：只对通过筛选的简历进详情页抓取完整信息
        """
        resumes = []

        # ═══ 阶段1：提取所有卡片基础信息 ═══
        card_info = await page.evaluate("""
            () => {
                const ageEls = document.querySelectorAll('.personal-detail-age, [class*="personal-detail"]');
                const cardSet = new Set();
                ageEls.forEach(el => {
                    let p = el;
                    for (let i = 0; i < 8; i++) {
                        p = p.parentElement;
                        if (!p) break;
                        const cls = p.className || '';
                        if (cls.includes('resume-card') || cls.includes('result-item') || cls.includes('tlog-common')) {
                            cardSet.add(p);
                            break;
                        }
                    }
                });
                return { cardCount: cardSet.size };
            }
        """)

        if card_info['cardCount'] == 0:
            console.print("[red]✗ 未找到简历卡片[/]")
            return resumes

        cards = await page.query_selector_all('.tlog-common-resume-card')
        if not cards:
            cards = await page.query_selector_all('[class*="resume-card"]')
        if not cards:
            console.print("[red]✗ 未找到简历卡片元素[/]")
            return resumes

        console.print(f"[green]✓ 找到 {len(cards)} 个简历卡片[/]")

        # 提取所有卡片的基础信息
        all_cards_info = []  # [(index, card_element, basic_info)]
        skipped_viewed = 0

        for i, card in enumerate(cards):
            try:
                if not await card.is_visible():
                    continue

                basic_info = await self._extract_basic_info(card)
                if not basic_info:
                    continue

                # 跳过已阅简历
                if basic_info.get('is_viewed'):
                    skipped_viewed += 1
                    console.print(f"  [{i+1}] {basic_info.get('name', '?')} - [dim]已阅，跳过[/]")
                    continue

                all_cards_info.append((i, card, basic_info))

            except Exception as e:
                console.print(f"  [{i+1}] [yellow]提取基础信息失败: {e}[/]")
                continue

        if skipped_viewed > 0:
            console.print(f"[dim]跳过 {skipped_viewed} 份已阅简历[/]")

        if not all_cards_info:
            console.print("[yellow]无有效简历卡片[/]")
            return resumes

        # ═══ 阶段1.5：规则快速筛选 ═══
        console.print(f"[bold]阶段1：快速筛选 {len(all_cards_info)} 份简历...[/]")

        promising_cards = []   # [(index, card_element, basic_info)]
        filtered_out = 0

        for i, card, basic_info in all_cards_info:
            is_promising, reason = self.quick_evaluate_card(basic_info)
            if is_promising:
                promising_cards.append((i, card, basic_info))
            else:
                filtered_out += 1
                name = basic_info.get('name', '?')
                console.print(f"  [{i+1}] {name} - [dim]跳过详情页: {reason}[/]")

        console.print(f"[cyan]快速筛选结果: {len(promising_cards)} 份值得查看详情，{filtered_out} 份已跳过[/]")

        # ═══ 阶段2：只对 promising 的简历进详情页 ═══
        if not promising_cards:
            console.print("[yellow]本轮无值得查看详情的简历[/]")
            return resumes

        console.print(f"[bold]阶段2：抓取 {len(promising_cards)} 份简历详情...[/]")

        for idx, (i, card, basic_info) in enumerate(promising_cards):
            try:
                name = basic_info.get('name', '?')
                console.print(f"  [{i+1}] {name} - 正在进入详情页 ({idx+1}/{len(promising_cards)})...")

                # 记录当前页面数量（用于检测新标签页）
                pages_before = len(page.context.pages)
                current_url = page.url

                # 模拟点击卡片进入详情页
                clicked = False
                try:
                    name_el = await card.query_selector('em')
                    if name_el and await name_el.is_visible():
                        await name_el.click()
                        clicked = True
                        console.print(f"    [dim]点击姓名元素[/]")
                except Exception:
                    pass

                if not clicked:
                    try:
                        await card.click()
                        clicked = True
                        console.print(f"    [dim]点击卡片[/]")
                    except Exception as e:
                        console.print(f"    [yellow]点击失败: {e}[/]")
                        continue

                await human_delay(1.0, 2.0)

                # 检测新标签页或页面跳转
                new_page = None
                if len(page.context.pages) > pages_before:
                    new_page = page.context.pages[-1]
                    try:
                        await new_page.wait_for_load_state('domcontentloaded', timeout=10000)
                    except Exception:
                        pass
                    console.print(f"    [dim]新标签页已打开: {new_page.url[:60]}[/]")

                elif page.url != current_url:
                    new_page = page
                    try:
                        await new_page.wait_for_load_state('domcontentloaded', timeout=10000)
                    except Exception:
                        pass
                    console.print(f"    [dim]页面已跳转: {new_page.url[:60]}[/]")

                if not new_page:
                    console.print(f"    [yellow]未检测到页面变化，跳过[/]")
                    continue

                # 从详情页抓取全部文字
                try:
                    await new_page.wait_for_load_state('networkidle', timeout=8000)
                except Exception:
                    pass
                await human_delay(0.5, 1.5)

                full_text = await new_page.evaluate("""
                    () => {
                        const body = document.body;
                        if (!body) return '';
                        const clone = body.cloneNode(true);
                        clone.querySelectorAll('script, style, noscript, iframe, nav, header, footer').forEach(el => el.remove());
                        return (clone.innerText || clone.textContent || '').trim();
                    }
                """)

                if full_text and len(full_text) > 100:
                    basic_info['full_text'] = full_text[:8000]
                    basic_info['detail_url'] = new_page.url
                    resumes.append(basic_info)
                    console.print(f"    [green]✓ 抓取成功 ({len(full_text)}字)[/]")
                else:
                    console.print(f"    [yellow]详情页内容太少 ({len(full_text)}字)，跳过[/]")

                # 如果是新标签页，关闭它并回到搜索页
                if new_page != page:
                    try:
                        await new_page.close()
                    except Exception:
                        pass
                    try:
                        await page.bring_to_front()
                    except Exception:
                        pass

                await between_cards_delay()

            except Exception as e:
                error_msg = str(e)
                if "closed" in error_msg.lower() or "Target page" in error_msg:
                    console.print(f"  [{i+1}] [red]抓取失败：浏览器已关闭，停止抓取[/]")
                    break
                console.print(f"  [{i+1}] [red]抓取失败：{error_msg}[/]")
                continue

        console.print(f"[green]本轮抓取完成: {len(resumes)}/{len(all_cards_info)} 份简历入库[/]")
        return resumes

    async def _go_next_page(self, page: Page) -> bool:
        """翻到下一页"""
        try:
            # 猎聘的翻页按钮
            next_selectors = [
                '[class*="next"]',
                '[class*="pagination"] .next',
                'li.ant-pagination-next',
                'button.ant-pagination-next',
                'a:has-text("下一页")',
            ]

            for sel in next_selectors:
                try:
                    btn = page.locator(sel).first
                    if await btn.count() > 0 and await btn.is_visible():
                        # 检查是否禁用
                        is_disabled = await btn.get_attribute('disabled')
                        cls = await btn.get_attribute('class') or ''
                        if is_disabled or 'disabled' in cls:
                            return False
                        await btn.click()
                        return True
                except Exception:
                    continue

            return False
        except Exception:
            return False

    def _save_results(self):
        """保存结果到文件"""
        if not self.results:
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # JSON
        json_path = self.data_dir / f"liepin_resumes_{timestamp}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(self.results, f, ensure_ascii=False, indent=2)

        # latest
        latest_path = self.data_dir / "latest.json"
        with open(latest_path, "w", encoding="utf-8") as f:
            json.dump(self.results, f, ensure_ascii=False, indent=2)

        console.print(f"[dim]结果已保存：{json_path}[/]")
