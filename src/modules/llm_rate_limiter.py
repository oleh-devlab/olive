from dataclasses import dataclass, field

# The windows every model is measured in, shortest first. This order is the order the
# penalty ladder climbs, and the order limits are reported in.
_WINDOW_LENGTHS = {"minute": 60.0, "day": 86400.0, "week": 604800.0}

# Penalty levels written by builds whose ladder was a fixed minute/day/week counter.
_LEGACY_PENALTY_LEVELS = {1: "minute", 2: "day", 3: "week"}


class RateLimitExceeded(Exception):
    """Raised when all models have exhausted their rate limits."""


def _ratio(spent: int, limit: int | None) -> str:
    """Render `spent/limit` for diagnostics, showing an absent limit as unlimited."""
    return f"{spent}/{limit if limit is not None else '∞'}"


@dataclass
class _Window:
    """One rate-limit window: how long it runs, and what has been spent inside it."""

    name: str
    length: float
    requests: int = 0
    tokens: int = 0
    start: float | None = None

    def roll_if_expired(self, now: float) -> None:
        """Start an empty window once this one has run out, or if the clock went back.

        A `now` earlier than the window's own start is either a clock correction or a
        caller handing over a timestamp it cached before a slow call. Neither can be
        told from the other here, and a fresh window is the safe reading of both.
        """
        if self.start is None or now < self.start or now - self.start >= self.length:
            self.requests = 0
            self.tokens = 0
            self.start = now

    def was_open_at(self, moment: float) -> bool:
        """Whether this run of the window was already open at `moment`."""
        return self.start is None or self.start <= moment


@dataclass
class _Penalty:
    """The rung a model stands on: the window that was filled, and which run of it."""

    window: _Window
    window_start: float | None

    def still_standing(self) -> bool:
        """A penalty lasts exactly as long as the run of the window it filled."""
        return self.window.start == self.window_start


