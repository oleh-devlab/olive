"""Storage boundary between the bot and the vendored inflation_calculator.

Cogs never touch `modules.inflation_calculator` directly — they go through the
`inflation_provider` singleton exported at the bottom of this module.
"""

import datetime
import json
import logging
from decimal import Decimal
from pathlib import Path

import settings

from modules.inflation_calculator.modules.api import InflationCalculator
from modules.inflation_calculator.modules.config import FALLBACK_ANNUAL_INFLATION_RATE
from modules.inflation_calculator.modules.exceptions import ValidationError
from modules.inflation_calculator.modules.storage import (
    load_inflation_rates_from_file,
    save_inflation_rates_to_file,
)
from modules.inflation_formatter import find_rate_gaps

logger = logging.getLogger(__name__)

# Re-exported so cogs can name the fallback rate in user-facing warnings without
# reaching into the vendored package themselves.
FALLBACK_ANNUAL_PERCENT = (FALLBACK_ANNUAL_INFLATION_RATE * 100).normalize()


def get_base_data_dir() -> Path:
    """Repo-root `data/`, shared with the schedule subsystem."""
    return Path(__file__).resolve().parent.parent.parent / "data"


def get_data_dir() -> Path:
    return Path(getattr(settings, "inflation_data_dir", get_base_data_dir() / "inflation"))


def get_records_dir() -> Path:
    return get_data_dir() / "records"


def get_records_file(user_id: int) -> Path:
    return get_records_dir() / f"{user_id}.json"


def get_rates_file() -> Path:
    return get_data_dir() / "inflation_rates.json"


def get_channels_file() -> Path:
    """Channel registry, kept next to `schedule_channels.json`."""
    return get_base_data_dir() / "inflation_channels.json"


class InflationProvider:
    """
    Owns record/rate persistence and the inflation channel registry.

    Calculator instances are cached per user because `InflationCalculator.from_json`
    re-reads both JSON files on construction, and the report loop would otherwise
    do that on every tick. Rates are shared by every user, so changing them bumps
    `_rates_version` and invalidates all cached calculators at once.
    """

    def __init__(self):
        self._calculators: dict[int, tuple[int, InflationCalculator]] = {}
        self._rates_version = 0

    # ------------------------------------------------------------------
    # Calculator access
    # ------------------------------------------------------------------

    def _get_calculator(self, user_id: int) -> InflationCalculator:
        cached = self._calculators.get(user_id)
        if cached and cached[0] == self._rates_version:
            return cached[1]

        get_records_dir().mkdir(parents=True, exist_ok=True)

        calculator = InflationCalculator.from_json(
            records_filepath=str(get_records_file(user_id)),
            inflation_rates_filepath=str(get_rates_file()),
        )
        self._calculators[user_id] = (self._rates_version, calculator)

        return calculator

    def invalidate(self, user_id: int | None = None) -> None:
        """Drop cached calculators, e.g. after the JSON files changed on disk."""
        if user_id is None:
            self._calculators.clear()
        else:
            self._calculators.pop(user_id, None)

    # ------------------------------------------------------------------
    # Records
    # ------------------------------------------------------------------

    def add_record(self, user_id: int, amount: str, date: datetime.date, comment: str = "") -> dict:
        calculator = self._get_calculator(user_id)

        max_records = getattr(settings, "inflation_max_records_per_user", 200)
        if calculator.records_count >= max_records:
            raise ValidationError(f"Record limit reached ({max_records}). Delete something first.")

        return calculator.add_record(amount, date, comment)

    def delete_record(self, user_id: int, record_id: int) -> dict:
        return self._get_calculator(user_id).delete_record(record_id)

    def list_records(self, user_id: int) -> list[dict]:
        return self._get_calculator(user_id).get_records()

    def count_records(self, user_id: int) -> int:
        return self._get_calculator(user_id).records_count

    def get_report(self, user_id: int) -> dict:
        return self._get_calculator(user_id).get_report()

    def count_users_with_records(self) -> int:
        """
        How many users have at least one record.

        The commands work without a channel, so this is the wider of the two
        usage numbers — `count_users_with_channels()` sees only those who ran
        `/inflation_channel create`. Files are read directly instead of through
        the calculator cache: this is a rare statistics call over tiny files,
        and caching every user's calculator for it would be worse.
        """
        records_dir = get_records_dir()
        if not records_dir.exists():
            return 0

        count = 0
        for path in records_dir.glob("*.json"):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    if json.load(f):
                        count += 1
            except (OSError, json.JSONDecodeError) as e:
                logger.warning(f"Could not read records file {path}: {e}")

        return count

    def count_users_with_channels(self) -> int:
        return len(self.load_channels())

    # ------------------------------------------------------------------
    # Inflation rates (shared by all users)
    # ------------------------------------------------------------------

    def _rates_calculator(self) -> InflationCalculator:
        """A record-less calculator used only to read and write the shared rates file."""
        rates_path = str(get_rates_file())

        def persist(rates: dict[str, Decimal]) -> None:
            get_data_dir().mkdir(parents=True, exist_ok=True)
            save_inflation_rates_to_file(rates, rates_path)

        return InflationCalculator(
            inflation_rates=load_inflation_rates_from_file(rates_path),
            on_rates_change=persist,
        )

    def get_rates(self) -> dict[str, Decimal]:
        return self._rates_calculator().get_inflation_rates()

    def has_rates(self) -> bool:
        return bool(self.get_rates())

    def set_rate(self, year_month: str, rate_percent: str) -> None:
        """
        Store a monthly CPI value. `rate_percent` is a CPI index (101.4 → +1.4%),
        matching the Ministry of Finance tables the data is copied from.
        """
        self._rates_calculator().set_inflation_rate(year_month, rate_percent)
        self._rates_version += 1
        self._calculators.clear()

    def get_rate_gaps(self) -> list[str]:
        """Months with no CPI data between the oldest known month and last month."""
        return find_rate_gaps(self.get_rates())

    # ------------------------------------------------------------------
    # Channel registry
    # ------------------------------------------------------------------

    def load_channels(self) -> dict:
        filepath = get_channels_file()
        if not filepath.exists():
            return {}

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.error(f"Error reading {filepath}: {e}")
            return {}

    def save_channels(self, data: dict) -> None:
        filepath = get_channels_file()
        filepath.parent.mkdir(parents=True, exist_ok=True)

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

    def get_channels(self, user_id: int) -> dict | None:
        return self.load_channels().get(str(user_id))

    def register_channels(self, user_id: int, guild_id: int, report_channel_id: int, management_channel_id: int):
        data = self.load_channels()
        data[str(user_id)] = {
            "report_channel_id": report_channel_id,
            "management_channel_id": management_channel_id,
            "guild_id": guild_id,
        }
        self.save_channels(data)

    def remove_channels(self, user_id: int) -> dict | None:
        data = self.load_channels()
        removed = data.pop(str(user_id), None)
        if removed is not None:
            self.save_channels(data)

        return removed

    def count_channels_in_guild(self, guild_id: int) -> int:
        return sum(1 for info in self.load_channels().values() if info.get("guild_id") == guild_id)

    def find_user_by_management_channel(self, channel_id: int) -> int | None:
        for user_id_str, info in self.load_channels().items():
            if info.get("management_channel_id") == channel_id:
                return int(user_id_str)

        return None


inflation_provider = InflationProvider()
