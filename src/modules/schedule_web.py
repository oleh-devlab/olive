"""The schedule's tasks, routines and time blocks as tables in a browser, signed in with Discord.

A second front door to the same data, not a copy of it: every write goes
through `ScheduleProvider` into the file `/task`, `/routine`, `/timeblock` and
the agent use, and every row through the validator they use, so none of them
can disagree about what an item is or drift apart. The server runs on the bot's
own event loop (see `cogs/schedule/web.py`), which is what makes that safe -- a
provider call is a synchronous read-modify-write, and nothing else runs while
it does.

An item goes out in the shape it is stored in (`schedule_provider.task_to_dict`
and its siblings) and comes back in the same one, so a row read from the API
saves back as it is: durations in whole minutes, a deadline or a block's bounds
as ISO timestamps, a routine's times as `HH:MM`.

The schedule itself is read, never written: it is the engine's last solve, the
same one the Discord message shows, and a new solve only when the data has
changed since -- or when the reader asks for one, because time has moved on.
"""

import contextlib
import dataclasses
import inspect
import json
import logging
import secrets
from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web

from core.web_auth import DiscordOAuth, OAuthError, SessionSigner, new_state
from modules.schedule_engine import get_solved_schedule
from modules.schedule_exceptions import ScheduleItemNotFoundError, ScheduleLimitError, ScheduleValidationError
from modules.schedule_models import Routine, SolvedSchedule, Task, TimeBlock
from modules.schedule_provider import (
    ScheduleProvider,
    max_routines_per_user,
    max_tasks_per_user,
    max_timeblocks_per_user,
    routine_to_dict,
    task_to_dict,
    timeblock_to_dict,
)
from modules.schedule_validators import (
    clean_text,
    parse_depends_on,
    validate_routine_creation_data,
    validate_skip_routine_data,
    validate_task_creation_data,
    validate_timeblock_creation_data,
)

logger = logging.getLogger(__name__)

# Where scripts/build-web.sh puts the page built from web/; not in git.
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


def page_is_built() -> bool:
    """Whether the page has been built where it is served from."""
    return (STATIC_DIR / "index.html").is_file()


def oauth_redirect_uri(base_url: str) -> str:
    """What has to be registered as a redirect in the Discord developer portal."""
    return base_url.rstrip("/") + CALLBACK_PATH


def _origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower()


def _object(row: object) -> dict:
    if not isinstance(row, dict):
        raise ScheduleValidationError("Expected a JSON object.")
    return row


def _text(row: dict, key: str) -> str | None:
    value = row.get(key)
    if value is not None and not isinstance(value, str):
        raise ScheduleValidationError(f"'{key}' must be text.")
    return value


def _required_text(row: dict, key: str) -> str:
    value = _text(row, key)
    if not value:
        raise ScheduleValidationError(f"'{key}' is required.")
    return value


def _name(row: dict, noun: str, required: bool = True) -> str:
    name = _text(row, "name") or ""
    if required and not name.strip():
        raise ScheduleValidationError(f"{noun} name cannot be empty.")
    if len(name) > MAX_NAME_LENGTH:
        raise ScheduleValidationError(f"{noun} name is limited to {MAX_NAME_LENGTH} characters.")
    return name


def _whole_number(row: dict, key: str) -> int | None:
    """A field that is either absent, null, or an integer -- never a bool or a string."""
    value = row.get(key)
    if value is None:
        return None

    if isinstance(value, bool) or not isinstance(value, int):
        raise ScheduleValidationError(f"'{key}' must be a whole number.")

    return value


def _required_number(row: dict, key: str) -> int:
    value = _whole_number(row, key)
    if value is None:
        raise ScheduleValidationError(f"'{key}' is required.")
    return value


def _number_list(row: dict, key: str) -> list[int]:
    value = row.get(key) or []
    if not isinstance(value, list) or any(isinstance(v, bool) or not isinstance(v, int) for v in value):
        raise ScheduleValidationError(f"'{key}' must be a list of whole numbers.")
    return value


def _dependencies(row: dict, user_id: int, item_id: int | None, item_type: str) -> list[int]:
    """
    Read here rather than by a creation validator, which has no item of its own
    to refuse as a dependency of itself.
    """
    ids = ", ".join(str(d) for d in _number_list(row, "depends_on"))
    return parse_depends_on(ids, user_id=user_id, self_id=item_id, item_type=item_type)


