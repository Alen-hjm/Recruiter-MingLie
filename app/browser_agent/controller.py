from .state import BrowserState
from .observer import PageObserver
from .planner import ActionPlanner
from .executor import ActionExecutor


class BrowserAgent:

    def __init__(self, page):

        self.page = page
        self.state = BrowserState()
        self.observer = PageObserver()
        self.planner = ActionPlanner()
        self.executor = ActionExecutor()


    async def run(self, goal, max_steps=10):

        self.state.goal = goal

        logs = []

        for i in range(max_steps):

            info = await self.observer.observe(
                self.page
            )

            self.state.update(info)

            action = self.planner.plan(
                self.state
            )

            result = await self.executor.execute(
                self.page,
                action
            )

            self.state.add_history(action)

            logs.append({
                "step": i + 1,
                "observe": info,
                "action": action,
                "result": result
            })

            await self.page.wait_for_timeout(1000)

        return {
            "success": True,
            "logs": logs
        }
