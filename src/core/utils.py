import json
import core.cache

# ---- UTILS ----

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


# ---- PHRASES TOOLS ----

def get_phrases(guild_id=None):
    """
    Returns a dictionary of phrases for a specific server.
    If no arguments are provided or guild_id=None, it returns phrases from the “global” key.
    """
    if guild_id is None:
        return core.cache._phrases.get("global", {})
    return core.cache._phrases.get(str(guild_id), {})

async def load_phrases():
    with open("phrases.json", "r", encoding="utf-8") as file:
        new_phrases = json.load(file)

    core.cache._phrases.clear()
    core.cache._phrases.update(new_phrases)