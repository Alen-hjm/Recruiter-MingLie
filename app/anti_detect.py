"""
反检测工具集
让脚本操作更像真人（速度优化版）
"""

import asyncio
import random


# ============================================================
# 1. 随机延迟 —— 替代固定的 sleep(3)（已提速）
# ============================================================

async def human_delay(min_sec=0.8, max_sec=2.0):
    """随机等待，模拟人类操作节奏（提速版）"""
    delay = random.uniform(min_sec, max_sec)
    await asyncio.sleep(delay)


async def reading_delay(text_length: int = 200):
    """根据文本长度模拟阅读时间（提速版）"""
    base = 0.5 + (text_length / 100) * 0.3
    jitter = random.uniform(-0.2, 0.5)
    await asyncio.sleep(max(0.5, base + jitter))


async def page_load_delay():
    """翻页后等待，模拟人类看新页面（提速版）"""
    await asyncio.sleep(random.uniform(1.2, 2.5))


async def between_cards_delay():
    """浏览完一个简历卡片后，看下一个之前的停顿（提速版）"""
    await asyncio.sleep(random.uniform(0.3, 1.0))


# ============================================================
# 2. 随机鼠标轨迹 —— 替代直接 click
# ============================================================

async def human_click(page, selector: str):
    """模拟人类鼠标移动 + 点击"""
    el = await page.query_selector(selector)
    if not el:
        return False

    box = await el.bounding_box()
    if not box:
        return False

    # 不是精确点中心，而是随机落在元素范围内
    x = box["x"] + random.uniform(box["width"] * 0.2, box["width"] * 0.8)
    y = box["y"] + random.uniform(box["height"] * 0.2, box["height"] * 0.8)

    # 先移动到附近（分1-3步）
    steps = random.randint(1, 3)
    for _ in range(steps):
        jitter_x = x + random.uniform(-20, 20)
        jitter_y = y + random.uniform(-10, 10)
        await page.mouse.move(jitter_x, jitter_y)
        await asyncio.sleep(random.uniform(0.03, 0.1))

    # 最终点击
    await page.mouse.click(x, y)
    return True


# ============================================================
# 3. 随机滚动 —— 模拟人类浏览
# ============================================================

async def human_scroll(page, direction="down", amount=None):
    """模拟人类滚动页面"""
    if amount is None:
        amount = random.randint(200, 600)

    if direction == "up":
        amount = -amount

    # 分2-4次滚动，不是一次到位
    chunks = random.randint(2, 4)
    chunk_size = amount // chunks
    for i in range(chunks):
        scroll = chunk_size + random.randint(-30, 30)
        await page.mouse.wheel(0, scroll)
        await asyncio.sleep(random.uniform(0.05, 0.2))

    # 偶尔停一下，好像在看内容
    if random.random() < 0.3:
        await asyncio.sleep(random.uniform(0.3, 1.0))


async def scroll_to_element(page, selector: str):
    """滚动到某个元素，像人一样先看到它"""
    el = await page.query_selector(selector)
    if el:
        await el.scroll_into_view_if_needed()
        await asyncio.sleep(random.uniform(0.2, 0.5))


# ============================================================
# 4. 操作节奏控制 —— 避免太规律
# ============================================================

class HumanPacer:
    """控制整体操作节奏，加入随机休息和停顿（提速版）"""

    def __init__(self):
        self.operation_count = 0
        self.session_start = asyncio.get_event_loop().time()

    async def before_operation(self):
        """每次操作前调用"""
        self.operation_count += 1

        # 每操作 8-20 次，随机休息 2-5 秒
        if self.operation_count % random.randint(8, 20) == 0:
            rest = random.uniform(2, 5)
            print(f"  [pace] 休息 {rest:.1f}s...")
            await asyncio.sleep(rest)

        # 每操作 30-50 次，长休息 5-15 秒
        if self.operation_count % random.randint(30, 50) == 0:
            rest = random.uniform(5, 15)
            print(f"  [pace] 长休息 {rest:.1f}s...")
            await asyncio.sleep(rest)

    async def should_stop(self) -> bool:
        """判断是否应该停止（避免一次性抓太多）"""
        elapsed = asyncio.get_event_loop().time() - self.session_start
        # 单次运行不超过30分钟
        if elapsed > 1800:
            print("  [pace] 运行超过30分钟，建议休息")
            return True
        return False


# ============================================================
# 5. 会话伪装 —— 修改浏览器指纹
# ============================================================

STEALTH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--disable-infobars",
    "--no-first-run",
    "--no-default-browser-check",
]

STEALTH_JS = """
// 去掉 webdriver 标记
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

// 伪装 plugins
Object.defineProperty(navigator, 'plugins', {
    get: () => [1, 2, 3, 4, 5],
});

// 伪装 languages
Object.defineProperty(navigator, 'languages', {
    get: () => ['zh-CN', 'zh', 'en'],
});

// 去掉自动化相关属性
delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;
"""
