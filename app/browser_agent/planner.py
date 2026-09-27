class ActionPlanner:

    def plan(self, state):

        actions = state.actions

        for text in actions:
            if "下一页" in text or "下页" in text:
                return {
                    "action": "click",
                    "selector": 'text=' + text,
                    "reason": "翻页获取更多候选人"
                }

        return {
            "action": "scroll",
            "distance": 600,
            "reason": "继续观察页面"
        }
