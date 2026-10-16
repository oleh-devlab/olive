"""The schedule's tasks as a table in a browser, signed in with Discord.

A second front door to the same data, not a copy of it: every write goes
through `ScheduleProvider` into the file `/task` and the agent use, and every
row through the validator they use, so the three can never disagree about what
a task is or drift apart. The server runs on the bot's own event loop (see
`cogs/schedule/web.py`), which is what makes that safe -- a provider call is a
synchronous read-modify-write, and nothing else runs while it does.

A task goes out in the shape it is stored in (`schedule_provider.task_to_dict`)
and comes back in the same one, so a row read from the API saves back as it
is: durations in whole minutes, the deadline as an ISO timestamp.
"""

import inspect
import json
import logging
import secrets
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web

from core.web_auth import DiscordOAuth, OAuthError, SessionSigner, new_state
from modules.schedule_exceptions import ScheduleItemNotFoundError, ScheduleLimitError, ScheduleValidationError
from modules.schedule_models import Task
from modules.schedule_provider import ScheduleProvider, max_tasks_per_user, task_to_dict
from modules.schedule_validators import parse_depends_on, validate_task_creation_data

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static" / "schedule"

SESSION_COOKIE = "olive_session"
STATE_COOKIE = "olive_oauth_state"
STATE_MAX_AGE = 600

MAX_NAME_LENGTH = 100
MAX_DESCRIPTION_LENGTH = 1000

CALLBACK_PATH = "/oauth/callback"

# Nothing on the page comes from anywhere but this server, so nothing else may.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
    "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def oauth_redirect_uri(base_url: str) -> str:
    """What has to be registered as a redirect in the Discord developer portal."""
    return base_url.rstrip("/") + CALLBACK_PATH


def _origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower()


def _whole_number(row: dict, key: str) -> int | None:
    """A field that is either absent, null, or an integer -- never a bool or a string."""
    value = row.get(key)
    if value is None:
        return None

    if isinstance(value, bool) or not isinstance(value, int):
        raise ScheduleValidationError(f"'{key}' must be a whole number.")

    return value


def creation_defaults() -> dict:
    """
    What a row gets for each field it leaves out -- read off the validator's own
    signature rather than from `settings` again, so the page prefills exactly
    what the server would fill in, and `/task add` falls back on the same.
    """
    parameters = inspect.signature(validate_task_creation_data).parameters
    return {
        "priority": parameters["priority"].default,
        "max_chunk_duration": parameters["max_chunk_duration_min"].default,
        "break_duration": parameters["break_duration_min"].default,
    }


def task_from_json(row: object, user_id: int, task_id: int | None = None) -> Task:
    """
    One table row, read through the validator `/task add` and the agent use.

    A row is the whole task, so an edit is the same read as a creation: what the
    page sends replaces what is stored, which is what lets a cleared cell clear
    a field -- something the partial `/task edit` validator cannot express.
    """
    if not isinstance(row, dict):
        raise ScheduleValidationError("Expected a JSON object.")

    name = row.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ScheduleValidationError("Task name cannot be empty.")
    if len(name) > MAX_NAME_LENGTH:
        raise ScheduleValidationError(f"Task name is limited to {MAX_NAME_LENGTH} characters.")

    description = row.get("description") or ""
    if not isinstance(description, str):
        raise ScheduleValidationError("'description' must be text.")
    if len(description) > MAX_DESCRIPTION_LENGTH:
        raise ScheduleValidationError(f"Description is limited to {MAX_DESCRIPTION_LENGTH} characters.")

    deadline = row.get("deadline")
    if deadline is not None and not isinstance(deadline, str):
        raise ScheduleValidationError("'deadline' must be an ISO date and time, or null.")

    depends_on = row.get("depends_on") or []
    if not isinstance(depends_on, list) or any(isinstance(d, bool) or not isinstance(d, int) for d in depends_on):
        raise ScheduleValidationError("'depends_on' must be a list of task IDs.")

    duration_min = _whole_number(row, "duration")
    if duration_min is None:
        raise ScheduleValidationError("'duration' is required.")

    # A field the row leaves out takes the validator's own default. Null is a
    # value of its own where it can be one: a session with no cap, no break.
    optional = {}
    if (priority := _whole_number(row, "priority")) is not None:
        optional["priority"] = priority
    if "max_chunk_duration" in row:
        optional["max_chunk_duration_min"] = _whole_number(row, "max_chunk_duration")
    if "break_duration" in row:
        optional["break_duration_min"] = _whole_number(row, "break_duration") or 0

    task = validate_task_creation_data(
        name=name,
        duration_min=duration_min,
        description=description,
        min_chunk_duration_min=_whole_number(row, "min_chunk_duration"),
        deadline=deadline,
        user_id=user_id,
        **optional,
    )

    # Read here rather than by the creation validator, which has no task of its
    # own to refuse as a dependency of itself.
    task.depends_on = parse_depends_on(
        ", ".join(str(d) for d in depends_on), user_id=user_id, self_id=task_id, item_type="task"
    )

    return task


