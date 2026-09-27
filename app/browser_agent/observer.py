class PageObserver:

    async def observe(self, page):
        state = {
            "url": page.url,
            "actions": []
        }

        try:
            state["title"] = await page.title()
        except:
            state["title"] = ""

        try:
            buttons = await page.locator("button").all_inner_texts()
            state["actions"].extend(buttons)
        except:
            pass

        try:
            links = await page.locator("a").all_inner_texts()
            state["actions"].extend(links)
        except:
            pass

        state["actions"] = list(
            set([x.strip() for x in state["actions"] if x.strip()])
        )

        return state
