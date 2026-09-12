import logging
from datetime import datetime
from typing import ClassVar

import settings
from disnake.ext import commands

from core.embed_cog import BaseEmbedCog
from core.time_utils import tz
from core.utils import format_phrase, get_phrases
from modules.elapsed_time import format_elapsed_breakdown, format_elapsed_total, measure, parse_date

logger = logging.getLogger(__name__)

# The unit words, used when `phrases.json` carries no `time_since_embed` section
# (a fresh checkout has no file at all). Two forms each, singular and plural, as
# `decline()` reads an English-shaped list; a deployment overrides all four from
# `phrases.json`, where Ukrainian gives three forms instead.
FALLBACK_FORMS: dict[str, list[str]] = {
    "hour_forms": ["hour", "hours"],
    "day_forms": ["day", "days"],
    "year_forms": ["year", "years"],
    "month_forms": ["month", "months"],
}

FALLBACK_ENTRY = "**{label}** *(since {date})*\n`{total}`\n`{breakdown}`"
FALLBACK_DATE_FORMAT = "%d.%m.%Y"
FALLBACK_OVERFLOW = "…and {count} more"

# Discord's own ceiling on an embed description. The list is operator-written
# rather than user-written, but a long one still has to lose its tail instead
# of losing the whole embed to a 400 from Discord.
DESCRIPTION_LIMIT = 4096


class TimeSinceEmbed(BaseEmbedCog):
    """
    One embed counting how long it has been since each configured date.

    Each date gets two counters — a running total in one unit and the calendar
    breakdown — because they answer different questions; `modules.elapsed_time`
    measures them and this cog only dresses them in words.
    """

    embed_key = "time_since"
    phrases_section = "time_since_embed"
    settings_key = "time_since_update_seconds"
    default_seconds = 240
    fallback_embed: ClassVar[dict] = {
        "title": ":hourglass: | Time Since",
        "description": "{entries}",
    }

    def __init__(self, bot):
        self.dates = self._load_dates()

        super().__init__(bot)

    @staticmethod
    def _load_dates() -> list[tuple[str, datetime]]:
        """
        Read `settings.time_since_dates` into label/date pairs, in order.

        Three spellings are accepted because all three are ones an operator
        reasonably writes: a `{label: date}` mapping, a list of
        `{"label": ..., "date": ...}` entries, and a bare list of dates, which
        label themselves. An unreadable date is dropped with a warning rather
        than taking the cog's import down with it.
        """
        configured = getattr(settings, "time_since_dates", None) or []
        raw_entries = configured.items() if isinstance(configured, dict) else configured

        dates = []
        for raw in raw_entries:
            if isinstance(raw, dict):
                label, value = raw.get("label"), raw.get("date")
            elif isinstance(raw, (tuple, list)) and len(raw) == 2:
                label, value = raw
            else:
                label, value = None, raw

            start = parse_date(value)
            if start is None:
                logger.warning("time_since_dates: cannot read the date %r, skipping it.", value)
                continue

            dates.append((str(label) if label else str(value), start.replace(tzinfo=tz)))

        return dates

    def should_start(self) -> bool:
        # With nothing configured there is nothing to count, and publishing an
        # empty embed would take up a slot in every statistic message.
        return bool(self.dates)

    async def get_data(self):
        section = get_phrases().get(self.phrases_section, {})
        forms = {name: section.get(name, fallback) for name, fallback in FALLBACK_FORMS.items()}
        date_format = section.get("date_format", FALLBACK_DATE_FORMAT)
        now = datetime.now(tz)

        entries = []
        for position, (label, start) in enumerate(self.dates):
            elapsed = measure(start, now)
            entry = format_phrase(
                section,
                "entry",
                FALLBACK_ENTRY,
                label=label,
                date=start.strftime(date_format),
                total=format_elapsed_total(elapsed, forms["hour_forms"], forms["day_forms"]),
                breakdown=format_elapsed_breakdown(
                    elapsed, forms["year_forms"], forms["month_forms"], forms["day_forms"]
                ),
            )

            # The overflow note has to fit too, so the budget accounts for it while
            # there are still entries that might not make it.
            cut = len(self.dates) - position
            note = format_phrase(section, "overflow", FALLBACK_OVERFLOW, count=cut)
            budget = DESCRIPTION_LIMIT - (len(note) + 2 if cut > 1 else 0)

            if len("\n\n".join([*entries, entry])) > budget:
                entries.append(note)
                break

            entries.append(entry)

        return {"entries": "\n\n".join(entries)}


def setup(bot: commands.Bot) -> None:
    bot.add_cog(TimeSinceEmbed(bot))
