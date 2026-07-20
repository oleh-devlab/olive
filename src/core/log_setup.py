import logging
from core.config import app_config

initial_debug_mode = app_config.getint("DEFAULT", "debug_mode", fallback=0)

logging.basicConfig(
    level=logging.DEBUG if initial_debug_mode else logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