def _priority_and_break(row: dict) -> dict:
    """
    The validator's arguments for the fields a row may leave out. Left out, the
    validator's own default applies; a null priority is that default too, and a
    null break is no break.
    """
    optional = {}
    if (priority := _whole_number(row, "priority")) is not None:
        optional["priority"] = priority
    if "break_duration" in row:
        optional["break_duration_min"] = _whole_number(row, "break_duration") or 0
    return optional


def validator_defaults(validator: Callable, fields: dict[str, str]) -> dict:
    """
    What a row gets for each field it leaves out -- read off the validator's own
    signature rather than from `settings` again, so the page prefills exactly
    what the server would fill in, and the slash commands fall back on the same.
    """
    parameters = inspect.signature(validator).parameters
    return {field: parameters[param].default for field, param in fields.items()}


TASK_DEFAULTS = {
    "priority": "priority",
    "max_chunk_duration": "max_chunk_duration_min",
    "break_duration": "break_duration_min",
}
ROUTINE_DEFAULTS = {"priority": "priority", "break_duration": "break_duration_min"}


def task_from_json(row: object, user_id: int, item_id: int | None = None) -> Task:
    """
    One table row, read through the validator `/task add` and the agent use.

    A row is the whole item, so an edit is the same read as a creation: what the
    page sends replaces what is stored, which is what lets a cleared cell clear
    a field -- something the partial `/task edit` validator cannot express.
    """
    row = _object(row)

    description = _text(row, "description") or ""
    if len(description) > MAX_DESCRIPTION_LENGTH:
        raise ScheduleValidationError(f"Description is limited to {MAX_DESCRIPTION_LENGTH} characters.")

    optional = _priority_and_break(row)
    # Null is a value of its own here: a session with no cap.
    if "max_chunk_duration" in row:
        optional["max_chunk_duration_min"] = _whole_number(row, "max_chunk_duration")

    task = validate_task_creation_data(
        name=_name(row, "Task"),
        duration_min=_required_number(row, "duration"),
        description=description,
        min_chunk_duration_min=_whole_number(row, "min_chunk_duration"),
        deadline=_text(row, "deadline"),
        user_id=user_id,
        **optional,
    )
    task.depends_on = _dependencies(row, user_id, item_id, "task")
    return task


def routine_from_json(row: object, user_id: int, item_id: int | None = None) -> Routine:
    """
    One routine row, read through the validator `/routine add_*` and the agent use.

    A skip (`resume_after`) is not part of it: that has an endpoint of its own,
    the way it has a command of its own, and an edit leaves it as it is.
    """
    row = _object(row)
    routine = validate_routine_creation_data(
        name=_name(row, "Routine"),
        routine_type=_text(row, "type"),
        repeat=_text(row, "repeat"),
        duration_min=_required_number(row, "duration"),
        time_str=_text(row, "time"),
        deadline_time_str=_text(row, "deadline_time"),
        weekdays=_number_list(row, "weekdays") or None,
        user_id=user_id,
        **_priority_and_break(row),
    )
    routine.depends_on = _dependencies(row, user_id, item_id, "routine")
    return routine


def timeblock_from_json(row: object, user_id: int, item_id: int | None = None) -> TimeBlock:
    """One time block row, read through the validator `/timeblock add` and the agent use."""
    row = _object(row)
    optional = {"repeat": _text(row, "repeat")} if "repeat" in row else {}
    return validate_timeblock_creation_data(
        _required_text(row, "start"),
        _required_text(row, "end"),
        name=clean_text(_name(row, "Time block", required=False)),
        weekdays=_number_list(row, "weekdays") or None,
        **optional,
    )


@dataclasses.dataclass(frozen=True)
class ItemKind:
    """One kind of schedule item as the API sees it: how a row is read, how an item is shown, and where it is kept."""

    noun: str
    from_json: Callable[..., object]
    to_dict: Callable[[object], dict]
    add: Callable[[int, object], int]
    get: Callable[[int, int], object | None]
    edit: Callable[..., bool]
    remove: Callable[[int, int], bool]
    # Stored fields a row does not replace when it is saved over an item.
    kept_on_edit: tuple[str, ...] = ("id",)

    def replaced_fields(self, item: object) -> dict:
        return {
            f.name: getattr(item, f.name)
            for f in dataclasses.fields(item)
            if f.init and f.name not in self.kept_on_edit
        }


