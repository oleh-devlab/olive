# Multi-Server Phrases Support

The phrases system (`phrases.json`) supports separate text sets for each Discord server. This allows the bot to respond in different languages or styles depending on the server.

## `phrases.json` Structure

The top-level key is a server ID (string) or `"global"` for phrases without a server context:

```json
{
    "global": {
        "main": { "token_file_not_found": "..." },
        "utils": { "on_connected": "...", "on_resumed": "..." }
    },
    "123456789012345678": {
        "errors": { "cooldown_message": "...", "access_denied": "..." },
        "utils": { "ping_response": "..." }
    }
}
```

## Usage

Phrases are accessed via the `get_phrases()` function from `core/utils.py`:

```python
from core.utils import get_phrases

# Phrases for a specific server
text = get_phrases(guild_id).get("errors", {}).get("cooldown_message", "Fallback text")

# Global phrases (no server context)
text = get_phrases().get("main", {}).get("token_file_not_found", "Fallback text")
```

| Call | Result |
|---|---|
| `get_phrases()` | Phrases from the `"global"` key |
| `get_phrases(guild_id)` | Phrases for server `str(guild_id)` |

If the key is not found, an empty `{}` is returned. All `.get()` calls with fallback values continue to work as before.

## Choosing the Right Context

### Guild-context — use `get_phrases(guild_id)`

When the code has access to a guild object — slash commands, prefix commands, event listeners like `on_message` or `on_command_error`:

```python
text = get_phrases(inter.guild.id).get("utils", {}).get("ping_response", "...")
text = get_phrases(message.guild.id).get("olive", {}).get("system_instruction", "...")
```

### Channel-context — use `get_phrases(channel.guild.id)`

When the bot sends a message to a channel but doesn't have a direct guild reference — error handlers, startup notifications:

```python
channel = await self.bot.get_or_fetch_channel(channel_id)
text = get_phrases(channel.guild.id).get("category", {}).get("key", "...")
```

### Global-context — use `get_phrases()`

When there is no server context at all — `print()` calls, embed generators that write to a shared cache, module initialization:

```python
raw_embed_data = get_phrases().get("uptime_embed", {}).get("embed_data", {...})
text = get_phrases().get("utils", {}).get("on_connected", "Bot connected.")
```
