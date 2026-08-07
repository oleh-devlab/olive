import json
import logging
import os
from datetime import datetime, timedelta
from typing import ClassVar

import aiohttp
from aiohttp import ClientTimeout
from disnake.ext import commands

from core.embed_cog import BaseEmbedCog

logger = logging.getLogger(__name__)


class CurrencyEmbed(BaseEmbedCog):
    embed_key = "currency"
    phrases_section = "currency_embed"
    phrases_key = "currency_embed_data"
    settings_key = "currency_update_seconds"
    default_seconds = 10
    fallback_embed: ClassVar[dict] = {"title": ":dollar: | Currency"}

    def __init__(self, bot):
        self.usd_eur_test = {"usd": 0, "eur": 0}

        self.CACHE_FILE = "currency_cache.json"
        self.url = "https://bank.gov.ua/NBUStatService/v1/statdirectory/exchangenew?json"
        self.HTTP_TIMEOUT = ClientTimeout(total=10)

        self.last_update = None
        self.cached_currencies = None

        super().__init__(bot)

    def _prime_cache_from_disk(self):
        """Seed the in-memory cache from disk once, tolerating the pre-versioned file layout."""
        if not os.path.exists(self.CACHE_FILE):
            self.cached_currencies = None
            self.last_update = datetime.min
            return

        try:
            with open(self.CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Handle old structure vs new structure
            if "currencies" in data and "last_update" in data:
                self.cached_currencies = data["currencies"]
                self.last_update = datetime.strptime(data["last_update"], "%Y-%m-%d %H:%M:%S")
            else:
                self.cached_currencies = data  # Old format, assume it's just the currency dict
                self.last_update = datetime.min  # Force update to rewrite in new format
        except Exception:
            self.cached_currencies = None
            self.last_update = datetime.min

    async def _fetch_currencies(self):
        logger.debug("Run currency update.")

        async with aiohttp.ClientSession(timeout=self.HTTP_TIMEOUT) as session, session.get(self.url) as response:
            data = await response.json()

        return {
            item.get("cc"): {"rate": item.get("rate"), "date": item.get("exchangedate")}
            for item in data
            if item.get("cc") in ["USD", "EUR"]
        }

    def _write_cache(self, currencies, now):
        try:
            with open(self.CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(
                    {"last_update": now.strftime("%Y-%m-%d %H:%M:%S"), "currencies": currencies},
                    f,
                    ensure_ascii=False,
                    indent=4,
                )
        except Exception as e:
            logger.error(f"[send] Error writing cache: {e}")

    async def get_data(self):
        now = datetime.now()

        if self.last_update is None:
            self._prime_cache_from_disk()

        # check if cache is still valid (less than 12 hours old)
        if self.cached_currencies and (now - self.last_update) < timedelta(hours=12):
            currencies = self.cached_currencies
        else:  # Try to get new data from bank
            try:
                currencies = await self._fetch_currencies()

                # Saving to cache
                self.cached_currencies = currencies
                self.last_update = now
                self._write_cache(currencies, now)
            except Exception as e:
                logger.error(f"[send] Error with currency update: {e}")
                if not self.cached_currencies:
                    return None
                currencies = self.cached_currencies

        if not currencies:
            logger.warning("Neither USD nor EUR came back from the bank; keeping the previous embed.")
            return None

        usd = currencies.get("USD")
        eur = currencies.get("EUR")

        usd_rate = usd.get("rate") if isinstance(usd, dict) else None
        eur_rate = eur.get("rate") if isinstance(eur, dict) else None
        usd_date = usd.get("date") if isinstance(usd, dict) else "N/A"
        eur_date = eur.get("date") if isinstance(eur, dict) else "N/A"

        if usd_rate is not None and eur_rate is not None and (self.usd_eur_test != {"usd": usd_rate, "eur": eur_rate}):
            logger.debug(f"USD: {usd_rate} грн, дата: {usd_date}")
            logger.debug(f"EUR: {eur_rate} грн, дата: {eur_date}")
            self.usd_eur_test = {"usd": usd_rate, "eur": eur_rate}

        return {
            "usd_rate": usd_rate if usd_rate is not None else "N/A",
            "usd_date": usd_date,
            "eur_rate": eur_rate if eur_rate is not None else "N/A",
            "eur_date": eur_date,
        }


def setup(bot: commands.Bot) -> None:
    bot.add_cog(CurrencyEmbed(bot))
