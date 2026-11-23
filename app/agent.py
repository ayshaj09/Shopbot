"""The ShopBot graph:

    START -> [safety] --blocked--> END
                | ok
                v
           [shop_agent]  (create_agent: model <-> tools loop)
                |
                v
               END

safety is plain code (no model call), so a blocked message costs 0 tokens.
"""
import re
from typing import Annotated, Any, NotRequired, TypedDict

from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain.messages import AIMessage, AnyMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from app.context import EFFORT, MAX_MESSAGE_CHARS, MAX_TOKENS, MODEL, TEMPERATURE, ShopContext
from app.middleware import build_middleware
from app.tools import TOOLS

# ── safety node ──
# Known prompt-injection and data-theft phrasings. Case-insensitive; checked before the model runs.
INJECTION_PATTERNS = [
    r"\b(ignore|disregard|forget|override|bypass)\b.{0,40}\b(instructions?|rules|prompts?|guidelines|system)\b",
    r"\b(reveal|print|show|repeat|output|display|leak|tell me)\b.{0,40}\b(your (instructions|rules|prompt)|"
    r"hidden prompt|initial prompt|developer (message|prompt))\b",
    r"\bsystem prompt\b",
    r"\b(developer|dan|god|jailbreak|admin|debug) mode\b",
    r"\bjailbreak",
    r"\byou are no longer\b|\bfrom now on,? you (are|will)\b",
    r"\b(act|pretend|roleplay) (as|to be)\b.{0,40}\b(unrestricted|unfiltered|no rules|without (any )?restrictions|another ai)\b",
    r"\b(other|another)\s+(users?|customers?)('s?|s')?\b.{0,30}\b(orders?|data|address(es)?|emails?|details|memor(y|ies)|history|phone)",
    r"\beveryone'?s\s+(orders?|data|address(es)?|details)\b",
    r"\b(dump|export)\b.{0,30}\b(database|users|customers|orders)\b|\b(list|show)\b.{0,10}\ball (users|customers)\b",
    r"\b(api[ _-]?key|secret key|admin token|password of|env(ironment)? variables?)\b",
    r"\b(drop|truncate|delete from|alter)\s+table\b|\bunion\s+select\b|;\s*--",
    r"\b(set|change|update|make)\b.{0,30}\b(price|stock)\b.{0,30}\b(to|=)\s*(rs\.?\s*)?\d",
]
INJECTION = re.compile("|".join(f"(?:{p})" for p in INJECTION_PATTERNS), re.I | re.S)

BLOCKED_REPLY = ("Sorry, I can't help with that. I'm ShopBot, the Bazaar Lane shopping assistant: I can help you "
                 "find products, place an order or track a delivery.")
TOO_LONG_REPLY = f"Your message is too long (over {MAX_MESSAGE_CHARS:,} characters). Please send a shorter one."


class GraphState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    pending_action: NotRequired[dict[str, Any] | None]
    blocked: NotRequired[bool]


def check_message(text: str) -> str | None:
    """Return the refusal to send, or None if the message may go to the model."""
    if len(text) > MAX_MESSAGE_CHARS:
        return TOO_LONG_REPLY
    if INJECTION.search(text):
        return BLOCKED_REPLY
    return None


def safety(state: GraphState) -> dict:
    last = state["messages"][-1]
    refusal = check_message(last.text if isinstance(last, HumanMessage) else "")
    if refusal:
        return {"messages": [AIMessage(refusal)], "blocked": True}
    return {"blocked": False}


def route_after_safety(state: GraphState) -> str:
    return END if state.get("blocked") else "shop_agent"


# ── model ──
def build_model():
    """claude-haiku-5-5 notes: no custom temperature (the API rejects it), thinking off so the
    trimmed history and per-turn prompt stay valid, effort 'low' for short, cheap replies."""
    kwargs: dict[str, Any] = {"max_tokens": MAX_TOKENS, "max_retries": 2, "timeout": 60}
    if MODEL.startswith("anthropic:"):
        kwargs["thinking"] = {"type": "disabled"}
        if EFFORT:
            kwargs["effort"] = EFFORT
    if TEMPERATURE.lower() != "none":
        kwargs["temperature"] = float(TEMPERATURE)
    return init_chat_model(MODEL, **kwargs)


def build_shop_agent(model=None):
    return create_agent(model or build_model(), tools=TOOLS, context_schema=ShopContext,
                        middleware=build_middleware(), name="shop_agent")


def build_graph(model=None, checkpointer=None, store=None):
    builder = StateGraph(GraphState, context_schema=ShopContext)
    builder.add_node("safety", safety)
    builder.add_node("shop_agent", build_shop_agent(model))
    builder.add_edge(START, "safety")
    builder.add_conditional_edges("safety", route_after_safety, ["shop_agent", END])
    builder.add_edge("shop_agent", END)
    return builder.compile(checkpointer=checkpointer, store=store)
