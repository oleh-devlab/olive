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

Three tabs — Tasks, Routines, Time blocks — with the same kind of table:

- Double-click a row or press ✎ to turn it into inputs. Enter saves, Esc cancels.
- The "+" row at the bottom adds a new item, prefilled with the same defaults as the matching command.
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
