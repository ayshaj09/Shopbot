"""Graph for `langgraph dev` (LangGraph Studio). The dev server supplies its own checkpointer and
store; the tools still need DATABASE_URL for products and orders.

    langgraph dev        # then open the Studio link it prints
"""
from app.agent import build_graph
from app.db import setup_database

setup_database()
graph = build_graph()
