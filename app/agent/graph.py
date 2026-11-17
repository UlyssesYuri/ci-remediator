from langgraph.graph import END, StateGraph

from app.agent.nodes import (
    claim,
    cleanup,
    decide,
    escalate,
    prepare,
    remediate,
    route_after_decide,
    succeed,
    validate,
)
from app.agent.state import AgentState


def initial_state(message: dict, log_s3_key: str) -> AgentState:
    """Fields the graph needs. logs_url is not one of them."""
    return {
        "run_id": message["run_id"],
        "full_name": message["full_name"],
        "clone_url": message["clone_url"],
        "github_run_id": int(message["github_run_id"]),
        "head_sha": message.get("head_sha") or "",
        "head_branch": message.get("head_branch"),
        "workflow_path": message.get("workflow_path"),
        "log_s3_key": log_s3_key,
        "sandbox_path": "",
        "log_text": "",
        "n": 1,
        "diagnosis": "",
        "test_output": "",
        "passed": False,
        "route": "",
    }


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("claim", claim)
    graph.add_node("prepare", prepare)
    graph.add_node("remediate", remediate)
    graph.add_node("validate", validate)
    graph.add_node("decide", decide)
    graph.add_node("succeed", succeed)
    graph.add_node("escalate", escalate)
    graph.add_node("cleanup", cleanup)

    graph.set_entry_point("claim")
    graph.add_edge("claim", "prepare")
    graph.add_edge("prepare", "remediate")
    graph.add_edge("remediate", "validate")
    graph.add_edge("validate", "decide")
    graph.add_conditional_edges(
        "decide",
        route_after_decide,
        {"retry": "remediate", "succeed": "succeed", "escalate": "escalate"},
    )
    graph.add_edge("succeed", "cleanup")
    graph.add_edge("escalate", "cleanup")
    graph.add_edge("cleanup", END)

    return graph.compile()


graph = build_graph()
