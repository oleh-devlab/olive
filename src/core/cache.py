import asyncio

embeds_to_send = {
    "server_load": None,
    "currency": None,
    "battery": None,
    "uptime": None,
    "active_cogs": None
}

configLock = None

googleClient = None

active_cogs_list = {}

phrases = {}