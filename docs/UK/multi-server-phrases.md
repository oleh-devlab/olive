# Мультисерверна підтримка фраз

Система фраз (`phrases.json`) підтримує окремі набори текстів для кожного Discord-сервера. Це дозволяє боту відповідати різними мовами або стилями залежно від сервера.

## Структура `phrases.json`

Першим ключем є ID сервера (string) або `"global"` для фраз без серверного контексту:

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

При завантаженні кожна серверна секція **deep-merge-иться поверх global** — серверний запис успадковує кожен глобальний ключ, який він не перевизначає.

## Використання

Доступ до фраз здійснюється через `get_phrases()` з `core/utils.py`, завжди з inline fallback-рядком:

```python
from core.utils import get_phrases

# Фрази для конкретного сервера
text = get_phrases(guild_id).get("errors", {}).get("cooldown_message", "Fallback text")

# Глобальні фрази (без серверного контексту)
text = get_phrases().get("main", {}).get("token_file_not_found", "Fallback text")
```

| Виклик | Результат |
|---|---|
| `get_phrases()` | Фрази з ключа `"global"` |
| `get_phrases(guild_id)` | Об'єднані фрази для `str(guild_id)`, з fallback на global, якщо ключ серверу не знайдено |

Коли ключ серверу не знайдено, `get_phrases(guild_id)` повертає глобальні фрази.

### `format_phrase()` для безпечного рендерингу

`phrases.json` редагується вручну, тому неправильний placeholder не повинен крашити бота. Використовуйте `format_phrase()`, коли фраза містить `{placeholder}` шаблони:

```python
from core.utils import format_phrase, get_phrases

section = get_phrases(guild_id).get("errors", {})
text = format_phrase(section, "cooldown_message", "Default: {seconds}s left", seconds=42)
```

Якщо placeholder-и шаблону не збігаються з keyword-аргументами, `format_phrase()` логує попередження і повертає default-рядок. `format_embed_data()` працює за тим самим принципом для embed-словників.

## Вибір правильного контексту

### Guild-context — використовується `get_phrases(guild_id)`

Коли в коді є доступ до об'єкта серверу — slash-команди, prefix-команди, event listeners типу `on_message` або `on_command_error`:

```python
text = get_phrases(inter.guild.id).get("utils", {}).get("ping_response", "...")
text = get_phrases(message.guild.id).get("olive", {}).get("system_instruction", "...")
```

### Channel-context — використовується `get_phrases(channel.guild.id)`

Коли бот відправляє повідомлення в канал, але не має прямого посилання на guild — error handlers, сповіщення при старті:

```python
channel = await self.bot.get_or_fetch_channel(channel_id)
text = get_phrases(channel.guild.id).get("category", {}).get("key", "...")
```

### Global-context — використовується `get_phrases()`

Коли серверного контексту взагалі немає — виклики `print()`, embed-генератори, що пишуть у спільний кеш, ініціалізація модулів:

```python
raw_embed_data = get_phrases().get("uptime_embed", {}).get("embed_data", {...})
text = get_phrases().get("utils", {}).get("on_connected", "Bot connected.")
```
