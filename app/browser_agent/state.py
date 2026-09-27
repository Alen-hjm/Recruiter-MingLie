from dataclasses import dataclass, field

@dataclass
class BrowserState:
    url: str = ""
    title: str = ""
    goal: str = ""
    actions: list = field(default_factory=list)
    history: list = field(default_factory=list)

    def update(self, data):
        for k, v in data.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def add_history(self, item):
        self.history.append(item)
