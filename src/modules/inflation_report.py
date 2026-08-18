"""User-facing composition of the inflation report.

`inflation_formatter` turns numbers into text; this module wraps that text into
the message the bot actually shows, taking every string from `phrases.json`.
Both the slash commands and the eternal report message go through here so the
two never drift apart.
"""

from core.utils import get_phrases
from modules.inflation_formatter import build_record_pages, format_date, format_money, format_percent
from modules.inflation_provider import FALLBACK_ANNUAL_PERCENT, inflation_provider

# Above this many missing months the warning switches to a "N months since X"
# form: listing them all would eat the message.
MAX_LISTED_GAPS = 7


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

    if not inflation_provider.has_rates():
        return phrases.get(
            "rates_empty",
            ":warning: No CPI data at all — a fallback of {fallback}% per year is used instead.",
        ).format(fallback=FALLBACK_ANNUAL_PERCENT)

    gaps = inflation_provider.get_rate_gaps()
    if not gaps:
        return ""

    if len(gaps) > MAX_LISTED_GAPS:
        return phrases.get(
            "rates_gaps_many",
            ":warning: No CPI data for {count} months (starting from {first}) — "
            "a fallback of {fallback}% per year is used for them.",
        ).format(count=len(gaps), first=gaps[0], fallback=FALLBACK_ANNUAL_PERCENT)

    return phrases.get(
        "rates_gaps",
        ":warning: No CPI data for: {months} — a fallback of {fallback}% per year is used for them.",
    ).format(months=", ".join(gaps), count=len(gaps), fallback=FALLBACK_ANNUAL_PERCENT)


def build_summary(report: dict, guild_id: int | None = None) -> str:
    """The header block: totals, the oldest record and any CPI warning."""
    phrases = get_phrases_section(guild_id)
    currency = get_currency(guild_id)

    oldest_date = report.get("oldest_date")
    summary = phrases.get(
        "summary",
        "**Inflation report**\n"
        "Nominal total: `{total_nominal}`\n"
        "Inflation-adjusted: `{total_adjusted}`\n"
        "Purchasing power change: `{loss_percent}`\n"
        "Oldest record: `{oldest_date}`\n"
        "Records: `{records_count}`",
    ).format(
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

    return phrases.get(
        "page_format",
        "{summary}\n\n**Records (page {current_page}/{max_pages}):**\n```text\n{page_content}\n```",
    ).format(
        summary=summary,
        current_page=page_index + 1,
        max_pages=len(pages),
        page_content=pages[page_index],
    )
