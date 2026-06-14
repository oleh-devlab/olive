embeds_to_send = {
    "server_load": None,
    "currency": None,
    "battery": None,
    "active_cogs": None,
    "uptime": None
}

configLock = None

llm_client = None

active_cogs_list = {}

_phrases = {}

# TODO: Fix this problem:
llm_cogs = ["olive"] # The list of cogs that depend on LLMClient (Google GenAI) is about AI. If `settings.enable_llm_cogs` is disabled, these cogs won't be loaded.