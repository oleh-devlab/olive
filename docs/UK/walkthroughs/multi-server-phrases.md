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

## Використання

Доступ до фраз здійснюється через функцію `get_phrases()` з `core/utils.py`:

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
| `get_phrases(guild_id)` | Фрази для сервера `str(guild_id)` |

Якщо ключ не знайдено, повертається порожній `{}`. Усі виклики `.get()` з fallback-значеннями продовжують працювати як і раніше.

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
