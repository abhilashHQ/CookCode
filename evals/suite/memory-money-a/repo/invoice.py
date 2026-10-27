from money import fmt


class Invoice:
    def __init__(self):
        self.lines = []

    def add(self, label, cents):
        self.lines.append((label, cents))

    def subtotal(self):
        return sum(cents for _, cents in self.lines)

    def total(self):
        return self.subtotal()

    def render(self):
        rows = [f"{label}: {fmt(cents)}" for label, cents in self.lines]
        return "\n".join([*rows, f"Total: {fmt(self.total())}"])
