from __future__ import annotations

import re
from typing import Any


_SELF_GROUNDING_KEYS = {"final_report", "grounding_validation", "grounding_evidence", "citation_badges"}
_IDENTITY_KEYS = {"brand", "name", "title", "product_name", "candidate_key"}
_URL_PATTERN = re.compile(r"https?://[^\s\]\)\"'<>]+")
_ORDINAL_MARKER_PATTERN = re.compile(r"(?m)(^|\s)\d+\.(?=\s)")
_PRODUCT_NAME_NUMBER_PATTERN = re.compile(
    r"(?<![\w.])(?:"
    r"-?\d[\d,]*(?:\.\d+)?\s*(?:팩|pack|장\s*가격|장가격|장|colors?|컬러|색상)(?=\W|$|[가-힣])"
    r"|\d{2}s(?=\W|$)"
    r")",
    re.IGNORECASE,
)
_CANDIDATE_AUDIT_LIST_PATTERN = re.compile(
    r"(?:제외/보류\s*후보|보류\s*후보|excluded\s+candidates?)\s*:[^.。]+",
    re.IGNORECASE,
)
_SCORE_FIELDS = {"overall", "size", "material", "price", "confidence"}
_STYLE_CLAIM_PATTERNS = {
    "overfit": [r"오버\s*핏", r"박시\s*핏", r"oversi[sz]ed", r"boxy"],
    "loose_fit": [r"루즈\s*핏", r"loose\s*fit"],
    "regular_fit": [r"레귤러\s*핏", r"regular\s*fit"],
    "slim_fit": [r"슬림\s*핏", r"slim\s*fit"],
    "wide_fit": [r"와이드", r"wide"],
    "cropped": [r"크롭", r"cropped?"],
    "charcoal": [r"차콜", r"먹색", r"charcoal"],
    "black": [r"블랙", r"검정", r"black"],
    "white": [r"화이트", r"흰색", r"white"],
    "navy": [r"네이비", r"navy"],
    "gray": [r"그레이", r"회색", r"gray", r"grey"],
    "beige": [r"베이지", r"beige"],
    "denim": [r"데님", r"denim"],
    "pigment": [r"피그먼트", r"pigment"],
    "washing": [r"워싱", r"washing", r"washed"],
    "graphic": [r"그래픽", r"프린트", r"print", r"graphic"],
    "pocket": [r"포켓", r"pocket"],
    "plain": [r"무지", r"plain"],
    "stripe": [r"스트라이프", r"stripe"],
}
_FIT_CLAIM_PATTERNS = {
    "similar_fit": [
        r"비슷한\s*핏",
        r"핏(?:이|은|도)?\s*(?:비슷|유사|가깝|가까)",
        r"(?:같은|동일한)\s*핏",
        r"기준[^.!?\n。;；]{0,40}핏[^.!?\n。;；]{0,20}(?:가깝|가까|유사|비슷)",
        r"similar\s+fit",
        r"same\s+fit",
        r"close\s+fit",
        r"fit\s+(?:is\s+)?(?:similar|close)",
    ],
    "close_measurements": [
        r"실측(?:이|은|도)?[^.!?\n。;；]{0,20}(?:가깝|가까|비슷|유사)",
        r"measurements?[^.!?\n。;；]{0,25}(?:close|similar)",
        r"close\s+measurements?",
    ],
    "true_to_size": [
        r"정\s*사이즈",
        r"true[-\s]*to[-\s]*size",
        r"\btts\b",
    ],
    "well_fit": [
        r"잘\s*맞(?:는|습니다|아요|을|아)?",
        r"fits?\s+well",
        r"good\s+fit",
    ],
}
_FIT_CLAIM_NEGATION_PATTERN = re.compile(
    r"근거가?\s*(?:없|부족)|근거\s*부족|확정(?:할 수)?\s*없|확정하지\s*않|"
    r"말할 수\s*없|판단(?:할 수)?\s*없|미확인|부족합니다|"
    r"cannot|can't|not\s+enough|insufficient|unconfirmed|unknown|"
    r"no\s+(?:measurement|fit)\s+evidence|without\s+measurement",
    re.IGNORECASE,
)
_EVIDENCE_CLAIM_FIELDS = {
    "measurements": [r"실측", r"치수", r"measurements?", r"size\s+chart"],
    "material": [r"소재", r"material"],
    "price": [r"가격", r"price"],
}
_EVIDENCE_CLAIM_POSITIVE_PATTERN = re.compile(
    r"확인된\s*근거|근거(?:를|가|는)?\s*(?:확인|확보|관찰|기록|검증)|"
    r"(?:evidence|grounding)\s+(?:confirmed|observed|available|recorded|verified)|"
    r"(?:confirmed|observed|verified)\s+(?:evidence|grounding)",
    re.IGNORECASE,
)
_EVIDENCE_CLAIM_NEGATION_PATTERN = re.compile(
    r"근거가?\s*(?:없|부족)|근거\s*부족|확인(?:할 수)?\s*없|확인되지\s*않|미확인|"
    r"missing\s+evidence|no\s+evidence|insufficient\s+evidence|not\s+observed|unconfirmed|unavailable",
    re.IGNORECASE,
)


