"""Concurrent presentation reads, isolated from uncached trading and risk requests."""

import threading
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import UTC, datetime


class DashboardReader:
    def __init__(self, broker):
        self.broker = broker
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="dashboard-read")
        self.condition = threading.Condition()
        self.loading = False
        self.result = None
        self.error = None

    def load(self):
        with self.condition:
            if self.loading:
                while self.loading:
                    self.condition.wait()
                if self.error:
                    raise self.error
                return self.result
            self.loading = True
        result, error = None, None
        try:
            futures = {
                "account": self.executor.submit(self.broker.get, "/v2/account"),
                "clock": self.executor.submit(self.broker.get, "/v2/clock"),
                "positions": self.executor.submit(self.broker.get, "/v2/positions"),
                "orders": self.executor.submit(
                    self.broker.get, "/v2/orders", status="all", limit=100, direction="desc"
                ),
            }
            wait(futures.values())
            result = {name: future.result() for name, future in futures.items()}
            result["received_at"] = datetime.now(UTC).isoformat()
            return result
        except Exception as exc:
            error = exc
            raise
        finally:
            with self.condition:
                self.result, self.error = result, error
                self.loading = False
                self.condition.notify_all()

    def close(self):
        self.executor.shutdown(wait=True, cancel_futures=True)
