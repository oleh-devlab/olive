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

# See core/llm_config.py: models, priorities and system instructions, by guild id.
_llm_config = {}

# channel_id -> core.paged_message.PagedChannelMessage
channel_states = {}

# management channel_id -> user_id, for the cogs that listen in those channels
tasks_channels = {}
