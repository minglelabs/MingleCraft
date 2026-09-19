class Scheduler:
    """Game-frame cadence: micro 250 ms, production 1 s, macro 3 s at 24 fps."""

    periods = {
        "economy": 6,
        "attack": 6,
        "defense": 6,
        "production": 24,
        "construction": 72,
        "scout": 72,
    }

    def __init__(self):
        self.last: dict[str, int] = {}

    def due(self, frame: int) -> set[str]:
        return {
            k for k, period in self.periods.items() if frame - self.last.get(k, -period) >= period
        }

    def selected(self, category: str, frame: int):
        if category != "wait":
            self.last[category] = frame
