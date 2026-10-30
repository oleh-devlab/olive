# Schedule web editor

A page where the owner of a schedule channel edits their tasks, routines and time blocks as tables. Sign-in is with Discord.

It is not a separate service and not a sync. The server runs inside the bot's process (`cogs/schedule/web.py`) and writes through the same `ScheduleProvider`, into the same `data/{user_id}_schedule.json`, as `/task`, `/routine`, `/timeblock` and the agent. The schedule message is recomputed right after every save.

Only users who have a schedule channel (`/schedule_channel create`) can sign in. Deleting the channel ends access to the page at once, without waiting for the session to expire.

## Setup

1. **Discord developer portal** → your application → **OAuth2**:
   - copy the **Client ID** and **Client Secret**;
   - under **Redirects** add `<site address>/oauth/callback`, e.g. `https://olive.example.com/oauth/callback`. It has to match exactly.
2. **`src/tokens.json`**:
   ```json
   "discord_oauth": {
       "client_id": "123456789012345678",
       "client_secret": "..."
   },
   "web_session_secret": "a long random string"
   ```
   `web_session_secret` signs the session cookies. Generate one with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Without it the bot makes one up at startup, and everyone has to sign in again after every restart. Changing it ends every session.
3. **`src/settings.py`**:
   ```python
   schedule_web_base_url = "https://olive.example.com"  # what the browser sees
   schedule_web_host = "127.0.0.1"                      # where the bot listens
   schedule_web_port = 8080
   schedule_web_session_days = 30
   ```
   While `schedule_web_base_url` is empty the server does not start.
4. **Reaching it from outside.** The bot listens on `host:port` over plain HTTP. Put HTTPS in front of it: a reverse proxy (Caddy, nginx) or a tunnel (Cloudflare Tunnel, Tailscale Funnel) that forwards `schedule_web_base_url` to `127.0.0.1:8080`. This also works on Termux, which has no public address.
   - When `base_url` starts with `https://`, cookies are marked `Secure` and the browser will not send them over HTTP.
   - Do not expose the port to the internet directly without HTTPS: the session cookie would travel in clear text.
5. **Build the page**: `scripts/build-web.sh`. The page is written in React and TypeScript under `web/`, and what the bot serves from `src/static/schedule/` is built from it rather than kept in git.
   - The build runs in a Node container, so the host needs no Node: podman if it is running, otherwise docker. With neither, the script uses the host's npm (Node 26 or newer). `CONTAINER_RUNTIME=podman`, `docker` or `none` picks one.
   - The result does not depend on which one built it, and the files belong to whoever ran the script, whether the runtime is rootless or not.
   - Run it again after every `git pull` that changes `web/`. Without a built page the server does not start, and the log says so.
6. Restart the bot. Users get the link with `/schedule_channel web`.

## What the page does

At the top is the schedule itself, as a calendar: one, three or seven days side by side, only the hours from the first task or routine in them to the last — time blocks are drawn but do not stretch that, so a night's sleep takes no room — and a line at the present moment. Each kind of entry has its own colour and time blocks are hatched. Above the calendar is what the Discord message says above the schedule. When it was calculated, in how long and the solver's status go in the toolbar while they fit there whole, and on a line below it when they do not; how many days ahead it plans at the least, and how often the bot calculates it again on its own, go beside **Recalculate** — or under your name in the page's header, when that leaves the toolbar a row fewer or room for the rest, and only then, since it adds a line to the header — and the legend of the colours between them and the buttons that pick how many days are shown; under it, on one line, are the tasks, by number and name, and the routines that did not fit. Each entry is as tall as its name and times need — on one line, the times after the name, when the whole of both fit there; else a long name wraps onto as many as three lines with the times below, unless the day is too narrow to break it into words, as a week on a phone is — and a little taller the longer it is, so a long one still stands out. Breaks and free time with nothing in them on any of the days shown are collapsed into a thin dashed band (hover over it to see which time it stands for), and time holding only time blocks into a single line, so the calendar is only as tall as what is planned. A calendar taller than the screen first draws itself a little tighter, a step at a time and only as far as it needs to fit — thinner bands, less height an hour, then lines a little closer together and slightly smaller times; one still a little taller than the screen just makes the page longer; one much taller scrolls inside itself and opens at the present moment — unless **Scroll** in the toolbar is turned off, and then the page scrolls for it too. The day names stay in sight either way. Hovering over an entry shows all of it, which a very short one still has no room to.

