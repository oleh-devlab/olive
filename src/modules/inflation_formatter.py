"""Pure formatting helpers for the inflation report.

Nothing here touches disnake, settings or the filesystem: every function works
on the plain dicts returned by `InflationCalculator.get_groups_report()`, so the
whole module is unit-testable without the bot. Discord-facing text (phrases,
localisation) is assembled one layer up, in `cogs/inflation/`.

Two packers live here. `pack_blocks` fills pages with independent blocks — one
record, one group sub-total. `pack_sections` fills them with a `Section`: a
header followed by the blocks that belong under it. A section that does not fit
on one page repeats its header on the next, so a group heading is never left
stranded at the bottom of a page without any of its records.
"""

import datetime
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")

# Discord's own limit on a message, and the floor a listing is never squeezed
# below: however little room a summary leaves, showing one entry beats showing
# none. Both live here rather than with either caller, because the report and
# the slash-command replies have to agree on them.
MESSAGE_LIMIT = 2000
MIN_RECORD_BLOCK = 200

# Records are rendered into a code block inside a message that also carries the
# timestamp header, the summary and possibly a CPI warning, so a page has to stay
# well below the 2000-character Discord limit to leave room for all of that.
DEFAULT_PAGE_LIMIT = 1200

# A single record must never be able to blow the message limit on its own.
MAX_COMMENT_LENGTH = 200

# How far a group's records are pushed in under its heading.
RECORD_INDENT = "    "

# Rule drawn above the grand total in the group-totals view.
TOTAL_RULE = "-" * 40

# The library's own tag for a lot credited by closing a deposit. Mirrored rather
# than imported so this module stays free of the vendored package and testable
# without it; the value is part of the library's stored format, so it is stable.
LOT_SOURCE_DEPOSIT_INTEREST = "deposit_interest"


def format_money(value: Decimal | int | float | str, currency: str = "") -> str:
    """Format an amount with two decimals and space-separated thousands."""
    # Half-up: the calculator's own values arrive pre-quantized, so this only
    # decides ad-hoc inputs, and half-even reads as a bug on a money figure.
    amount = Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)
    text = f"{amount:,.2f}".replace(",", " ")

    return f"{text} {currency}" if currency else text


def format_percent(value: Decimal | int | float | str) -> str:
    """Format a percentage with two decimals and an explicit sign."""
    percent = Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)

    return f"{percent:+.2f}%"


def format_rate(value: Decimal | int | float | str) -> str:
    """Format an interest rate. Unsigned, unlike `format_percent`.

    A deposit rate is never a gain or a loss relative to anything, so the
    explicit `+` that makes an inflation delta readable reads as a bug here.
    """
    return f"{Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP):.2f}%"


def format_date(value: datetime.date) -> str:
    return value.strftime("%d.%m.%Y")


def format_record(record: dict, currency: str = "") -> str:
    """Render a single report record as a two-line block.

    A row standing for several folded lots is told apart by its `count`, never
    by its comment and never by `id` being None — the id key is present and
    null on a folded row, so `.get("id", "?")` would print `ID None`.
    """
    comment = record.get("comment") or ""
    if len(comment) > MAX_COMMENT_LENGTH:
        comment = comment[: MAX_COMMENT_LENGTH - 1] + "…"
    comment_part = f" | {comment}" if comment else ""

    if record.get("count", 1) > 1:
        when = f"{format_date(record['first_date'])}…{format_date(record['last_date'])}"
        head = f"x{record['count']}. {format_money(record['amount'], currency)} | {when}{comment_part}"
    else:
        head = (
            f"ID {record.get('id', '?')}. {format_money(record['amount'], currency)}"
            f" | {format_date(record['date'])}{comment_part}"
        )

    tail = f"   -> {format_money(record['adjusted_value'], currency)} ({format_percent(record['loss_percent'])})"

    return f"{head}\n{tail}"


# ----------------------------------------------------------------------
# Budget groups
# ----------------------------------------------------------------------


def format_node_label(node: dict, name: str | None = None) -> str:
    """`[Salary] (3)` — a group node's name and how many records it holds.

    `name` overrides the stored one, which is how the caller localises the
    library's English `"(ungrouped)"` bucket without the formatter knowing any
    user-facing prose.
    """
    return f"[{name if name is not None else node['name']}] ({node['records_count']})"


def format_node_totals(node_or_report: dict, currency: str = "") -> str:
    """`45 000.00 -> 52 130.00 UAH (+15.84%)` — one line of sub-totals.

    Takes any dict carrying the three total keys, so it renders a group node and
    the whole report alike.
    """
    nominal = format_money(node_or_report["total_nominal"])
    adjusted = format_money(node_or_report["total_adjusted"], currency)

    return f"{nominal} -> {adjusted} ({format_percent(node_or_report['loss_percent'])})"


