"""Bounded interaction pacing, independent of browser fingerprint handling."""
import asyncio
import random
import time


class InteractionPacer:
    """Space out UI work; retain locator actionability and normal page checks."""

    RANGES = {
        "action": (0.8, 1.8),
        "hover": (0.2, 0.6),
        "detail": (3.0, 6.0),
        "between": (2.0, 4.0),
        "page": (3.0, 5.0),
        "break": (10.0, 20.0),
    }

    def __init__(self, *, rng=None, sleep=None, clock=None, on_progress=None):
        self.rng = rng or random.Random()
        self.sleep = sleep or asyncio.sleep
        self.clock = clock or time.monotonic
        self.on_progress = on_progress
        self.session_start = self.clock()
        self.operation_count = 0
        self.next_break = self.rng.randint(6, 10)

    async def pause(self, kind):
        low, high = self.RANGES[kind]
        await self.sleep(self.rng.uniform(low, high))

    async def before_operation(self):
        # Select a break threshold once, not a new modulo divisor on every click.
        if self.operation_count >= self.next_break:
            if self.on_progress:
                self.on_progress("正在短暂休息，随后继续采集")
            await self.pause("break")
            self.next_break = self.operation_count + self.rng.randint(6, 10)
        await self.pause("action")
        self.operation_count += 1

    async def click(self, target, check_page):
        await self.before_operation()
        await check_page()
        await target.scroll_into_view_if_needed()
        await target.hover()
        await self.pause("hover")
        # A challenge may have appeared during the wait. Never click through it.
        await check_page()
        await target.click(delay=self.rng.randint(60, 140))

    async def should_stop(self):
        return self.clock() - self.session_start >= 1800
