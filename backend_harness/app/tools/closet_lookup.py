from __future__ import annotations

import re
from typing import Any


def lookup_existing_closet_item(parsed: dict[str, Any], traits: dict[str, Any]) -> dict[str, Any]:
    """Search boundary for enriching an existing closet item.

    The harness uses a deterministic fixture for the current item. Production
    should replace this with provider adapters such as Naver Shopping, Danawa,
    Coupang, brand pages, or a general search API.
    """
    name = parsed.get("name") or ""
    brand = parsed.get("brand") or ""
    query = " ".join(part for part in [brand, name] if part).strip()

    if "포켓 게이머" not in name:
        return {
            "status": "no_fixture_match",
            "query": query,
            "provider": "fixture_lookup",
            "external_api_called": False,
            "matches": [],
            "enrichment": {},
            "missing_after_lookup": _missing_after_lookup(parsed, {}),
        }

    matches = [
        {
            "source": "danawa",
            "title": "피그먼트 가까이 유니언즈 GAKKAI UNIONS 포켓 게이머 반팔 티셔츠 차콜 G7770106CH 270783",
            "url": "https://m.danawa.com/product/product.html?code=97756184",
            "confidence": 0.91,
            "fields": {
                "category": "t_shirt",
                "gender": "men",
                "manufacturer": "피그먼트",
                "product_code": "G7770106CH",
                "registered_month": "2025-09",
                "observed_price_krw": 34590,
            },
        },
        {
            "source": "danawa",
            "title": "피그먼트 가까이 유니언즈 GAKKAI UNIONS 포켓 게이머 반팔 티셔츠 차콜 G7770106CH 393932",
            "url": "https://prod.danawa.com/info/?pcode=106165895",
            "confidence": 0.86,
            "fields": {
                "category": "t_shirt",
                "gender": "men",
                "manufacturer": "피그먼트",
                "product_code": "G7770106CH",
                "registered_month": "2026-02",
                "observed_price_krw": 34830,
            },
        },
        {
            "source": "coupang",
            "title": "가까이 유니언즈 포켓 게이머 피그먼트 반팔 티셔츠 차콜 806526",
            "url": "https://www.coupang.com/vp/products/8892552553",
            "confidence": 0.72,
            "fields": {
                "category": "t_shirt",
                "color_family": "gray",
                "season": "summer",
                "observed_price_krw": 44100,
            },
        },
    ]

    enrichment = _merge_lookup_matches(matches, traits)
    return {
        "status": "completed",
        "query": query,
        "provider": "fixture_lookup",
        "external_api_called": False,
        "matches": matches,
        "enrichment": enrichment,
        "missing_after_lookup": _missing_after_lookup(parsed, enrichment),
    }


def _merge_lookup_matches(matches: list[dict[str, Any]], traits: dict[str, Any]) -> dict[str, Any]:
    high_confidence_matches = [match for match in matches if match["confidence"] >= 0.8]
    source_urls = [match["url"] for match in matches]

    prices = [
        match["fields"]["observed_price_krw"]
        for match in matches
        if match["fields"].get("observed_price_krw")
    ]
    product_codes = {
        match["fields"]["product_code"]
        for match in high_confidence_matches
        if match["fields"].get("product_code")
    }

    enrichment: dict[str, Any] = {
        "lookup_confidence": max(match["confidence"] for match in matches),
        "source_urls": source_urls,
        "matched_titles": [match["title"] for match in matches],
        "category": traits.get("category"),
    }

    if product_codes:
        enrichment["product_code"] = sorted(product_codes)[0]
    if prices:
        enrichment["observed_price_range_krw"] = {
            "min": min(prices),
            "max": max(prices),
        }

    gender_votes = [
        match["fields"].get("gender")
        for match in high_confidence_matches
        if match["fields"].get("gender")
    ]
    if gender_votes and len(set(gender_votes)) == 1:
        enrichment["gender"] = gender_votes[0]

    manufacturers = [
        match["fields"].get("manufacturer")
        for match in high_confidence_matches
        if match["fields"].get("manufacturer")
    ]
    if manufacturers:
        enrichment["manufacturer"] = manufacturers[0]

    registered_months = [
        match["fields"].get("registered_month")
        for match in high_confidence_matches
        if match["fields"].get("registered_month")
    ]
    if registered_months:
        enrichment["registered_month_candidates"] = sorted(set(registered_months))

    return enrichment


def _missing_after_lookup(parsed: dict[str, Any], enrichment: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    measurements = parsed.get("measurements") or {}
    for field in _required_measurement_fields(parsed):
        if field not in measurements:
            missing.append(f"measurements.{field}")
    if not parsed.get("size_label"):
        missing.append("size_label")
    if not parsed.get("material"):
        missing.append("material")
    if "observed_price_range_krw" not in enrichment:
        missing.append("price")
    return missing


def _required_measurement_fields(parsed: dict[str, Any]) -> list[str]:
    category = str(parsed.get("category") or "").lower()
    name = str(parsed.get("name") or "")
    by_category = {
        "t_shirt": ["length", "shoulder", "chest", "sleeve"],
        "shirt": ["length", "shoulder", "chest", "sleeve"],
        "knit": ["length", "shoulder", "chest", "sleeve"],
        "hoodie": ["length", "shoulder", "chest", "sleeve"],
        "jacket": ["length", "shoulder", "chest", "sleeve"],
        "coat": ["length", "shoulder", "chest", "sleeve"],
        "pants": ["length", "waist", "rise", "thigh", "hem_width"],
        "skirt": ["length", "waist", "hip", "hem_width"],
        "dress": ["length", "shoulder", "chest", "waist"],
        "bag": ["width", "height", "depth"],
        "hat": ["head_circumference"],
        "shoes": ["foot_length", "foot_width"],
    }
    if category in by_category:
        return by_category[category]
    for pattern, fields in [
        (r"(청바지|데님|팬츠|바지|pants|denim|jeans)", ["length", "waist", "rise", "thigh", "hem_width"]),
        (r"(발라클라바|바라클라바|balaclava)", ["length", "neck_width", "hood_width", "hem_width"]),
        (r"(가방|백|bag)", ["width", "height", "depth"]),
        (r"(신발|스니커즈|구두|shoes|sneakers)", ["foot_length", "foot_width"]),
        (r"(모자|cap|hat)", ["head_circumference"]),
    ]:
        if re.search(pattern, name, re.IGNORECASE):
            return fields
    return ["length", "shoulder", "chest", "sleeve"]
