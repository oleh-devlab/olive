"""Storage boundary between the bot and the vendored inflation_calculator.

Cogs never touch `modules.inflation_calculator` directly — they go through the
`inflation_provider` singleton exported at the bottom of this module.

An owner's report preferences — view mode, and whether deposit interest is
folded into one row — are kept here too, in the channel registry entry next to
the channel ids. `PersonalChannelRegistry` preserves keys it does not know about
precisely so a module can do this — the schedule keeps its solver settings in the
same record — and it means the choices survive a restart without a file of their
own.
"""

import datetime
import json
import logging
from decimal import Decimal
from pathlib import Path

import settings

from core.personal_channels import PersonalChannelRegistry
from modules.inflation_calculator.modules.api import InflationCalculator
from modules.inflation_calculator.modules.config import (
    DEFAULT_DEPOSIT_TAX_PERCENT,
    FALLBACK_ANNUAL_INFLATION_RATE,
)
from modules.inflation_calculator.modules.deposit import (
    CapitalizationPeriod,
    DepositResult,
    DepositTerms,
)
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

# How a report renders its records. `tree` lists every record under the group it
# belongs to; `summary` reports only each group's sub-total. Stored per owner, so
# the strings are part of the on-disk format and must stay stable.
VIEW_TREE = "tree"
VIEW_SUMMARY = "summary"
VIEW_MODES = (VIEW_TREE, VIEW_SUMMARY)

VIEW_MODE_KEY = "view_mode"

# How often a deposit adds its interest back to the balance. The values are the
# library's own enum values, and they reach Discord as command choices.
CAPITALIZATION_MODES = tuple(period.value for period in CapitalizationPeriod)
DEFAULT_CAPITALIZATION = CapitalizationPeriod.MONTHLY.value

# The tax the library assumes when a caller does not name one (18% PIT + 5%
# military levy). Re-exported so a command can show it as its default.
DEFAULT_TAX_PERCENT = DEFAULT_DEPOSIT_TAX_PERCENT

# Whether a report folds a group's deposit-interest lots into one row. Closing a
# year of monthly capitalization credits twelve of them, which is a page of
# noise in a channel capped at 2000 characters. Stored per owner next to the
# view mode, so it is part of the on-disk format too.
COLLAPSE_INTEREST_KEY = "collapse_interest"


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


def get_default_view() -> str:
    """The view mode an owner who never chose one gets."""
    mode = getattr(settings, "inflation_default_view", VIEW_TREE)

    return mode if mode in VIEW_MODES else VIEW_TREE


