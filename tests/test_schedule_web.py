"""Tests for the schedule's web editor: signing in, and the task table's API.

Every request runs through the real `ScheduleProvider` and validators against a
temporary data directory, so what the page writes is checked by reading it back
the way `/task list` and the solver do. Discord is a fake that hands back a
fixed user; `core.web_auth`'s own suite covers the real exchange.

Reaching the provider pulls in the vendored dataclasses -- whose package
re-exports the scheduler, so `ortools` comes with them -- and, through
`core.personal_channels`, `disnake`. This suite needs all three present.
"""

import datetime
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

import aiohttp
from aiohttp.test_utils import TestClient, TestServer, unused_port

# Setup path so we can import from src
src_root = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(src_root))

# The provider imports the operator's `settings` module, absent from a checkout.
sys.modules.setdefault("settings", types.ModuleType("settings"))

import settings  # noqa: E402

import cogs.schedule.web as web_cog  # noqa: E402
from core.time_utils import tz  # noqa: E402
from core.web_auth import OAuthError, SessionSigner  # noqa: E402
from modules import schedule_provider  # noqa: E402
from modules import schedule_web  # noqa: E402
from modules.schedule_web import SESSION_COOKIE, STATE_COOKIE, ScheduleWebApp  # noqa: E402

USER = 123456789012345678
OTHER_USER = 876543210987654321
# Plain http, because that is what the test server speaks: over https the
# cookies are marked Secure, and a client on http rightly never sends them back.
BASE_URL = "http://olive.example"


class FakeOAuth:
    """Discord's half of the code flow, reduced to: this code is this user."""

    def __init__(self):
        self.user = {"id": str(USER), "username": "olive", "global_name": "Olive"}
        self.fail = False

    def authorize_url(self, state: str) -> str:
        return f"https://discord.example/oauth2/authorize?state={state}"

    async def fetch_user(self, code, session):
        if self.fail:
            raise OAuthError("Discord said no.")
        return self.user


def row(**overrides) -> dict:
    data = {
        "name": "Write report",
        "description": "",
        "duration": 90,
        "priority": 2,
        "deadline": None,
        "max_chunk_duration": 45,
        "min_chunk_duration": None,
        "break_duration": 10,
        "depends_on": [],
    }
    data.update(overrides)
    return data


def stand_in_page(directory: Path) -> Path:
    """
    A page where the built one would be: the real page is built from web/ and
    is not in git, and what these tests check is how the server hands it out.
    """
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "index.html").write_text("<!doctype html><title>Schedule</title><div id=root></div>")
    return directory


