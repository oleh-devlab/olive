"""User-facing composition of the inflation report.

`inflation_formatter` turns numbers into text; this module wraps that text into
the message the bot actually shows, taking every string from `phrases.json`.
Both the slash commands and the eternal report message go through here so the
two never drift apart.

The report renders in one of two modes (`inflation_provider.VIEW_MODES`): the
full tree, where every record is listed under the group it belongs to, or the
group totals alone. Which one an owner sees is their stored preference, changed
with `/inflation view`; the numbers behind both are the same report, so the two
can never disagree.

A group under a deposit carries it in its heading in both modes, and a matured
one — which the library never closes by itself — is named in the summary, the
one part of the message every mode shows.
"""

from typing import NamedTuple

from core.utils import format_phrase, get_phrases
from modules.inflation_formatter import (
    Section,
    build_record_pages,
    build_single_record_page,
    deposit_fields,
    fold_consumed_lots,
    format_date,
    format_grand_total,
    format_group_heading,
    format_group_summary_line,
    format_money,
    format_percent,
    format_record,
    indent_blocks,
    pack_blocks,
    pack_sections,
    pack_sections_single_page,
    pack_single_page,
    trim_to_whole_lines,
)
from modules.inflation_provider import (
    FALLBACK_ANNUAL_PERCENT,
    SERVER_SCOPE,
    USER_SCOPE,
    VIEW_SUMMARY,
    VIEW_TREE,
    inflation_provider,
)

# Above this many missing months the warning switches to a "N months since X"
# form: listing them all would eat the message.
MAX_LISTED_GAPS = 7

# The same cap for matured deposits. A server budget allows fifty groups, and
# naming every one of them would blow the message limit on the warning alone.
MAX_LISTED_DEPOSITS = 7

# Discord's message limit, and what the server report keeps back from it for the
# code fence, the page header and a possible truncation note.
MESSAGE_LIMIT = 2000
FENCE_RESERVE = 200

# However little room the summary leaves, showing one record beats showing none.
MIN_RECORD_BLOCK = 200


class RenderedReport(NamedTuple):
    """What `build_report` hands to the caller, including the mode it resolved."""

    summary: str
    pages: list[str]
    mode: str


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
    has_rates, gaps = inflation_provider.get_rate_status()

    if not has_rates:
        return format_phrase(
            phrases,
            "rates_empty",
            ":warning: No CPI data at all — a fallback of {fallback}% per year is used instead.",
            fallback=FALLBACK_ANNUAL_PERCENT,
        )

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


def build_deposits_warning(report: dict, guild_id: int | None = None) -> str:
    """Name the groups whose deposit has matured but is still attached.

    A deposit never closes itself, and its interest stays outside the group's
    balance until it does, so a matured one left sitting quietly makes the whole
    report understate the money. This rides in the summary rather than in the
    tree because the summary is the one part both view modes always show.
    """
    names = [node_name(node, guild_id) for node in report_nodes(report) if (node.get("deposit") or {}).get("matured")]
    if not names:
        return ""

    phrases = get_phrases_section(guild_id)

    # Naming them all is friendlier, but fifty group names is more than the
    # whole message may spend — so past a handful the count has to do.
    if len(names) > MAX_LISTED_DEPOSITS:
        return format_phrase(
            phrases,
            "deposits_matured_many",
            "⚠️ `{count}` matured deposit(s) are waiting to be closed. "
            "Their interest is not in the totals until you run `/inflation_deposit close`.",
            count=len(names),
        )

    return format_phrase(
        phrases,
        "deposits_matured",
        "⚠️ Matured deposit(s) waiting to be closed: {groups}. "
        "Their interest is not in the totals until you run `/inflation_deposit close`.",
        groups=", ".join(f"`{name}`" for name in names),
        count=len(names),
    )