def as_group_ref(group: int | str | None) -> int | str | None:
    """
    Turn a Discord option into something the calculator can look a group up by.

    Slash options arrive as strings, and the calculator matches a string against
    group names only. Group ids are shown to the reader — `/inflation_groups
    create` replies with one — so a digits-only value is taken as an id, the way
    records are already addressed. `None` stays `None`: no group at all.
    """
    if isinstance(group, str):
        group = group.strip()
        if not group:
            return None
        if group.isdigit():
            return int(group)

    return group


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
    # Limits
    # ------------------------------------------------------------------

    def _max_records(self, scope: str = USER_SCOPE) -> int:
        if scope == SERVER_SCOPE:
            return getattr(settings, "inflation_max_records_per_server", 500)

        return getattr(settings, "inflation_max_records_per_user", 200)

    def _max_groups(self, scope: str = USER_SCOPE) -> int:
        if scope == SERVER_SCOPE:
            return getattr(settings, "inflation_max_groups_per_server", 50)

        return getattr(settings, "inflation_max_groups_per_user", 20)

    # ------------------------------------------------------------------
    # Records
    # ------------------------------------------------------------------

    def add_record(
        self,
        owner_id: int,
        amount: str,
        date: datetime.date,
        comment: str = "",
        scope: str = USER_SCOPE,
        group: int | str | None = None,
    ) -> dict:
        calculator = self._get_calculator(owner_id, scope)

        max_records = self._max_records(scope)
        if calculator.records_count >= max_records:
            raise ValidationError(f"Record limit reached ({max_records}). Delete something first.")

        # Keyword, not positional: the calculator's third positional is `comment`.
        return calculator.add_record(amount, date, comment, group=as_group_ref(group))

    def delete_record(self, owner_id: int, record_id: int, scope: str = USER_SCOPE) -> dict:
        return self._get_calculator(owner_id, scope).delete_record(record_id)

    def withdraw(
        self,
        owner_id: int,
        amount: str,
        group: int | str | None = None,
        scope: str = USER_SCOPE,
    ) -> dict:
        """
        Spend money out of a group, oldest lots first.

        `group=None` withdraws from the ungrouped lots. A deposit covering the
        group never blocks the call — the returned `warning` names the interest
        it puts at risk and the owner decides.
        """
        return self._get_calculator(owner_id, scope).withdraw(amount, as_group_ref(group))

    def list_records(self, owner_id: int, scope: str = USER_SCOPE) -> list[dict]:
        return self._get_calculator(owner_id, scope).get_records()

    def count_records(self, owner_id: int, scope: str = USER_SCOPE) -> int:
        return self._get_calculator(owner_id, scope).records_count

    def get_groups_report(
        self,
        owner_id: int,
        scope: str = USER_SCOPE,
        *,
        detailed: bool = True,
        collapse_interest: bool = True,
    ) -> dict:
        """
        Every group's sub-total plus the grand total, records included when asked.

        This is the only report the bot builds: with no groups it degenerates to
        one `ungrouped` node holding everything, and its totals are the same ones
        `InflationCalculator.get_report()` would produce.

        `collapse_interest` folds a group's deposit-interest lots into one row.
        It defaults on because a closed year of monthly capitalization is twelve
        lots, and the totals are identical either way — the library sums the
        individual adjusted values, so a folded node still reconciles.
        """
        return self._get_calculator(owner_id, scope).get_groups_report(
            detailed=detailed, collapse_interest=collapse_interest
        )

    # ------------------------------------------------------------------
    # Budget groups
    # ------------------------------------------------------------------

    def create_group(self, owner_id: int, name: str, comment: str = "", scope: str = USER_SCOPE) -> dict:
        calculator = self._get_calculator(owner_id, scope)

        max_groups = self._max_groups(scope)
        if calculator.groups_count >= max_groups:
            raise ValidationError(f"Group limit reached ({max_groups}). Delete one first.")

        return dict(calculator.create_group(name, comment))

    def rename_group(self, owner_id: int, group: int | str, new_name: str, scope: str = USER_SCOPE) -> dict:
        return dict(self._get_calculator(owner_id, scope).rename_group(as_group_ref(group), new_name))

    def delete_group(
        self,
        owner_id: int,
        group: int | str,
        *,
        delete_records: bool = False,
        scope: str = USER_SCOPE,
    ) -> dict:
        calculator = self._get_calculator(owner_id, scope)

        return dict(calculator.delete_group(as_group_ref(group), delete_records=delete_records))

    def assign_record(self, owner_id: int, record_id: int, group: int | str | None, scope: str = USER_SCOPE) -> dict:
        return dict(self._get_calculator(owner_id, scope).assign_record_to_group(record_id, as_group_ref(group)))

    def list_groups(self, owner_id: int, scope: str = USER_SCOPE) -> list[dict]:
        """
        The owner's groups, as copies.

        The calculator hands out its live inner dicts, and a cog mutating one
        would change stored state without ever firing the save callback.
        """
        return [dict(group) for group in self._get_calculator(owner_id, scope).get_groups()]

    # ------------------------------------------------------------------
    # Deposits
    # ------------------------------------------------------------------

    def attach_deposit(
        self,
        owner_id: int,
        group: int | str,
        *,
        annual_rate_percent: str,
        start_date: datetime.date,
        end_date: datetime.date,
        capitalization: str = DEFAULT_CAPITALIZATION,
        tax_percent: str | None = None,
        early_withdrawal_rate_percent: str | None = None,
        tax_withheld_on_payout: bool = True,
        round_each_period: bool = True,
        comment: str = "",
        scope: str = USER_SCOPE,
    ) -> dict:
        """
        Put a group under a deposit.

        A deposit covers the group's whole balance: every lot is a contribution
        dated when the money arrived, so a top-up earns from its own date. The
        terms are validated by `DepositTerms` itself, which raises the same
        `ValidationError` every other call here does.
        """
        terms = DepositTerms(
            annual_rate_percent=annual_rate_percent,
            start_date=start_date,
            end_date=end_date,
            capitalization=capitalization,
            # The library's defaults are the Ukrainian ones, so an unset option
            # has to fall through to them rather than to a value of our own.
            tax_percent=DEFAULT_TAX_PERCENT if tax_percent is None else tax_percent,
            early_withdrawal_rate_percent=(
                Decimal("0") if early_withdrawal_rate_percent is None else early_withdrawal_rate_percent
            ),
            tax_withheld_on_payout=tax_withheld_on_payout,
            round_each_period=round_each_period,
            comment=comment,
        )

        return dict(self._get_calculator(owner_id, scope).attach_deposit(as_group_ref(group), terms))

    def detach_deposit(self, owner_id: int, group: int | str, scope: str = USER_SCOPE) -> dict:
        """Drop a deposit without crediting anything — for undoing a mistake."""
        return dict(self._get_calculator(owner_id, scope).detach_deposit(as_group_ref(group)))

    def find_group(self, owner_id: int, group: int | str, scope: str = USER_SCOPE) -> dict | None:
        """The stored group behind a reference, or None. Copied, like `list_groups`."""
        found = self._get_calculator(owner_id, scope).find_group(as_group_ref(group))

        return dict(found) if found else None

    def get_deposit_terms(self, owner_id: int, group: int | str, scope: str = USER_SCOPE) -> DepositTerms | None:
        return self._get_calculator(owner_id, scope).get_deposit_terms(as_group_ref(group))

    def get_deposit_projection(
        self,
        owner_id: int,
        group: int | str,
        on_date: datetime.date | None = None,
        scope: str = USER_SCOPE,
    ) -> DepositResult | None:
        """Value a running deposit without closing it. Nothing is stored."""
        return self._get_calculator(owner_id, scope).get_deposit_projection(as_group_ref(group), on_date)

    def close_deposit(
        self,
        owner_id: int,
        group: int | str,
        on_date: datetime.date | None = None,
        scope: str = USER_SCOPE,
    ) -> DepositResult:
        """
        Close a deposit and credit its interest into the group.

        The interest lands as one dated lot per capitalization period, so a year
        of monthly capitalization adds twelve records at once. The library adds
        them through its own `add_record`, which does not know about this bot's
        per-owner limits — so the limit is checked here, before the close, or it
        would be silently exceeded.
        """
        calculator = self._get_calculator(owner_id, scope)
        group_ref = as_group_ref(group)

        projection = calculator.get_deposit_projection(group_ref, on_date)
        if projection is None:
            raise ValidationError("This group has no deposit to close.")

        new_records = sum(1 for period in projection.periods if period.net_interest > 0)
        max_records = self._max_records(scope)
        if calculator.records_count + new_records > max_records:
            raise ValidationError(
                f"Closing this deposit would add {new_records} record(s) and pass the "
                f"limit ({max_records}). Delete something first."
            )

        return calculator.close_deposit(group_ref, on_date)

    # ------------------------------------------------------------------
    # Report preferences
    # ------------------------------------------------------------------

    def _registry(self, scope: str = USER_SCOPE) -> PersonalChannelRegistry:
        return self.server_channels if scope == SERVER_SCOPE else self.channels

    def _remember(self, owner_id: int, key: str, value, scope: str = USER_SCOPE) -> bool:
        """
        Store one preference in the owner's registry entry.

        Returns False when the owner has no entry — every preference here is
        about a report channel, so there is nowhere to keep it until one exists.
        """
        registry = self._registry(scope)
        data = registry.load()
        entry = data.get(str(owner_id))
        if entry is None:
            return False

        entry[key] = value
        registry.save(data)

        return True

    def get_view_mode(self, owner_id: int, scope: str = USER_SCOPE) -> str:
        """The owner's chosen view mode, or the configured default."""
        entry = self._registry(scope).get(owner_id) or {}
        mode = entry.get(VIEW_MODE_KEY)

        return mode if mode in VIEW_MODES else get_default_view()

    def set_view_mode(self, owner_id: int, mode: str, scope: str = USER_SCOPE) -> bool:
        """Remember how this owner's report should render. False: no entry yet."""
        if mode not in VIEW_MODES:
            raise ValidationError(f"Unknown view mode: {mode!r}")

        return self._remember(owner_id, VIEW_MODE_KEY, mode, scope)

    def get_collapse_interest(self, owner_id: int, scope: str = USER_SCOPE) -> bool:
        """Whether this owner's report folds deposit-interest lots into one row."""
        entry = self._registry(scope).get(owner_id) or {}
        collapse = entry.get(COLLAPSE_INTEREST_KEY)

        if isinstance(collapse, bool):
            return collapse

        return bool(getattr(settings, "inflation_collapse_interest", True))

    def set_collapse_interest(self, owner_id: int, collapse: bool, scope: str = USER_SCOPE) -> bool:
        """Remember whether to fold interest rows. False: no entry yet."""
        return self._remember(owner_id, COLLAPSE_INTEREST_KEY, bool(collapse), scope)

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
                    profile = json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                logger.warning(f"Could not read records file {path}: {e}")
                continue

            # A profile is `{"records": [...], "groups": [...]}`; the legacy
            # format was a bare list of records. Either way it is the records
            # that decide, not the file being non-empty — a profile holding
            # nothing but groups is truthy and has nothing to report on.
            records = profile.get("records", []) if isinstance(profile, dict) else profile
            if records:
                count += 1

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

    def get_rate_status(self) -> tuple[bool, list[str]]:
        """
        `(has any CPI data, missing "YYYY-MM" months)`.

        Both answers come off one calculator because building it re-reads the
        rates file, and every report render asks this question.
        """
        calculator = self._rates_calculator()

        return calculator.has_inflation_data, calculator.find_data_gaps()

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