def validate_report_grounding(
    *,
    report_text: str,
    run: dict[str, Any],
    candidate_key: str | None = None,
) -> dict[str, Any]:
    observations = run.get("state", {}).get("observations", [])
    relevant = [
        item
        for item in observations
        if _observation_relevant_to_candidate(item, candidate_key)
    ]
    if not relevant:
        return {
            "valid": False,
            "reason": "no_relevant_observations",
            "suspicious_numbers": [],
            "suspicious_urls": [],
            "suspicious_material_claims": [],
            "suspicious_score_claims": [],
            "suspicious_identity_claims": [],
            "suspicious_style_claims": [],
            "suspicious_fit_claims": [],
            "suspicious_evidence_claims": [],
            "grounding_evidence": [],
            "citation_badges": [],
        }

    has_match_observation = any(_is_grounding_match_observation(item) for item in relevant)
    has_report_observation = any(_is_grounding_report_observation(item) for item in relevant)
    no_result_search_evidence = _no_result_search_evidence(report_text, relevant)
    has_no_result_search_observation = bool(no_result_search_evidence)
    evidence_index = _claim_evidence_from_obj(relevant)
    number_evidence = evidence_index["number"]
    url_evidence = evidence_index["url"]
    material_evidence = evidence_index["material"]
    identity_evidence = evidence_index["identity"]
    style_evidence = evidence_index["style"]
    fit_evidence = evidence_index["fit"]
    score_evidence = evidence_index["score"]
    evidence_coverage_evidence = evidence_index["evidence_coverage"]
    report_score_claims = _score_claims_from_text(report_text)
    suspicious_score_claims = sorted(report_score_claims - set(score_evidence))
    score_claim_numbers = _numbers_from_score_claims(report_score_claims)
    allowed_numbers = set(number_evidence)
    report_numbers = _numbers_from_text(report_text)
    report_numbers = report_numbers - score_claim_numbers
    suspicious_numbers = sorted(report_numbers - allowed_numbers)
    allowed_urls = set(url_evidence)
    report_urls = _urls_from_text(report_text)
    suspicious_urls = sorted(report_urls - allowed_urls)
    allowed_material_claims = set(material_evidence)
    report_material_claims = _material_claims_from_text(report_text)
    suspicious_material_claims = sorted(report_material_claims - allowed_material_claims)
    report_identity_claims = _identity_claims_from_text(report_text)
    suspicious_identity_claims = _suspicious_identity_claims(
        report_identity_claims,
        identity_evidence,
    )
    allowed_style_claims = set(style_evidence)
    report_style_claims = _style_claims_from_text(report_text)
    suspicious_style_claims = sorted(report_style_claims - allowed_style_claims)
    allowed_fit_claims = set(fit_evidence)
    report_fit_claims = _fit_claims_from_text(report_text)
    suspicious_fit_claims = sorted(report_fit_claims - allowed_fit_claims)
    allowed_evidence_claims = set(evidence_coverage_evidence)
    report_evidence_claims = _evidence_coverage_claims_from_text(report_text)
    suspicious_evidence_claims = sorted(report_evidence_claims - allowed_evidence_claims)
    grounding_evidence = _select_grounding_evidence(
        report_numbers=report_numbers,
        report_urls=report_urls,
        report_material_claims=report_material_claims,
        report_score_claims=report_score_claims,
        report_identity_claims=report_identity_claims,
        report_style_claims=report_style_claims,
        report_fit_claims=report_fit_claims,
        report_evidence_claims=report_evidence_claims,
        number_evidence=number_evidence,
        url_evidence=url_evidence,
        material_evidence=material_evidence,
        score_evidence=score_evidence,
        identity_evidence=identity_evidence,
        style_evidence=style_evidence,
        fit_evidence=fit_evidence,
        evidence_coverage_evidence=evidence_coverage_evidence,
    )
    grounding_evidence.extend(no_result_search_evidence)
    citation_badges = build_grounding_citation_badges(grounding_evidence)

    if suspicious_numbers:
        return {
            "valid": False,
            "reason": "report_contains_unobserved_numbers",
            "suspicious_numbers": suspicious_numbers,
            "suspicious_urls": suspicious_urls,
            "suspicious_material_claims": suspicious_material_claims,
            "suspicious_score_claims": suspicious_score_claims,
            "suspicious_identity_claims": suspicious_identity_claims,
            "suspicious_style_claims": suspicious_style_claims,
            "suspicious_fit_claims": suspicious_fit_claims,
            "suspicious_evidence_claims": suspicious_evidence_claims,
            "grounding_evidence": grounding_evidence,
            "citation_badges": citation_badges,
            "has_match_observation": has_match_observation,
            "has_report_observation": has_report_observation,
            "has_no_result_search_observation": has_no_result_search_observation,
        }
    if suspicious_score_claims:
        return {
            "valid": False,
            "reason": "report_contains_unobserved_score_claims",
            "suspicious_numbers": suspicious_numbers,
            "suspicious_urls": suspicious_urls,
            "suspicious_material_claims": suspicious_material_claims,
            "suspicious_score_claims": suspicious_score_claims,
            "suspicious_identity_claims": suspicious_identity_claims,
            "suspicious_style_claims": suspicious_style_claims,
            "suspicious_fit_claims": suspicious_fit_claims,
            "suspicious_evidence_claims": suspicious_evidence_claims,
            "grounding_evidence": grounding_evidence,
            "citation_badges": citation_badges,
            "has_match_observation": has_match_observation,
            "has_report_observation": has_report_observation,
            "has_no_result_search_observation": has_no_result_search_observation,
        }
    if suspicious_urls:
        return {
            "valid": False,
            "reason": "report_contains_unobserved_urls",
            "suspicious_numbers": [],
            "suspicious_urls": suspicious_urls,
            "suspicious_material_claims": suspicious_material_claims,
            "suspicious_score_claims": [],
            "suspicious_identity_claims": suspicious_identity_claims,
            "suspicious_style_claims": suspicious_style_claims,
            "suspicious_fit_claims": suspicious_fit_claims,
            "suspicious_evidence_claims": suspicious_evidence_claims,
            "grounding_evidence": grounding_evidence,
            "citation_badges": citation_badges,
            "has_match_observation": has_match_observation,
            "has_report_observation": has_report_observation,
        }
    if suspicious_material_claims:
        return {
            "valid": False,
            "reason": "report_contains_unobserved_material_claims",
            "suspicious_numbers": [],
            "suspicious_urls": [],
            "suspicious_material_claims": suspicious_material_claims,
            "suspicious_score_claims": [],
            "suspicious_identity_claims": suspicious_identity_claims,
            "suspicious_style_claims": suspicious_style_claims,
            "suspicious_fit_claims": suspicious_fit_claims,
            "suspicious_evidence_claims": suspicious_evidence_claims,
            "grounding_evidence": grounding_evidence,
            "citation_badges": citation_badges,
            "has_match_observation": has_match_observation,
            "has_report_observation": has_report_observation,
        }
    if suspicious_identity_claims:
        return {
            "valid": False,
            "reason": "report_contains_unobserved_identity_claims",
            "suspicious_numbers": [],
            "suspicious_urls": [],
            "suspicious_material_claims": [],
            "suspicious_score_claims": [],
            "suspicious_identity_claims": suspicious_identity_claims,
            "suspicious_style_claims": suspicious_style_claims,
            "suspicious_fit_claims": suspicious_fit_claims,
            "suspicious_evidence_claims": suspicious_evidence_claims,
            "grounding_evidence": grounding_evidence,
            "citation_badges": citation_badges,
            "has_match_observation": has_match_observation,
            "has_report_observation": has_report_observation,
        }
    if suspicious_style_claims:
        return {
            "valid": False,
            "reason": "report_contains_unobserved_style_claims",
            "suspicious_numbers": [],
            "suspicious_urls": [],
            "suspicious_material_claims": [],
            "suspicious_score_claims": [],
            "suspicious_identity_claims": [],
            "suspicious_style_claims": suspicious_style_claims,
            "suspicious_fit_claims": suspicious_fit_claims,
            "suspicious_evidence_claims": suspicious_evidence_claims,
            "grounding_evidence": grounding_evidence,
            "citation_badges": citation_badges,
            "has_match_observation": has_match_observation,
            "has_report_observation": has_report_observation,
        }
    if suspicious_fit_claims:
        return {
            "valid": False,
            "reason": "report_contains_unobserved_fit_claims",
            "suspicious_numbers": [],
            "suspicious_urls": [],
            "suspicious_material_claims": [],
            "suspicious_score_claims": [],
            "suspicious_identity_claims": [],
            "suspicious_style_claims": [],
            "suspicious_fit_claims": suspicious_fit_claims,
            "suspicious_evidence_claims": suspicious_evidence_claims,
            "grounding_evidence": grounding_evidence,
            "citation_badges": citation_badges,
            "has_match_observation": has_match_observation,
            "has_report_observation": has_report_observation,
        }
    if suspicious_evidence_claims:
        return {
            "valid": False,
            "reason": "report_contains_unobserved_evidence_claims",
            "suspicious_numbers": [],
            "suspicious_urls": [],
            "suspicious_material_claims": [],
            "suspicious_score_claims": [],
            "suspicious_identity_claims": [],
            "suspicious_style_claims": [],
            "suspicious_fit_claims": [],
            "suspicious_evidence_claims": suspicious_evidence_claims,
            "grounding_evidence": grounding_evidence,
            "citation_badges": citation_badges,
            "has_match_observation": has_match_observation,
            "has_report_observation": has_report_observation,
        }
    if not grounding_evidence:
        return {
            "valid": False,
            "reason": "report_contains_no_grounded_claims",
            "suspicious_numbers": [],
            "suspicious_urls": [],
            "suspicious_material_claims": [],
            "suspicious_score_claims": [],
            "suspicious_identity_claims": [],
            "suspicious_style_claims": [],
            "suspicious_fit_claims": [],
            "suspicious_evidence_claims": [],
            "grounding_evidence": [],
            "citation_badges": [],
            "has_match_observation": has_match_observation,
            "has_report_observation": has_report_observation,
        }

    grounded_observation = bool(
        has_match_observation
        or has_report_observation
        or has_no_result_search_observation
    )
    return {
        "valid": grounded_observation,
        "reason": "ok" if grounded_observation else "missing_match_report_or_no_result_search_observation",
        "suspicious_numbers": [],
        "suspicious_urls": [],
        "suspicious_material_claims": [],
        "suspicious_score_claims": [],
        "suspicious_identity_claims": [],
        "suspicious_style_claims": [],
        "suspicious_fit_claims": [],
        "suspicious_evidence_claims": [],
        "grounding_evidence": grounding_evidence,
        "citation_badges": citation_badges,
        "has_match_observation": has_match_observation,
        "has_report_observation": has_report_observation,
        "has_no_result_search_observation": has_no_result_search_observation,
    }