def format_group_heading(node: dict, currency: str = "", name: str | None = None) -> str:
    """The two-line heading a group's records are listed under."""
    return f"{format_node_label(node, name)}\n  {format_node_totals(node, currency)}"


def format_group_summary_line(node: dict, currency: str = "", name: str | None = None) -> str:
    """The same information as `format_group_heading`, folded onto one line."""
    return f"{format_node_label(node, name)} {format_node_totals(node, currency)}"


def format_grand_total(report: dict, label: str, currency: str = "") -> str:
    """The report's own totals, under a rule, in the shape of a group line.

    `label` is user-facing prose and therefore comes from the caller.
    """
    nodes = [*report["groups"], report["ungrouped"]]
    total = {"name": label, "records_count": sum(node["records_count"] for node in nodes)}

    return f"{TOTAL_RULE}\n{format_node_label(total)} {format_node_totals(report, currency)}"


def report_nodes(report: dict) -> list[dict]:
    """
    Every group, plus the ungrouped bucket when it actually holds something.

    The report is indexed rather than `.get()`-ed: its shape is
    `InflationCalculator.get_groups_report()`'s contract, pinned by that
    library's own tests. A malformed report should raise and surface as
    "could not build the report", not quietly render zero money.
    """
    nodes = list(report["groups"])
    ungrouped = report["ungrouped"]

    if ungrouped["records_count"]:
        nodes.append(ungrouped)

    return nodes


# ----------------------------------------------------------------------
# Deposits
# ----------------------------------------------------------------------


def deposit_fields(deposit: dict, currency: str = "") -> dict[str, str]:
    """Every number of a group's deposit, pre-rendered for phrase interpolation.

    The caller owns the prose: this returns named strings and never a sentence,
    the same split `format_grand_total` makes by taking its label as an argument.
    """
    return {
        "rate": format_rate(deposit["annual_rate_percent"]),
        "capitalization": deposit["capitalization"],
        "start_date": format_date(deposit["start_date"]),
        "end_date": format_date(deposit["end_date"]),
        "comment": deposit.get("comment") or "",
        "earned": format_money(deposit["net_interest_so_far"], currency),
        "balance": format_money(deposit["balance_so_far"], currency),
        "projected": format_money(deposit["projected_net_interest"], currency),
        "projected_total": format_money(deposit["projected_final_amount"], currency),
        "effective_rate": format_rate(deposit["effective_annual_rate_percent"]),
        "at_risk": format_money(deposit["at_risk_if_broken_now"], currency),
    }


def fold_consumed_lots(consumed: list[dict]) -> list[dict]:
    """Fold the deposit-interest lots a withdrawal ate into one entry.

    Spending a closed year of monthly capitalization consumes twelve interest
    lots, which is twelve lines of a reply nobody reads. The folded entry carries
    `count`, `first_date` and `last_date`; a manual lot is passed through
    untouched, and one interest lot on its own is not worth folding.
    """
    interest = [entry for entry in consumed if entry.get("source") == LOT_SOURCE_DEPOSIT_INTEREST]
    if len(interest) < 2:
        return list(consumed)

    rest = [entry for entry in consumed if entry.get("source") != LOT_SOURCE_DEPOSIT_INTEREST]
    dates = [entry["date"] for entry in interest]

    folded = {
        "id": None,
        "date": min(dates),
        "first_date": min(dates),
        "last_date": max(dates),
        "source": LOT_SOURCE_DEPOSIT_INTEREST,
        "count": len(interest),
        "taken": sum((entry["taken"] for entry in interest), Decimal("0")),
        # Only the last lot a withdrawal touches is ever left partly spent, so
        # what remains across the folded ones is what remains in that one.
        "remaining": sum((entry["remaining"] for entry in interest), Decimal("0")),
    }

    return [*rest, folded]


def indent_blocks(blocks: list[str], indent: str = RECORD_INDENT) -> list[str]:
    """Push every line of every block in, so records sit under their heading."""
    return ["\n".join(indent + line for line in block.split("\n")) for block in blocks]


# ----------------------------------------------------------------------
# Page packing
# ----------------------------------------------------------------------


@dataclass(slots=True)
class Section:
    """A header and the blocks filed under it, packed as one unit."""

    header: str
    blocks: list[str] = field(default_factory=list)
    # Used instead of `header` when the section spills onto another page.
    # Empty repeats `header` unchanged.
    continued_header: str = ""


def _append(lines: list[str], length: int, text: str) -> int:
    """Add `text` as a line and return the new joined length."""
    # +1 for the newline that joins this line to the previous one.
    length += len(text) + (1 if lines else 0)
    lines.append(text)

    return length


