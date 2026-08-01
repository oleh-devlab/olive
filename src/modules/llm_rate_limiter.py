from dataclasses import dataclass, field


class RateLimitExceeded(Exception):
    """Raised when all models have exhausted their rate limits."""

    pass


@dataclass
class ModelConfig:
    """Configuration and rate-limit state for a single model."""

    name: str
    rpm: int = 15  # requests per minute
    rpd: int = 1500  # requests per day
    rpw: int | None = None  # requests per week (optional)
    tpm: int | None = None  # tokens per minute
    max_context_tokens: int = 128000  # context size limit in tokens
    thinking_level: str | None = None
    thinking_budget: int | None = None

    # Internal state
    _minute_requests: int = field(default=0, repr=False)
    _day_requests: int = field(default=0, repr=False)
    _week_requests: int = field(default=0, repr=False)
    _minute_tokens: int = field(default=0, repr=False)
    _day_tokens: int = field(default=0, repr=False)
    _week_tokens: int = field(default=0, repr=False)
    _minute_window_start: float | None = field(default=None, repr=False)
    _day_window_start: float | None = field(default=None, repr=False)
    _week_window_start: float | None = field(default=None, repr=False)
    _consecutive_429s: int = field(default=0, repr=False)

    def _reset_windows_if_needed(self, now: float):
        """Reset counters if their time windows have expired. Handles NTP backwards jumps."""
        if (
            self._minute_window_start is None
            or now < self._minute_window_start
            or (now - self._minute_window_start) >= 60.0
        ):
            self._minute_requests = 0
            self._minute_tokens = 0
            self._minute_window_start = now

        if self._day_window_start is None or now < self._day_window_start or (now - self._day_window_start) >= 86400.0:
            self._day_requests = 0
            self._day_tokens = 0
            self._day_window_start = now

        if (
            self._week_window_start is None
            or now < self._week_window_start
            or (now - self._week_window_start) >= 604800.0
        ):
            self._week_requests = 0
            self._week_tokens = 0
            self._week_window_start = now

    def to_dict(self) -> dict:
        return {
            "minute_requests": self._minute_requests,
            "day_requests": self._day_requests,
            "week_requests": self._week_requests,
            "minute_tokens": self._minute_tokens,
            "day_tokens": self._day_tokens,
            "week_tokens": self._week_tokens,
            "minute_window_start": self._minute_window_start,
            "day_window_start": self._day_window_start,
            "week_window_start": self._week_window_start,
            "consecutive_429s": self._consecutive_429s,
        }

    def load_from_dict(self, data: dict):
        self._minute_requests = data.get("minute_requests", 0)
        self._day_requests = data.get("day_requests", 0)
        self._week_requests = data.get("week_requests", 0)
        self._minute_tokens = data.get("minute_tokens", 0)
        self._day_tokens = data.get("day_tokens", 0)
        self._week_tokens = data.get("week_tokens", 0)
        self._minute_window_start = data.get("minute_window_start")
        self._day_window_start = data.get("day_window_start")
        self._week_window_start = data.get("week_window_start")
        self._consecutive_429s = data.get("consecutive_429s", 0)

    def is_available(self, now: float, anticipated_tokens: int = 0) -> bool:
        """Check if this model can handle another request right now."""
        self._reset_windows_if_needed(now)
        if self._minute_requests >= self.rpm or self._day_requests >= self.rpd:
            return False
        if self.rpw is not None and self._week_requests >= self.rpw:
            return False
        if self.tpm is not None and (self._minute_tokens + anticipated_tokens) >= self.tpm:
            return False
        return True

    def record_request(self, now: float):
        """Increment counters after a successful request or for reservation."""
        self._reset_windows_if_needed(now)
        self._minute_requests += 1
        self._day_requests += 1
        self._week_requests += 1

    def record_tokens(self, now: float, tokens: int):
        """Add tokens used by a request to the counters."""
        self._reset_windows_if_needed(now)
        self._minute_tokens += tokens
        self._day_tokens += tokens
        self._week_tokens += tokens

    def refund_request(self):
        """Refund a request if the API call failed."""
        if self._minute_requests > 0:
            self._minute_requests -= 1
        if self._day_requests > 0:
            self._day_requests -= 1
        if self._week_requests > 0:
            self._week_requests -= 1

    def handle_429(self):
        """Apply rate limits on 429: minute limit, then daily, then weekly. Handles concurrent 429s."""
        # If the penalty is already active for this window, ignore concurrent 429s
        if self._consecutive_429s == 1 and self._minute_requests >= self.rpm:
            return
        if self._consecutive_429s == 2 and self._day_requests >= self.rpd:
            return
        if self._consecutive_429s >= 3 and (self.rpw is None or self._week_requests >= self.rpw):
            return

        self._consecutive_429s += 1
        if self._consecutive_429s == 1:
            self._minute_requests = self.rpm
        elif self._consecutive_429s == 2:
            self._day_requests = self.rpd
        elif self._consecutive_429s >= 3 and self.rpw is not None:
            self._week_requests = self.rpw

    def record_success(self):
        """Reset consecutive 429s on success."""
        self._consecutive_429s = 0

    def get_status(self, now: float) -> dict:
        """Return a snapshot of the current limits state for diagnostics."""
        self._reset_windows_if_needed(now)
        return {
            "model": self.name,
            "minute_req": f"{self._minute_requests}/{self.rpm}",
            "day_req": f"{self._day_requests}/{self.rpd}",
            "week_req": f"{self._week_requests}/{self.rpw if self.rpw is not None else '∞'}",
            "minute_tokens": f"{self._minute_tokens}/{self.tpm if self.tpm is not None else '∞'}",
            "day_tokens": self._day_tokens,
            "week_tokens": self._week_tokens,
            "available": self.is_available(now),
        }
