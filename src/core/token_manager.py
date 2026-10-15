import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


class TokenRegistry:
    """Centralized token storage. Reads all secrets from a single tokens.json file."""

    def __init__(self, config_path: str = "tokens.json"):
        self.config_path = Path(__file__).resolve().parent.parent / config_path

        self._discord_token: str | None = None
        self._genai_tokens: dict[str, str] = {}
        self._discord_oauth: dict[str, str] = {}
        self._web_session_secret: str | None = None

        self._load_tokens()

    def _load_tokens(self):
        if not self.config_path.exists():
            logger.warning("Tokens file not found at %s. Will fallback to environment variables.", self.config_path)
            return

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if not isinstance(data, dict):
                logger.error("tokens.json must be a JSON object, got %s", type(data).__name__)
                return

            # Discord bot token (simple string)
            discord_raw = data.get("discord", "")
            if discord_raw and isinstance(discord_raw, str):
                self._discord_token = discord_raw.strip()

            # GenAI tokens (dict of role -> key)
            genai_data = data.get("genai", {})
            if isinstance(genai_data, dict):
                self._genai_tokens = {str(k): str(v).strip() for k, v in genai_data.items() if v}

            # The OAuth2 application the schedule's web editor signs users in with
            oauth_data = data.get("discord_oauth", {})
            if isinstance(oauth_data, dict):
                self._discord_oauth = {str(k): str(v).strip() for k, v in oauth_data.items() if v}

            session_secret = data.get("web_session_secret", "")
            if session_secret and isinstance(session_secret, str):
                self._web_session_secret = session_secret.strip()

            logger.info("Loaded tokens from %s", self.config_path)
        except Exception as e:
            logger.error("Error loading tokens from %s: %s", self.config_path, e)

    def get_discord_token(self) -> str | None:
        """Get the Discord bot token."""
        return self._discord_token or os.environ.get("DISCORD_BOT_TOKEN")

    def get_genai_token(self, role: str = "default") -> str | None:
        """Get Google GenAI token by role. Falls back to 'default' role if the requested role is not found."""
        token = self._genai_tokens.get(role)
        if token:
            return token

        if role != "default":
            default_token = self._genai_tokens.get("default")
            if default_token:
                logger.debug("GenAI role '%s' not found, falling back to 'default'", role)
                return default_token

        return os.environ.get("GENAI_API_KEY")

    def get_discord_oauth(self) -> tuple[str, str] | None:
        """The `(client_id, client_secret)` pair of the bot's OAuth2 application, if both are set."""
        client_id = self._discord_oauth.get("client_id")
        client_secret = self._discord_oauth.get("client_secret")
        if client_id and client_secret:
            return client_id, client_secret
        return None

    def get_web_session_secret(self) -> str | None:
        """The key web sessions are signed with. Without one, they last only until a restart."""
        return self._web_session_secret


# Global singleton
token_registry = TokenRegistry()
