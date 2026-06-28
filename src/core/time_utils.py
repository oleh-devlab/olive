from datetime import datetime

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    tz = ZoneInfo("Europe/Kyiv")
except (ImportError, ZoneInfoNotFoundError, ModuleNotFoundError) as e:
    tz = datetime.now().astimezone().tzinfo
    print(f"[Warning] tzdata не знайдено, використовується локальний час. {e}")