def build_summary(report: dict, guild_id: int | None = None) -> str:
    """The header block: totals, the oldest record and any warnings."""
    phrases = get_phrases_section(guild_id)
    currency = get_currency(guild_id)

    nodes = [*report["groups"], report["ungrouped"]]
    oldest_date = report["oldest_date"]

    summary = format_phrase(
        phrases,
        "summary",
        "**Inflation report**\n"
        "Nominal total: `{total_nominal}`\n"
        "Inflation-adjusted: `{total_adjusted}`\n"
        "Purchasing power change: `{loss_percent}`\n"
        "Oldest record: `{oldest_date}`\n"
        "Records: `{records_count}` in `{groups_count}` group(s)",
        total_nominal=format_money(report["total_nominal"], currency),
        total_adjusted=format_money(report["total_adjusted"], currency),
        loss_percent=format_percent(report["loss_percent"]),
        oldest_date=format_date(oldest_date) if oldest_date else "-",
        records_count=sum(node["records_count"] for node in nodes),
        groups_count=len(report["groups"]),
    )

    warnings = [build_rates_warning(guild_id), build_deposits_warning(report, guild_id)]

    return "\n\n".join([summary, *[warning for warning in warnings if warning]])


def get_view_label(mode: str, guild_id: int | None = None) -> str:
    """What the page header calls the rendering the reader is looking at."""
    phrases = get_phrases_section(guild_id)

    if mode == VIEW_SUMMARY:
        return phrases.get("view_summary_label", "Group totals")

    return phrases.get("view_tree_label", "Records by group")


def node_name(node: dict, guild_id: int | None = None) -> str:
    """A node's display name, with the library's English bucket localised."""
    if node.get("id") is None:
        return get_phrases_section(guild_id).get("ungrouped_name", "(ungrouped)")

    return node["name"]


def report_nodes(report: dict) -> list[dict]:
    """
    Every group, plus the ungrouped bucket when it actually holds something.

    Every function here indexes the report rather than `.get()`-ing it: the
    shape is `InflationCalculator.get_groups_report()`'s contract, pinned by
    that library's own tests. A malformed report should raise and surface as
    "could not build the report", not quietly render zero money.
    """
    nodes = list(report["groups"])
    ungrouped = report["ungrouped"]

    if ungrouped["records_count"]:
        nodes.append(ungrouped)

    return nodes


def build_deposit_lines(node: dict, guild_id: int | None = None) -> list[str]:
    """The deposit covering a group, as lines to hang under its heading.

    Empty for a group with no deposit, which is most of them. A matured deposit
    gets its own phrase: the number that matters there is not what it is earning
    but what is waiting to be credited.
    """
    deposit = node.get("deposit")
    if not deposit:
        return []

    phrases = get_phrases_section(guild_id)
    fields = deposit_fields(deposit, get_currency(guild_id))

    if deposit["matured"]:
        return [
            format_phrase(
                phrases,
                "deposit_matured",
                "Deposit {rate} MATURED on {end_date} — {projected} waiting, close it to add it here",
                **fields,
            )
        ]

    return [
        format_phrase(
            phrases,
            "deposit_line",
            "Deposit {rate} until {end_date} ({capitalization}): "
            "earned {earned}, projected {projected} (effective {effective_rate})",
            **fields,
        )
    ]


def build_deposit_marker(node: dict, guild_id: int | None = None) -> str:
    """The same deposit as one short suffix, for a listing rather than a report.

    `/inflation_groups list` answers "which groups do I have", and the full
    deposit line costs it two thirds of the groups it can fit into one message.
    """
    deposit = node.get("deposit")
    if not deposit:
        return ""

    phrases = get_phrases_section(guild_id)
    fields = deposit_fields(deposit, get_currency(guild_id))

    if deposit["matured"]:
        return format_phrase(phrases, "group_deposit_marker_matured", " [deposit {rate} MATURED]", **fields)

    return format_phrase(phrases, "group_deposit_marker", " [deposit {rate} until {end_date}]", **fields)


def build_group_block(node: dict, heading: str, guild_id: int | None = None) -> str:
    """A group's heading with its deposit lines indented underneath."""
    lines = build_deposit_lines(node, guild_id)

    return "\n".join([heading, *[f"  {line}" for line in lines]])