def schedule_to_json(schedule: SolvedSchedule) -> dict:
    """A solve as the page draws it: what goes where and when, and what did not fit."""
    return {
        "computed_at": schedule.computed_at.isoformat(timespec="seconds") if schedule.computed_at else None,
        "status": schedule.status,
        "solve_time": round(schedule.solve_time, 3),
        "planning_days": schedule.planning_days,
        "items": [
            {
                "type": item.item_type,
                "id": item.item_id,
                "name": item.task_name,
                "start": item.dt_start.isoformat(),
                "end": item.dt_end.isoformat(),
                "session": int(item.session_index),
                "sessions": item.total_sessions,
            }
            for item in schedule.items
        ],
        "skipped_task_ids": list(schedule.skipped_task_ids),
        "skipped_routines": list(schedule.skipped_routines),
    }


@contextlib.contextmanager
def _as_http_errors():
    """What the validators and the provider refuse, answered with the HTTP status for it."""
    try:
        yield
    except ScheduleLimitError as e:
        raise _error(web.HTTPConflict, str(e)) from None
    except (ScheduleValidationError, ValueError) as e:
        raise _error(web.HTTPBadRequest, str(e)) from None


def _error(exc_cls: type[web.HTTPException], message: str) -> web.HTTPException:
    return exc_cls(text=json.dumps({"error": message}, ensure_ascii=False), content_type="application/json")


