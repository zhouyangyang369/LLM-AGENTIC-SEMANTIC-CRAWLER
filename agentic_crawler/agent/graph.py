"""
LangGraph ワークフロー定義。

グラフ構造:
  load_sitemap
    → filter_urls
    → find_navigation
    → discover_subsites
    → crawl_pages
    → process_subsites
    → crawl_follow_queue
    → tavily_fallback
    → audit_completeness ─→ (is_complete=False かつ rounds < max) → tavily_fallback
                         └→ (完了) → finalize
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from langgraph.graph import StateGraph, END
from typing import Literal

from agent.schemas import AgentState
from agent.nodes import (
    node_load_sitemap,
    node_filter_urls,
    node_find_navigation,
    node_discover_subsites,
    node_crawl_pages,
    node_process_subsites,
    node_crawl_follow_queue,
    node_tavily_fallback,
    node_audit_completeness,
    node_finalize,
)
from config import MAX_AUDIT_ROUNDS


def _route_after_audit(state: AgentState) -> Literal["tavily_fallback", "finalize"]:
    """完備性審査後のルーティング"""
    if not state.is_complete and state.audit_rounds < MAX_AUDIT_ROUNDS:
        return "tavily_fallback"
    return "finalize"


def build_graph() -> StateGraph:
    builder = StateGraph(AgentState)

    # ノード登録
    builder.add_node("load_sitemap",        node_load_sitemap)
    builder.add_node("filter_urls",         node_filter_urls)
    builder.add_node("find_navigation",     node_find_navigation)
    builder.add_node("discover_subsites",   node_discover_subsites)
    builder.add_node("crawl_pages",         node_crawl_pages)
    builder.add_node("process_subsites",    node_process_subsites)
    builder.add_node("crawl_follow_queue",  node_crawl_follow_queue)
    builder.add_node("tavily_fallback",     node_tavily_fallback)
    builder.add_node("audit_completeness",  node_audit_completeness)
    builder.add_node("finalize",            node_finalize)

    # エッジ（直線フロー）
    builder.set_entry_point("load_sitemap")
    builder.add_edge("load_sitemap",       "filter_urls")
    builder.add_edge("filter_urls",        "find_navigation")
    builder.add_edge("find_navigation",    "discover_subsites")
    builder.add_edge("discover_subsites",  "crawl_pages")
    builder.add_edge("crawl_pages",        "process_subsites")
    builder.add_edge("process_subsites",   "crawl_follow_queue")
    builder.add_edge("crawl_follow_queue", "tavily_fallback")
    builder.add_edge("tavily_fallback",    "audit_completeness")

    # 条件分岐: 完備でなければ tavily_fallback へ戻る
    builder.add_conditional_edges(
        "audit_completeness",
        _route_after_audit,
        {
            "tavily_fallback": "tavily_fallback",
            "finalize":        "finalize",
        },
    )
    builder.add_edge("finalize", END)

    return builder.compile()


# グラフシングルトン
_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph
