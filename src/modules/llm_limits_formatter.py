"""Rendering of the LLM rate-limit snapshot into the body of the `llm_limits` embed.

`LLMClientPool.get_unique_clients_status()` reports a model list per API key, and the
same models are configured for every key — so seven models across two keys is fourteen
readings, nearly all of them zeros. Given a heading and four lines each, that is a
screenful of nothing. Here the whole snapshot is one monospace table instead: a line
per model, a column only where some model has a limit or a spend to put in it, and the
untouched models folded into a single line.

Free of disnake, settings and phrases on purpose — the cog looks the labels up and
hands them over, which is what keeps this module's suite runnable on a bare checkout.
"""

from dataclasses import dataclass

INFINITY = "∞"

# The marker column: one character in front of every model line.
READY_MARK = " "
BLOCKED_MARK = "!"

# Names longer than this are cut short; the shared prefix comes off first, so the cut
# only bites on names that stay long without it.
MAX_NAME_LEN = 20
# How much of the tail survives the cut. Short enough that the head still carries the
# generation, long enough to hold the tier the name ends on.
NAME_TAIL_LEN = 6

# What Discord leaves for a description, minus the fences we wrap the table in.
DEFAULT_CHAR_LIMIT = 4000

DEFAULT_LABELS = {
    "total": "Total",
    "idle": "idle",
    "hidden": "not shown",
    "models": "model",
}


@dataclass(frozen=True)
class _Column:
    """One reading of one window, and how much room it is allowed to ask for."""

    key: str  # what the header says
    field: str  # the key it is read from in the status snapshot
    tokens: bool  # counts tokens, so large numbers are written 1.2k / 3.4M
    optional: bool  # may be dropped when nothing fills it, or to buy room


# Two families of three windows. Inside a family the longer window only ever reads the
# same as the shorter one until the shorter one rolls, which is what `_visible_columns`
# leans on to leave a column out.
_COLUMNS = (
    _Column("rpm", "minute_req", tokens=False, optional=False),
    _Column("rpd", "day_req", tokens=False, optional=False),
    _Column("rpw", "week_req", tokens=False, optional=True),
    _Column("tpm", "minute_tokens", tokens=True, optional=True),
    _Column("tpd", "day_tokens", tokens=True, optional=True),
    _Column("tpw", "week_tokens", tokens=True, optional=True),
)
_FAMILIES = (("rpm", "rpd", "rpw"), ("tpm", "tpd", "tpw"))

# The order the optional columns are given up in when the table will not fit: the
# widest and least urgent first, the minute's token budget last.
_DROP_ORDER = ("tpw", "tpd", "rpw", "tpm")


@dataclass(frozen=True)
class _Cell:
    """What one model has spent in one window, against the cap it has there."""

    spent: int
    limit: int | None


@dataclass(frozen=True)
class _Row:
    name: str
    available: bool
    cells: dict[str, _Cell]

    @property
    def idle(self) -> bool:
        """Nothing spent anywhere and nothing standing in its way — nothing to report."""
        return self.available and all(cell.spent == 0 for cell in self.cells.values())


@dataclass(frozen=True)
class _Group:
    """The models behind one API key, titled with the roles that share it."""

    title: str
    rows: list[_Row]


def _to_int(value: str) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _parse_ratio(value) -> _Cell:
    """Read back a `spent/limit` snapshot field. A bare number is a spend with no cap."""
    spent, _, limit = str(value).partition("/")
    return _Cell(_to_int(spent), None if limit in ("", INFINITY) else _to_int(limit))


def _compact(number: int) -> str:
    """Token counts written to fit a column: 4100 → 4.1k, 1200000 → 1.2M."""
    for cutoff, suffix in ((1_000_000, "M"), (1_000, "k")):
        if abs(number) >= cutoff:
            scaled = f"{number / cutoff:.1f}".removesuffix(".0")
            return f"{scaled}{suffix}"
    return str(number)


def _shared_prefix(names: list[str]) -> str:
    """The `family-` every model is named after, if they all are. Empty when they are not.

    Cut at a separator rather than at the last matching character: `gemini-2.5-pro` and
    `gemini-2.0-flash` share `gemini-2.`, and only `gemini-` is a name anyone would read.
    """
    if len(names) < 2:
        return ""

    first, *rest = names
    prefix = first
    for name in rest:
        while prefix and not name.startswith(prefix):
            prefix = prefix[:-1]

    cut = max(prefix.rfind("-"), prefix.rfind("_"))
    prefix = prefix[: cut + 1]

    # A family worth naming, and never so much of the name that what is left of the
    # shortest of them stops being a model anyone could recognise.
    if len(prefix) < 4 or any(len(name) - len(prefix) < 3 for name in names):
        return ""
    return prefix