def build_tree_sections(report: dict, guild_id: int | None = None) -> list[Section]:
    """One section per group: its heading, then its records indented under it."""
    phrases = get_phrases_section(guild_id)
    currency = get_currency(guild_id)
    continued = phrases.get("group_continued", "(continued)")

    sections = []
    for node in report_nodes(report):
        name = node_name(node, guild_id)
        blocks = indent_blocks([format_record(record, currency) for record in node["records"]])

        sections.append(
            Section(
                header=build_group_block(node, format_group_heading(node, currency, name), guild_id),
                blocks=blocks,
                # The marker rides inside the name so the heading keeps its shape:
                # `[Salary (continued)] (3)`.
                continued_header=build_group_block(
                    node, format_group_heading(node, currency, f"{name} {continued}"), guild_id
                ),
            )
        )

    return sections


def build_summary_blocks(report: dict, guild_id: int | None = None) -> list[str]:
    """One line per group, closed by the grand total."""
    currency = get_currency(guild_id)
    total_label = get_phrases_section(guild_id).get("total_label", "TOTAL")

    blocks = [
        build_group_block(node, format_group_summary_line(node, currency, node_name(node, guild_id)), guild_id)
        for node in report_nodes(report)
    ]
    if blocks:
        blocks.append(format_grand_total(report, total_label, currency))

    return blocks


def build_view_pages(report: dict, guild_id: int | None = None, mode: str = VIEW_TREE) -> list[str]:
    """Render the report's records into pages, according to the mode."""
    if mode == VIEW_SUMMARY:
        return pack_blocks(build_summary_blocks(report, guild_id))

    # No groups at all means nothing to file records under, so they are listed
    # plainly — the same output this report had before groups existed.
    if not report["groups"]:
        return build_record_pages(report["ungrouped"]["records"], get_currency(guild_id))

    return pack_sections(build_tree_sections(report, guild_id))


def build_report(
    owner_id: int,
    guild_id: int | None = None,
    *,
    mode: str | None = None,
    scope: str = USER_SCOPE,
) -> RenderedReport:
    """The summary block and the paginated record blocks for one owner."""
    mode = mode or inflation_provider.get_view_mode(owner_id, scope)
    report = inflation_provider.get_groups_report(
        owner_id,
        scope,
        detailed=mode == VIEW_TREE,
        collapse_interest=inflation_provider.get_collapse_interest(owner_id, scope),
    )

    return RenderedReport(build_summary(report, guild_id), build_view_pages(report, guild_id, mode), mode)


def render_page(
    summary: str,
    pages: list[str],
    page_index: int = 0,
    guild_id: int | None = None,
    mode: str = VIEW_TREE,
) -> str:
    """Compose the final message content for one page of the report."""
    phrases = get_phrases_section(guild_id)

    if not pages:
        empty = phrases.get("no_records", "No records yet. Add one with `/inflation add`.")
        return f"{summary}\n\n{empty}"

    page_index = min(max(page_index, 0), len(pages) - 1)

    return format_phrase(
        phrases,
        "page_format",
        "{summary}\n\n**{view_label} (page {current_page}/{max_pages}):**\n```text\n{page_content}\n```",
        summary=summary,
        view_label=get_view_label(mode, guild_id),
        current_page=page_index + 1,
        max_pages=len(pages),
        page_content=pages[page_index],
    )


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

    # Both the wrapper and the note come from `phrases.json` and can be rewritten
    # to any length, so what they cost is measured rather than guessed at.
    overhead = len(wrap("")) + len(truncation_note(0))
    page, shown = pack_single_page(blocks, max(MIN_RECORD_BLOCK, MESSAGE_LIMIT - overhead))

    content = wrap(page) + truncation_note(shown)
    if len(content) <= MESSAGE_LIMIT:
        return content

    # Rewritten phrases are over budget on their own. Cutting into the listing
    # would leave its code fence unclosed, so it goes entirely and the note is
    # all that is left to say.
    return trim_to_whole_lines(truncation_note(0).lstrip("\n"), MESSAGE_LIMIT)


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

    overhead = len(wrap("")) + len(truncation_note(0)) + len(warning)
    page, shown = pack_single_page(blocks, max(MIN_RECORD_BLOCK, MESSAGE_LIMIT - overhead))

    content = wrap(page) + truncation_note(shown) + warning
    if len(content) <= MESSAGE_LIMIT:
        return content

    # Rewritten phrases are over budget on their own. Cutting into the listing
    # would leave its code fence unclosed, so it goes entirely; what must
    # survive is that the money left, and what breaking the deposit costs.
    return trim_to_whole_lines(
        f"{format_money(result['amount'], currency)}\n{result['warning'] or ''}".strip(), MESSAGE_LIMIT
    )


