import json
import core.cache
import asyncio


async def u_decline(number, forms):
    """
    Відмінює українське слово після числа.

    :param number: число (int)
    :param forms: список з 3 форм слова: ['година', 'години', 'годин']
    :return: рядок: "число слово"
    """
    number = abs(int(number))
    last_two = number % 100
    last = number % 10

    if 11 <= last_two <= 14:
        form = forms[2]
    elif last == 1:
        form = forms[0]
    elif 2 <= last <= 4:
        form = forms[1]
    else:
        form = forms[2]

    return f"{number} {form}"


def format_embed_data(data, **kwargs):
    if isinstance(data, dict):
        return {key: format_embed_data(value, **kwargs) for key, value in data.items()}
    elif isinstance(data, list):
        return [format_embed_data(item, **kwargs) for item in data]
    elif isinstance(data, str):
        try:
            return data.format(**kwargs)
        except KeyError:
            return data
    else:
        return data


def _split_text(text, max_length=2000):
    chunks = []
    while len(text) > max_length:
        split_pos = text.rfind("\n", 0, max_length)
        if split_pos == -1 or split_pos == 0:
            split_pos = text.rfind(" ", 0, max_length)
        if split_pos == -1 or split_pos == 0:
            split_pos = max_length

        chunks.append(text[:split_pos])
        text = text[split_pos:].lstrip("\n")
    if text:
        chunks.append(text)
    return chunks


def paginate_text(text: str, max_chars: int = 1000) -> list[str]:
    pages = []
    lines = text.split("\n")
    current_page = ""
    for line in lines:
        if len(current_page) + len(line) + 1 > max_chars:
            if current_page:
                pages.append(current_page)
            current_page = line + "\n"
        else:
            current_page += line + "\n"
    if current_page:
        pages.append(current_page)

    if not pages:
        pages = ["Порожньо"]
    return pages


async def send_long_message(target, text, max_length=2000, **kwargs):
    # TODO: epheremal fix
    chunks = _split_text(text, max_length)
    sent_messages = []
    for chunk in chunks:
        msg = await target.send(chunk, **kwargs)
        sent_messages.append(msg)
        await asyncio.sleep(0.25)
    return sent_messages


def get_phrases(guild_id=None):
    """
    Returns a dictionary of phrases for a specific server.
    If no arguments are provided or guild_id=None, it returns phrases from the “global” key.
    """
    if guild_id is None:
        return core.cache._phrases.get("global", {})
    return core.cache._phrases.get(str(guild_id), {})


async def load_phrases():
    try:
        with open("phrases.json", "r", encoding="utf-8") as file:
            new_phrases = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Error loading phrases: {e}")
        return

    core.cache._phrases.clear()
    core.cache._phrases.update(new_phrases)
