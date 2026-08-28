from dataclasses import dataclass, field


class RateLimitExceeded(Exception):
    """Raised when all models have exhausted their rate limits."""


@dataclass
class ModelConfig:
    """Configuration and rate-limit state for a single model."""

    name: str
    rpm: int | None = 15  # requests per minute (None: no such limit)
    rpd: int | None = 1500  # requests per day (None: no such limit)
    rpw: int | None = None  # requests per week (None: no such limit)
    tpm: int | None = None  # tokens per minute (None: no such limit)
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
    _penalty_rung: int = field(default=0, repr=False)  # 0 = unpenalised, else the 1-based rung
    _penalty_window_start: float | None = field(default=None, repr=False)

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
            "penalty_rung": self._penalty_rung,
            "penalty_window_start": self._penalty_window_start,
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
        # "consecutive_429s" is what the penalty level was called before the ladder
        # was built from the configured limits; existing state files still carry it.
        self._penalty_rung = data.get("penalty_rung", data.get("consecutive_429s", 0))
        self._penalty_window_start = data.get("penalty_window_start")

    def is_available(self, now: float, anticipated_tokens: int = 0) -> bool:
        """Check if this model can handle another request right now."""
        self._reset_windows_if_needed(now)
        if self.rpm is not None and self._minute_requests >= self.rpm:
            return False
        if self.rpd is not None and self._day_requests >= self.rpd:
            return False
        if self.rpw is not None and self._week_requests >= self.rpw:
            return False
        return not (self.tpm is not None and (self._minute_tokens + anticipated_tokens) >= self.tpm)

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

    def _ladder(self) -> list[tuple[str, str, int]]:
        """The penalty rungs: one per limit that exists, shortest window first."""
        return [
            (counter, window, cap)
            for counter, window, cap in (
                ("_minute_requests", "_minute_window_start", self.rpm),
                ("_day_requests", "_day_window_start", self.rpd),
                ("_week_requests", "_week_window_start", self.rpw),
            )
            if cap is not None
        ]

    def handle_429(self, now: float):
        """Step the penalty ladder: the shortest limit first, then the next one up.

        After the longest rung it starts over at the shortest, so a model the provider
        keeps refusing stays throttled instead of running out of rungs. A rung fills its
        limit to the brim, which blocks the model until that window rolls; the penalty is
        anchored to that window, so further 429s reported from inside it — a burst of
        concurrent requests failing together — count as the one event they are.
        """
        rungs = self._ladder()
        if not rungs:
            return  # nothing is limited, so there is nothing to fill

        self._reset_windows_if_needed(now)
        if self._penalty_rung > len(rungs):
            self._penalty_rung = 0  # the ladder shrank under us: start over

        if self._penalty_rung:
            counter, window, cap = rungs[self._penalty_rung - 1]
            if self._penalty_window_start == getattr(self, window):
                # Still the window this penalty was applied in, so the rest of a burst
                # is the same event and must not climb a rung. It must not drain this
                # one either: every failure refunds its reservation on the way here.
                setattr(self, counter, max(getattr(self, counter), cap))
                return

        self._penalty_rung = self._penalty_rung % len(rungs) + 1
        counter, window, cap = rungs[self._penalty_rung - 1]
        setattr(self, counter, max(getattr(self, counter), cap))
        self._penalty_window_start = getattr(self, window)

    def record_success(self):
        """Clear the penalty on success: the model has capacity after all."""
        self._penalty_rung = 0
        self._penalty_window_start = None

    def get_status(self, now: float) -> dict:
        """Return a snapshot of the current limits state for diagnostics."""
        self._reset_windows_if_needed(now)
        return {
            "model": self.name,
            "minute_req": f"{self._minute_requests}/{self.rpm if self.rpm is not None else '∞'}",
            "day_req": f"{self._day_requests}/{self.rpd if self.rpd is not None else '∞'}",
            "week_req": f"{self._week_requests}/{self.rpw if self.rpw is not None else '∞'}",
            "minute_tokens": f"{self._minute_tokens}/{self.tpm if self.tpm is not None else '∞'}",
            "day_tokens": self._day_tokens,
            "week_tokens": self._week_tokens,
            "available": self.is_available(now),
        }