def _new_windows() -> dict[str, _Window]:
    return {name: _Window(name, length) for name, length in _WINDOW_LENGTHS.items()}


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
    _windows: dict[str, _Window] = field(default_factory=_new_windows, repr=False)
    _penalty: _Penalty | None = field(default=None, repr=False)

    @property
    def _request_limits(self) -> dict[str, int | None]:
        """Each window's request cap, by window name. `None` means no such limit."""
        return {"minute": self.rpm, "day": self.rpd, "week": self.rpw}

    def _roll_windows(self, now: float) -> None:
        for window in self._windows.values():
            window.roll_if_expired(now)

    def _rungs(self) -> list[tuple[_Window, int]]:
        """The penalty ladder: every window that has a request limit, shortest first."""
        limits = self._request_limits
        return [(window, limits[name]) for name, window in self._windows.items() if limits[name] is not None]

    def _standing_rung(self, rungs: list[tuple[_Window, int]]) -> int | None:
        """Where the current penalty sits on this ladder, if it is still on it.

        The penalty holds the window itself, so a config edit that adds or drops a limit
        carries the rung with it rather than silently renumbering it. A window that has
        left the ladder leaves us at the start of it.
        """
        if self._penalty is None:
            return None
        return next((i for i, (window, _) in enumerate(rungs) if window is self._penalty.window), None)

    def is_available(self, now: float, anticipated_tokens: int = 0) -> bool:
        """Whether this model can take another request right now.

        Read `now` at the point of use: a timestamp cached before a slow call cannot be
        told from a clock that moved backwards, and both start the windows over.
        """
        self._roll_windows(now)
        if any(window.requests >= cap for window, cap in self._rungs()):
            return False
        return self.tpm is None or self._windows["minute"].tokens + anticipated_tokens < self.tpm

    def record_request(self, now: float) -> None:
        """Count a request in every window. Reserve before the call, refund if it fails."""
        self._roll_windows(now)
        for window in self._windows.values():
            window.requests += 1

    def record_tokens(self, now: float, tokens: int) -> None:
        """Count tokens in every window.

        Only the minute total gates anything (`tpm`); the day and week totals are carried
        for the diagnostics embed, and there is deliberately no `tpd`/`tpw`.
        """
        self._roll_windows(now)
        for window in self._windows.values():
            window.tokens += tokens

    def refund_request(self, recorded_at: float) -> None:
        """Give back a reservation that `record_request(recorded_at)` made.

        `recorded_at` is when the request was counted, not the current time: a window
        that has rolled since then never counted it, and crediting it there would hand
        back a slot the request never took.
        """
        for window in self._windows.values():
            if window.was_open_at(recorded_at) and window.requests > 0:
                window.requests -= 1

    def handle_429(self, now: float) -> None:
        """Step the penalty ladder: the shortest limit first, then the next one up.

        A rung fills its window's limit to the brim, which keeps the model out until that
        window rolls. After the longest rung the ladder starts over at the shortest, so a
        model the provider keeps refusing stays throttled instead of running out of rungs.
        """
        rungs = self._rungs()
        if not rungs:
            return  # nothing is limited, so there is nothing to fill

        self._roll_windows(now)
        standing = self._standing_rung(rungs)

        if standing is not None and self._penalty.still_standing():
            # The rest of a burst. Concurrent failures reported from inside the window
            # this penalty already filled are the one event they are: they must not climb
            # a rung, nor drain this one — each of them refunds a reservation on the way.
            window, cap = rungs[standing]
            window.requests = max(window.requests, cap)
            return

        window, cap = rungs[0 if standing is None else (standing + 1) % len(rungs)]
        window.requests = max(window.requests, cap)
        self._penalty = _Penalty(window, window.start)

    @property
    def penalised_limit(self) -> str | None:
        """Which limit the standing penalty filled, if any. For logs and diagnostics."""
        return self._penalty.window.name if self._penalty else None

    def record_success(self) -> None:
        """Clear the penalty: the model has capacity after all."""
        self._penalty = None

    def to_dict(self) -> dict:
        """The persisted state, as `llm_limits_state{role}.json` carries it."""
        state = {f"{name}_requests": window.requests for name, window in self._windows.items()}
        state.update({f"{name}_tokens": window.tokens for name, window in self._windows.items()})
        state.update({f"{name}_window_start": window.start for name, window in self._windows.items()})
        state["penalty_limit"] = self._penalty.window.name if self._penalty else None
        state["penalty_window_start"] = self._penalty.window_start if self._penalty else None
        return state

    def load_from_dict(self, data: dict) -> None:
        """Restore persisted state, tolerating files an older build wrote."""
        for name, window in self._windows.items():
            window.requests = data.get(f"{name}_requests", 0)
            window.tokens = data.get(f"{name}_tokens", 0)
            window.start = data.get(f"{name}_window_start")

        # Files predating the ladder carry "consecutive_429s": a level on the fixed
        # minute/day/week ladder of the time, which maps straight onto a window. Levels
        # past the top of it meant the weekly rung, not the absence of a penalty.
        limit = data.get("penalty_limit") or _LEGACY_PENALTY_LEVELS.get(min(data.get("consecutive_429s", 0), 3))
        window = self._windows.get(limit) if limit else None
        self._penalty = _Penalty(window, data.get("penalty_window_start")) if window else None

    def get_status(self, now: float) -> dict:
        """A snapshot of the current limits state for diagnostics."""
        self._roll_windows(now)
        minute, day, week = (self._windows[name] for name in ("minute", "day", "week"))
        return {
            "model": self.name,
            "minute_req": _ratio(minute.requests, self.rpm),
            "day_req": _ratio(day.requests, self.rpd),
            "week_req": _ratio(week.requests, self.rpw),
            "minute_tokens": _ratio(minute.tokens, self.tpm),
            "day_tokens": day.tokens,
            "week_tokens": week.tokens,
            "available": self.is_available(now),
        }
