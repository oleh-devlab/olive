import configparser
from settings import paths

config_ini_path = paths["config_ini"]
app_config = configparser.ConfigParser()
app_config.read(config_ini_path)
