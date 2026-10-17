import time
import random

class RateLimiter:
    """İstek engellemelerini (rate-limiting) önlemek için akıllı gecikme yöneticisi"""
    def __init__(self, min_delay: float = 0.5, max_delay: float = 1.5):
        self.min_delay = min_delay
        self.max_delay = max_delay

    def wait(self):
        time.sleep(random.uniform(self.min_delay, self.max_delay))