def _shorten(name: str, prefix: str) -> str:
    """Lift the family name off, then cut out of the middle rather than off the end.

    What tells two models apart sits at both ends — the generation early, the size or
    the tier last — so `gemini-2.5-flash-lite` and `gemini-2.0-flash-lite` cut off the
    tail read as the same model, and cut through the middle do not.
    """
    name = name[len(prefix) :] if prefix and name.startswith(prefix) else name
    if len(name) <= MAX_NAME_LEN:
        return name
    return f"{name[: MAX_NAME_LEN - 1 - NAME_TAIL_LEN]}…{name[len(name) - NAME_TAIL_LEN :]}"


def _build_groups(clients: list[dict]) -> tuple[list[_Group], str]:
    """Turn the pool's snapshot into rows, with the family name lifted off the models."""
    names = [str(status["model"]) for client in clients for status in client.get("status_list", [])]
    prefix = _shared_prefix(names)

    groups = []
    for client in clients:
        rows = [
            _Row(
                name=_shorten(str(status["model"]), prefix),
                available=bool(status.get("available", True)),
                cells={column.key: _parse_ratio(status.get(column.field, 0)) for column in _COLUMNS},
            )
            for status in client.get("status_list", [])
        ]
        groups.append(_Group(title=", ".join(client.get("roles", [])).title(), rows=rows))
    return groups, prefix


def _totals(groups: list[_Group]) -> dict[str, _Cell]:
    """What every key has spent together. Caps do not add up across keys, so none is kept."""
    return {
        column.key: _Cell(sum(row.cells[column.key].spent for group in groups for row in group.rows), None)
        for column in _COLUMNS
    }


def _differs_from(groups: list[_Group], key: str, other: str) -> bool:
    """Whether any model reads differently in these two windows."""
    return any(row.cells[key].spent != row.cells[other].spent for group in groups for row in group.rows)


def _visible_columns(groups: list[_Group], dropped: tuple[str, ...]) -> list[_Column]:
    """Every column that says something the one before it in its family does not.

    A week's request cap is usually unset and, until a day has rolled under it, its
    counter repeats the day's to the digit; the same holds for tokens by the day and by
    the week against the minute they were all spent in. Repeating a number under a second
    heading teaches nobody anything, so a column stays out until it has its own cap to
    report, or a reading that has come apart from the window below it.
    """
    kept: list[_Column] = []
    shown: dict[str, str | None] = {}
    for column in _COLUMNS:
        if column.key in dropped:
            continue

        family = next((f for f in _FAMILIES if column.key in f), ())
        previous = shown.get(family[0] if family else "")
        capped = any(row.cells[column.key].limit is not None for group in groups for row in group.rows)
        earns = capped or previous is None or _differs_from(groups, column.key, previous)

        if not column.optional or earns:
            kept.append(column)
            if family:
                shown[family[0]] = column.key
    return kept


def _cell_text(cell: _Cell, column: _Column, column_has_limits: bool) -> tuple[str, str]:
    """The two halves of a cell: what was spent, and the cap it was spent against."""
    spent = _compact(cell.spent) if column.tokens else str(cell.spent)
    if not column_has_limits:
        return spent, ""
    if cell.limit is None:
        return spent, INFINITY
    return spent, (_compact(cell.limit) if column.tokens else str(cell.limit))


class _Table:
    """The measured table: every column sized once, so the rows line up down the block."""

    def __init__(
        self,
        groups: list[_Group],
        totals: dict[str, _Cell],
        columns: list[_Column],
        labels: dict,
        prefix: str,
    ):
        self._columns = columns
        self._labels = labels
        self.title = f"{prefix}*" if prefix else labels["models"]
        self._has_limits = {
            column.key: any(row.cells[column.key].limit is not None for group in groups for row in group.rows)
            for column in columns
        }

        cells = [row.cells for group in groups for row in group.rows]
        self._spent_w = {}
        self._limit_w = {}
        for column in columns:
            halves = [_cell_text(cell[column.key], column, self._has_limits[column.key]) for cell in cells]
            halves.append(_cell_text(totals[column.key], column, False))
            self._limit_w[column.key] = max((len(limit) for _, limit in halves), default=0)

            # The spent side is never narrower than the heading above it, so every
            # heading ends exactly where the figures it names end.
            self._spent_w[column.key] = max(*(len(spent) for spent, _ in halves), len(column.key))

        names = [row.name for group in groups for row in group.rows]
        self.name_w = max([len(name) for name in names] + [len(labels["total"]), len(self.title)])

    def width(self) -> int:
        """How wide a rendered line runs, marker column included."""
        return 1 + self.name_w + sum(1 + self._column_w(column) for column in self._columns)

    def _column_w(self, column: _Column) -> int:
        limit_w = self._limit_w[column.key]
        return self._spent_w[column.key] + (limit_w + 1 if limit_w else 0)

    def render_cells(self, cells: dict[str, _Cell], mark: str = READY_MARK, name: str = "", bare: bool = False) -> str:
        """One line: the marker, the name, then every visible column.

        `bare` is the totals row, which has spends but no caps of its own — caps do not
        add up across API keys — and would otherwise report every one of them as ∞.
        """
        parts = [f"{mark}{name.ljust(self.name_w)}"]
        for column in self._columns:
            spent, _ = _cell_text(cells[column.key], column, False)
            limit = "" if bare else _cell_text(cells[column.key], column, self._has_limits[column.key])[1]
            text = spent.rjust(self._spent_w[column.key])
            if self._limit_w[column.key]:
                separator = "/" if limit else " "
                text = f"{text}{separator}{limit.ljust(self._limit_w[column.key])}"
            parts.append(text)
        return " ".join(parts).rstrip()

    def header(self) -> str:
        """The column names, each ending where its own spent figures end."""
        parts = [f" {self.title.ljust(self.name_w)}"]
        for column in self._columns:
            limit_w = self._limit_w[column.key]
            head = column.key.rjust(self._spent_w[column.key])
            parts.append(f"{head}{' ' * (limit_w + 1)}" if limit_w else head)
        return " ".join(parts).rstrip()


