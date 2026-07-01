---
name: owned-baseline-url
description: Use when a URL refers to clothing the user owns, bought, or frequently wears and should become baseline evidence.
triggers: 샀, 구매, 예전, 옛날, 가지고, 자주 입, owned, bought, purchased
tools: plan_context_and_sources, load_closet_memory, enrich_closet_item_from_web, search_web_candidates, fetch_product_page, classify_product_assets, extract_size_material_info, normalize_product_spec, score_candidate_match, write_purchase_report
priority: 7
---

# Owned Baseline URL

1. Classify the user URL as baseline evidence, not a recommendation candidate.
2. Fetch that URL directly before running a separate baseline search.
3. Extract and normalize measurements, material, size, and design evidence.
4. Persist the enriched item in run memory and use it for later candidate comparison.
5. Do not ask for the same URL again when it is already stored.

