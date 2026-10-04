from langgraph.graph import StateGraph, END
from app.agent.nodes import (
    State,
    extract_profile,
    route,
    greet,
    ask,
    retrieve,
    answer,
)


def build_graph():
    g = StateGraph(State)
    g.add_node("extract_profile", extract_profile)
    g.add_node("greet", greet)
    g.add_node("ask", ask)
    g.add_node("retrieve", retrieve)
    g.add_node("answer", answer)

    g.set_entry_point("extract_profile")
    g.add_conditional_edges(
        "extract_profile",
        route,
        {"greet": "greet", "ask": "ask", "retrieve": "retrieve"},
    )
    g.add_edge("greet", END)
    g.add_edge("ask", END)
    g.add_edge("retrieve", "answer")
    g.add_edge("answer", END)
    return g.compile()