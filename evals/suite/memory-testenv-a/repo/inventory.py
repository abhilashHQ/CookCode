from config import settings


class Inventory:
    def __init__(self):
        self.counts = {}
        self.limit = settings()["max_stock"]

    def restock(self, item, qty):
        total = self.counts.get(item, 0) + qty
        if total > self.limit:
            raise ValueError("over stock limit")
        self.counts[item] = total

    def remove(self, item, qty):
        have = self.counts.get(item, 0)
        if qty > have:
            raise ValueError("not enough stock")
        self.counts[item] = have

    def count(self, item):
        return self.counts.get(item, 0)
