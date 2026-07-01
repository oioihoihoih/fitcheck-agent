from __future__ import annotations

from typing import Any


MEASUREMENT_THRESHOLDS = {
    "length": {"good": 2.0, "caution": 5.0, "label": "총장"},
    "shoulder": {"good": 2.0, "caution": 4.0, "label": "어깨"},
    "chest": {"good": 2.0, "caution": 4.0, "label": "가슴단면"},
    "sleeve": {"good": 1.5, "caution": 3.0, "label": "소매"},
    "waist": {"good": 1.5, "caution": 3.0, "label": "허리"},
    "rise": {"good": 1.5, "caution": 3.0, "label": "밑위"},
    "thigh": {"good": 1.5, "caution": 3.0, "label": "허벅지"},
    "hip": {"good": 2.0, "caution": 4.0, "label": "엉덩이"},
    "hem_width": {"good": 1.0, "caution": 2.5, "label": "밑단"},
    "inseam": {"good": 2.0, "caution": 5.0, "label": "인심"},
    "outseam": {"good": 2.0, "caution": 5.0, "label": "아웃심"},
    "width": {"good": 1.5, "caution": 4.0, "label": "가로"},
    "height": {"good": 1.5, "caution": 4.0, "label": "세로"},
    "depth": {"good": 1.0, "caution": 3.0, "label": "폭"},
    "neck_width": {"good": 1.0, "caution": 2.5, "label": "넥너비"},
    "hood_width": {"good": 1.0, "caution": 2.5, "label": "후드폭"},
    "head_circumference": {"good": 1.0, "caution": 3.0, "label": "머리둘레"},
    "foot_length": {"good": 0.5, "caution": 1.0, "label": "발길이"},
    "foot_width": {"good": 0.5, "caution": 1.0, "label": "발볼"},
}


MEASUREMENT_THRESHOLDS.update(
    {
        "length": {"good": 1.0, "caution": 2.0, "label": "length"},
        "shoulder": {"good": 1.0, "caution": 2.0, "label": "shoulder"},
        "chest": {"good": 1.0, "caution": 2.0, "label": "chest"},
        "sleeve": {"good": 1.0, "caution": 2.0, "label": "sleeve"},
        "waist": {"good": 1.0, "caution": 2.0, "label": "waist"},
        "rise": {"good": 1.0, "caution": 2.0, "label": "rise"},
        "thigh": {"good": 1.0, "caution": 2.0, "label": "thigh"},
        "hip": {"good": 1.0, "caution": 2.0, "label": "hip"},
        "hem_width": {"good": 1.0, "caution": 1.5, "label": "hem"},
        "inseam": {"good": 1.0, "caution": 2.0, "label": "inseam"},
        "outseam": {"good": 1.0, "caution": 2.0, "label": "outseam"},
        "width": {"good": 1.0, "caution": 2.0, "label": "width"},
        "height": {"good": 1.0, "caution": 2.0, "label": "height"},
        "depth": {"good": 0.8, "caution": 1.5, "label": "depth"},
    }
)


