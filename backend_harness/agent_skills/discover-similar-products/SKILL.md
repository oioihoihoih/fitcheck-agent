---
name: discover-similar-products
description: Use when the user wants products similar to an owned or liked item by fit, material, size, silhouette, or design.
triggers: 비슷한, 유사한, 대체할, 자주 입는, similar, replace
tools: plan_context_and_sources, load_closet_memory, enrich_closet_item_from_web, search_web_candidates, fetch_product_page, classify_product_assets, extract_size_material_info, normalize_product_spec, score_candidate_match, ask_missing_info, write_purchase_report
priority: 4
---

# Similar Product Discovery

1. Resolve the liked baseline from accumulated conversation and closet memory.
2. Preserve the requested replacement or discovery target as free text. Do not require it to match a
   known category or parser keyword.
3. Enrich the baseline from its URL or purchase clues before asking the user for facts that web tools can recover.
4. Search and enrich a broad product-detail candidate set, then fetch and normalize their evidence.
5. Revise weak searches autonomously using new queries, sources, and tool observations. Do not stop
   at the first page, the first acceptable-looking item, or a size-label-only match.
6. Treat size labels such as S/M/L/free/one-size as weak search hints only. Do not claim fit similarity
   from labels alone; require observed garment measurements or explicitly mark measurement evidence missing.
7. Distinguish garment measurements from body/model measurements, ranking numbers, pack counts, and size-guide
   explanation text before scoring fit.
8. Score only from stored observations.
9. Ask the user only for irreducible personal decisions or private facts, not ordinary search failure.
10. Write a grounded shortlist only after a close measured match is found, or write a grounded
    no-result report after the close-fit search budget is exhausted.
