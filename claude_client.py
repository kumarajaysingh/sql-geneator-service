import os
from functools import lru_cache

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()


@lru_cache(maxsize=1)
def get_claude_client() -> Anthropic:
    """Build and cache a singleton Claude client using the API key from .env."""
    api_key = os.getenv("CLAUDE_API_KEY")
    if not api_key:
        raise RuntimeError("CLAUDE_API_KEY is not set. Add it to your .env file.")
    return Anthropic(api_key=api_key)
