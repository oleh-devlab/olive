from disnake.ext import commands, tasks
from datetime import datetime, timedelta
import os
import json
import disnake
import aiohttp
from core.utils import format_embed_data, get_phrases
from aiohttp import ClientTimeout

import traceback

import core.cache
from settings import channels
from core.task_handler import ResilientTaskHandler

class CurrencyEmbed(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.usd_eur_test = {'usd': 0, 'eur':0}

        self.CACHE_FILE = "currency_cache.json"
        self.LAST_UPDATE_FILE = "last_currency_update.txt" # File to store the timestamp of the last successful update
        self.url = "https://bank.gov.ua/NBUStatService/v1/statdirectory/exchangenew?json"
        self.HTTP_TIMEOUT = ClientTimeout(total=10)
        
        self.error_handler = ResilientTaskHandler(bot, self.currency_embed, "CurrencyEmbedLoop")

        self.currency_embed.start()

    def cog_unload(self):
        self.currency_embed.stop()
    
    @tasks.loop(seconds=10)
    async def currency_embed(self):
        currencies = None
        now = datetime.now()
        
        # Try to read from cache
        cached = None
        try:
            if os.path.exists(self.CACHE_FILE):
                with open(self.CACHE_FILE, "r", encoding="utf-8") as f:
                    cached = json.load(f)
        except Exception:
            cached = None
            
        # Read last update time
        if os.path.exists(self.LAST_UPDATE_FILE):
            with open(self.LAST_UPDATE_FILE, "r", encoding="utf-8") as f:
                try:
                    last_update = datetime.strptime(f.read().strip(), "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    last_update = datetime.min
        else:
            last_update = datetime.min

        # check if cache is still valid (less than 12 hours old)
        if cached and (now - last_update) < timedelta(hours=12):
            currencies = cached
        else: # Try to get new data from bank
            try:
                print("Run currency update.")
                async with aiohttp.ClientSession(timeout=self.HTTP_TIMEOUT) as session:
                    async with session.get(self.url) as response:
                        data = await response.json()

                currencies = {}
                for item in data:
                    if item.get("cc") in ["USD", "EUR"]:
                        currencies[item.get("cc")] = {"rate": item.get("rate"), "date": item.get("exchangedate")}

                # Saving to cache
                try:
                    with open(self.CACHE_FILE, "w", encoding="utf-8") as f:
                        json.dump(currencies, f)
                    with open(self.LAST_UPDATE_FILE, "w", encoding="utf-8") as f:
                        f.write(now.strftime("%Y-%m-%d %H:%M:%S"))
                except Exception as e:
                    print(f"[send] Error writing cache: {e}")
            except Exception as e:
                print(f"[send] Error with currency update: {e}")
                if cached:
                    currencies = cached
                else:
                    return

        if currencies:
            usd = currencies.get("USD")
            eur = currencies.get("EUR")

            usd_rate = usd.get('rate') if isinstance(usd, dict) else None
            eur_rate = eur.get('rate') if isinstance(eur, dict) else None
            usd_date = usd.get('date') if isinstance(usd, dict) else 'N/A'
            eur_date = eur.get('date') if isinstance(eur, dict) else 'N/A'

            if usd_rate is not None and eur_rate is not None and (self.usd_eur_test != {'usd': usd_rate, 'eur': eur_rate}):
                print(f"USD: {usd_rate} грн, дата: {usd_date}")
                print(f"EUR: {eur_rate} грн, дата: {eur_date}")
                self.usd_eur_test = {'usd': usd_rate, 'eur': eur_rate}
        
        raw_embed_data = get_phrases().get("currency_embed", {}).get("currency_embed_data", { "title": "Економіка" })
        formatted_embed_data = format_embed_data(raw_embed_data, usd_rate=(usd_rate if usd_rate is not None else 'N/A'), usd_date=usd_date, eur_rate=(eur_rate if eur_rate is not None else 'N/A'), eur_date=eur_date)
        embed0 = disnake.Embed.from_dict(formatted_embed_data)
        
        core.cache.embeds_to_send["currency"] = embed0

    @currency_embed.error
    async def on_currency_error(self, error):
        await self.error_handler.handle_error(error)

def setup(bot):
    bot.add_cog(CurrencyEmbed(bot))