The page shows the schedule the bot calculated last — the same one the Discord message shows — and calculates it again only when your data has changed since. So it takes no button to see a change: the schedule is read again when the page opens, after every change made on it, and when you come back to the tab. A schedule starts at the moment it was calculated, though, and the bot recalculates it on its own only every `schedule_loop_update_seconds`; **Recalculate** asks for one from now, and the Discord message then shows that one too.

Beside the calendar is a timer for the task being worked on: the session going on now in the schedule, or the next one, with the break that comes after it and how much is left on the task. It starts at the session's length, which the −5 and +5 buttons or a number typed in change. **Start** and **Pause** run it; **Log** beside them writes the time it counted off the task, as `/task spend` does — only when pressed, and never more than the timer was set to. Below it, **To log** is for time worked without the timer: set the minutes with its own −5 and +5 or by typing a number, and its own **Log** writes them off the same task. The two are apart: the timer never fills that field in, and logging one leaves the other as it is. A timer that has counted anything stays with its task even when the schedule moves on. It lives only in the open page, nothing of it is stored: a reload or a closed tab drops what it counted, so log it first, and each tab has a timer of its own. While it runs, the tab's title counts down. The « button folds the panel away into a » button at the start of the calendar's toolbar, which leaves the calendar the whole width; beside it, the time left still shows, and folding loses nothing the timer counted. The first time, on a computer, the page opens with the panel unfolded and three days, or one when three would not fit beside it; on a phone it opens folded. After that it opens as you left it: whether the panel is folded, how many days are shown and whether **Scroll** is off are the only three things the page keeps in the browser.

Below the calendar and the timer, across the whole width, are three tabs — Tasks, Routines, Time blocks — with the same kind of table:

- Double-click a row or press ✎ to turn it into inputs. Enter saves, Esc cancels.
- The "+" row at the bottom adds a new item, prefilled with the same defaults as the matching command.
- The name takes whatever width the other columns leave. The priority column is headed "P.", as the note under the table says, and a task's Description narrows to "Desc." while no task has one.
- ✕ deletes the item, and removes it from other items' dependencies.
- Tasks: ⏱ logs minutes spent, like `/task spend`; once the time runs out, the task moves to the history.
- Routines: ⏸ skips a routine up to and including the date you pick, like `/routine skip`. Editing the row leaves the skip as it is.
- Time blocks: a one-off block takes a date and time, a daily or weekly one only a time of day. An end before the start means the block runs past midnight.

A row is checked by the same validators as `/task add`, `/routine add_*` and `/timeblock add`, and the limits on how many items there may be are kept by the provider itself, the same for the commands, the agent and the page. Saving a row replaces the whole item, so a cleared cell (description, deadline, dependencies) really is cleared.

## Changing the page

```bash
cd web
npm ci
npm run watch   # rebuilds into src/static/schedule/ on every save; reload the browser
npm run lint
npm run build   # typecheck, then build
```

The bot has to be running with the web editor on for the page to have an API to talk to. The `Schedule Web Page` workflow runs the lint and the build on every push that touches `web/`, and checks that npm, docker and podman build the very same page.

## Security

- Only the `identify` scope is requested. The bot never stores Discord's access token: it is used once, to learn the user's id.
- A session is an HMAC-signed cookie (`HttpOnly`, `SameSite=Lax`). Nothing is stored on the server, so a single session cannot be revoked. Logging out drops the cookie from the browser, and changing `web_session_secret` ends every session.
- Requests that change data are accepted only from the site's own origin, and only with a JSON body.
- The page is served with a strict Content-Security-Policy: scripts and styles come from the same server only.
