import calendar
import logging
from datetime import datetime
from typing import ClassVar

import settings
from disnake.ext import commands

from core.embed_cog import BaseEmbedCog
from core.time_utils import tz

logger = logging.getLogger(__name__)

# Ukrainian `[1, 2-4, 5-0]` forms, hardcoded rather than looked up in
# phrases.json: this embed is temporary, and a phrases section would outlive it.
HOURS = ("година", "години", "годин")
DAYS = ("день", "дні", "днів")
MONTHS = ("місяць", "місяці", "місяців")
YEARS = ("рік", "роки", "років")

# Below this the running counter stays in hours: "26 годин" is still the answer a
# reader wants on the first day, and "1 день" throws away two thirds of it.
DAYS_BEFORE_COUNTING_IN_DAYS = 2

DATE_FORMAT = "%d.%m.%Y"


def decline(number: int, forms: tuple[str, str, str]) -> str:
    """Ukrainian declension of the word after a number."""
    last_two, last = abs(number) % 100, abs(number) % 10

    if 11 <= last_two <= 14:
        form = forms[2]
    elif last == 1:
        form = forms[0]
    elif 2 <= last <= 4:
        form = forms[1]
    else:
        form = forms[2]

    return f"{number} {form}"


def add_months(moment: datetime, months: int) -> datetime:
    """
    Shift by whole months, clamping the day to the target month's length.

    31 January plus one month is 28 February, the answer a calendar gives when
    asked; spilling into 3 March would make the month count walk forward on its own.
    """
    year, month = divmod(moment.year * 12 + moment.month - 1 + months, 12)
    month += 1

    return moment.replace(year=year, month=month, day=min(moment.day, calendar.monthrange(year, month)[1]))


class TimeSinceEmbed(BaseEmbedCog):
    """
    How long it has been since each date in `settings.time_since_dates`.

    Each date gets two readings of the same span, because they answer different
    questions: a running total in one unit, and the calendar breakdown. A year
    and a half is "548 днів" to one and "1 рік 6 місяців 1 день" to the other.
    """

    embed_key = "time_since"
    settings_key = "time_since_update_seconds"
    default_seconds = 240
    # No `phrases_section` on purpose — the text below is the whole of it.
    fallback_embed: ClassVar[dict] = {
        "title": ":hourglass: | Скільки часу минуло",
        "description": "{entries}",
    }

    def __init__(self, bot):
        self.dates = self._load_dates()

        super().__init__(bot)

    @staticmethod
    def _load_dates() -> list[tuple[str, datetime]]:
        """
        Read the configured `{label: date}` mapping, in order.

        An unreadable date is dropped with a warning rather than taking the cog's
        import down: `settings.py` is hand-written.
        """
        dates = []
        for label, value in (getattr(settings, "time_since_dates", None) or {}).items():
            try:
                start = datetime.fromisoformat(value)
            except (TypeError, ValueError):
                logger.warning("time_since_dates: cannot read the date %r under %r, skipping it.", value, label)
                continue

            dates.append((label, start.replace(tzinfo=tz)))

        return dates

    def should_start(self) -> bool:
        # Nothing configured means nothing to count, and an empty embed would
        # still take up a slot in every statistic message.
        return bool(self.dates)

    async def get_data(self):
        now = datetime.now(tz)

        return {"entries": "\n\n".join(self._count(label, start, now) for label, start in self.dates)}

    @staticmethod
    def _count(label: str, start: datetime, now: datetime) -> str:
        header = f"**{label}** *(з {start.strftime(DATE_FORMAT)})*"
        if now <= start:
            # A mistyped year reads as unstarted rather than as negative counts.
            return f"{header}\n`ще не настало`"

        delta = now - start

        # Counted off in whole months rather than by subtracting the date fields:
        # borrowing a day count from "the previous month" breaks at the ends of
        # the month (31 January to 1 March has no 30 days of February to borrow).
        months_total = (now.year - start.year) * 12 + (now.month - start.month)
        if add_months(start, months_total) > now:
            months_total -= 1
        years, months = divmod(months_total, 12)
        days = (now - add_months(start, months_total)).days

        total = (
            decline(delta.days, DAYS)
            if delta.days >= DAYS_BEFORE_COUNTING_IN_DAYS
            else decline(int(delta.total_seconds() // 3600), HOURS)
        )

        # The empty units are dropped — "2 роки 5 днів" says what "2 роки
        # 0 місяців 5 днів" says — but the days stay when there is nothing else.
        parts = [decline(years, YEARS)] if years else []
        if months:
            parts.append(decline(months, MONTHS))
        if days or not parts:
            parts.append(decline(days, DAYS))

        return f"{header}\n`{total}`\n`{' '.join(parts)}`"


def setup(bot: commands.Bot) -> None:
    bot.add_cog(TimeSinceEmbed(bot))
