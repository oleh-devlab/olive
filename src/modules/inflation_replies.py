"""Replies to a slash command: one message, sent once, never paged.

The report has a channel of its own and a pager to spill onto. These do not:
each is an ephemeral answer to a command, and Discord refuses an over-long
message rather than truncating it, so everything here is built through
`fit_into_message` and trimmed to fit rather than risked.

They read the same report the channel does and describe it with the same
fragments from `inflation_phrases`, so a group's deposit reads the same way
whether the reader met it here or in their report channel.
"""

from collections.abc import Callable

from core.utils import format_phrase
from modules.inflation_formatter import (
    MESSAGE_LIMIT,
    MIN_RECORD_BLOCK,
    fold_consumed_lots,
    format_date,
    format_group_summary_line,
    format_money,
    pack_single_page,
    report_nodes,
    trim_to_whole_lines,
)
from modules.inflation_phrases import (
    build_deposit_marker,
    get_currency,
    get_phrases_section,
    node_name,
)
from modules.inflation_provider import USER_SCOPE, inflation_provider


def fit_into_message(
    blocks: list[str],
    wrap: Callable[[str], str],
    note: Callable[[int], str],
    *,
    reserved: str = "",
    fallback: Callable[[], str] | None = None,
) -> str:
    """
    Fit a list of blocks into one Discord message, trimming rather than failing.

    Discord refuses an over-long message instead of truncating it, so the reader
    would get nothing at all. Every part of the frame — the wrapper and the note
    alike — comes from `phrases.json` and can be rewritten to any length, so what
    they cost is measured rather than guessed at.

    Args:
        blocks: The listing, one block per entry.
        wrap: Renders the finished page into its frame; called with "" to price
              that frame.
        note: Renders "only N of M fit" for the number of blocks shown, and ""
              when nothing was cut.
        reserved: Text appended after the note and reserved before the listing
              competes for room — for anything that must survive being trimmed.
        fallback: What to say when the frame alone is over budget. Defaults to
              the note by itself: cutting into the listing would leave its code
              fence unclosed, so it goes entirely.
    """
    overhead = len(wrap("")) + len(note(0)) + len(reserved)
    page, shown = pack_single_page(blocks, max(MIN_RECORD_BLOCK, MESSAGE_LIMIT - overhead))

    content = wrap(page) + note(shown) + reserved
    if len(content) <= MESSAGE_LIMIT:
        return content

    return trim_to_whole_lines(fallback() if fallback else note(0).lstrip("\n"), MESSAGE_LIMIT)


def build_group_list(owner_id: int, guild_id: int | None = None, scope: str = USER_SCOPE) -> str:
    """
    The owner's groups, a line each plus any deposit, trimmed to fit one message.

    Server budgets allow enough groups (and long enough names) to run past the
    2000-character limit on their own, and an over-long reply is not truncated
    by Discord — it is refused, so the reader gets nothing at all.
    """
    phrases = get_phrases_section(guild_id)
    report = inflation_provider.get_groups_report(owner_id, scope, detailed=False)

    # The question is "which groups do I have", so no groups is a complete
    # answer: the ungrouped bucket is not one, and its records are what
    # `/inflation report` is for.
    if not report["groups"]:
        return phrases.get("group_list_empty", "No groups yet. Create one with `/inflation_groups create`.")

    blocks = [
        format_group_summary_line(node, get_currency(guild_id), node_name(node, guild_id))
        + build_deposit_marker(node, guild_id)
        for node in report_nodes(report)
    ]

    def wrap(page: str) -> str:
        return format_phrase(phrases, "group_list", "```text\n{groups}\n```", groups=page)

    def truncation_note(shown: int) -> str:
        if shown >= len(blocks):
            return ""

        return "\n" + format_phrase(
            phrases,
            "group_list_truncated",
            "*Only the first {shown} of {total} lines fit into this message.*",
            shown=shown,
            total=len(blocks),
        )

    return fit_into_message(blocks, wrap, truncation_note)


def build_consumed_line(entry: dict, guild_id: int | None = None) -> str:
    """One line of what a withdrawal ate, folded or not."""
    phrases = get_phrases_section(guild_id)
    currency = get_currency(guild_id)

    if entry.get("count", 1) > 1:
        return format_phrase(
            phrases,
            "withdraw_consumed_folded",
            "{first_date}…{last_date}: took {taken} from {count} interest record(s)",
            first_date=format_date(entry["first_date"]),
            last_date=format_date(entry["last_date"]),
            taken=format_money(entry["taken"], currency),
            count=entry["count"],
        )

    return format_phrase(
        phrases,
        "withdraw_consumed_line",
        "ID {record_id} ({date}): took {taken}, {remaining} left",
        record_id=entry.get("id"),
        date=format_date(entry["date"]),
        taken=format_money(entry["taken"], currency),
        remaining=format_money(entry["remaining"], currency),
    )


def build_withdrawal_message(result: dict, guild_id: int | None = None) -> str:
    """
    What a withdrawal ate, trimmed to fit into a single message.

    A withdrawal can consume every record an owner has — two hundred of them by
    default — and Discord refuses an over-long message rather than truncating
    it. Refused here would be the worst case in this module: the money has
    already left and been saved, so the reader would be left with no
    confirmation of a change that did happen.
    """
    phrases = get_phrases_section(guild_id)
    currency = get_currency(guild_id)

    blocks = [build_consumed_line(entry, guild_id) for entry in fold_consumed_lots(result["consumed"])]

    def wrap(page: str) -> str:
        return format_phrase(
            phrases,
            "withdrawn",
            "Withdrew {amount}, oldest money first:\n```text\n{consumed}\n```",
            amount=format_money(result["amount"], currency),
            consumed=page,
        )

    def truncation_note(shown: int) -> str:
        if shown >= len(blocks):
            return ""

        return "\n" + format_phrase(
            phrases,
            "withdraw_truncated",
            "*Only the first {shown} of {total} lines fit into this message.*",
            shown=shown,
            total=len(blocks),
        )

    # The warning names real money at risk, so it is reserved before the listing
    # rather than left to compete with it for room.
    warning = ""
    if result["warning"]:
        warning = "\n" + format_phrase(phrases, "withdraw_warning", "⚠️ {warning}", warning=result["warning"])

    # What must survive a rewritten phrase is that the money left, and what
    # breaking the deposit cost — not the listing of which records paid for it.
    def bare_confirmation() -> str:
        return f"{format_money(result['amount'], currency)}\n{result['warning'] or ''}".strip()

    return fit_into_message(blocks, wrap, truncation_note, reserved=warning, fallback=bare_confirmation)
