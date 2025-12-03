from collections import deque

class PriceBuffer:
    def __init__(self):
        self.buffer = deque()
        self.current_price = 0.0
    def add(self, p, t):
        self.current_price = p
        self.buffer.append((t, p))
        while self.buffer and self.buffer[0][0] < (t - 600): self.buffer.popleft()
    def get_change(self, sec=60):
        if not self.buffer: return 0.0
        target = self.buffer[-1][0] - sec
        old = next((p for t, p in self.buffer if t >= target), self.buffer[0][1])
        return ((self.current_price - old) / old) * 100
    

