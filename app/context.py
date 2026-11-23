"""Settings and the per-request context. Everything secret comes from environment variables."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()   # local runs read .env; on Hugging Face the Space secrets are already env vars

# Model settings (see README "Model notes"). claude-haiku-5-5 rejects a non-default temperature,
# so TEMPERATURE is only sent when you set it explicitly.
MODEL = os.getenv("MODEL", "anthropic:claude-haiku-5-5")
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "1024"))
EFFORT = os.getenv("EFFORT", "low")                      # low | medium | high
TEMPERATURE = os.getenv("TEMPERATURE", "none")           # "none" = don't send it

STORE_NAME = os.getenv("STORE_NAME", "Bazaar Lane")
DATABASE_URL = os.getenv("DATABASE_URL", "")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")

# Guards (budget and safety)
MAX_MESSAGE_CHARS = 2000      # longer messages are blocked by the safety node
HISTORY_MESSAGES = 16         # the model sees only the last 16 messages of a thread
MODEL_CALL_LIMIT = 6          # model calls per customer message
TOOL_CALL_LIMIT = 8           # tool calls per customer message
MEMORY_LIMIT = 20             # long-term facts loaded into every prompt
RATE_LIMIT_PER_MIN = int(os.getenv("RATE_LIMIT_PER_MIN", "30"))   # per user_id, 0 = off

# Price per million tokens, for the dashboard's cost estimate (claude-haiku-5-5, prompts <= 100K tokens)
PRICE_IN_PER_M = float(os.getenv("PRICE_IN_PER_M", "0.10"))
PRICE_OUT_PER_M = float(os.getenv("PRICE_OUT_PER_M", "0.50"))


@dataclass
class ShopContext:
    """Set by the server for every request, never by the model. Tools read user_id from here."""
    user_id: str = "studio-user"
