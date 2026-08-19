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

from core.personal_channels import PersonalChannelRegistry
from modules.inflation_calculator.modules.api import InflationCalculator
from modules.inflation_calculator.modules.config import FALLBACK_ANNUAL_INFLATION_RATE
from modules.inflation_calculator.modules.exceptions import ValidationError
from modules.inflation_calculator.modules.storage import (
    load_inflation_rates_from_file,
    save_inflation_rates_to_file,
)

logger = logging.getLogger(__name__)

# Re-exported so cogs can name the fallback rate in user-facing warnings without
# reaching into the vendored package themselves.
FALLBACK_ANNUAL_PERCENT = (FALLBACK_ANNUAL_INFLATION_RATE * 100).normalize()

# Whose records a call is about: one user's own, or a guild's shared budget.
# The two live in separate directories so that a guild id can never be mistaken
# for a user id — `count_users_with_records()` reads the user directory as a
# whole and would otherwise count server budgets as users.
USER_SCOPE = "user"
SERVER_SCOPE = "server"


def get_base_data_dir() -> Path:
    """Repo-root `data/`, shared with the schedule subsystem."""
    return Path(__file__).resolve().parent.parent.parent / "data"


def get_data_dir() -> Path:
    return Path(getattr(settings, "inflation_data_dir", get_base_data_dir() / "inflation"))


def get_records_dir() -> Path:
    return get_data_dir() / "records"


def get_server_records_dir() -> Path:
    return get_data_dir() / "server_records"


def get_scope_records_dir(scope: str = USER_SCOPE) -> Path:
    """
    The directory a scope's records live in, refusing anything else.

    A misspelled scope used to mean "user", which would quietly write a guild's
    shared budget into someone's personal file — the one mistake here that is
    not obvious from the outside.
    """
    if scope == SERVER_SCOPE:
        return get_server_records_dir()

    if scope != USER_SCOPE:
        raise ValueError(f"Unknown inflation scope: {scope!r}")

    return get_records_dir()


def get_records_file(owner_id: int, scope: str = USER_SCOPE) -> Path:
    """Records of one owner: a user by default, a guild under `SERVER_SCOPE`."""
    return get_scope_records_dir(scope) / f"{owner_id}.json"


def get_rates_file() -> Path:
    return get_data_dir() / "inflation_rates.json"


def get_channels_file() -> Path:
    """Channel registry, kept next to `schedule_channels.json`."""
    return get_base_data_dir() / "inflation_channels.json"


def get_server_channels_file() -> Path:
    """Registry of the public per-guild report channels."""
    return get_base_data_dir() / "inflation_server_channels.json"


class InflationProvider:
    """
    Owns record/rate persistence and the inflation channel registries.

    Calculator instances are cached per owner because `InflationCalculator.from_json`
    re-reads both JSON files on construction, and the report loop would otherwise
    do that on every tick. Rates are shared by everyone, so changing them drops
    the whole cache.

    There are two kinds of owner, told apart by `scope`: a user with their own
    private records, and a guild with one shared budget its administrators edit.
    """

    def __init__(self):
        self._calculators: dict[tuple[str, int], InflationCalculator] = {}

        # Key names match the file this shipped with, so nothing on disk moves.
        self.channels = PersonalChannelRegistry(
            get_channels_file(), display_key="report_channel_id", management_key="management_channel_id"
        )

        # Keyed by guild id, and with no management channel: the server report is
        # public and its commands are run wherever the administrator happens to be.
        self.server_channels = PersonalChannelRegistry(get_server_channels_file(), display_key="report_channel_id")

    # ------------------------------------------------------------------
    # Calculator access
    # ------------------------------------------------------------------

    def _get_calculator(self, owner_id: int, scope: str = USER_SCOPE) -> InflationCalculator:
        calculator = self._calculators.get((scope, owner_id))
        if calculator is not None:
            return calculator

        get_scope_records_dir(scope).mkdir(parents=True, exist_ok=True)

        calculator = InflationCalculator.from_json(
            records_filepath=str(get_records_file(owner_id, scope)),
            inflation_rates_filepath=str(get_rates_file()),
        )
        self._calculators[(scope, owner_id)] = calculator

        return calculator

    def invalidate(self, owner_id: int | None = None, scope: str = USER_SCOPE) -> None:
        """Drop cached calculators, e.g. after the JSON files changed on disk."""
        if owner_id is None:
            self._calculators.clear()
        else:
            self._calculators.pop((scope, owner_id), None)

    # ------------------------------------------------------------------
    # Records
    # ------------------------------------------------------------------

    def add_record(
        self, owner_id: int, amount: str, date: datetime.date, comment: str = "", scope: str = USER_SCOPE
    ) -> dict:
        calculator = self._get_calculator(owner_id, scope)

        if scope == SERVER_SCOPE:
            max_records = getattr(settings, "inflation_max_records_per_server", 500)
        else:
            max_records = getattr(settings, "inflation_max_records_per_user", 200)

        if calculator.records_count >= max_records:
            raise ValidationError(f"Record limit reached ({max_records}). Delete something first.")

        return calculator.add_record(amount, date, comment)

    def delete_record(self, owner_id: int, record_id: int, scope: str = USER_SCOPE) -> dict:
        return self._get_calculator(owner_id, scope).delete_record(record_id)

    def list_records(self, owner_id: int, scope: str = USER_SCOPE) -> list[dict]:
        return self._get_calculator(owner_id, scope).get_records()

    def count_records(self, owner_id: int, scope: str = USER_SCOPE) -> int:
        return self._get_calculator(owner_id, scope).records_count

    def get_report(self, owner_id: int, scope: str = USER_SCOPE) -> dict:
        return self._get_calculator(owner_id, scope).get_report()

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

    def set_rate(self, year_month: str, rate_percent: str) -> None:
        """
        Store a monthly CPI value. `rate_percent` is a CPI index (101.4 → +1.4%),
        matching the Ministry of Finance tables the data is copied from.
        """
        self._rates_calculator().set_inflation_rate(year_month, rate_percent)

        # Every cached calculator holds a copy of the old rates.
        self.invalidate()

    # ------------------------------------------------------------------
    # Channel registry
    # ------------------------------------------------------------------

    def get_channels(self, user_id: int) -> dict | None:
        return self.channels.get(user_id)

    def get_report_channel_id(self, user_id: int) -> int | None:
        entry = self.channels.get(user_id)

        return self.channels.display_channel_id(entry) if entry else None

    def get_server_report_channel_id(self, guild_id: int) -> int | None:
        entry = self.server_channels.get(guild_id)

        return self.server_channels.display_channel_id(entry) if entry else None

    def count_users_with_channels(self) -> int:
        return self.channels.count()

    def count_servers_with_channels(self) -> int:
        return self.server_channels.count()


inflation_provider = InflationProvider()
