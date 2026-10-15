"""Signing in to the bot's web pages with a Discord account.

Two pieces, both free of `settings` so they stay unit-testable: a signed
cookie that says who a browser belongs to, and the OAuth2 code flow that finds
out in the first place. Only the `identify` scope is asked for -- the page needs
the user's id and nothing else -- and Discord's access token is dropped as soon
as the id is known, so there is no third-party credential on disk to leak.
"""

import base64
import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import urlencode

import aiohttp

DISCORD_API = "https://discord.com/api"
DISCORD_AUTHORIZE_URL = "https://discord.com/oauth2/authorize"


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def new_state() -> str:
    """An unguessable value tying an OAuth callback to the browser that set off."""
    return secrets.token_urlsafe(32)


class SessionSigner:
    """
    A session as a cookie the server can verify but the browser cannot forge.

    Stateless on purpose: nothing to store, and a session outlives a restart of
    the bot as long as the secret does. The price is that one cannot be revoked
    before it expires -- logging out only drops the browser's copy.
    """

    def __init__(self, secret: str | bytes, max_age_seconds: int):
        if not secret:
            raise ValueError("A session secret is required.")

        self.secret = secret.encode("utf-8") if isinstance(secret, str) else secret
        self.max_age = max_age_seconds

    def _sign(self, payload: str) -> str:
        return _b64encode(hmac.new(self.secret, payload.encode("ascii"), hashlib.sha256).digest())

    def dumps(self, data: dict, now: float | None = None) -> str:
        now = time.time() if now is None else now
        body = dict(data, exp=int(now) + self.max_age)
        payload = _b64encode(json.dumps(body, separators=(",", ":")).encode("utf-8"))
        return f"{payload}.{self._sign(payload)}"

    def loads(self, token: str | None, now: float | None = None) -> dict | None:
        """The data a cookie was signed with, or `None` for anything else."""
        if not token or token.count(".") != 1:
            return None

        payload, signature = token.split(".")
        try:
            expected = self._sign(payload)
        except UnicodeEncodeError:
            return None

        if not hmac.compare_digest(signature.encode("utf-8"), expected.encode("ascii")):
            return None

        try:
            data = json.loads(_b64decode(payload))
        except ValueError:
            return None

        now = time.time() if now is None else now
        if not isinstance(data, dict) or not isinstance(data.get("exp"), int) or data["exp"] <= now:
            return None

        return data


class OAuthError(Exception):
    """Discord would not tell us who signed in."""


class DiscordOAuth:
    """The authorization-code flow, down to a Discord user."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        api_base: str = DISCORD_API,
        authorize_url: str = DISCORD_AUTHORIZE_URL,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.api_base = api_base.rstrip("/")
        self.authorize_base = authorize_url

    def authorize_url(self, state: str) -> str:
        query = urlencode(
            {
                "client_id": self.client_id,
                "response_type": "code",
                "redirect_uri": self.redirect_uri,
                "scope": "identify",
                "state": state,
                # Someone who has already authorized the app is sent straight
                # back instead of being shown the same consent screen again.
                "prompt": "none",
            }
        )
        return f"{self.authorize_base}?{query}"

    async def fetch_user(self, code: str, session: aiohttp.ClientSession) -> dict:
        """Trade the callback's code for the user it was issued to."""
        token_form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }

        try:
            async with session.post(f"{self.api_base}/oauth2/token", data=token_form) as response:
                if response.status != 200:
                    raise OAuthError(f"Token exchange failed with HTTP {response.status}.")
                token = await response.json()

            access_token = token.get("access_token")
            if not access_token:
                raise OAuthError("Discord returned no access token.")

            headers = {"Authorization": f"Bearer {access_token}"}
            async with session.get(f"{self.api_base}/users/@me", headers=headers) as response:
                if response.status != 200:
                    raise OAuthError(f"Fetching the user failed with HTTP {response.status}.")
                user = await response.json()
        except (aiohttp.ClientError, TimeoutError, ValueError) as e:
            raise OAuthError(f"Could not reach Discord: {e}") from e

        if not isinstance(user, dict) or not str(user.get("id", "")).isdigit():
            raise OAuthError("Discord returned a user without an id.")

        return user