def _no_result_search_evidence(
    report_text: str,
    observations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not re.search(r"(찾지 못|후보가? 없|검색 결과 없음|no[- ]result|no valid product)", report_text, re.IGNORECASE):
        return []
    for index in range(len(observations) - 1, -1, -1):
        observation = observations[index]
        if observation.get("tool") != "search_web_candidates":
            continue
        result = observation.get("result") or {}
        if int(result.get("candidate_count") or 0) != 0:
            return []
        if not result.get("search_deferred") and not result.get("automatic_recovery_exhausted"):
            return []
        return [
            {
                "claim_type": "search_no_result",
                "claim": "no validated product candidates",
                "evidence": [
                    {
                        "path": f"observations[{index}].result.candidate_count",
                        "value": result.get("candidate_count"),
                    },
                    {
                        "path": f"observations[{index}].result.query",
                        "value": result.get("query"),
                    },
                ],
            }
        ]
    return []


def build_grounding_citation_badges(grounding_evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    badges: list[dict[str, Any]] = []
    for index, item in enumerate(grounding_evidence, start=1):
        sources = item.get("evidence") or []
        if not sources:
            continue
        source = sources[0]
        source_path = str(source.get("path") or "")
        badges.append(
            {
                "id": f"G{index}",
                "label": f"[G{index}]",
                "claim_type": item.get("claim_type"),
                "claim": item.get("claim"),
                "source_path": source_path,
                "source_value_preview": _preview_value(source.get("value")),
                "observation_index": _observation_index_from_path(source_path),
                "source_kind": _source_kind_from_path(source_path),
                "source_count": len(sources),
            }
        )
    return badges


def _numbers_from_obj(value: Any) -> set[str]:
    text = repr(value)
    return _numbers_from_text(text)


def _urls_from_obj(value: Any) -> set[str]:
    return _urls_from_text(repr(value))


def _material_claims_from_obj(value: Any) -> set[str]:
    return _material_claims_from_text(repr(value))


def _claim_evidence_from_obj(value: Any) -> dict[str, dict[str, list[dict[str, Any]]]]:
    evidence: dict[str, dict[str, list[dict[str, Any]]]] = {
        "number": {},
        "url": {},
        "material": {},
        "score": {},
        "identity": {},
        "style": {},
        "fit": {},
        "evidence_coverage": {},
    }
    _collect_claim_evidence(value, path="observations", evidence=evidence)
    return evidence


def _collect_claim_evidence(
    value: Any,
    *,
    path: str,
    evidence: dict[str, dict[str, list[dict[str, Any]]]],
) -> None:
    if isinstance(value, dict):
        _collect_structured_fit_evidence(value, path=path, evidence=evidence)
        _collect_structured_evidence_coverage(value, path=path, evidence=evidence)
        skip_keys = set(_SELF_GROUNDING_KEYS)
        if value.get("tool") == "write_purchase_report":
            skip_keys.add("result")
        for key, item in value.items():
            if key in skip_keys:
                continue
            key_path = f"{path}.{key}"
            _collect_claims_from_text(str(key), path=f"{key_path}#key", value=key, evidence=evidence)
            if _is_identity_key(key):
                _collect_identity_claim_from_value(item, path=key_path, evidence=evidence)
            _collect_claim_evidence(item, path=key_path, evidence=evidence)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _collect_claim_evidence(item, path=f"{path}[{index}]", evidence=evidence)
        return
    if value is None:
        return
    _collect_claims_from_text(str(value), path=path, value=value, evidence=evidence)


def _collect_claims_from_text(
    text: str,
    *,
    path: str,
    value: Any,
    evidence: dict[str, dict[str, list[dict[str, Any]]]],
) -> None:
    if _can_ground_number_claims(path, value):
        for claim in _numbers_from_text(text):
            _append_evidence(evidence["number"], claim, path=path, value=value)
            score_field = _score_field_from_path(path)
            if score_field:
                _append_evidence(evidence["score"], f"{score_field}:{claim}", path=path, value=value)
    for claim in _urls_from_text(text):
        _append_evidence(evidence["url"], claim, path=path, value=value)
    if _can_ground_material_claims(path):
        for claim in _material_claims_from_text(text):
            _append_evidence(evidence["material"], claim, path=path, value=value)
    if _can_ground_style_claims(path):
        for claim in _style_claims_from_text(text):
            _append_evidence(evidence["style"], claim, path=path, value=value)
    if _can_ground_fit_claims(path):
        for claim in _fit_claims_from_text(text):
            _append_evidence(evidence["fit"], claim, path=path, value=value)


def _collect_identity_claim_from_value(
    value: Any,
    *,
    path: str,
    evidence: dict[str, dict[str, list[dict[str, Any]]]],
) -> None:
    if not isinstance(value, str):
        return
    claim = _clean_identity_claim(value)
    if not claim:
        return
    _append_evidence(evidence["identity"], claim, path=path, value=value)


def _append_evidence(target: dict[str, list[dict[str, Any]]], claim: str, *, path: str, value: Any) -> None:
    rows = target.setdefault(claim, [])
    if len(rows) >= 5:
        return
    rows.append(
        {
            "path": path,
            "value": value,
        }
    )


def _select_grounding_evidence(
    *,
    report_numbers: set[str],
    report_urls: set[str],
    report_material_claims: set[str],
    report_score_claims: set[str],
    report_identity_claims: set[str],
    report_style_claims: set[str],
    report_fit_claims: set[str],
    report_evidence_claims: set[str],
    number_evidence: dict[str, list[dict[str, Any]]],
    url_evidence: dict[str, list[dict[str, Any]]],
    material_evidence: dict[str, list[dict[str, Any]]],
    score_evidence: dict[str, list[dict[str, Any]]],
    identity_evidence: dict[str, list[dict[str, Any]]],
    style_evidence: dict[str, list[dict[str, Any]]],
    fit_evidence: dict[str, list[dict[str, Any]]],
    evidence_coverage_evidence: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for claim_type, claims, source in [
        ("score", report_score_claims, score_evidence),
        ("number", report_numbers, number_evidence),
        ("url", report_urls, url_evidence),
        ("material", report_material_claims, material_evidence),
        ("identity", report_identity_claims, identity_evidence),
        ("style", report_style_claims, style_evidence),
        ("fit", report_fit_claims, fit_evidence),
        ("evidence_coverage", report_evidence_claims, evidence_coverage_evidence),
    ]:
        for claim in sorted(claims):
            source_claim = _source_identity_claim(claim, source) if claim_type == "identity" else claim
            if source_claim not in source:
                continue
            selected.append(
                {
                    "claim_type": claim_type,
                    "claim": claim,
                    "evidence": _prioritized_evidence_rows(claim_type, source[source_claim]),
                }
            )
    return selected


def _prioritized_evidence_rows(claim_type: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: _evidence_priority(claim_type, str(row.get("path") or "")),
    )


def _evidence_priority(claim_type: str, path: str) -> tuple[int, str]:
    if claim_type == "score":
        return (0 if ".score." in path else 9, path)
    if claim_type == "number":
        if ".score." in path:
            return (0, path)
        if ".measurement_deltas." in path or ".measurements." in path:
            return (1, path)
        if ".normalized_spec.price" in path or ".price_candidates[" in path or ".result.price" in path:
            return (2, path)
        if any(marker in path for marker in [".candidate_count", ".match_count", ".total_count", ".asset_count", ".evidence_count"]):
            return (5, path)
        if any(marker in path for marker in [".text_length", ".timeout_seconds", ".duration_ms"]):
            return (8, path)
        return (4, path)
    if claim_type in {"style", "fit"}:
        if ".normalized_spec." in path:
            return (0, path)
        if ".result.candidates[" in path or ".result.ranked_matches[" in path:
            return (1, path)
        if ".raw_text" in path or ".snippet" in path:
            return (2, path)
        if _path_is_prompt_or_planning_context(path):
            return (9, path)
    if claim_type == "evidence_coverage":
        if ".result.evidence_coverage.observed_fields" in path:
            return (0, path)
        if ".result.ranked_matches[" in path and ".evidence_coverage.observed_fields" in path:
            return (1, path)
        if ".normalized_spec." in path:
            return (2, path)
    return (3, path)


def _preview_value(value: Any) -> str:
    text = str(value)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:120]


def _observation_index_from_path(path: str) -> int | None:
    match = re.search(r"observations\[(\d+)\]", path)
    if not match:
        return None
    return int(match.group(1))


def _source_kind_from_path(path: str) -> str:
    if ".result." in path:
        return "tool_result"
    if ".arguments." in path:
        return "tool_arguments"
    if path.endswith("#key"):
        return "field_name"
    return "observation"


def _is_grounding_match_observation(item: dict[str, Any]) -> bool:
    if item.get("tool") != "score_candidate_match":
        return False
    result = item.get("result") or {}
    if result.get("status") != "completed":
        return False
    if result.get("score_deferred"):
        reason = str(result.get("reason") or "")
        evidence_status = str(result.get("evidence_status") or "")
        if reason not in {
            "candidate_enrichment_required_before_scoring",
            "continue_search_for_closer_measurements_before_report",
        } and evidence_status not in {"needs_more_tool_evidence", "no_close_measurement_match_yet"}:
            return False
    return bool(result.get("score") or result.get("ranked_matches"))


def _is_grounding_report_observation(item: dict[str, Any]) -> bool:
    if item.get("tool") != "write_purchase_report":
        return False
    result = item.get("result") or {}
    if result.get("status") != "completed":
        return False
    if result.get("grounded") is False:
        return False
    return bool(result.get("final_report"))


def _observation_relevant_to_candidate(item: dict[str, Any], candidate_key: str | None) -> bool:
    if not candidate_key:
        return True
    if item.get("arguments", {}).get("candidate_key") == candidate_key:
        return True
    result = item.get("result") or {}
    if item.get("tool") == "score_candidate_match" and str(result.get("candidate_key") or "").lower() in {"all", "*", "__all__"}:
        return any(
            match.get("candidate_key") == candidate_key
            for match in result.get("ranked_matches", [])
        )
    return False


def _numbers_from_text(text: str) -> set[str]:
    text_without_candidate_audit = _CANDIDATE_AUDIT_LIST_PATTERN.sub(" ", text)
    text_without_urls = _URL_PATTERN.sub(" ", text_without_candidate_audit)
    text_without_ordinals = _ORDINAL_MARKER_PATTERN.sub(" ", text_without_urls)
    text_without_product_name_numbers = _PRODUCT_NAME_NUMBER_PATTERN.sub(" ", text_without_ordinals)
    return {
        _canonical_number(match)
        for match in re.findall(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?", text_without_product_name_numbers)
    }


def _urls_from_text(text: str) -> set[str]:
    return {
        _normalize_url(match)
        for match in _URL_PATTERN.findall(text)
    }


def _material_claims_from_text(text: str) -> set[str]:
    lowered = text.lower()
    claims: set[str] = set()
    patterns = {
        "cotton": [
            r"cotton",
            r"코튼",
            r"(?<!단)면\s*(?:\d|%|소재|계열|혼용|100)",
        ],
        "polyester": [r"polyester", r"폴리에스터", r"폴리\s*(?:\d|%|소재|계열|혼용)"],
        "nylon": [r"nylon", r"나일론"],
        "rayon": [r"rayon", r"레이온"],
        "linen": [r"linen", r"린넨"],
        "wool": [r"wool", r"울\s*(?:\d|%|소재|계열|혼용)", r"(?<!메리)노울"],
        "acrylic": [r"acrylic", r"아크릴"],
        "polyurethane": [r"polyurethane", r"polyurethan", r"elastane", r"spandex", r"폴리우레탄", r"스판"],
        "modal": [r"modal", r"모달"],
        "tencel": [r"tencel", r"텐셀"],
        "cashmere": [r"cashmere", r"캐시미어"],
    }
    for label, aliases in patterns.items():
        if any(re.search(pattern, lowered) for pattern in aliases):
            claims.add(label)
    return claims


def _score_claims_from_text(text: str) -> set[str]:
    claims: set[str] = set()
    aliases = {
        "overall": [r"전체", r"종합", r"총점", r"overall"],
        "size": [r"사이즈", r"size"],
        "material": [r"소재", r"material"],
        "price": [r"가격", r"price"],
        "confidence": [r"신뢰도", r"confidence"],
    }
    for field, field_aliases in aliases.items():
        label_pattern = "|".join(field_aliases)
        pattern = rf"(?:{label_pattern})\s*(?:점수|score)?\s*[:：]?\s*(-?\d[\d,]*(?:\.\d+)?)\s*(?:점|points?)?"
        for match in re.finditer(pattern, text, re.IGNORECASE):
            claims.add(f"{field}:{_canonical_number(match.group(1))}")
    return claims


def _numbers_from_score_claims(claims: set[str]) -> set[str]:
    numbers = set()
    for claim in claims:
        if ":" not in claim:
            continue
        numbers.add(claim.split(":", 1)[1])
    return numbers


def _identity_claims_from_text(text: str) -> set[str]:
    claims: set[str] = set()
    patterns = [
        r"\*\*([^*\n]{2,80})\*\*",
        r"`([^`\n]{2,80})`",
        r"[\"'“”‘’]([^\"'“”‘’\n]{2,80})[\"'“”‘’]",
        r"(?m)^\s*\d+\.\s+([^\n:-]{2,80})(?=\s*(?:[:\-]|$))",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            claim = _clean_identity_claim(match.group(1))
            if claim and _looks_like_product_identity(claim):
                claims.add(claim)
    return claims


def _style_claims_from_text(text: str) -> set[str]:
    lowered = text.lower()
    claims: set[str] = set()
    for label, patterns in _STYLE_CLAIM_PATTERNS.items():
        if any(re.search(pattern, lowered, re.IGNORECASE) for pattern in patterns):
            claims.add(label)
    return claims


def _fit_claims_from_text(text: str) -> set[str]:
    claims: set[str] = set()
    for sentence in _claim_sentences(text):
        if _FIT_CLAIM_NEGATION_PATTERN.search(sentence):
            continue
        for label, patterns in _FIT_CLAIM_PATTERNS.items():
            if any(re.search(pattern, sentence, re.IGNORECASE) for pattern in patterns):
                claims.add(label)
    return claims


def _evidence_coverage_claims_from_text(text: str) -> set[str]:
    claims: set[str] = set()
    for sentence in _claim_sentences(text):
        for fragment in _evidence_claim_fragments(sentence):
            if not _EVIDENCE_CLAIM_POSITIVE_PATTERN.search(fragment):
                continue
            if _EVIDENCE_CLAIM_NEGATION_PATTERN.search(fragment):
                continue
            for label, patterns in _EVIDENCE_CLAIM_FIELDS.items():
                if any(re.search(pattern, fragment, re.IGNORECASE) for pattern in patterns):
                    claims.add(label)
    return claims


def _evidence_claim_fragments(sentence: str) -> list[str]:
    return [
        fragment.strip()
        for fragment in re.split(r"\b(?:but|however)\b|(?:이고|이며|하지만|다만)", sentence, flags=re.IGNORECASE)
        if fragment.strip()
    ]


def _claim_sentences(text: str) -> list[str]:
    return [
        sentence.strip()
        for sentence in re.split(r"[\n.!?。;；]+", text)
        if sentence.strip()
    ]


def _can_ground_style_claims(path: str) -> bool:
    if ".arguments" in path:
        return False
    if path.endswith("#key"):
        return False
    if _path_is_prompt_or_planning_context(path):
        return False
    return ".result." in path or path.startswith("observations[")


def _can_ground_material_claims(path: str) -> bool:
    if ".arguments" in path:
        return False
    if path.endswith("#key") and not _path_is_material_field_context(path):
        return False
    if _path_is_prompt_or_planning_context(path):
        return False
    if _path_is_product_identity_context(path) and not _path_is_material_field_context(path):
        return False
    return _path_is_material_field_context(path) or ".result." in path or path.startswith("observations[")


def _can_ground_fit_claims(path: str) -> bool:
    if ".arguments" in path:
        return False
    if path.endswith("#key"):
        return False
    if _path_is_prompt_or_planning_context(path):
        return False
    return ".result." in path or path.startswith("observations[")


def _path_is_prompt_or_planning_context(path: str) -> bool:
    return any(
        marker in path
        for marker in [
            ".context_snapshot.",
            ".search_constraints.",
            ".grounding_policy.",
            ".search_query_hint",
            ".query_hint",
            ".result.query",
        ]
    )


def _path_is_product_identity_context(path: str) -> bool:
    return any(
        marker in path
        for marker in [
            ".brand",
            ".candidate_key",
            ".name",
            ".product_name",
            ".source_url",
            ".title",
            ".url",
        ]
    )


def _path_is_material_field_context(path: str) -> bool:
    return any(
        marker in path
        for marker in [
            ".fabric",
            ".material",
            ".material_terms",
            ".composition",
        ]
    )


def _collect_structured_evidence_coverage(
    value: dict[str, Any],
    *,
    path: str,
    evidence: dict[str, dict[str, list[dict[str, Any]]]],
) -> None:
    if _path_is_prompt_or_planning_context(path):
        return
    if _looks_like_evidence_coverage(value):
        _append_evidence_coverage_observed_fields(
            value.get("observed_fields") or [],
            path=f"{path}.observed_fields",
            evidence=evidence,
        )
    if _looks_like_normalized_spec(path, value):
        if _has_observed_measurements(value.get("measurements")):
            _append_evidence(evidence["evidence_coverage"], "measurements", path=f"{path}.measurements", value=value.get("measurements"))
        if _has_observed_material(value.get("material")):
            _append_evidence(evidence["evidence_coverage"], "material", path=f"{path}.material", value=value.get("material"))
        if value.get("price") is not None:
            _append_evidence(evidence["evidence_coverage"], "price", path=f"{path}.price", value=value.get("price"))


def _looks_like_evidence_coverage(value: dict[str, Any]) -> bool:
    return isinstance(value.get("observed_fields"), list) and (
        "critical_missing_fields" in value
        or "missing_fields" in value
        or "recommendation_policy" in value
    )


def _append_evidence_coverage_observed_fields(
    observed_fields: list[Any],
    *,
    path: str,
    evidence: dict[str, dict[str, list[dict[str, Any]]]],
) -> None:
    observed = {str(item) for item in observed_fields}
    if any(item.startswith("measurements.") for item in observed):
        _append_evidence(evidence["evidence_coverage"], "measurements", path=path, value=observed_fields)
    if "material" in observed:
        _append_evidence(evidence["evidence_coverage"], "material", path=path, value=observed_fields)
    if "price" in observed:
        _append_evidence(evidence["evidence_coverage"], "price", path=path, value=observed_fields)


def _looks_like_normalized_spec(path: str, value: dict[str, Any]) -> bool:
    return path.endswith(".normalized_spec") or (
        "candidate_key" in value
        and any(key in value for key in ["measurements", "material", "price"])
        and "missing_fields" in value
    )


def _has_observed_measurements(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return any(item is not None for item in value.values())


def _has_observed_material(value: Any) -> bool:
    if isinstance(value, dict):
        return any(item is not None for item in value.values())
    return bool(value)


def _collect_structured_fit_evidence(
    value: dict[str, Any],
    *,
    path: str,
    evidence: dict[str, dict[str, list[dict[str, Any]]]],
) -> None:
    if not _can_ground_fit_claims(path):
        return
    if not _structured_measurement_fit_evidence_is_close(value):
        return
    deltas = value.get("measurement_deltas")
    source_path = f"{path}.measurement_deltas"
    for claim in ["similar_fit", "close_measurements", "well_fit"]:
        _append_evidence(evidence["fit"], claim, path=source_path, value=deltas)


def _structured_measurement_fit_evidence_is_close(value: dict[str, Any]) -> bool:
    deltas = value.get("measurement_deltas")
    if not isinstance(deltas, dict):
        return False
    numeric_deltas = []
    for item in deltas.values():
        try:
            numeric_deltas.append(abs(float(item)))
        except (TypeError, ValueError):
            continue
    if not numeric_deltas:
        return False
    score = value.get("score")
    if isinstance(score, dict):
        try:
            if int(score.get("size") or 0) >= 80:
                return True
        except (TypeError, ValueError):
            pass
    return max(numeric_deltas) <= 3.0


def _can_ground_number_claims(path: str, value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    if path.endswith("#key") or ".arguments." in path:
        return False
    numeric_result_markers = [
        ".score.",
        ".ranked_matches[",
        ".measurement_deltas.",
        ".measurements.",
        ".normalized_spec.price",
        ".normalized_spec.price_candidates[",
        ".price_candidates[",
        ".result.price",
        ".result.candidate_count",
        ".result.active_candidate_count",
        ".result.deferred_candidate_count",
        ".result.match_count",
        ".result.total_count",
        ".result.asset_count",
        ".result.evidence_count",
        ".result.timeout_seconds",
        ".result.duration_ms",
    ]
    return any(marker in path for marker in numeric_result_markers)


def _score_field_from_path(path: str) -> str | None:
    match = re.search(r"\.score\.(overall|size|material|price|confidence)$", path)
    if not match:
        return None
    field = match.group(1)
    return field if field in _SCORE_FIELDS else None


def _suspicious_identity_claims(
    report_claims: set[str],
    identity_evidence: dict[str, list[dict[str, Any]]],
) -> list[str]:
    return sorted(
        claim
        for claim in report_claims
        if _source_identity_claim(claim, identity_evidence) is None
    )


def _source_identity_claim(
    claim: str,
    identity_evidence: dict[str, list[dict[str, Any]]],
) -> str | None:
    normalized_claim = _normalize_identity_claim(claim)
    if not normalized_claim:
        return None
    for source_claim in identity_evidence:
        normalized_source = _normalize_identity_claim(source_claim)
        if not normalized_source:
            continue
        if normalized_claim in normalized_source or normalized_source in normalized_claim:
            return source_claim
    return None


def _is_identity_key(key: str) -> bool:
    normalized = key.lower()
    return normalized in _IDENTITY_KEYS or normalized.endswith("_name") or normalized.endswith("_title")


def _clean_identity_claim(value: str) -> str:
    cleaned = _URL_PATTERN.sub(" ", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" \t\r\n-:：,，.。;；")
    if not cleaned or len(cleaned) > 80:
        return ""
    if cleaned.lower() in {"completed", "failed", "buy", "avoid", "caution", "need_more_info", "all"}:
        return ""
    return cleaned


def _looks_like_product_identity(value: str) -> bool:
    if re.search(r"(반팔|긴팔|티셔츠|티\b|후드|셔츠|니트|바지|팬츠|자켓|재킷|코트|데님|원피스|스커트|맨투맨)", value):
        return True
    return bool(re.search(r"[A-Za-z가-힣0-9]", value) and "_" in value)


def _normalize_identity_claim(value: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", value.lower())


def _canonical_number(value: str) -> str:
    cleaned = value.replace(",", "")
    if "." not in cleaned:
        return cleaned
    try:
        number = float(cleaned)
    except ValueError:
        return cleaned
    if number.is_integer():
        return str(int(number))
    return str(number)


def _normalize_url(value: str) -> str:
    return value.rstrip(".,;:!?")
