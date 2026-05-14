"""
Configuration for Sebco Market Intel.

Database path defaults to local. To share via OneDrive, create a config.json
file in this directory with:
    {"db_path": "/Users/Shared/OneDrive/sebco-market-intel/market_data.db"}
or on Windows:
    {"db_path": "C:\\Users\\YourName\\OneDrive\\sebco-market-intel\\market_data.db"}
"""

import json
import os

_DIR = os.path.dirname(os.path.abspath(__file__))
_CONFIG_FILE = os.path.join(_DIR, "config.json")

DEFAULT_DB_PATH = os.path.join(_DIR, "market_data.db")


def load_config() -> dict:
    if os.path.exists(_CONFIG_FILE):
        with open(_CONFIG_FILE, "r") as f:
            return json.load(f)
    return {}


def get_db_path() -> str:
    cfg = load_config()
    return cfg.get("db_path", DEFAULT_DB_PATH)
