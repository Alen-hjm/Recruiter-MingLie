class ActionExecutor:

    async def execute(self, page, command):

        action = command.get("action")

        if action == "click":

            selector = command.get("selector")

            try:
                # 与 scraper_liepin.py 保持一致：
                # 使用 wait_for_selector + ElementHandle.click
                el = await page.wait_for_selector(
                    selector,
                    timeout=5000
                )

                await el.click()

                return {
                    "success": True,
                    "action": "click",
                    "selector": selector
                }

            except Exception as e:
                return {
                    "success": False,
                    "error": str(e)
                }


        elif action == "scroll":

            await page.mouse.wheel(
                0,
                command.get("distance", 600)
            )

            return {
                "success": True,
                "action": "scroll"
            }


        await page.wait_for_timeout(1000)

        return {
            "success": True,
            "action": "wait"
        }
