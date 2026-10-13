"""采集异常只描述可观察状态，不把所有空页面归因于风控。"""
from urllib.parse import urlsplit


class ScrapeNeedsAttention(RuntimeError):
    pass


def page_problem(url: str, visible_text: str) -> str | None:
    parsed = urlsplit(url)
    text = (visible_text or "").strip()
    if url.startswith(("about:blank", "chrome-error:")) or not text:
        return "页面空白或未能加载，已停止采集。请手动确认猎聘页面能正常打开后重新继续。"
    if parsed.hostname == "safe.liepin.com":
        return "页面要求安全验证，已停止采集。请人工处理，待平台允许访问后重新继续。"
    if any(word in parsed.path.lower() for word in ("login", "passport")) or (parsed.hostname or "").startswith("passport."):
        return "登录已失效，请在采集浏览器完成登录后重新继续。"
    if len(text) < 3000 and any(word in text for word in (
        "请完成安全验证", "请完成验证", "拖动滑块", "访问过于频繁", "访问受限", "异常访问", "安全校验",
    )):
        return "页面要求验证或限制访问，已停止采集。请人工处理，待平台允许访问后重新继续。"
    return None
