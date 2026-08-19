"""User-facing composition of the inflation report.

`inflation_formatter` turns numbers into text; this module wraps that text into
the message the bot actually shows, taking every string from `phrases.json`.
Both the slash commands and the eternal report message go through here so the
two never drift apart.
"""

from core.utils import format_phrase, get_phrases
from modules.inflation_formatter import (
    build_record_pages,
    build_single_record_page,
    find_rate_gaps,
    format_date,
    format_money,
    format_percent,
    trim_to_whole_lines,
)
from modules.inflation_provider import FALLBACK_ANNUAL_PERCENT, SERVER_SCOPE, inflation_provider

# Above this many missing months the warning switches to a "N months since X"
# form: listing them all would eat the message.
MAX_LISTED_GAPS = 7

# Discord's message limit, and what the server report keeps back from it for the
# code fence, the page header and a possible truncation note.
MESSAGE_LIMIT = 2000
FENCE_RESERVE = 200

# However little room the summary leaves, showing one record beats showing none.
MIN_RECORD_BLOCK = 200


def get_phrases_section(guild_id: int | None = None) -> dict:
    return get_phrases(guild_id).get("inflation", {})


def get_currency(guild_id: int | None = None) -> str:
    return get_phrases_section(guild_id).get("currency", "UAH")


def build_rates_warning(guild_id: int | None = None) -> str:
    """
    Describe missing CPI data, or return an empty string when it is complete.

    Without this the bot silently substitutes the fallback annual rate for every
    month it has no data for, and the report looks authoritative anyway.
    """
    phrases = get_phrases_section(guild_id)
    rates = inflation_provider.get_rates()

    if not rates:
        return format_phrase(
            phrases,
            "rates_empty",
            ":warning: No CPI data at all — a fallback of {fallback}% per year is used instead.",
            fallback=FALLBACK_ANNUAL_PERCENT,
        )

    gaps = find_rate_gaps(rates)
    if not gaps:
        return ""

    if len(gaps) > MAX_LISTED_GAPS:
        return format_phrase(
            phrases,
            "rates_gaps_many",
            ":warning: No CPI data for {count} months (starting from {first}) — "
            "a fallback of {fallback}% per year is used for them.",
            count=len(gaps),
            first=gaps[0],
            fallback=FALLBACK_ANNUAL_PERCENT,
        )

    return format_phrase(
        phrases,
        "rates_gaps",
        ":warning: No CPI data for: {months} — a fallback of {fallback}% per year is used for them.",
        months=", ".join(gaps),
        count=len(gaps),
        fallback=FALLBACK_ANNUAL_PERCENT,
    )


def build_summary(report: dict, guild_id: int | None = None) -> str:
    """The header block: totals, the oldest record and any CPI warning."""
    phrases = get_phrases_section(guild_id)
    currency = get_currency(guild_id)

    oldest_date = report.get("oldest_date")
    summary = format_phrase(
        phrases,
        "summary",
        "**Inflation report**\n"
        "Nominal total: `{total_nominal}`\n"
        "Inflation-adjusted: `{total_adjusted}`\n"
        "Purchasing power change: `{loss_percent}`\n"
        "Oldest record: `{oldest_date}`\n"
        "Records: `{records_count}`",
        total_nominal=format_money(report.get("total_nominal", 0), currency),
        total_adjusted=format_money(report.get("total_adjusted", 0), currency),
        loss_percent=format_percent(report.get("loss_percent", 0)),
        oldest_date=format_date(oldest_date) if oldest_date else "-",
        records_count=len(report.get("records", [])),
    )

    warning = build_rates_warning(guild_id)

    return f"{summary}\n\n{warning}" if warning else summary


def build_report(user_id: int, guild_id: int | None = None) -> tuple[str, list[str]]:
    """Return the summary block and the paginated record blocks for one user."""
    report = inflation_provider.get_report(user_id)
    pages = build_record_pages(report.get("records", []), get_currency(guild_id))

    return build_summary(report, guild_id), pages


def render_page(summary: str, pages: list[str], page_index: int = 0, guild_id: int | None = None) -> str:
    """Compose the final message content for one page of the report."""
    phrases = get_phrases_section(guild_id)

    if not pages:
        empty = phrases.get("no_records", "No records yet. Add one with `/inflation add`.")
        return f"{summary}\n\n{empty}"

    page_index = min(max(page_index, 0), len(pages) - 1)

    return format_phrase(
        phrases,
        "page_format",
        "{summary}\n\n**Records (page {current_page}/{max_pages}):**\n```text\n{page_content}\n```",
        summary=summary,
        current_page=page_index + 1,
        max_pages=len(pages),
        page_content=pages[page_index],
    )


def build_server_report(guild_id: int, reserve: int = 0) -> str:
    """
    The guild's shared budget as one message, trimmed to fit.

    Same report as a user's — same summary, same CPI warning, same record
    rendering — only there is no pager to spill onto, so the record list gets
    whatever is left of the message and says what it had to leave out.
    `reserve` is what the caller adds around this text, e.g. the page header.
    """
    phrases = get_phrases_section(guild_id)

    report = inflation_provider.get_report(guild_id, scope=SERVER_SCOPE)
    summary = build_summary(report, guild_id)
    records = report.get("records", [])

    def truncation_note(shown: int) -> str:
        if shown >= len(records):
            return ""

        return "\n" + format_phrase(
            phrases,
            "server_truncated",
            "*Only the first {shown} of {total} records fit into this message.*",
            shown=shown,
            total=len(records),
        )

    page_limit = max(MIN_RECORD_BLOCK, MESSAGE_LIMIT - len(summary) - reserve - FENCE_RESERVE)
    page, shown = build_single_record_page(records, get_currency(guild_id), page_limit)

    content = render_page(summary, [page] if page else [], 0, guild_id) + truncation_note(shown)
    if len(content) <= MESSAGE_LIMIT:
        return content

    # The summary alone is over budget — an enormous CPI warning, or a rewritten
    # `summary` phrase. Cutting into the record block would leave its code fence
    # unclosed, so the block goes entirely and the note says the records are gone.
    note = truncation_note(0)

    return trim_to_whole_lines(summary, MESSAGE_LIMIT - len(note)) + note