def _fits(lines: list[str], length: int, texts: list[str], page_limit: int) -> bool:
    """Whether `texts` still fit, given what the page already holds.

    An empty page always fits: a block longer than `page_limit` gets a page of
    its own rather than being cut in half, or dropped for good.
    """
    if not lines:
        return True

    extra = sum(len(text) + 1 for text in texts)

    return length + extra <= page_limit


def pack_blocks(blocks: list[str], page_limit: int = DEFAULT_PAGE_LIMIT) -> list[str]:
    """
    Pack blocks into pages that fit into a single Discord message.

    Blocks are kept whole: one longer than `page_limit` gets a page of its own.
    Returns an empty list for no blocks — the caller decides what to show instead.
    """
    pages: list[str] = []
    current: list[str] = []
    length = 0

    for block in blocks:
        if not _fits(current, length, [block], page_limit):
            pages.append("\n".join(current))
            current, length = [], 0

        length = _append(current, length, block)

    if current:
        pages.append("\n".join(current))

    return pages


def pack_single_page(blocks: list[str], page_limit: int = DEFAULT_PAGE_LIMIT) -> tuple[str, int]:
    """
    Pack as many whole blocks as fit into one page, and say how many that was.

    `pack_blocks` spills what does not fit onto the next page; here there is no
    next page, so the list is cut instead and the caller can tell the reader how
    much is missing.
    """
    lines: list[str] = []
    length = 0
    shown = 0

    for block in blocks:
        if not _fits(lines, length, [block], page_limit):
            break

        length = _append(lines, length, block)
        shown += 1

    return "\n".join(lines), shown


def pack_sections(sections: list[Section], page_limit: int = DEFAULT_PAGE_LIMIT) -> list[str]:
    """
    Pack sections into pages, repeating a header when its section spills.

    A header is only ever written together with at least one of its blocks, so
    no page ends with a group heading and nothing under it. A section with no
    blocks at all is still announced — an empty group is worth seeing.
    """
    pages: list[str] = []
    current: list[str] = []
    length = 0

    def flush() -> None:
        nonlocal current, length
        if current:
            pages.append("\n".join(current))
            current, length = [], 0

    for section in sections:
        if not section.blocks:
            if not _fits(current, length, [section.header], page_limit):
                flush()
            length = _append(current, length, section.header)
            continue

        header: str | None = section.header
        for block in section.blocks:
            pending = [header, block] if header else [block]

            if not _fits(current, length, pending, page_limit):
                flush()
                header = section.continued_header or section.header

            if header:
                length = _append(current, length, header)
                header = None

            length = _append(current, length, block)

    flush()

    return pages


def pack_sections_single_page(
    sections: list[Section],
    page_limit: int = DEFAULT_PAGE_LIMIT,
) -> tuple[str, int]:
    """
    `pack_sections` for a message with no second page: what does not fit is cut.

    Returns the page and how many *blocks* made it in — headers are not counted,
    so the number means the same thing as `pack_single_page`'s does.
    """
    lines: list[str] = []
    length = 0
    shown = 0

    for section in sections:
        if not section.blocks:
            if not _fits(lines, length, [section.header], page_limit):
                return "\n".join(lines), shown
            length = _append(lines, length, section.header)
            continue

        header: str | None = section.header
        for block in section.blocks:
            pending = [header, block] if header else [block]

            if not _fits(lines, length, pending, page_limit):
                return "\n".join(lines), shown

            if header:
                length = _append(lines, length, header)
                header = None

            length = _append(lines, length, block)
            shown += 1

    return "\n".join(lines), shown


def build_record_pages(
    records: list[dict],
    currency: str = "",
    page_limit: int = DEFAULT_PAGE_LIMIT,
) -> list[str]:
    """Render records into pages that fit into a single Discord message."""
    return pack_blocks([format_record(record, currency) for record in records], page_limit)


def build_single_record_page(
    records: list[dict],
    currency: str = "",
    page_limit: int = DEFAULT_PAGE_LIMIT,
) -> tuple[str, int]:
    """Render as many whole records as fit into one page, and how many that was."""
    return pack_single_page([format_record(record, currency) for record in records], page_limit)


def trim_to_whole_lines(text: str, limit: int) -> str:
    """
    The longest run of whole lines of `text` that fits into `limit`.

    Cutting mid-line is the last resort, for a single line that is over the
    limit on its own; everywhere else a line boundary keeps the markdown intact.
    """
    if len(text) <= limit:
        return text

    head = text[:limit]
    last_break = head.rfind("\n")

    return head[:last_break] if last_break > 0 else head
