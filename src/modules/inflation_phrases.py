"""How the bot talks about an inflation report, in the reader's language.

Three modules stand between the calculator and Discord, and the line between
them is what each one is allowed to know:

- `inflation_formatter` knows numbers and text, and nothing else — no phrases,
  no settings, no filesystem, which is what keeps its tests free of all three.
- this module knows `phrases.json`. Every function here takes a guild id and
  returns one localized fragment: what a group is called, what the currency is,
  how a deposit reads. Nothing here composes a message or counts its length.
- `inflation_report` and `inflation_replies` compose those fragments into the
  two kinds of message the bot sends, and own the budget each has to fit.

Splitting the fragments out is what lets a report and a slash-command reply say
the same thing about the same deposit without either importing the other.
"""

from core.utils import format_phrase, get_phrases
from modules.inflation_formatter import deposit_fields
from modules.inflation_provider import VIEW_SUMMARY


def get_phrases_section(guild_id: int | None = None) -> dict:
    return get_phrases(guild_id).get("inflation", {})


def get_currency(guild_id: int | None = None) -> str:
    return get_phrases_section(guild_id).get("currency", "UAH")


def node_name(node: dict, guild_id: int | None = None) -> str:
    """A node's display name, with the library's English bucket localised."""
    if node.get("id") is None:
        return get_phrases_section(guild_id).get("ungrouped_name", "(ungrouped)")

    return node["name"]


def get_view_label(mode: str, guild_id: int | None = None) -> str:
    """What the page header calls the rendering the reader is looking at."""
    phrases = get_phrases_section(guild_id)

    if mode == VIEW_SUMMARY:
        return phrases.get("view_summary_label", "Group totals")

    return phrases.get("view_tree_label", "Records by group")


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