def choose_closet_targets(
    closet_items: list[dict[str, Any]],
    product_spec: dict[str, Any],
    closet_item_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    if closet_item_ids:
        wanted = set(closet_item_ids)
        return [item for item in closet_items if item.get("id") in wanted]

    same_category = [
        item
        for item in closet_items
        if item.get("category") == product_spec.get("category")
    ]
    candidates = same_category or closet_items
    return sorted(
        candidates,
        key=lambda item: (item.get("preference_score") or 0, item.get("created_at") or ""),
        reverse=True,
    )[:1]


def calculate_match(
    product_spec: dict[str, Any],
    closet_item: dict[str, Any],
    *,
    compare_measurements: bool = True,
    required_measurement_fields: list[str] | None = None,
) -> dict[str, Any]:
    measurement_result = (
        _score_measurements(
            product_spec.get("measurements", {}),
            closet_item.get("measurements", {}),
            required_fields=required_measurement_fields,
        )
        if compare_measurements
        else _neutral_measurement_result(product_spec)
    )
    material_result = _score_material_with_terms(
        product_spec.get("material", {}),
        closet_item.get("material", {}),
        product_spec.get("material_terms", []),
        closet_item.get("material_terms", []),
    )
    price_result = _score_price(product_spec, closet_item)
    confidence_result = _score_confidence(product_spec)
    review_risks = _review_risks(product_spec.get("review_keywords", []))

    risks = (
        measurement_result["risks"]
        + material_result["risks"]
        + price_result["risks"]
        + confidence_result["risks"]
        + review_risks
    )

    critical_missing = [
        field
        for field in product_spec.get("missing_fields", [])
        if field.startswith("measurements.") and compare_measurements
    ]
    if critical_missing:
        recommendation = "need_more_info"
    else:
        high_risk_count = len([risk for risk in risks if risk["level"] == "high"])
        weighted_overall = round(
            measurement_result["score"] * 0.45
            + material_result["score"] * 0.20
            + price_result["score"] * 0.15
            + confidence_result["score"] * 0.20
        )
        if high_risk_count >= 2 or weighted_overall < 55:
            recommendation = "avoid"
        elif high_risk_count >= 1 or weighted_overall < 80:
            recommendation = "caution"
        else:
            recommendation = "buy"

    score = {
        "overall": _overall_score(
            recommendation,
            measurement_result["score"],
            material_result["score"],
            price_result["score"],
            confidence_result["score"],
        ),
        "size": measurement_result["score"],
        "material": material_result["score"],
        "price": price_result["score"],
        "confidence": confidence_result["score"],
    }

    return {
        "recommendation": recommendation,
        "score": score,
        "measurement_deltas": measurement_result["deltas"],
        "score_basis": {
            "size": measurement_result.get("basis", {}),
            "size_field_results": measurement_result.get("field_results", []),
            "material": material_result.get("basis", {"method": "legacy_material_score"}),
            "price": price_result.get("basis", {"method": "legacy_price_score"}),
            "confidence": confidence_result.get("basis", {"method": "legacy_overall_confidence_score"}),
            "overall": {
                "method": "weighted_sum_with_need_more_info_cap",
                "weights": {"size": 0.45, "material": 0.20, "price": 0.15, "confidence": 0.20},
            },
        },
        "preference_alignment": _preference_alignment(
            product_spec=product_spec,
            closet_item=closet_item,
            size_score=measurement_result["score"],
            material_score=material_result["score"],
        ),
        "risks": risks,
        "missing_fields": product_spec.get("missing_fields", []),
    }


def _neutral_measurement_result(product_spec: dict[str, Any]) -> dict[str, Any]:
    observed = sorted((product_spec.get("measurements") or {}).keys())
    return {
        "score": 55,
        "deltas": {},
        "field_results": [],
        "basis": {
            "method": "measurement_comparison_not_applicable",
            "observed_measurement_fields": observed,
            "score_policy": "neutral_unknown_fit_score_not_affirmative_similarity_evidence",
            "score_cap": 55,
        },
        "risks": [
            {
                "level": "info",
                "type": "measurement_comparison_not_applicable",
                "message": "Fit measurements were recorded as product evidence but not compared because the current goal does not have comparable baseline measurements.",
                "observed_measurement_fields": observed,
            }
        ],
    }


def _score_measurements(
    candidate: dict[str, float],
    baseline: dict[str, float],
    *,
    required_fields: list[str] | None = None,
) -> dict[str, Any]:
    deltas: dict[str, float | None] = {}
    risks = []
    field_results = []
    penalties = 0

    fields = required_fields or ["length", "shoulder", "chest", "sleeve"]
    for field in fields:
        threshold = MEASUREMENT_THRESHOLDS.get(field, {"good": 2.0, "caution": 5.0, "label": field})
        label = threshold["label"]
        candidate_missing = field not in candidate
        baseline_missing = field not in baseline
        if candidate_missing or baseline_missing:
            deltas[field] = None
            penalties += 18
            if candidate_missing and baseline_missing:
                missing_side = "both"
                message = f"{label} 비교에 필요한 후보와 기준 옷 실측 정보가 부족합니다."
            elif candidate_missing:
                missing_side = "candidate"
                message = f"{label} 비교에 필요한 후보 상품 실측 정보가 부족합니다."
            else:
                missing_side = "baseline"
                message = f"{label} 비교에 필요한 기준 옷 실측 정보가 부족합니다."
            risks.append(
                {
                    "level": "high",
                    "type": f"missing_{field}",
                    "missing_side": missing_side,
                    "message": message,
                }
            )
            field_results.append(
                {
                    "field": field,
                    "label": label,
                    "status": "missing",
                    "candidate_value": candidate.get(field),
                    "baseline_value": baseline.get(field),
                    "delta_cm": None,
                    "good_threshold_cm": threshold["good"],
                    "caution_threshold_cm": threshold["caution"],
                    "penalty": 18,
                }
            )
            continue

        delta = round(candidate[field] - baseline[field], 1)
        if _measurement_semantics_may_be_mixed(field, candidate[field], baseline[field]):
            deltas[field] = None
            field_results.append(
                {
                    "field": field,
                    "label": label,
                    "status": "ambiguous_semantics",
                    "candidate_value": candidate[field],
                    "baseline_value": baseline[field],
                    "delta_cm": None,
                    "good_threshold_cm": threshold["good"],
                    "caution_threshold_cm": threshold["caution"],
                    "penalty": 0,
                }
            )
            risks.append(
                {
                    "level": "info",
                    "type": f"ambiguous_{field}_semantics",
                    "candidate_value": candidate[field],
                    "baseline_value": baseline[field],
                    "message": (
                        f"{label} value may mix half-width and circumference conventions, "
                        "so it was recorded as evidence but excluded from hard fit penalties."
                    ),
                }
            )
            continue
        deltas[field] = delta
        abs_delta = abs(delta)
        if abs_delta <= threshold["good"]:
            field_results.append(
                {
                    "field": field,
                    "label": label,
                    "status": "close",
                    "candidate_value": candidate[field],
                    "baseline_value": baseline[field],
                    "delta_cm": delta,
                    "good_threshold_cm": threshold["good"],
                    "caution_threshold_cm": threshold["caution"],
                    "penalty": 0,
                }
            )
            continue
        if abs_delta <= threshold["caution"]:
            penalties += 8
            field_results.append(
                {
                    "field": field,
                    "label": label,
                    "status": "caution",
                    "candidate_value": candidate[field],
                    "baseline_value": baseline[field],
                    "delta_cm": delta,
                    "good_threshold_cm": threshold["good"],
                    "caution_threshold_cm": threshold["caution"],
                    "penalty": 8,
                }
            )
            risks.append(
                {
                    "level": "caution",
                    "type": field,
                    "message": f"기준 옷보다 {label}이 {abs_delta:g}cm 차이납니다.",
                }
            )
        else:
            penalties += 22
            direction = _delta_direction(field, delta)
            particle = _subject_particle(label)
            field_results.append(
                {
                    "field": field,
                    "label": label,
                    "status": "risk",
                    "candidate_value": candidate[field],
                    "baseline_value": baseline[field],
                    "delta_cm": delta,
                    "good_threshold_cm": threshold["good"],
                    "caution_threshold_cm": threshold["caution"],
                    "penalty": 22,
                }
            )
            risks.append(
                {
                    "level": "high",
                    "type": field,
                    "message": f"기준 옷보다 {label}{particle} {abs_delta:g}cm {direction}.",
                }
            )

    return {
        "score": max(0, 100 - penalties),
        "deltas": deltas,
        "field_results": field_results,
        "basis": {
            "method": "per_measurement_delta_thresholds",
            "required_fields": fields,
            "penalty_total": penalties,
            "score_formula": "max(0, 100 - sum(field_penalties))",
        },
        "risks": risks,
    }


def _measurement_semantics_may_be_mixed(field: str, candidate_value: float, baseline_value: float) -> bool:
    if field not in {"waist", "hip", "thigh", "hem_width"}:
        return False
    try:
        candidate = float(candidate_value)
        baseline = float(baseline_value)
    except (TypeError, ValueError):
        return False
    if candidate <= 0 or baseline <= 0:
        return False
    if field == "hem_width" and max(candidate, baseline) >= 55:
        return True
    larger = max(candidate, baseline)
    smaller = min(candidate, baseline)
    ratio = larger / smaller
    return 1.75 <= ratio <= 2.25


def _score_material(candidate: dict[str, int], baseline: dict[str, int]) -> dict[str, Any]:
    return _score_material_with_terms(candidate, baseline, [], [])


def _score_material_with_terms(
    candidate: dict[str, int],
    baseline: dict[str, int],
    candidate_terms: list[str],
    baseline_terms: list[str],
) -> dict[str, Any]:
    if not candidate and not baseline:
        candidate_term_set = set(candidate_terms or [])
        baseline_term_set = set(baseline_terms or [])
        if candidate_term_set and baseline_term_set:
            overlap = candidate_term_set & baseline_term_set
            if overlap:
                return {
                    "score": 65,
                    "basis": {
                        "method": "material_family_terms_overlap_without_percentages",
                        "overlap_terms": sorted(overlap),
                    },
                    "risks": [
                        {
                            "level": "caution",
                            "type": "material_terms_only",
                            "message": "Material family overlaps, but exact composition percentages are missing.",
                        }
                    ],
                }
            return {
                "score": 45,
                "basis": {
                    "method": "material_family_terms_mismatch_without_percentages",
                    "candidate_terms": sorted(candidate_term_set),
                    "baseline_terms": sorted(baseline_term_set),
                },
                "risks": [
                    {
                        "level": "caution",
                        "type": "material_terms_mismatch",
                        "message": "Material family evidence differs and exact composition percentages are missing.",
                    }
                ],
            }
        if candidate_term_set and not baseline_term_set:
            return {
                "score": 50,
                "basis": {
                    "method": "candidate_terms_only_baseline_missing",
                    "candidate_terms": sorted(candidate_term_set),
                },
                "risks": [
                    {
                        "level": "caution",
                        "type": "baseline_material_missing",
                        "message": "Candidate material family was observed, but baseline material evidence is missing.",
                    }
                ],
            }
        if baseline_term_set and not candidate_term_set:
            return {
                "score": 35,
                "basis": {
                    "method": "baseline_terms_only_candidate_missing",
                    "baseline_terms": sorted(baseline_term_set),
                },
                "risks": [
                    {
                        "level": "high",
                        "type": "candidate_material_missing",
                        "message": "Baseline material family was observed, but candidate material evidence is missing.",
                    }
                ],
            }
        return {
            "score": 45,
            "basis": {"method": "both_material_missing_default"},
            "risks": [
                {
                    "level": "caution",
                    "type": "material_missing",
                    "message": "후보와 기준 옷 모두 소재 정보가 부족합니다.",
                }
            ],
        }
    if candidate and not baseline:
        return {
            "score": 60,
            "basis": {"method": "candidate_composition_observed_baseline_missing"},
            "risks": [
                {
                    "level": "caution",
                    "type": "baseline_material_missing",
                    "message": "기준 옷 소재 정보가 없어 촉감 유사도를 확정하기 어렵습니다.",
                }
            ],
        }
    if not candidate and baseline:
        return {
            "score": 35,
            "basis": {"method": "baseline_composition_observed_candidate_missing"},
            "risks": [
                {
                    "level": "high",
                    "type": "candidate_material_missing",
                    "message": "후보 상품 소재 정보가 없어 재질 유사도를 확정하기 어렵습니다.",
                }
            ],
        }

    overlap = 0
    for material, value in candidate.items():
        overlap += min(value, baseline.get(material, 0))
    score = max(0, min(100, overlap))
    risks = []
    if score < 50:
        risks.append(
            {
                "level": "high",
                "type": "material_mismatch",
                "message": "기준 옷과 소재 구성이 크게 다릅니다.",
            }
        )
    elif score < 80:
        risks.append(
            {
                "level": "caution",
                "type": "material_difference",
                "message": "기준 옷과 소재 구성이 일부 다릅니다.",
            }
        )
    return {
        "score": score,
        "basis": {
            "method": "exact_composition_overlap",
            "candidate_material": candidate,
            "baseline_material": baseline,
            "score_formula": "sum(min(candidate_percent, baseline_percent)) clamped to 0..100",
        },
        "risks": risks,
    }


def _score_price(product_spec: dict[str, Any], closet_item: dict[str, Any]) -> dict[str, Any]:
    price = product_spec.get("price")
    baseline_price = closet_item.get("price")
    lookup_range = closet_item.get("lookup_enrichment", {}).get("observed_price_range_krw")
    risks = []
    if price is None:
        return {
            "score": 40,
            "basis": {"method": "candidate_price_missing_default"},
            "risks": [
                {
                    "level": "caution",
                    "type": "price_missing",
                    "message": "후보 상품 가격 정보가 없습니다.",
                }
            ],
        }

    if baseline_price:
        min_price = baseline_price * 0.8
        max_price = baseline_price * 1.25
    elif lookup_range:
        min_price = lookup_range["min"] * 0.8
        max_price = lookup_range["max"] * 1.25
    else:
        return {
            "score": 70,
            "basis": {"method": "candidate_price_observed_no_baseline_price_default", "candidate_price": price},
            "risks": [],
        }

    if min_price <= price <= max_price:
        return {
            "score": 90,
            "basis": {
                "method": "within_baseline_price_range",
                "candidate_price": price,
                "min_price": min_price,
                "max_price": max_price,
            },
            "risks": [],
        }

    if price > max_price:
        risks.append(
            {
                "level": "caution",
                "type": "price_high",
                "message": "기준 옷의 관측 가격대보다 비싼 편입니다.",
            }
        )
    else:
        risks.append(
            {
                "level": "caution",
                "type": "price_low",
                "message": "기준 옷보다 가격이 낮아 소재/마감 차이를 확인해야 합니다.",
            }
        )
    return {
        "score": 65,
        "basis": {
            "method": "outside_baseline_price_range",
            "candidate_price": price,
            "min_price": min_price,
            "max_price": max_price,
        },
        "risks": risks,
    }


def _score_confidence(product_spec: dict[str, Any]) -> dict[str, Any]:
    score = round((product_spec.get("overall_confidence") or 0.0) * 100)
    risks = []
    if score < 60:
        risks.append(
            {
                "level": "high",
                "type": "low_confidence",
                "message": "상품 정보 추출 신뢰도가 낮아 자동 판단을 확정할 수 없습니다.",
            }
        )
    elif score < 80:
        risks.append(
            {
                "level": "caution",
                "type": "medium_confidence",
                "message": "일부 상품 정보의 추출 신뢰도가 낮습니다.",
            }
        )
    return {
        "score": score,
        "basis": {
            "method": "normalized_spec_overall_confidence",
            "source_field": "product_spec.overall_confidence",
            "raw_overall_confidence": product_spec.get("overall_confidence") or 0.0,
            "field_confidence": product_spec.get("field_confidence") or {},
        },
        "risks": risks,
    }


def _review_risks(review_keywords: list[str]) -> list[dict[str, str]]:
    risks = []
    for keyword in ["작아요", "짧아요", "얇아요", "비쳐요"]:
        if keyword in review_keywords:
            risks.append(
                {
                    "level": "caution",
                    "type": "review_warning",
                    "message": f"리뷰에서 '{keyword}' 표현이 발견되었습니다.",
                }
            )
    return risks


def _preference_alignment(
    product_spec: dict[str, Any],
    closet_item: dict[str, Any],
    size_score: int,
    material_score: int,
) -> list[dict[str, str]]:
    alignments: list[dict[str, str]] = []
    signals = closet_item.get("preference_signals", [])

    if "fit" in signals:
        if size_score >= 85:
            status = "matched"
            evidence = f"실측 비교 점수가 {size_score}점으로 기준 옷 핏과 가깝습니다."
        elif size_score >= 60:
            status = "caution"
            evidence = f"실측 비교 점수가 {size_score}점으로 일부 차이가 있습니다."
        else:
            status = "risk"
            evidence = f"실측 비교 점수가 {size_score}점으로 기준 옷 핏과 차이가 큽니다."
        alignments.append({"signal": "fit", "status": status, "evidence": evidence})

    if "durability" in signals:
        if not closet_item.get("material"):
            status = "needs_baseline_material"
            evidence = "기준 옷 소재 정보가 없어 오래 입어도 괜찮은 이유를 소재 기준으로 확정하기 어렵습니다."
        elif material_score >= 80:
            status = "matched"
            evidence = f"소재 유사도 점수가 {material_score}점입니다."
        else:
            status = "caution"
            evidence = f"소재 유사도 점수가 {material_score}점이라 내구/촉감 차이를 확인해야 합니다."
        alignments.append({"signal": "durability", "status": status, "evidence": evidence})

    if "design" in signals:
        closet_name = closet_item.get("name") or ""
        product_name = product_spec.get("name") or ""
        common_tokens = [
            token
            for token in ["차콜", "피그먼트", "포켓", "그래픽", "워싱"]
            if token in closet_name and token in product_name
        ]
        if common_tokens:
            status = "partial_match"
            evidence = f"상품명에서 공통 디자인 키워드가 발견되었습니다: {', '.join(common_tokens)}."
        else:
            status = "unknown"
            evidence = "현재 harness는 디자인을 상품명 키워드 수준으로만 비교합니다."
        alignments.append({"signal": "design", "status": status, "evidence": evidence})

    return alignments


def _overall_score(
    recommendation: str,
    size_score: int,
    material_score: int,
    price_score: int,
    confidence_score: int,
) -> int:
    weighted = round(
        size_score * 0.45
        + material_score * 0.20
        + price_score * 0.15
        + confidence_score * 0.20
    )
    if recommendation == "need_more_info":
        return min(weighted, 50)
    return weighted


def _delta_direction(field: str, delta: float) -> str:
    if field in {"length", "sleeve"}:
        return "깁니다" if delta > 0 else "짧습니다"
    return "큽니다" if delta > 0 else "작습니다"


def _subject_particle(label: str) -> str:
    if label in {"어깨", "소매"}:
        return "가"
    return "이"
