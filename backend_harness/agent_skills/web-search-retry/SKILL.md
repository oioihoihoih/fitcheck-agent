---
name: web-search-retry
description: Use when the user asks to search the web, search again, or continue discovery from a short follow-up command.
triggers: 웹서치, 웹 검색, 검색해, 찾아봐, 다시 찾아, search web, web search
tools: plan_context_and_sources, load_closet_memory, search_web_candidates, fetch_product_page, classify_product_assets, extract_size_material_info, normalize_product_spec, score_candidate_match, ask_missing_info, write_purchase_report
priority: 6
---

# Web Search Retry

1. Reuse accumulated user text, replies, source references, and search constraints.
2. Do not ask the user to repeat information already in run state.
3. Refresh the search provider and vary or broaden the query when prior results were empty or weak.
4. Continue candidate enrichment before asking for missing information.

