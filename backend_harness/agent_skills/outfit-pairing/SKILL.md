---
name: outfit-pairing
description: Use when the user wants any item to wear with, coordinate with, or style alongside an owned item; the target item is free-form.
triggers: 같이 입, 함께 입, 어울리, 코디, 매치, 스타일링, pair with, outfit
tools: plan_context_and_sources, load_closet_memory, enrich_closet_item_from_web, search_web_candidates, fetch_product_page, classify_product_assets, extract_size_material_info, normalize_product_spec, score_candidate_match, ask_missing_info, write_purchase_report
priority: 8
---

# Outfit Pairing

1. Keep the owned baseline and requested target item as separate entities.
2. Preserve the target phrase as free text through planning, search, observations, and reporting.
3. Treat category classification as an optional query hint only. An unknown or unseen target phrase
   must still be searched and must not be rejected because it is absent from a keyword list.
4. Use the free-text target item, not the baseline category, to construct and validate candidate search.
5. When results are weak or empty, revise the plan automatically: generate contextual query variants,
   change sources/domains, inspect more results, and validate product-detail pages.
6. Do not ask the user to resolve search failure, unknown vocabulary, or category ambiguity.
7. If autonomous recovery is exhausted, write a grounded no-result report from search observations.
8. Enrich the owned item when a URL or purchase clue is available.
9. Search and compare multiple target-item candidates.
10. Treat size labels as weak hints only. For same-category fit-sensitive pairing, require observed garment
    measurements before claiming fit similarity; for cross-category styling, avoid reusing baseline garment
    measurements as hard fit constraints.
11. For styling compatibility, report observed product facts and clearly label inferred coordination judgments.
