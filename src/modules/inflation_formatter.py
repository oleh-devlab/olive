"""Pure formatting helpers for the inflation report.

Nothing here touches disnake, settings or the filesystem: every function works
on the plain dicts returned by `InflationCalculator.get_report()`, so the whole
module is unit-testable without the bot. Discord-facing text (phrases,
localisation) is assembled one layer up, in `cogs/inflation/`.
"""

import datetime
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")

# Records are rendered into a code block inside a message that also carries the
# timestamp header, the summary and possibly a CPI warning, so a page has to stay
# well below the 2000-character Discord limit to leave room for all of that.
DEFAULT_PAGE_LIMIT = 1200

# A single record must never be able to blow the message limit on its own.
MAX_COMMENT_LENGTH = 200


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


def format_date(value: datetime.date) -> str:
    return value.strftime("%d.%m.%Y")


def format_record(record: dict, currency: str = "") -> str:
    """Render a single report record as a two-line block."""
    record_id = record.get("id", "?")
    comment = record.get("comment") or ""
    if len(comment) > MAX_COMMENT_LENGTH:
        comment = comment[: MAX_COMMENT_LENGTH - 1] + "…"
    comment_part = f" | {comment}" if comment else ""

    head = f"ID {record_id}. {format_money(record['amount'], currency)} | {format_date(record['date'])}{comment_part}"
    tail = f"   -> {format_money(record['adjusted_value'], currency)} ({format_percent(record['loss_percent'])})"

    return f"{head}\n{tail}"


def build_record_pages(
    records: list[dict],
    currency: str = "",
    page_limit: int = DEFAULT_PAGE_LIMIT,
) -> list[str]:
    """
    Render records into pages that fit into a single Discord message.

    Records are kept whole: a record longer than `page_limit` gets a page of its
    own rather than being cut in half. Returns an empty list for no records —
    the caller decides what to show instead.
    """
    pages: list[str] = []
    current: list[str] = []
    current_len = 0

    for record in records:
        block = format_record(record, currency)
        # +1 for the newline that will join this block to the previous one.
        extra = len(block) + (1 if current else 0)

        if current and current_len + extra > page_limit:
            pages.append("\n".join(current))
            current = [block]
            current_len = len(block)
        else:
            current.append(block)
            current_len += extra

    if current:
        pages.append("\n".join(current))

    return pages


def _next_month(day: datetime.date) -> datetime.date:
    """First day of the month following `day`."""
    return datetime.date(day.year + day.month // 12, day.month % 12 + 1, 1)


def find_rate_gaps(rates: dict[str, Decimal], today: datetime.date | None = None) -> list[str]:
    """
    Return the `YYYY-MM` keys missing between the oldest known rate and the last
    completed month.

    The current month is never reported: its CPI is not published yet. An empty
    rates dict has no gaps by this definition — it is a separate condition the
    caller reports on its own.

    This deliberately does not reuse `InflationCalculator.check_data_gaps()`:
    that one returns a ready-made English CLI string which also claims missing
    months are treated as 0% inflation, while `logic.fill_missing_inflation_data`
    actually substitutes the fallback annual rate.
    """
    if not rates:
        return []

    today = today or datetime.date.today()
    last_complete_month = today.replace(day=1) - datetime.timedelta(days=1)

    cursor = datetime.date.fromisoformat(f"{min(rates)}-01")
    missing = []

    while cursor <= last_complete_month:
        key = cursor.strftime("%Y-%m")
        if key not in rates:
            missing.append(key)
        cursor = _next_month(cursor)

    return missing