def _error(exc_cls: type[web.HTTPException], message: str) -> web.HTTPException:
    return exc_cls(text=json.dumps({"error": message}, ensure_ascii=False), content_type="application/json")


class ScheduleWebApp:
    """
    The routes, with everything they reach handed in.

    Who may sign in and what happens after a write are the caller's to decide:
    the cog lets in whoever has a schedule channel and redraws that channel, a
    test passes a set and a list.
    """

    def __init__(
        self,
        *,
        provider: ScheduleProvider,
        oauth: DiscordOAuth,
        signer: SessionSigner,
        base_url: str,
        is_allowed: Callable[[int], bool],
        on_change: Callable[[int], None],
        static_dir: Path = STATIC_DIR,
    ):
        self.provider = provider
        self.oauth = oauth
        self.signer = signer
        self.base_url = base_url.rstrip("/")
        self.origin = _origin_of(self.base_url)
        self.secure_cookies = self.base_url.startswith("https://")
        self.is_allowed = is_allowed
        self.on_change = on_change
        self.static_dir = static_dir

    def make_app(self) -> web.Application:
        app = web.Application(middlewares=[self._security_middleware], client_max_size=64 * 1024)
        app.add_routes(
            [
                web.get("/", self.index),
                web.get("/login", self.login),
                web.get(CALLBACK_PATH, self.oauth_callback),
                web.post("/logout", self.logout),
                web.get("/api/me", self.me),
                web.get("/api/tasks", self.list_tasks),
                web.post("/api/tasks", self.create_task),
                web.put(r"/api/tasks/{task_id:\d+}", self.update_task),
                web.delete(r"/api/tasks/{task_id:\d+}", self.delete_task),
                web.post(r"/api/tasks/{task_id:\d+}/spend", self.spend_task),
                web.static("/static", self.static_dir),
            ]
        )
        return app

    # --- Plumbing ---

    @web.middleware
    async def _security_middleware(self, request: web.Request, handler):
        try:
            self._check_origin(request)
            response = await handler(request)
        except web.HTTPException as e:
            self._add_headers(request, e)
            raise

        self._add_headers(request, response)
        return response

    def _check_origin(self, request: web.Request):
        """
        A cookie alone must not be enough to write.

        A page on another site can make the browser send the cookie, but not lie
        about where the request came from. `SameSite=Lax` and the JSON-only
        bodies already keep such a request from carrying the session; this is
        the check that does not depend on the browser getting either right.
        """
        if request.method not in UNSAFE_METHODS:
            return

        origin = request.headers.get("Origin")
        if origin is not None and origin.lower() != self.origin:
            raise _error(web.HTTPForbidden, "Cross-origin request refused.")
        if request.headers.get("Sec-Fetch-Site") == "cross-site":
            raise _error(web.HTTPForbidden, "Cross-origin request refused.")

    def _add_headers(self, request: web.Request, response: web.StreamResponse):
        response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Cache-Control", "no-store" if request.path.startswith("/api/") else "no-cache")

    def _session(self, request: web.Request) -> dict | None:
        return self.signer.loads(request.cookies.get(SESSION_COOKIE))

    def _user_id(self, request: web.Request) -> int:
        """Whose data this request is about. Checked on every call, not only at sign-in."""
        session = self._session(request)
        if session is None or not str(session.get("uid", "")).isdigit():
            raise _error(web.HTTPUnauthorized, "Not signed in.")

        user_id = int(session["uid"])
        if not self.is_allowed(user_id):
            raise _error(web.HTTPForbidden, "This account has no schedule channel.")

        return user_id

    async def _json_body(self, request: web.Request):
        if request.content_type != "application/json":
            raise _error(web.HTTPUnsupportedMediaType, "Expected application/json.")
        try:
            return await request.json()
        except ValueError:
            raise _error(web.HTTPBadRequest, "The request body is not valid JSON.") from None

    def _changed(self, user_id: int):
        try:
            self.on_change(user_id)
        except Exception:
            # The write already landed; a schedule redrawn on the next tick
            # instead of now is no reason to tell the browser it failed.
            logger.exception("Could not announce a schedule change for user %s", user_id)

    @staticmethod
    def _redirect(location: str) -> web.Response:
        return web.Response(status=302, headers={"Location": location})

    def _redirect_home(self, error: str | None = None) -> web.Response:
        return self._redirect("/" if error is None else f"/?error={error}")

    # --- Pages and sign-in ---

    async def index(self, request: web.Request) -> web.FileResponse:
        return web.FileResponse(self.static_dir / "index.html")

    async def login(self, request: web.Request) -> web.Response:
        state = new_state()
        response = self._redirect(self.oauth.authorize_url(state))
        response.set_cookie(
            STATE_COOKIE,
            state,
            max_age=STATE_MAX_AGE,
            httponly=True,
            secure=self.secure_cookies,
            samesite="Lax",
            path=CALLBACK_PATH,
        )
        return response

    async def oauth_callback(self, request: web.Request) -> web.Response:
        expected_state = request.cookies.get(STATE_COOKIE)
        state = request.query.get("state", "")

        if "error" in request.query:
            # The user pressed "Cancel" on Discord's consent screen.
            response = self._redirect_home("denied")
        elif not expected_state or not secrets.compare_digest(state.encode(), expected_state.encode()):
            response = self._redirect_home("state")
        else:
            response = await self._sign_in(request.query.get("code", ""))

        response.del_cookie(STATE_COOKIE, path=CALLBACK_PATH)
        return response

    async def _sign_in(self, code: str) -> web.Response:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as http:
                user = await self.oauth.fetch_user(code, http)
        except OAuthError as e:
            logger.warning("Discord sign-in failed: %s", e)
            return self._redirect_home("discord")

        user_id = int(user["id"])
        if not self.is_allowed(user_id):
            return self._redirect_home("no_channel")

        name = user.get("global_name") or user.get("username") or str(user_id)
        response = self._redirect_home()
        response.set_cookie(
            SESSION_COOKIE,
            self.signer.dumps({"uid": str(user_id), "name": str(name)[:64]}),
            max_age=self.signer.max_age,
            httponly=True,
            secure=self.secure_cookies,
            samesite="Lax",
            path="/",
        )
        logger.info("User %s signed in to the schedule editor.", user_id)
        return response

    async def logout(self, request: web.Request) -> web.Response:
        response = web.Response(status=204)
        response.del_cookie(SESSION_COOKIE, path="/")
        return response

    # --- API ---

    async def me(self, request: web.Request) -> web.Response:
        user_id = self._user_id(request)
        session = self._session(request) or {}
        # As a string: a Discord id does not fit in a JavaScript number.
        return web.json_response({"id": str(user_id), "name": session.get("name", "")})

    async def list_tasks(self, request: web.Request) -> web.Response:
        user_id = self._user_id(request)
        return web.json_response(
            {
                "tasks": [task_to_dict(t) for t in self.provider.list_tasks(user_id)],
                "completed": [task_to_dict(t) for t in self.provider.list_completed_tasks(user_id)],
                "limits": {
                    "max_tasks": max_tasks_per_user(),
                    "name_length": MAX_NAME_LENGTH,
                    "description_length": MAX_DESCRIPTION_LENGTH,
                },
                "defaults": creation_defaults(),
            }
        )

    async def create_task(self, request: web.Request) -> web.Response:
        user_id = self._user_id(request)
        row = await self._json_body(request)

        try:
            task = task_from_json(row, user_id)
            self.provider.add_task(user_id, task)
        except ScheduleLimitError as e:
            raise _error(web.HTTPConflict, str(e)) from None
        except (ScheduleValidationError, ValueError) as e:
            raise _error(web.HTTPBadRequest, str(e)) from None

        self._changed(user_id)
        return web.json_response(task_to_dict(task), status=201)

    async def update_task(self, request: web.Request) -> web.Response:
        user_id = self._user_id(request)
        task_id = int(request.match_info["task_id"])
        row = await self._json_body(request)

        if self.provider.get_task(user_id, task_id) is None:
            raise _error(web.HTTPNotFound, f"Task {task_id} not found.")

        try:
            task = task_from_json(row, user_id, task_id=task_id)
            fields = {
                "name": task.name,
                "description": task.description,
                "duration": task.duration,
                "priority": task.priority,
                "deadline": task.deadline,
                "max_chunk_duration": task.max_chunk_duration,
                "min_chunk_duration": task.min_chunk_duration,
                "break_duration": task.break_duration,
                "depends_on": task.depends_on,
            }
            self.provider.edit_task(user_id, task_id, **fields)
        except (ScheduleValidationError, ValueError) as e:
            raise _error(web.HTTPBadRequest, str(e)) from None

        self._changed(user_id)
        return web.json_response(task_to_dict(self.provider.get_task(user_id, task_id)))

    async def delete_task(self, request: web.Request) -> web.Response:
        user_id = self._user_id(request)
        task_id = int(request.match_info["task_id"])

        if not self.provider.remove_task(user_id, task_id):
            raise _error(web.HTTPNotFound, f"Task {task_id} not found.")

        self._changed(user_id)
        return web.Response(status=204)

    async def spend_task(self, request: web.Request) -> web.Response:
        user_id = self._user_id(request)
        task_id = int(request.match_info["task_id"])
        body = await self._json_body(request)

        minutes = body.get("minutes") if isinstance(body, dict) else None
        if isinstance(minutes, bool) or not isinstance(minutes, int):
            raise _error(web.HTTPBadRequest, "'minutes' must be a whole number.")

        try:
            completed, remaining = self.provider.spend_task_time(user_id, task_id, minutes)
        except ScheduleItemNotFoundError:
            raise _error(web.HTTPNotFound, f"Task {task_id} not found.") from None
        except ValueError as e:
            raise _error(web.HTTPBadRequest, str(e)) from None

        self._changed(user_id)
        return web.json_response({"completed": completed, "remaining_min": remaining})
