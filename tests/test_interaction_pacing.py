import asyncio
import random

import pytest

from app.interaction_pacing import InteractionPacer


def test_click_checks_state_after_wait_and_keeps_locator_target():
    events = []
    async def sleep(seconds): events.append(("wait", seconds))
    async def check(): events.append(("check",))
    class Target:
        async def scroll_into_view_if_needed(self): events.append(("scroll",))
        async def hover(self): events.append(("hover",))
        async def click(self, **kwargs): events.append(("click", kwargs))
    pacer = InteractionPacer(rng=random.Random(4), sleep=sleep)
    asyncio.run(pacer.click(Target(), check))
    assert [e[0] for e in events] == ["wait", "check", "scroll", "hover", "wait", "check", "click"]
    assert 0.8 <= events[0][1] <= 1.8
    assert 0.2 <= events[4][1] <= 0.6
    assert 60 <= events[-1][1]["delay"] <= 140


def test_new_restriction_during_hover_prevents_click():
    calls = []
    async def sleep(seconds): pass
    async def check():
        calls.append("check")
        if len(calls) == 2:
            raise RuntimeError("verification required")
    class Target:
        async def scroll_into_view_if_needed(self): pass
        async def hover(self): pass
        async def click(self, **kwargs): pytest.fail("must not click verification")
    with pytest.raises(RuntimeError, match="verification"):
        asyncio.run(InteractionPacer(sleep=sleep).click(Target(), check))


def test_break_threshold_is_stable_until_reached():
    waits = []
    async def sleep(seconds): waits.append(seconds)
    pacer = InteractionPacer(rng=random.Random(1), sleep=sleep, clock=lambda: 0)
    threshold = pacer.next_break
    async def run():
        for _ in range(threshold):
            await pacer.before_operation()
        assert all(t < 2 for t in waits)
        await pacer.before_operation()
    asyncio.run(run())
    assert len([t for t in waits if 10 <= t <= 20]) == 1
    assert pacer.next_break > threshold


def test_session_budget_uses_elapsed_time():
    now = [0]
    pacer = InteractionPacer(clock=lambda: now[0])
    now[0] = 1800
    assert asyncio.run(pacer.should_stop()) is True