def _group_lines(group: _Group, table: _Table, labels: dict, collapse_idle: bool) -> list[str]:
    """A key's heading, its busy models, and one line standing in for the quiet ones."""
    shown = [row for row in group.rows if not (collapse_idle and row.idle)]
    idle = [row for row in group.rows if collapse_idle and row.idle]

    lines = [f"{group.title}:"]
    lines += [table.render_cells(row.cells, READY_MARK if row.available else BLOCKED_MARK, row.name) for row in shown]

    if idle:
        lines.append(_idle_line(idle, table.width(), labels))
    return lines


def _idle_line(idle: list[_Row], width: int, labels: dict) -> str:
    """`+4 idle: a, b, +2` — the count first, then whichever names the width still holds."""
    head = f"  +{len(idle)} {labels['idle']}"
    named: list[str] = []
    for row in idle:
        rest = len(idle) - len(named) - 1
        tail = f", +{rest}" if rest else ""
        if len(head) + 2 + len(", ".join([*named, row.name])) + len(tail) > width:
            break
        named.append(row.name)

    if not named:
        return head
    rest = len(idle) - len(named)
    return f"{head}: {', '.join(named)}" + (f", +{rest}" if rest else "")


def _render(
    groups: list[_Group],
    totals: dict[str, _Cell],
    prefix: str,
    labels: dict,
    collapse_idle: bool,
    dropped: tuple[str, ...],
    hidden: int,
) -> str:
    table = _Table(groups, totals, _visible_columns(groups, dropped), labels, prefix)

    lines = [table.header(), table.render_cells(totals, READY_MARK, labels["total"], bare=True)]
    for group in groups:
        lines.append("")
        lines += _group_lines(group, table, labels, collapse_idle)
    if hidden:
        lines.append(f"  +{hidden} {labels['hidden']}")

    body = "\n".join(lines)
    return f"```\n{body}\n```"


def _trim_longest_group(groups: list[_Group]) -> list[_Group] | None:
    """Drop the last model of whichever key lists the most of them, or give up."""
    widest = max(range(len(groups)), key=lambda i: len(groups[i].rows), default=None)
    if widest is None or len(groups[widest].rows) <= 1:
        return None
    trimmed = list(groups)
    trimmed[widest] = _Group(groups[widest].title, groups[widest].rows[:-1])
    return trimmed


def render_limits(
    clients: list[dict],
    *,
    labels: dict | None = None,
    collapse_idle: bool = True,
    char_limit: int = DEFAULT_CHAR_LIMIT,
) -> str:
    """Render the pool's snapshot as one fenced table that fits inside `char_limit`.

    Room is bought in the order that costs a reader least: first the models with nothing
    to report are folded away, then the columns nobody is close to filling are dropped
    from the right, and only then are models themselves taken off the bottom — counted,
    never silently. An embed that says a little is worth more than one that says it did
    not fit, which is what this used to do.
    """
    labels = {**DEFAULT_LABELS, **(labels if isinstance(labels, dict) else {})}
    if not clients:
        return ""

    groups, prefix = _build_groups(clients)
    totals = _totals(groups)

    attempts = [(collapse_idle, ())]
    if not collapse_idle:
        attempts.append((True, ()))
    attempts += [(True, tuple(_DROP_ORDER[: i + 1])) for i in range(len(_DROP_ORDER))]

    for collapse, dropped in attempts:
        text = _render(groups, totals, prefix, labels, collapse, dropped, hidden=0)
        if len(text) <= char_limit:
            return text

    hidden = 0
    dropped = tuple(_DROP_ORDER)
    while True:
        trimmed = _trim_longest_group(groups)
        if trimmed is None:
            return text[:char_limit]
        groups, hidden = trimmed, hidden + 1
        text = _render(groups, totals, prefix, labels, True, dropped, hidden)
        if len(text) <= char_limit:
            return text
