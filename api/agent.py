"""
LangGraph agent for intelligent query routing and retrieval.

Graph:
  START → classify → [simple]  → retrieve → guard → END
                   → [complex] → decompose → retrieve_multi → merge → guard → END

Adds real value over plain /retrieve:
- Comparison questions get decomposed into sub-queries for targeted retrieval
- Faithfulness guard flags low-confidence results
- Graph metadata shows the decision path (useful for debugging and interviews)
"""
import os
from typing import TypedDict
from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
class AgentState(TypedDict):
    query: str                          # Original user query
    query_type: str                     # "simple" | "complex"
    sub_queries: list[str]              # Decomposed queries (complex only)
    chunks: list[dict]                  # Final retrieved chunks
    context_sufficient: bool            # Guard result
    guard_reason: str                   # Why guard flagged (if it did)
    steps: list[str]                    # Trace of graph execution


# ---------------------------------------------------------------------------
# Retrieval helper (uses existing function_app functions)
# ---------------------------------------------------------------------------
def _retrieve_chunks(query: str, embed_fn, search_fn, rerank_fn) -> list[dict]:
    """Run the full retrieval pipeline for a single query."""
    vector = embed_fn(query)
    raw = search_fn(vector)
    return rerank_fn(query, raw)


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------
def build_graph(embed_fn, search_fn, rerank_fn):
    """Build and compile the LangGraph agent.

    Args:
        embed_fn: function(query) -> vector
        search_fn: function(vector, top_k) -> chunks
        rerank_fn: function(query, chunks, top_n) -> reranked_chunks
    """
    # Shared LLM — reused across classifier and decomposer nodes
    llm = ChatOpenAI(
        model=os.environ.get("LLM_MODEL", "gpt-5.4-mini"),
        api_key=os.environ["OPENAI_API_KEY"],
        temperature=0,
    )

    # Closed-over retrieval function — no global state needed
    def do_retrieve(query: str) -> list[dict]:
        return _retrieve_chunks(query, embed_fn, search_fn, rerank_fn)

    # -------------------------------------------------------------------
    # Nodes (closures over llm and do_retrieve)
    # -------------------------------------------------------------------
    def classify_query(state: AgentState) -> dict:
        """Classify query as simple or complex using LLM."""
        response = llm.invoke([
            {"role": "system", "content": (
                "Classify the user's aviation regulation question as either 'simple' or 'complex'.\n"
                "- 'simple': a direct factual question about one topic (e.g., 'What are VFR fuel requirements?')\n"
                "- 'complex': a comparison, multi-part, or cross-reference question "
                "(e.g., 'Compare VFR and IFR fuel requirements', 'What are the differences between day and night VFR?')\n"
                "Respond with ONLY the word 'simple' or 'complex'."
            )},
            {"role": "user", "content": state["query"]},
        ])

        query_type = response.content.strip().lower()
        if query_type not in ("simple", "complex"):
            query_type = "simple"

        return {
            "query_type": query_type,
            "steps": state.get("steps", []) + [f"classify:{query_type}"],
        }

    def retrieve(state: AgentState) -> dict:
        """Single retrieval for simple queries."""
        chunks = do_retrieve(state["query"])
        return {
            "chunks": chunks,
            "steps": state.get("steps", []) + ["retrieve"],
        }

    def decompose(state: AgentState) -> dict:
        """Break complex query into 2-3 targeted sub-queries."""
        response = llm.invoke([
            {"role": "system", "content": (
                "Break this aviation regulation question into 2-3 simpler sub-questions "
                "that can each be answered independently. Each sub-question should target "
                "a specific regulation or topic.\n"
                "Return ONLY the sub-questions, one per line. No numbering, no extra text."
            )},
            {"role": "user", "content": state["query"]},
        ])

        sub_queries = [q.strip() for q in response.content.strip().split("\n") if q.strip()]
        if not sub_queries:
            sub_queries = [state["query"]]

        return {
            "sub_queries": sub_queries,
            "steps": state.get("steps", []) + [f"decompose:{len(sub_queries)}_sub_queries"],
        }

    def retrieve_multi(state: AgentState) -> dict:
        """Retrieve for each sub-query, merge and deduplicate."""
        all_chunks = []
        seen_sections = set()

        for sq in state["sub_queries"]:
            chunks = do_retrieve(sq)
            for c in chunks:
                key = (c["section"], c["text"][:100])
                if key not in seen_sections:
                    seen_sections.add(key)
                    all_chunks.append(c)

        return {
            "chunks": all_chunks,
            "steps": state.get("steps", []) + [f"retrieve_multi:{len(all_chunks)}_chunks"],
        }

    def guard(state: AgentState) -> dict:
        """Check if retrieved context is sufficient. Flag if not."""
        chunks = state.get("chunks", [])

        if not chunks:
            return {
                "context_sufficient": False,
                "guard_reason": "no_chunks_retrieved",
                "steps": state.get("steps", []) + ["guard:fail_no_chunks"],
            }

        # Check rerank scores — if best score is very low, context is likely irrelevant
        top_score = max(
            (c.get("rerank_score", c.get("score", 0)) for c in chunks),
            default=0,
        )

        if top_score < 0.1:
            return {
                "context_sufficient": False,
                "guard_reason": "low_relevance",
                "steps": state.get("steps", []) + [f"guard:fail_low_relevance({top_score:.3f})"],
            }

        return {
            "context_sufficient": True,
            "guard_reason": "",
            "steps": state.get("steps", []) + ["guard:pass"],
        }

    def route_by_type(state: AgentState) -> str:
        """Conditional edge: route based on query classification."""
        return "retrieve" if state["query_type"] == "simple" else "decompose"

    # -------------------------------------------------------------------
    # Build graph
    # -------------------------------------------------------------------
    graph = StateGraph(AgentState)

    graph.add_node("classify", classify_query)
    graph.add_node("retrieve", retrieve)
    graph.add_node("decompose", decompose)
    graph.add_node("retrieve_multi", retrieve_multi)
    graph.add_node("guard", guard)

    graph.set_entry_point("classify")
    graph.add_conditional_edges("classify", route_by_type, {
        "retrieve": "retrieve",
        "decompose": "decompose",
    })
    graph.add_edge("retrieve", "guard")
    graph.add_edge("decompose", "retrieve_multi")
    graph.add_edge("retrieve_multi", "guard")
    graph.add_edge("guard", END)

    return graph.compile()