class WebTestCase(unittest.IsolatedAsyncioTestCase):
    base_url = BASE_URL

    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.object(schedule_provider, "get_data_dir", return_value=Path(tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

        self.provider = schedule_provider.ScheduleProvider()
        self.oauth = FakeOAuth()
        self.allowed = {USER, OTHER_USER}
        self.redrawn = []

        app = ScheduleWebApp(
            provider=self.provider,
            oauth=self.oauth,
            signer=SessionSigner("test-secret", max_age_seconds=3600),
            base_url=self.base_url,
            is_allowed=lambda user_id: user_id in self.allowed,
            on_change=self.redrawn.append,
            static_dir=stand_in_page(Path(tmp.name) / "page"),
        ).make_app()

        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def sign_in(self):
        """The whole round trip: to Discord and back through the callback."""
        response = await self.client.get("/login", allow_redirects=False)
        state = parse_qs(urlsplit(response.headers["Location"]).query)["state"][0]
        response = await self.client.get(f"/oauth/callback?code=ok&state={state}", allow_redirects=False)
        return response

    async def tasks(self) -> list[dict]:
        response = await self.client.get("/api/tasks")
        self.assertEqual(response.status, 200)
        return (await response.json())["tasks"]


class TestSignIn(WebTestCase):
    async def test_the_page_is_served_with_a_strict_policy(self):
        response = await self.client.get("/")
        self.assertEqual(response.status, 200)
        self.assertIn("<div id=root>", await response.text())
        self.assertIn("script-src 'self'", response.headers["Content-Security-Policy"])
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")

    async def test_the_api_refuses_a_browser_that_has_not_signed_in(self):
        for method, path in (("GET", "/api/me"), ("GET", "/api/tasks"), ("POST", "/api/tasks")):
            with self.subTest(path=path):
                response = await self.client.request(method, path, json=row())
                self.assertEqual(response.status, 401)
                self.assertEqual(response.headers["Cache-Control"], "no-store")

    async def test_signing_in_leads_to_discord_and_back(self):
        response = await self.client.get("/login", allow_redirects=False)
        self.assertEqual(response.status, 302)
        self.assertTrue(response.headers["Location"].startswith("https://discord.example/"))

        response = await self.sign_in()
        self.assertEqual(response.status, 302)
        self.assertEqual(response.headers["Location"], "/")
        session_cookie = response.cookies[SESSION_COOKIE]
        self.assertTrue(session_cookie["httponly"])
        self.assertEqual(session_cookie["samesite"], "Lax")

        response = await self.client.get("/api/me")
        self.assertEqual(await response.json(), {"id": str(USER), "name": "Olive"})

    async def test_a_callback_that_did_not_start_here_signs_no_one_in(self):
        await self.client.get("/login", allow_redirects=False)
        response = await self.client.get("/oauth/callback?code=ok&state=forged", allow_redirects=False)
        self.assertEqual(response.headers["Location"], "/?error=state")
        self.assertEqual((await self.client.get("/api/me")).status, 401)

    async def test_a_callback_without_a_state_cookie_signs_no_one_in(self):
        response = await self.client.get("/oauth/callback?code=ok&state=whatever", allow_redirects=False)
        self.assertEqual(response.headers["Location"], "/?error=state")

    async def test_cancelling_on_discord_comes_back_as_denied(self):
        await self.client.get("/login", allow_redirects=False)
        response = await self.client.get("/oauth/callback?error=access_denied", allow_redirects=False)
        self.assertEqual(response.headers["Location"], "/?error=denied")

    async def test_discord_failing_comes_back_as_an_error(self):
        self.oauth.fail = True
        with self.assertLogs("modules.schedule_web", "WARNING"):
            response = await self.sign_in()
        self.assertEqual(response.headers["Location"], "/?error=discord")
        self.assertEqual((await self.client.get("/api/me")).status, 401)

    async def test_an_account_without_a_schedule_channel_is_not_let_in(self):
        self.allowed.discard(USER)
        response = await self.sign_in()
        self.assertEqual(response.headers["Location"], "/?error=no_channel")
        self.assertNotIn(SESSION_COOKIE, response.cookies)

    async def test_losing_the_channel_ends_access_without_waiting_for_the_cookie_to_expire(self):
        await self.sign_in()
        self.allowed.discard(USER)
        self.assertEqual((await self.client.get("/api/tasks")).status, 403)

    async def test_logging_out_drops_the_session(self):
        await self.sign_in()
        response = await self.client.post("/logout")
        self.assertEqual(response.status, 204)
        self.assertEqual((await self.client.get("/api/me")).status, 401)


class TestSignInOverHttps(WebTestCase):
    base_url = "https://olive.example"

    async def test_both_cookies_are_kept_off_plain_http(self):
        response = await self.client.get("/login", allow_redirects=False)
        state_cookie = response.cookies[STATE_COOKIE]
        self.assertTrue(state_cookie["secure"])

        # Sent by hand: the test server is plain http, so the jar holds it back.
        response = await self.client.get(
            f"/oauth/callback?code=ok&state={state_cookie.value}",
            headers={"Cookie": f"{STATE_COOKIE}={state_cookie.value}"},
            allow_redirects=False,
        )
        self.assertEqual(response.headers["Location"], "/")
        self.assertTrue(response.cookies[SESSION_COOKIE]["secure"])


class TestTaskTable(WebTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.sign_in()

    async def test_a_new_row_becomes_a_task_and_redraws_the_schedule(self):
        response = await self.client.post("/api/tasks", json=row(deadline="2026-10-20T18:00", depends_on=[]))
        self.assertEqual(response.status, 201)
        created = await response.json()

        stored = self.provider.get_task(USER, created["id"])
        self.assertEqual(stored.name, "Write report")
        self.assertEqual(stored.duration.total_seconds(), 90 * 60)
        self.assertEqual(stored.break_duration.total_seconds(), 10 * 60)
        # A bare time is the bot's own, the way a typed deadline always is.
        self.assertEqual(stored.deadline, datetime.datetime(2026, 10, 20, 18, 0, tzinfo=tz))
        self.assertEqual(self.redrawn, [USER])

        listed = await self.tasks()
        # Handed out exactly as stored, not in a shape of the page's own.
        self.assertEqual(listed, [schedule_provider.task_to_dict(stored)])
        self.assertEqual(listed[0]["max_chunk_duration"], 45)
        # Longer than one session, so the validator picked a shortest one.
        self.assertEqual(listed[0]["min_chunk_duration"], 15)

    async def test_a_row_read_from_the_api_saves_back_unchanged(self):
        first = await (await self.client.post("/api/tasks", json=row(name="First"))).json()
        await self.client.post(
            "/api/tasks", json=row(deadline="2026-12-01T09:30", description="Draft", depends_on=[first["id"]])
        )
        before = await self.tasks()

        for task in before:
            response = await self.client.put(f"/api/tasks/{task['id']}", json=task)
            self.assertEqual(response.status, 200, await response.text())

        self.assertEqual(await self.tasks(), before)

    async def test_a_deadline_with_an_offset_keeps_its_moment(self):
        created = await (await self.client.post("/api/tasks", json=row(deadline="2026-10-20T15:00:00+00:00"))).json()
        stored = self.provider.get_task(USER, created["id"])
        self.assertEqual(stored.deadline, datetime.datetime(2026, 10, 20, 15, 0, tzinfo=datetime.UTC))

    async def test_the_table_lists_only_the_signed_in_users_tasks(self):
        other_task = schedule_provider._dict_to_task({"name": "Not yours", "duration": 30})
        self.provider.add_task(OTHER_USER, other_task)
        await self.client.post("/api/tasks", json=row(name="Mine"))
        self.assertEqual([t["name"] for t in await self.tasks()], ["Mine"])

    async def test_rows_the_validators_refuse_are_refused_and_nothing_is_stored(self):
        cases = {
            "empty name": row(name="   "),
            "zero duration": row(duration=0),
            "missing duration": {k: v for k, v in row().items() if k != "duration"},
            "duration as text": row(duration="90"),
            "duration as a bool": row(duration=True),
            "fractional duration": row(duration=1.5),
            "priority above ten": row(priority=11),
            "deadline that is not a date": row(deadline="tomorrow"),
            "deadline as a number": row(deadline=1_800_000_000),
            "shortest session above the longest": row(min_chunk_duration=60, max_chunk_duration=30),
            "negative break": row(break_duration=-5),
            "dependency that does not exist": row(depends_on=[99]),
            "dependencies as text": row(depends_on="1, 2"),
            "name too long": row(name="x" * 101),
            "not an object": [row()],
        }
        for label, body in cases.items():
            with self.subTest(label):
                response = await self.client.post("/api/tasks", json=body)
                self.assertEqual(response.status, 400, await response.text())
                self.assertIn("error", await response.json())

        self.assertEqual(self.provider.list_tasks(USER), [])
        self.assertEqual(self.redrawn, [])

    async def test_a_body_that_is_not_json_is_refused(self):
        response = await self.client.post("/api/tasks", data="name=x", headers={"Content-Type": "text/plain"})
        self.assertEqual(response.status, 415)

    async def test_a_row_that_leaves_fields_out_gets_what_the_page_prefills(self):
        defaults = (await (await self.client.get("/api/tasks")).json())["defaults"]
        bare = {"name": "Bare", "duration": 120}
        created = await (await self.client.post("/api/tasks", json=bare)).json()

        self.assertEqual({key: created[key] for key in defaults}, defaults)

    async def test_null_means_no_cap_and_no_break_rather_than_the_default(self):
        created = await (
            await self.client.post("/api/tasks", json=row(max_chunk_duration=None, break_duration=None, priority=None))
        ).json()
        defaults = (await (await self.client.get("/api/tasks")).json())["defaults"]

        self.assertIsNone(created["max_chunk_duration"])
        self.assertEqual(created["break_duration"], 0)
        self.assertEqual(created["priority"], defaults["priority"])

    async def test_the_task_limit_holds(self):
        with mock.patch.object(settings, "schedule_max_tasks_per_user", 1, create=True):
            self.assertEqual((await self.client.post("/api/tasks", json=row())).status, 201)
            response = await self.client.post("/api/tasks", json=row(name="One too many"))

        self.assertEqual(response.status, 409)
        self.assertEqual(len(self.provider.list_tasks(USER)), 1)

    async def test_saving_a_row_replaces_the_task_so_a_cleared_cell_clears_the_field(self):
        first = await (await self.client.post("/api/tasks", json=row(name="First"))).json()
        created = await (
            await self.client.post(
                "/api/tasks",
                json=row(description="Draft", deadline="2026-11-01T09:00", depends_on=[first["id"]]),
            )
        ).json()
        self.redrawn.clear()

        response = await self.client.put(
            f"/api/tasks/{created['id']}",
            json=row(name="Renamed", duration=30, max_chunk_duration=None, description="", deadline=None),
        )
        self.assertEqual(response.status, 200, await response.text())

        stored = self.provider.get_task(USER, created["id"])
        self.assertEqual(stored.name, "Renamed")
        self.assertEqual(stored.description, "")
        self.assertIsNone(stored.deadline)
        self.assertEqual(stored.depends_on, [])
        self.assertIsNone(stored.max_chunk_duration)
        self.assertEqual(stored.duration.total_seconds(), 30 * 60)
        self.assertEqual(self.redrawn, [USER])

    async def test_a_task_cannot_wait_for_itself(self):
        created = await (await self.client.post("/api/tasks", json=row())).json()
        response = await self.client.put(f"/api/tasks/{created['id']}", json=row(depends_on=[created["id"]]))
        self.assertEqual(response.status, 400)
        self.assertEqual(self.provider.get_task(USER, created["id"]).depends_on, [])

    async def test_saving_a_task_that_is_gone_is_not_found(self):
        response = await self.client.put("/api/tasks/7", json=row())
        self.assertEqual(response.status, 404)
        self.assertEqual(self.provider.list_tasks(USER), [])
        self.assertEqual(self.redrawn, [])

    async def test_removing_a_task_also_drops_it_from_what_waited_for_it(self):
        first = await (await self.client.post("/api/tasks", json=row(name="First"))).json()
        second = await (await self.client.post("/api/tasks", json=row(name="Second", depends_on=[first["id"]]))).json()

        response = await self.client.delete(f"/api/tasks/{first['id']}")
        self.assertEqual(response.status, 204)
        self.assertEqual(self.provider.get_task(USER, second["id"]).depends_on, [])
        self.assertEqual((await self.client.delete(f"/api/tasks/{first['id']}")).status, 404)

    async def test_spent_time_comes_off_the_task_until_it_is_done(self):
        created = await (await self.client.post("/api/tasks", json=row(duration=60))).json()
        path = f"/api/tasks/{created['id']}/spend"

        response = await self.client.post(path, json={"minutes": 20})
        self.assertEqual(await response.json(), {"completed": False, "remaining_min": 40})

        response = await self.client.post(path, json={"minutes": 40})
        self.assertEqual(await response.json(), {"completed": True, "remaining_min": 0})

        listing = await (await self.client.get("/api/tasks")).json()
        self.assertEqual(listing["tasks"], [])
        self.assertEqual([t["name"] for t in listing["completed"]], ["Write report"])

    async def test_spending_on_a_task_that_is_gone_is_not_found(self):
        response = await self.client.post("/api/tasks/7/spend", json={"minutes": 10})
        self.assertEqual(response.status, 404)
        self.assertEqual(self.redrawn, [])

    async def test_spending_needs_a_positive_whole_number(self):
        created = await (await self.client.post("/api/tasks", json=row())).json()
        for minutes in (0, -5, "10", 2.5, None):
            with self.subTest(minutes=minutes):
                response = await self.client.post(f"/api/tasks/{created['id']}/spend", json={"minutes": minutes})
                self.assertEqual(response.status, 400)

    async def test_a_write_from_another_site_is_refused(self):
        response = await self.client.post("/api/tasks", json=row(), headers={"Origin": "https://evil.example"})
        self.assertEqual(response.status, 403)

        response = await self.client.post("/api/tasks", json=row(), headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(response.status, 403)
        self.assertEqual(self.provider.list_tasks(USER), [])

        response = await self.client.post("/api/tasks", json=row(), headers={"Origin": BASE_URL})
        self.assertEqual(response.status, 201)


def routine_row(**overrides) -> dict:
    data = {
        "name": "Morning run",
        "type": "fixed",
        "repeat": "daily",
        "duration": 30,
        "time": "07:00",
        "deadline_time": None,
        "weekdays": None,
        "priority": 2,
        "break_duration": 5,
        "depends_on": [],
    }
    data.update(overrides)
    return data


def block_row(**overrides) -> dict:
    data = {"name": "", "repeat": "once", "start": "2026-10-23T14:00", "end": "2026-10-23T15:30", "weekdays": None}
    data.update(overrides)
    return data


class SignedInTestCase(WebTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.sign_in()

    async def listing(self, path: str, key: str) -> list[dict]:
        response = await self.client.get(path)
        self.assertEqual(response.status, 200)
        return (await response.json())[key]

    async def assert_all_refused(self, path: str, cases: dict):
        for label, body in cases.items():
            with self.subTest(label):
                response = await self.client.post(path, json=body)
                self.assertEqual(response.status, 400, await response.text())
                self.assertIn("error", await response.json())


class TestRoutineTable(SignedInTestCase):
    async def routines(self) -> list[dict]:
        return await self.listing("/api/routines", "routines")

    async def test_a_new_row_becomes_a_routine_listed_as_stored(self):
        response = await self.client.post("/api/routines", json=routine_row())
        self.assertEqual(response.status, 201)
        created = await response.json()

        stored = self.provider.get_routine(USER, created["id"])
        self.assertEqual(stored.time, datetime.time(7, 0))
        self.assertEqual(stored.duration, datetime.timedelta(minutes=30))
        self.assertEqual(await self.routines(), [schedule_provider.routine_to_dict(stored)])
        self.assertEqual(self.redrawn, [USER])

    async def test_a_flexible_weekly_routine_keeps_its_deadline_and_days(self):
        body = routine_row(type="flexible", time=None, deadline_time="22:00", repeat="weekly", weekdays=[0, 2])
        created = await (await self.client.post("/api/routines", json=body)).json()

        stored = self.provider.get_routine(USER, created["id"])
        self.assertIsNone(stored.time)
        self.assertEqual(stored.deadline_time, datetime.time(22, 0))
        self.assertEqual(stored.weekdays, [0, 2])

    async def test_rows_the_routine_validator_refuses_are_refused_and_nothing_is_stored(self):
        await self.assert_all_refused(
            "/api/routines",
            {
                "empty name": routine_row(name=" "),
                "unknown type": routine_row(type="sometimes"),
                "fixed without a time": routine_row(time=None),
                "flexible without a deadline": routine_row(type="flexible", time=None),
                "time in another format": routine_row(time="7am"),
                "weekly without days": routine_row(repeat="weekly"),
                "a day past Sunday": routine_row(repeat="weekly", weekdays=[7]),
                "days as text": routine_row(repeat="weekly", weekdays="0,2"),
                "zero duration": routine_row(duration=0),
                "priority above ten": routine_row(priority=11),
                "dependency that does not exist": routine_row(depends_on=[9]),
                "not an object": [routine_row()],
            },
        )
        self.assertEqual(self.provider.list_routines(USER), [])
        self.assertEqual(self.redrawn, [])

    async def test_a_row_read_from_the_api_saves_back_unchanged_skip_included(self):
        first = await (await self.client.post("/api/routines", json=routine_row())).json()
        second = await (
            await self.client.post("/api/routines", json=routine_row(name="Read", depends_on=[first["id"]]))
        ).json()
        until = (datetime.date.today() + datetime.timedelta(days=3)).strftime("%d.%m.%Y")
        await self.client.post(f"/api/routines/{second['id']}/skip", json={"resume_after": until})
        before = await self.routines()

        for routine in before:
            response = await self.client.put(f"/api/routines/{routine['id']}", json=routine)
            self.assertEqual(response.status, 200, await response.text())

        self.assertEqual(await self.routines(), before)
        self.assertEqual(before[1]["resume_after"], until)

    async def test_saving_a_row_replaces_the_routine(self):
        created = await (await self.client.post("/api/routines", json=routine_row())).json()
        body = routine_row(name="Evening run", type="flexible", time=None, deadline_time="21:00", priority=None)

        response = await self.client.put(f"/api/routines/{created['id']}", json=body)
        self.assertEqual(response.status, 200, await response.text())
        stored = self.provider.get_routine(USER, created["id"])
        self.assertEqual((stored.name, stored.type, stored.time), ("Evening run", "flexible", None))
        self.assertEqual(stored.deadline_time, datetime.time(21, 0))

    async def test_a_routine_cannot_wait_for_itself(self):
        created = await (await self.client.post("/api/routines", json=routine_row())).json()
        response = await self.client.put(f"/api/routines/{created['id']}", json=routine_row(depends_on=[created["id"]]))
        self.assertEqual(response.status, 400)

    async def test_skipping_takes_what_routine_skip_takes(self):
        created = await (await self.client.post("/api/routines", json=routine_row())).json()
        path = f"/api/routines/{created['id']}/skip"
        today = datetime.date.today()

        response = await self.client.post(path, json={"days": 2})
        self.assertEqual(
            (await response.json())["resume_after"], (today + datetime.timedelta(days=1)).strftime("%d.%m.%Y")
        )

        response = await self.client.post(path, json={})
        self.assertEqual((await response.json())["resume_after"], today.strftime("%d.%m.%Y"))

        yesterday = (today - datetime.timedelta(days=1)).strftime("%d.%m.%Y")
        for body in ({"resume_after": yesterday}, {"days": 0}, {"days": 1, "resume_after": yesterday}, ["x"]):
            with self.subTest(body=body):
                self.assertEqual((await self.client.post(path, json=body)).status, 400)

        self.assertEqual((await self.client.post("/api/routines/99/skip", json={})).status, 404)

    async def test_the_routine_limit_holds(self):
        with mock.patch.object(settings, "schedule_max_routines_per_user", 1, create=True):
            self.assertEqual((await self.client.post("/api/routines", json=routine_row())).status, 201)
            response = await self.client.post("/api/routines", json=routine_row(name="One too many"))
        self.assertEqual(response.status, 409)

    async def test_a_routine_that_is_gone_is_not_found(self):
        self.assertEqual((await self.client.put("/api/routines/5", json=routine_row())).status, 404)
        self.assertEqual((await self.client.delete("/api/routines/5")).status, 404)
        self.assertEqual(self.redrawn, [])

    async def test_removing_a_routine_drops_it_from_what_waited_for_it(self):
        first = await (await self.client.post("/api/routines", json=routine_row())).json()
        second = await (
            await self.client.post("/api/routines", json=routine_row(name="After", depends_on=[first["id"]]))
        ).json()

        self.assertEqual((await self.client.delete(f"/api/routines/{first['id']}")).status, 204)
        self.assertEqual(self.provider.get_routine(USER, second["id"]).depends_on, [])


class TestTimeBlockTable(SignedInTestCase):
    async def blocks(self) -> list[dict]:
        return await self.listing("/api/time-blocks", "time_blocks")

    async def test_a_one_off_block_lands_on_its_own_day(self):
        response = await self.client.post("/api/time-blocks", json=block_row(name="Dentist"))
        self.assertEqual(response.status, 201)
        created = await response.json()

        stored = self.provider.get_time_block(USER, created["id"])
        self.assertEqual(stored.start, datetime.datetime(2026, 10, 23, 14, 0, tzinfo=tz))
        self.assertEqual(stored.end, datetime.datetime(2026, 10, 23, 15, 30, tzinfo=tz))
        self.assertEqual(await self.blocks(), [schedule_provider.timeblock_to_dict(stored)])
        self.assertEqual(self.redrawn, [USER])

    async def test_a_weekly_block_past_midnight_ends_the_next_day(self):
        body = block_row(repeat="weekly", weekdays=[1, 3], start="2026-10-20T22:00", end="2026-10-20T01:00")
        created = await (await self.client.post("/api/time-blocks", json=body)).json()

        stored = self.provider.get_time_block(USER, created["id"])
        self.assertEqual(stored.weekdays, [1, 3])
        self.assertEqual(stored.end - stored.start, datetime.timedelta(hours=3))

    async def test_a_bare_time_is_taken_too(self):
        created = await (
            await self.client.post("/api/time-blocks", json=block_row(repeat="daily", start="08:00", end="09:00"))
        ).json()
        self.assertEqual(created["repeat"], "daily")

    async def test_rows_the_time_block_validator_refuses_are_refused_and_nothing_is_stored(self):
        await self.assert_all_refused(
            "/api/time-blocks",
            {
                "no start": block_row(start=None),
                "an end days before the start": block_row(end="2026-10-21T15:30"),
                "an hour past the day": block_row(start="25:00"),
                "weekly without days": block_row(repeat="weekly"),
                "days on a daily block": block_row(repeat="daily", weekdays=[1]),
                "unknown repeat": block_row(repeat="monthly"),
                "name too long": block_row(name="x" * 101),
            },
        )
        self.assertEqual(self.provider.list_time_blocks(USER), [])

    async def test_a_row_read_from_the_api_saves_back_unchanged(self):
        await self.client.post("/api/time-blocks", json=block_row(name="Dentist"))
        await self.client.post(
            "/api/time-blocks",
            json=block_row(repeat="weekly", weekdays=[4], start="2026-10-20T23:00", end="2026-10-20T01:00"),
        )
        before = await self.blocks()

        for block in before:
            response = await self.client.put(f"/api/time-blocks/{block['id']}", json=block)
            self.assertEqual(response.status, 200, await response.text())

        self.assertEqual(await self.blocks(), before)

    async def test_saving_a_row_can_change_how_a_block_repeats(self):
        created = await (
            await self.client.post("/api/time-blocks", json=block_row(repeat="weekly", weekdays=[0]))
        ).json()

        response = await self.client.put(f"/api/time-blocks/{created['id']}", json=block_row(repeat="daily"))
        self.assertEqual(response.status, 200, await response.text())
        stored = self.provider.get_time_block(USER, created["id"])
        self.assertTrue(stored.daily)
        self.assertIsNone(stored.weekdays)

    async def test_the_time_block_limit_holds(self):
        with mock.patch.object(settings, "schedule_max_timeblocks_per_user", 1, create=True):
            self.assertEqual((await self.client.post("/api/time-blocks", json=block_row())).status, 201)
            response = await self.client.post("/api/time-blocks", json=block_row(name="One too many"))
        self.assertEqual(response.status, 409)

    async def test_a_block_that_is_gone_is_not_found(self):
        self.assertEqual((await self.client.put("/api/time-blocks/5", json=block_row())).status, 404)
        self.assertEqual((await self.client.delete("/api/time-blocks/5")).status, 404)

        created = await (await self.client.post("/api/time-blocks", json=block_row())).json()
        self.assertEqual((await self.client.delete(f"/api/time-blocks/{created['id']}")).status, 204)
        self.assertEqual(self.provider.list_time_blocks(USER), [])


class FakeRegistry:
    def __init__(self, entries: dict):
        self.entries = entries

    def get(self, owner_id):
        return self.entries.get(owner_id)

    def display_channel_id(self, entry):
        return entry.get("channel_id")


class TestCog(unittest.IsolatedAsyncioTestCase):
    """The cog's part: whether the server starts at all, and what a write redraws."""

    def setUp(self):
        self.bot = mock.Mock()
        self.cog = web_cog.ScheduleWebServer(self.bot)
        registry = FakeRegistry({USER: {"channel_id": 555, "tasks_channel_id": 556}})
        # Who may sign in is read off the registry, where a write lands off the provider.
        for target, attribute in ((web_cog, "channels_registry"), (schedule_provider.ScheduleProvider, "channels")):
            patcher = mock.patch.object(target, attribute, registry)
            patcher.start()
            self.addCleanup(patcher.stop)

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.page = Path(tmp.name)
        patcher = mock.patch.object(schedule_web, "STATIC_DIR", stand_in_page(self.page))
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_without_a_base_url_nothing_listens(self):
        with mock.patch.object(settings, "schedule_web_base_url", "", create=True):
            await self.cog.cog_load()
        self.assertIsNone(self.cog.runner)

    async def test_without_oauth_credentials_nothing_listens(self):
        with (
            mock.patch.object(settings, "schedule_web_base_url", BASE_URL, create=True),
            mock.patch.object(web_cog.token_registry, "get_discord_oauth", return_value=None),
            self.assertLogs(web_cog.logger, "WARNING"),
        ):
            await self.cog.cog_load()
        self.assertIsNone(self.cog.runner)

    async def test_an_unbuilt_page_is_not_served_and_says_how_to_build_it(self):
        (self.page / "index.html").unlink()
        with (
            mock.patch.object(settings, "schedule_web_base_url", BASE_URL, create=True),
            self.assertLogs(web_cog.logger, "ERROR") as logs,
        ):
            await self.cog.cog_load()

        self.assertIsNone(self.cog.runner)
        self.assertIn("scripts/build-web.sh", logs.output[0])

    async def test_once_configured_the_page_is_served_until_the_cog_unloads(self):
        port = unused_port()
        with (
            mock.patch.object(settings, "schedule_web_base_url", BASE_URL, create=True),
            mock.patch.object(settings, "schedule_web_host", "127.0.0.1", create=True),
            mock.patch.object(settings, "schedule_web_port", port, create=True),
            mock.patch.object(web_cog.token_registry, "get_discord_oauth", return_value=("id", "secret")),
            mock.patch.object(web_cog.token_registry, "get_web_session_secret", return_value="s"),
        ):
            await self.cog.cog_load()

        async with aiohttp.ClientSession() as http, http.get(f"http://127.0.0.1:{port}/") as response:
            self.assertEqual(response.status, 200)

        self.cog.cog_unload()
        await self.cog.cleanup_task
        async with aiohttp.ClientSession() as http:
            with self.assertRaises(aiohttp.ClientConnectionError):
                await http.get(f"http://127.0.0.1:{port}/")

    def test_only_an_owner_of_a_schedule_channel_may_sign_in(self):
        self.assertTrue(web_cog.has_schedule_channel(USER))
        self.assertFalse(web_cog.has_schedule_channel(OTHER_USER))

    def test_a_write_redraws_the_owners_schedule_message(self):
        self.cog.redraw_schedule(USER)
        self.bot.dispatch.assert_called_once_with("schedule_update", 555)

        self.bot.dispatch.reset_mock()
        self.cog.redraw_schedule(OTHER_USER)
        self.bot.dispatch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
