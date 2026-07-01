from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse


def build_web_candidate_fixtures(probe_result: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert keyword web probe output into product-analysis fixtures."""
    image_ocr_by_source = _image_ocr_by_source(probe_result.get("image_results", []))
    candidates: list[dict[str, Any]] = []

    for page in probe_result.get("page_results", []):
        if page.get("status") != "completed":
            continue
        source_id = str(page.get("source_id") or _slugify(page.get("title") or page.get("url") or "candidate"))

        page_specs = page.get("extracted_specs", {})
        image_specs = image_ocr_by_source.get(source_id, {})
        merged = _merge_specs(page_specs, image_specs)

        if not merged:
            continue

        assets = [
            {
                "name": f"{source_id} html extracted spec",
                "asset_type_hint": "detail_text",
                "ocr_text": _spec_to_text(page_specs),
                "ocr_confidence": 0.72 if page_specs else 0.0,
            }
        ]
        if image_specs:
            assets.append(
                {
                    "name": f"{source_id} visual ocr spec",
                    "asset_type_hint": "size_chart",
                    "ocr_text": _spec_to_text(image_specs),
                    "ocr_confidence": 0.86,
                }
            )

        candidate = {
            "candidate_key": f"web_{_slugify(source_id)}",
            "category": _infer_category(page),
            "brand": _infer_brand(page),
            "name": _infer_name(page),
            "source_url": page.get("url"),
            "price": merged.get("price"),
            "target_size_label": merged.get("size_label"),
            "fit_keywords": _fit_keywords(page, merged),
            "explicit_fields": {
                "measurements": merged.get("measurements", {}),
                "material": merged.get("material", {}),
                "field_confidence": _field_confidence(merged),
            },
            "web_probe_metadata": {
                "source_id": source_id,
                "keyword_hits": page.get("keyword_hits", {}),
                "spec_sources": merged.get("spec_sources", []),
                "note": "Generated from explicit keyword web probe inputs.",
            },
            "assets": assets,
        }
        candidates.append(candidate)

    return candidates


def _field_confidence(merged: dict[str, Any]) -> dict[str, float]:
    sources = set(merged.get("spec_sources", []))
    confidence: dict[str, float] = {}
    if "visual_ocr_measurements" in sources:
        confidence["measurements"] = 0.86
    elif "html_measurements" in sources:
        confidence["measurements"] = 0.72

    if "visual_ocr_material" in sources:
        confidence["material"] = 0.86
    elif "html_material" in sources:
        confidence["material"] = 0.72

    if "html_price" in sources:
        confidence["price"] = 0.78

    return confidence


def _image_ocr_by_source(image_results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for image in image_results:
        ocr = image.get("ocr_result") or image.get("codex_visual_ocr") or {}
        normalized = ocr.get("normalized") or {}
        if not normalized:
            continue
        if normalized.get("spec_usefulness") == "style_only":
            continue
        output.setdefault(image["source_id"], {}).update(normalized)
    return output


def _merge_specs(page_specs: dict[str, Any], image_specs: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    spec_sources: list[str] = []

    if page_specs.get("measurements"):
        merged["measurements"] = page_specs["measurements"]
        spec_sources.append("html_measurements")
    if page_specs.get("material"):
        merged["material"] = page_specs["material"]
        spec_sources.append("html_material")
    if page_specs.get("price") is not None:
        merged["price"] = page_specs["price"]
        spec_sources.append("html_price")

    if image_specs.get("measurements"):
        merged["measurements"] = image_specs["measurements"]
        spec_sources.append("visual_ocr_measurements")
    if image_specs.get("material"):
        merged["material"] = image_specs["material"]
        spec_sources.append("visual_ocr_material")
    if image_specs.get("size_label"):
        merged["size_label"] = image_specs["size_label"]
        spec_sources.append("visual_ocr_size_label")
    if image_specs.get("fabric_notes"):
        merged["fabric_notes"] = image_specs["fabric_notes"]
    if image_specs.get("washing_notes"):
        merged["washing_notes"] = image_specs["washing_notes"]

    merged["spec_sources"] = spec_sources
    return merged


def _spec_to_text(spec: dict[str, Any]) -> str:
    parts: list[str] = []
    measurements = spec.get("measurements", {})
    if measurements:
        label_map = {
            "length": "총장",
            "shoulder": "어깨",
            "chest": "가슴단면",
            "sleeve": "소매길이",
        }
        parts.append(
            " ".join(
                f"{label_map[key]} {value}"
                for key, value in measurements.items()
                if key in label_map
            )
        )
    material = spec.get("material", {})
    if material:
        material_label = {"cotton": "면", "polyester": "폴리에스터"}.get
        parts.append(
            " ".join(
                f"{material_label(key, key)} {value}%"
                for key, value in material.items()
            )
        )
    if spec.get("price") is not None:
        parts.append(f"가격 {spec['price']}원")
    return "\n".join(part for part in parts if part)


def _infer_brand(page: dict[str, Any]) -> str:
    explicit = page.get("brand")
    if explicit:
        return str(explicit)
    host = urlparse(str(page.get("url") or "")).netloc
    host = host.removeprefix("www.").removeprefix("m.")
    return host.split(".")[0] if host else "unknown"


def _infer_name(page: dict[str, Any]) -> str:
    return str(page.get("name") or page.get("title") or page.get("source_id") or "candidate").strip()


def _fit_keywords(page: dict[str, Any], merged: dict[str, Any]) -> list[str]:
    keywords = [
        str(keyword)
        for keyword, matched in (page.get("keyword_hits") or {}).items()
        if matched
    ]
    text = " ".join(
        str(value or "")
        for value in [
            page.get("title"),
            page.get("text_sample"),
            merged.get("fabric_notes"),
            merged.get("washing_notes"),
        ]
    ).lower()
    inferred = {
        "oversized": ["오버", "박시", "oversize", "boxy"],
        "pigment_dyed": ["피그먼트", "pigment"],
        "charcoal": ["차콜", "먹색", "charcoal"],
        "durable_finish": ["텐타", "덤블", "워싱", "내구", "탄탄"],
    }
    for label, needles in inferred.items():
        if any(needle in text for needle in needles):
            keywords.append(label)
    return sorted(set(keywords))


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9가-힣]+", "_", str(value)).strip("_").lower()
    return slug or "candidate"


def _infer_category(page: dict[str, Any]) -> str:
    text = " ".join(str(page.get(key) or "") for key in ["title", "name", "text_sample"]).lower()
    category_keywords = [
        ("t_shirt", ["반팔", "티셔츠", "tee", "t-shirt"]),
        ("hoodie", ["후드티", "후디", "hoodie"]),
        ("shirt", ["셔츠", "남방"]),
        ("knit", ["니트", "스웨터"]),
        ("pants", ["바지", "팬츠", "슬랙스", "데님", "청바지"]),
        ("jacket", ["자켓", "재킷", "점퍼", "아우터"]),
    ]
    for category, keywords in category_keywords:
        if any(keyword in text for keyword in keywords):
            return category
    return "unknown"