def build_deposit_overview(owner_id: int, guild_id: int | None = None, scope: str = USER_SCOPE) -> str:
    """Every group under a deposit, one entry each, trimmed to fit one message.

    Built from the report rather than from the calculator so it says exactly
    what the report channel says, and so a group with no records — whose deposit
    the library leaves unvalued — is reported as having nothing to show.
    """
    phrases = get_phrases_section(guild_id)
    report = inflation_provider.get_groups_report(owner_id, scope, detailed=False)

    blocks = [
        "\n".join([f"[{node_name(node, guild_id)}]", *build_deposit_lines(node, guild_id)])
        for node in report_nodes(report)
        if node.get("deposit")
    ]
    if not blocks:
        return phrases.get(
            "deposit_overview_empty", "No group is under a deposit. Attach one with `/inflation_deposit attach`."
        )

    def wrap(page: str) -> str:
        return format_phrase(phrases, "deposit_overview", "```text\n{deposits}\n```", deposits=page)

    def truncation_note(shown: int) -> str:
        if shown >= len(blocks):
            return ""

        return "\n" + format_phrase(
            phrases,
            "deposit_overview_truncated",
            "*Only the first {shown} of {total} deposits fit into this message.*",
            shown=shown,
            total=len(blocks),
        )

    overhead = len(wrap("")) + len(truncation_note(0))
    page, shown = pack_single_page(blocks, max(MIN_RECORD_BLOCK, MESSAGE_LIMIT - overhead))

    content = wrap(page) + truncation_note(shown)
    if len(content) <= MESSAGE_LIMIT:
        return content

    return trim_to_whole_lines(truncation_note(0).lstrip("\n"), MESSAGE_LIMIT)


def build_server_report(guild_id: int, reserve: int = 0, mode: str | None = None) -> str:
    """
    The guild's shared budget as one message, trimmed to fit.

    Same report as a user's — same summary, same CPI warning, same rendering —
    only there is no pager to spill onto, so the record list gets whatever is
    left of the message and says what it had to leave out. `reserve` is what the
    caller adds around this text, e.g. the page header.
    """
    phrases = get_phrases_section(guild_id)
    currency = get_currency(guild_id)

    mode = mode or inflation_provider.get_view_mode(guild_id, SERVER_SCOPE)
    report = inflation_provider.get_groups_report(
        guild_id,
        SERVER_SCOPE,
        detailed=mode == VIEW_TREE,
        collapse_interest=inflation_provider.get_collapse_interest(guild_id, SERVER_SCOPE),
    )

    summary = build_summary(report, guild_id)
    total_records = sum(node["records_count"] for node in report_nodes(report))

    def truncation_note(shown: int) -> str:
        if mode == VIEW_SUMMARY or shown >= total_records:
            return ""

        return "\n" + format_phrase(
            phrases,
            "server_truncated",
            "*Only the first {shown} of {total} records fit into this message.*",
            shown=shown,
            total=total_records,
        )

    page_limit = max(MIN_RECORD_BLOCK, MESSAGE_LIMIT - len(summary) - reserve - FENCE_RESERVE)

    if mode == VIEW_SUMMARY:
        page, shown = pack_single_page(build_summary_blocks(report, guild_id), page_limit)
    elif not report["groups"]:
        page, shown = build_single_record_page(report["ungrouped"]["records"], currency, page_limit)
    else:
        page, shown = pack_sections_single_page(build_tree_sections(report, guild_id), page_limit)

    content = render_page(summary, [page] if page else [], 0, guild_id, mode) + truncation_note(shown)
    if len(content) <= MESSAGE_LIMIT:
        return content

    # The summary alone is over budget — an enormous CPI warning, or a rewritten
    # `summary` phrase. Cutting into the record block would leave its code fence
    # unclosed, so the block goes entirely and the note says the records are gone.
    note = truncation_note(0)

    return trim_to_whole_lines(summary, MESSAGE_LIMIT - len(note)) + note
