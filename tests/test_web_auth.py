"""Tests for signing in to the bot's web pages: the session cookie and the OAuth2 flow.

`core.web_auth` needs nothing but `aiohttp`, so this suite runs on a bare
checkout. Discord itself is a local aiohttp server standing in for its two
endpoints, never the network.
"""

import sys
import unittest
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestServer

# Setup path so we can import from src
src_root = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(src_root))

import aiohttp  # noqa: E402

from core.web_auth import DiscordOAuth, OAuthError, SessionSigner, new_state  # noqa: E402

NOW = 1_800_000_000.0


class TestSessionSigner(unittest.TestCase):
    def setUp(self):
        self.signer = SessionSigner("secret", max_age_seconds=3600)

    def test_a_cookie_reads_back_as_what_was_signed(self):
        token = self.signer.dumps({"uid": "42", "name": "Олива"}, now=NOW)
        data = self.signer.loads(token, now=NOW + 10)
        self.assertEqual(data["uid"], "42")
        self.assertEqual(data["name"], "Олива")

    def test_a_cookie_expires_after_its_max_age(self):
        token = self.signer.dumps({"uid": "42"}, now=NOW)
        self.assertIsNotNone(self.signer.loads(token, now=NOW + 3599))
        self.assertIsNone(self.signer.loads(token, now=NOW + 3600))

    def test_an_edited_payload_is_refused(self):
        token = self.signer.dumps({"uid": "42"}, now=NOW)
        forged = self.signer.dumps({"uid": "43"}, now=NOW)
        # Someone else's payload under this cookie's signature.
        spliced = forged.split(".")[0] + "." + token.split(".")[1]
        self.assertIsNone(self.signer.loads(spliced, now=NOW))

    def test_an_edited_signature_is_refused(self):
        payload, signature = self.signer.dumps({"uid": "42"}, now=NOW).split(".")
        flipped = ("A" if signature[0] != "A" else "B") + signature[1:]
        self.assertIsNone(self.signer.loads(f"{payload}.{flipped}", now=NOW))

    def test_a_cookie_signed_with_another_secret_is_refused(self):
        token = SessionSigner("other", max_age_seconds=3600).dumps({"uid": "42"}, now=NOW)
        self.assertIsNone(self.signer.loads(token, now=NOW))

    def test_anything_that_is_not_a_cookie_is_refused(self):
        for junk in (None, "", "abc", "a.b.c", "ünïcode.sïgnature", "e30.AAAA"):
            with self.subTest(junk=junk):
                self.assertIsNone(self.signer.loads(junk, now=NOW))

    def test_there_is_no_signing_without_a_secret(self):
        with self.assertRaises(ValueError):
            SessionSigner("", max_age_seconds=60)

    def test_every_sign_in_gets_its_own_state(self):
        self.assertNotEqual(new_state(), new_state())


class FakeDiscord:
    """The two endpoints of Discord's API the code flow reaches."""

    def __init__(self, token_status=200, user_status=200, user=None):
        self.token_status = token_status
        self.user_status = user_status
        self.user = user if user is not None else {"id": "123456789012345678", "username": "olive"}
        self.token_forms = []
        self.auth_headers = []

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/api/oauth2/token", self.token)
        app.router.add_get("/api/users/@me", self.me)
        return app

    async def token(self, request):
        self.token_forms.append(dict(await request.post()))
        if self.token_status != 200:
            return web.json_response({"error": "invalid_grant"}, status=self.token_status)
        return web.json_response({"access_token": "discord-access", "token_type": "Bearer"})

    async def me(self, request):
        self.auth_headers.append(request.headers.get("Authorization"))
        if self.user_status != 200:
            return web.json_response({"message": "401: Unauthorized"}, status=self.user_status)
        return web.json_response(self.user)


class TestDiscordOAuth(unittest.IsolatedAsyncioTestCase):
    async def start(self, discord: FakeDiscord) -> DiscordOAuth:
        self.server = TestServer(discord.app())
        await self.server.start_server()
        self.addAsyncCleanup(self.server.close)
        return DiscordOAuth(
            "client-id",
            "client-secret",
            "https://olive.example/oauth/callback",
            api_base=str(self.server.make_url("/api")),
        )

    def test_the_authorize_url_asks_for_the_identity_only(self):
        url = DiscordOAuth("client-id", "secret", "https://olive.example/oauth/callback").authorize_url("xyz")
        self.assertTrue(url.startswith("https://discord.com/oauth2/authorize?"))
        self.assertIn("client_id=client-id", url)
        self.assertIn("scope=identify", url)
        self.assertIn("state=xyz", url)
        self.assertIn("redirect_uri=https%3A%2F%2Folive.example%2Foauth%2Fcallback", url)
        self.assertNotIn("secret", url)

    async def test_a_code_is_traded_for_the_user_it_was_issued_to(self):
        discord = FakeDiscord()
        oauth = await self.start(discord)

        async with aiohttp.ClientSession() as http:
            user = await oauth.fetch_user("the-code", http)

        self.assertEqual(user["id"], "123456789012345678")
        form = discord.token_forms[0]
        self.assertEqual(form["grant_type"], "authorization_code")
        self.assertEqual(form["code"], "the-code")
        self.assertEqual(form["redirect_uri"], "https://olive.example/oauth/callback")
        self.assertEqual(discord.auth_headers, ["Bearer discord-access"])

    async def test_a_refused_code_is_an_oauth_error(self):
        oauth = await self.start(FakeDiscord(token_status=400))
        async with aiohttp.ClientSession() as http:
            with self.assertRaises(OAuthError):
                await oauth.fetch_user("stale", http)

    async def test_a_refused_user_lookup_is_an_oauth_error(self):
        oauth = await self.start(FakeDiscord(user_status=401))
        async with aiohttp.ClientSession() as http:
            with self.assertRaises(OAuthError):
                await oauth.fetch_user("code", http)

    async def test_a_user_without_an_id_is_an_oauth_error(self):
        oauth = await self.start(FakeDiscord(user={"username": "ghost"}))
        async with aiohttp.ClientSession() as http:
            with self.assertRaises(OAuthError):
                await oauth.fetch_user("code", http)

    async def test_discord_out_of_reach_is_an_oauth_error(self):
        oauth = await self.start(FakeDiscord())
        await self.server.close()

        async with aiohttp.ClientSession() as http:
            with self.assertRaises(OAuthError):
                await oauth.fetch_user("code", http)


if __name__ == "__main__":
    unittest.main()