class ScheduleWebApp:
    """
    The routes, with everything they reach handed in.

    Who may sign in and what happens after a write are the caller's to decide:
    the cog lets in whoever has a schedule channel and redraws that channel, a
    test passes a set and a list. So is where a solved schedule comes from: the
    engine, unless a test hands in something that does not run the solver.
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
        static_dir: Path | None = None,
        get_schedule: Callable[..., Awaitable[SolvedSchedule]] | None = None,
    ):
        self.provider = provider
        self.oauth = oauth
        self.signer = signer
        self.base_url = base_url.rstrip("/")
        self.origin = _origin_of(self.base_url)
        self.secure_cookies = self.base_url.startswith("https://")
        self.is_allowed = is_allowed
        self.on_change = on_change
        self.static_dir = static_dir or STATIC_DIR
        self.get_schedule = get_schedule or get_solved_schedule

        # Keyed by the path each kind is served under.
        self.kinds = {
            "tasks": ItemKind(
                "Task",
                task_from_json,
                task_to_dict,
                provider.add_task,
                provider.get_task,
                provider.edit_task,
                provider.remove_task,
            ),
            "routines": ItemKind(
                "Routine",
                routine_from_json,
                routine_to_dict,
                provider.add_routine,
                provider.get_routine,
                provider.edit_routine,
                provider.remove_routine,
                kept_on_edit=("id", "resume_after"),
            ),
            "time-blocks": ItemKind(
                "Time block",
                timeblock_from_json,
                timeblock_to_dict,
                provider.add_time_block,
                provider.get_time_block,
                provider.edit_time_block,
                provider.remove_time_block,
            ),
        }

    def make_app(self) -> web.Application:
        app = web.Application(middlewares=[self._security_middleware], client_max_size=64 * 1024)
        routes = [
            web.get("/", self.index),
            web.get("/login", self.login),
            web.get(CALLBACK_PATH, self.oauth_callback),
            web.post("/logout", self.logout),
            web.get("/api/me", self.me),
            web.get("/api/schedule", self.schedule),
            web.post("/api/schedule/recalculate", self.recalculate_schedule),
            web.get("/api/tasks", self.list_tasks),
            web.get("/api/routines", self.list_routines),
            web.get("/api/time-blocks", self.list_time_blocks),
            web.post(r"/api/tasks/{item_id:\d+}/spend", self.spend_task),
            web.post(r"/api/routines/{item_id:\d+}/skip", self.skip_routine),
            web.static("/static", self.static_dir),
        ]
        for path, kind in self.kinds.items():
            routes += [
                web.post(f"/api/{path}", self._for_kind(self.create_item, kind)),
                web.put(rf"/api/{path}/{{item_id:\d+}}", self._for_kind(self.update_item, kind)),
                web.delete(rf"/api/{path}/{{item_id:\d+}}", self._for_kind(self.delete_item, kind)),
            ]
        app.add_routes(routes)
        return app

    @staticmethod
    def _for_kind(method, kind: ItemKind):
        async def handler(request: web.Request) -> web.StreamResponse:
            return await method(kind, request)

        return handler

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

    async def schedule(self, request: web.Request) -> web.Response:
        """The last solve, or a new one if the data has changed since: cheap whenever nothing has."""
        user_id = self._user_id(request)
        return web.json_response(schedule_to_json(await self._solved(user_id, force=False)))

    async def recalculate_schedule(self, request: web.Request) -> web.Response:
        """A new solve whatever the data, for a schedule that starts now rather than at the last one."""
        user_id = self._user_id(request)
        schedule = await self._solved(user_id, force=True)

        # The Discord message redraws from the solve just made: the engine keeps
        # it, and nothing has changed since, so that costs no second solve.
        self._changed(user_id)
        return web.json_response(schedule_to_json(schedule))

    async def _solved(self, user_id: int, force: bool) -> SolvedSchedule:
        try:
            return await self.get_schedule(user_id, force=force)
        except TimeoutError as e:
            # The engine's own words: what took too long, and what to shorten.
            raise _error(web.HTTPServiceUnavailable, str(e)) from None
        except Exception:
            logger.exception("Could not solve the schedule of user %s", user_id)
            raise _error(web.HTTPInternalServerError, "The schedule could not be calculated.") from None

    async def list_tasks(self, request: web.Request) -> web.Response:
        user_id = self._user_id(request)
        return web.json_response(
            {
                "tasks": [task_to_dict(t) for t in self.provider.list_tasks(user_id)],
                "completed": [task_to_dict(t) for t in self.provider.list_completed_tasks(user_id)],
                "limits": {
                    "max_items": max_tasks_per_user(),
                    "name_length": MAX_NAME_LENGTH,
                    "description_length": MAX_DESCRIPTION_LENGTH,
                },
                "defaults": validator_defaults(validate_task_creation_data, TASK_DEFAULTS),
            }
        )

    async def list_routines(self, request: web.Request) -> web.Response:
        user_id = self._user_id(request)
        return web.json_response(
            {
                "routines": [routine_to_dict(r) for r in self.provider.list_routines(user_id)],
                "limits": {"max_items": max_routines_per_user(), "name_length": MAX_NAME_LENGTH},
                "defaults": validator_defaults(validate_routine_creation_data, ROUTINE_DEFAULTS),
            }
        )

    async def list_time_blocks(self, request: web.Request) -> web.Response:
        user_id = self._user_id(request)
        return web.json_response(
            {
                "time_blocks": [timeblock_to_dict(b) for b in self.provider.list_time_blocks(user_id)],
                "limits": {"max_items": max_timeblocks_per_user(), "name_length": MAX_NAME_LENGTH},
                "defaults": {},
            }
        )

    async def create_item(self, kind: ItemKind, request: web.Request) -> web.Response:
        user_id = self._user_id(request)
        row = await self._json_body(request)

        with _as_http_errors():
            item = kind.from_json(row, user_id)
            kind.add(user_id, item)

        self._changed(user_id)
        return web.json_response(kind.to_dict(item), status=201)

    async def update_item(self, kind: ItemKind, request: web.Request) -> web.Response:
        user_id = self._user_id(request)
        item_id = int(request.match_info["item_id"])
        row = await self._json_body(request)

        with _as_http_errors():
            item = kind.from_json(row, user_id, item_id)
            found = kind.edit(user_id, item_id, **kind.replaced_fields(item))

        if not found:
            raise _error(web.HTTPNotFound, f"{kind.noun} {item_id} not found.")

        self._changed(user_id)
        return web.json_response(kind.to_dict(kind.get(user_id, item_id)))

    async def delete_item(self, kind: ItemKind, request: web.Request) -> web.Response:
        user_id = self._user_id(request)
        item_id = int(request.match_info["item_id"])

        if not kind.remove(user_id, item_id):
            raise _error(web.HTTPNotFound, f"{kind.noun} {item_id} not found.")

        self._changed(user_id)
        return web.Response(status=204)

    async def spend_task(self, request: web.Request) -> web.Response:
        user_id = self._user_id(request)
        task_id = int(request.match_info["item_id"])
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

    async def skip_routine(self, request: web.Request) -> web.Response:
        """`/routine skip`: through a given date (`resume_after`, `DD.MM.YYYY`), for a number of `days`, or today."""
        user_id = self._user_id(request)
        routine_id = int(request.match_info["item_id"])
        body = await self._json_body(request)

        with _as_http_errors():
            body = _object(body)
            resume_after = validate_skip_routine_data(
                days=_whole_number(body, "days"), resume_after=_text(body, "resume_after")
            )

        if not self.provider.skip_routine(user_id, routine_id, resume_after):
            raise _error(web.HTTPNotFound, f"Routine {routine_id} not found.")

        self._changed(user_id)
        return web.json_response(routine_to_dict(self.provider.get_routine(user_id, routine_id)))
