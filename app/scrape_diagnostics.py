"""Minimal local navigation timeline. Never save query values or response bodies."""
import json
import time
from datetime import datetime, timezone
from uuid import uuid4
from urllib.parse import urlsplit


def page_kind(url):
    parsed = urlsplit(url)
    if parsed.scheme == "about":
        return "blank" if parsed.path == "blank" else "about"
    if parsed.hostname != "h.liepin.com":
        return "external"
    path = parsed.path.lower()
    if "login" in path or "passport" in path:
        return "login"
    if path == "/":
        return "home"
    if path == "/search/getconditionitem":
        return "search"
    return "other_liepin"


class NavigationLog:
    def __init__(self, directory):
        self.directory = directory
        self.started = time.monotonic()
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.events = []
        self.pages = []

    def stage(self, name, **fields):
        if len(self.events) < 500:
            self.events.append({"seconds": round(time.monotonic() - self.started, 3),
                                "event": name, **fields})

    def attach(self, page):
        if page in self.pages:
            return
        self.pages.append(page)
        page.on("framenavigated", lambda frame: self.stage(
            "navigation", page=page_kind(frame.url)) if frame == page.main_frame else None)
        page.on("popup", self.attach)
        page.on("crash", lambda _: self.stage("page_crash"))

    def save(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"navigation-{uuid4().hex}.json"
        path.write_text(json.dumps({"created_at": self.created_at, "events": self.events},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
        return path
