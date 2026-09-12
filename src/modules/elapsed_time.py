"""How much time has passed since a date, in the two shapes a reader wants.

Pure numbers and text: no disnake, no `settings`, no filesystem, no phrases —
which is what keeps the suite dependency-free. The unit words are parameters
rather than constants here, so the localisation stays one layer up, in
`cogs/embeds/time_since.py`.

Two counters come out of one span, because they answer different questions.
`format_elapsed_total()` is the running count in a single unit — hours until
there is more than one day to show, days after that — and
`format_elapsed_breakdown()` is the calendar reading: years, months and days.
A span of a year and a half is "548 days" to one and "1 year 6 months 1 day" to
the other, and neither number is the one the reader always means.
"""

import calendar
from dataclasses import dataclass
from datetime import date, datetime

# Below this, the running counter stays in hours: "26 hours" is still the answer
# a reader wants on the first day, and "1 day" throws away two thirds of what
# they asked for.
DAYS_BEFORE_COUNTING_IN_DAYS = 2

ACCEPTED_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d",
    "%d.%m.%Y %H:%M",
    "%d.%m.%Y",
)


@dataclass(frozen=True)
class Elapsed:
    """One span measured both ways. `days` is the remainder after the months."""

    total_hours: int
    total_days: int
    years: int
    months: int
    days: int


def parse_date(value: str | datetime | date) -> datetime | None:
    """
    Read one configured date, or `None` when it is unreadable.

    `settings.py` is hand-written, so a typo in a date must not take the bot
    down on import — the caller drops the entry and keeps the others.
    Whatever comes back is naive: attaching the timezone is the caller's job,
    since only it knows which clock the comparison runs on.
    """
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if not isinstance(value, str):
        return None

    for date_format in ACCEPTED_DATE_FORMATS:
        try:
            return datetime.strptime(value.strip(), date_format)
        except ValueError:
            continue

    return None


def add_months(moment: datetime, months: int) -> datetime:
    """
    Shift by whole months, clamping the day to the target month's length.

    31 January plus one month is 28 February, the same answer a calendar gives
    when asked, and the same one `relativedelta` would give — the alternative,
    spilling into 3 March, would make the month count walk forward on its own.
    """
    total = (moment.year * 12 + moment.month - 1) + months
    year, month = divmod(total, 12)
    month += 1

    return moment.replace(year=year, month=month, day=min(moment.day, calendar.monthrange(year, month)[1]))


def measure(start: datetime, now: datetime) -> Elapsed:
    """
    Measure `start` → `now` both ways at once.

    A date in the future reads as all zeros rather than as a negative count:
    an operator who mistyped the year gets an obviously-unstarted counter
    instead of a line of minus signs.
    """
    if now <= start:
        return Elapsed(total_hours=0, total_days=0, years=0, months=0, days=0)

    delta = now - start

    # Counted off whole months rather than by subtracting the fields: borrowing
    # a day count from "the previous month" breaks on the ends of the month
    # (31 January to 1 March has no 30 days of February to borrow from).
    months_total = (now.year - start.year) * 12 + (now.month - start.month)
    if add_months(start, months_total) > now:
        months_total -= 1
    years, months = divmod(months_total, 12)

    return Elapsed(
        total_hours=int(delta.total_seconds() // 3600),
        total_days=delta.days,
        years=years,
        months=months,
        days=(now - add_months(start, months_total)).days,
    )


def decline(number: int, forms: list[str] | tuple[str, ...]) -> str:
    """
    Put the word after a number into the form that number takes.

    How many forms are given says which rule applies. Three are the Slavic
    `[1, 2-4, 5-0]` set, selected by the last digits: "21 день", "22 дні",
    "25 днів". Two are a language that only counts one against many, English
    among them, where the singular belongs to exactly one — the Slavic rule
    would read "21 day" there, which is why the count decides the rule rather
    than the words doing it.

    A single form, or none, is used for every number rather than raising: the
    forms come from `phrases.json`, which an operator edits by hand.
    """
    usable = [str(form) for form in forms] or [""]

    if len(usable) == 2:
        return f"{number} {usable[0] if abs(number) == 1 else usable[1]}".strip()

    while len(usable) < 3:
        usable.append(usable[-1])

    last_two = abs(number) % 100
    last = abs(number) % 10

    if 11 <= last_two <= 14:
        form = usable[2]
    elif last == 1:
        form = usable[0]
    elif 2 <= last <= 4:
        form = usable[1]
    else:
        form = usable[2]

    return f"{number} {form}".strip()


def format_elapsed_total(elapsed: Elapsed, hour_forms, day_forms) -> str:
    """The running counter in one unit: hours on the first day, days after it."""
    if elapsed.total_days >= DAYS_BEFORE_COUNTING_IN_DAYS:
        return decline(elapsed.total_days, day_forms)

    return decline(elapsed.total_hours, hour_forms)


def format_elapsed_breakdown(elapsed: Elapsed, year_forms, month_forms, day_forms) -> str:
    """
    The calendar reading: years, months and days, with the empty units dropped.

    Zeros are left out because they carry nothing — "2 years 5 days" says what
    "2 years 0 months 5 days" says. A span with nothing in any unit still
    renders its days, so the line is never empty.
    """
    parts = []
    if elapsed.years:
        parts.append(decline(elapsed.years, year_forms))
    if elapsed.months:
        parts.append(decline(elapsed.months, month_forms))
    if elapsed.days or not parts:
        parts.append(decline(elapsed.days, day_forms))

    return " ".join(parts)
