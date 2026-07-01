---
name: uploaded-image-evidence
description: Use when the user supplies clothing photos, labels, screenshots, or size-chart images.
triggers: 사진, 이미지, 라벨, 사이즈표, screenshot, image, photo
tools: inspect_uploaded_images, plan_context_and_sources, load_closet_memory, search_web_candidates, extract_size_material_info, normalize_product_spec, score_candidate_match, ask_missing_info, write_purchase_report
priority: 7
---

# Uploaded Image Evidence

1. Inspect attachments before making visual or OCR claims.
2. Treat file metadata as metadata only, not evidence of fit, color, material, or condition.
3. Store OCR or vision output as observations with provider labels.
4. Merge only observed fields into baseline or candidate state.
5. Ask for a clearer image only after the configured image/OCR tools fail.

