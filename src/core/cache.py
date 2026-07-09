from modules.llm_consent_manager import LLMConsentManager

embeds_to_send = {
    "server_load": None,
    "currency": None,
    "battery": None,
    "active_cogs": None,
    "uptime": None,
    "llm_limits": None,
    "llm_context": None,
}

configLock = None

llm_client = None
llm_consent = LLMConsentManager()
openai_context_manager = None

active_cogs_list = {}

_phrases = {}

schedule_states = {}
