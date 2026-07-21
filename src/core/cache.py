embeds_to_send = {
    "server_load": None,
    "currency": None,
    "battery": None,
    "active_cogs": None,
    "uptime": None,
    "llm_limits": None,
    "llm_context": None,
    "usage_stats": None,
}

configLock = None

llm_pool = None  # LLMClientPool instance
llm_consent_manager = None

active_cogs_list = {}

_phrases = {}

schedule_states = {}
