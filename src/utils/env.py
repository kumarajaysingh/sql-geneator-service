"""Shared helpers for reading required, typed config values from the
environment (.env). Fail fast at import time when a value is missing or
malformed, instead of silently defaulting in code.
"""

import os


def required_env_int(name: str) -> int:
    value = os.getenv(name)
    if value is None:
        raise RuntimeError(f"Missing required environment variable: {name!r}. Set it in .env.")
    try:
        return int(value)
    except ValueError:
        raise RuntimeError(f"Environment variable {name!r} must be an integer, got {value!r}.")
