from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import re
from pathlib import Path
from time import perf_counter
from typing import Any, Callable
from uuid import uuid4

from .tools.llm_client import (
    extract_closet_intake_with_llm,
    generate_match_report_summary,
    get_llm_status,
)
from .tools.match_tools import calculate_match


@dataclass
class AgentToolCall:
    name: str
    reason: str
    status: str
    input: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ClothingPurchaseAgent:
    """Task-oriented agent that chooses tools to verify clothing candidates.

    This is intentionally not an LLM chat wrapper. It is a local policy agent:
    it holds a goal, inspects current state, selects tool boundaries, executes
    them, and records a trace that the UI can show.
    """

    def __init__(
        self,
        web_result_path: Path,
        keyword_result_path: Path,
        refresh_web_candidates: Callable[[], None],
    ) -> None:
        self.web_result_path = web_result_path
        self.keyword_result_path = keyword_result_path
        self.refresh_web_candidates = refresh_web_candidates

    def run(self, intake_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        intake = intake_snapshot or {}
        run_id = f"agent_run_{uuid4().hex[:10]}"
        calls: list[AgentToolCall] = []
        decisions: list[dict[str, Any]] = []

        def call(
            name: str,
            reason: str,
            tool: Callable[[], Any],
            input_payload: dict[str, Any] | None = None,
        ) -> Any:
            start = perf_counter()
            try:
                output = tool()
                status = "completed"
                return output
            except Exception as exc:
                output = {"error": str(exc)}
                status = "failed"
                raise
            finally:
                calls.append(
                    AgentToolCall(
                        name=name,
                        reason=reason,
                        status=status,
                        input=input_payload or {},
                        output=_summarize_tool_output(name, output),
                        duration_ms=round((perf_counter() - start) * 1000),
                    )
                )

        search_strategy = call(
            "decide_search_strategy",
            "기준 옷 입력과 후보 cache 상태를 보고 웹 검색을 다시 돌릴지 결정합니다.",
            lambda: self._decide_search_strategy(intake),
            {"refresh_web": bool(intake.get("refresh_web"))},
        )
        decisions.append(search_strategy)

        raw_result = call(
            "ensure_candidate_cache",
            "후보 상품 spec이 없거나 refresh_web이 켜진 경우 검색/OCR 하네스를 실행합니다.",
            lambda: self._ensure_candidate_cache(search_strategy),
            search_strategy,
        )

        keyword_probe = call(
            "load_keyword_probe",
            "검색/OCR 하네스의 페이지 추출 결과와 이미지 OCR 근거를 읽습니다.",
            self._load_keyword_probe,
        )

        llm_parse = call(
            "normalize_free_text_with_llm",
            "자유 텍스트 옷 설명을 LLM API로 구조화합니다. 설정이 없으면 configuration_required를 기록합니다.",
            lambda: extract_closet_intake_with_llm(intake.get("free_text") or ""),
            {"free_text_chars": len(intake.get("free_text") or "")},
        )

        merged_intake = call(
            "merge_intake_fields",
            "LLM 추출값, rule parser fallback, 명시 입력 필드를 하나의 기준 옷 입력으로 합칩니다.",
            lambda: _merge_intake_fields(intake, llm_parse),
            {
                "llm_status": llm_parse.get("status"),
                "explicit_measurement_keys": sorted((intake.get("measurements") or {}).keys()),
            },
        )

        closet_item = call(
            "build_closet_memory",
            "사용자가 입력한 실측, 소재, 가격, 마감/봉제 메모를 기준 옷 memory에 반영합니다.",
            lambda: _apply_intake_snapshot(raw_result["closet_result"]["closet_item"], merged_intake),
            {
                "has_intake": bool(merged_intake),
                "measurement_keys": sorted((merged_intake.get("measurements") or {}).keys()),
                "material_text": merged_intake.get("material_text"),
                "price_text": merged_intake.get("price_text"),
            },
        )

        products = call(
            "load_candidate_products",
            "후보 상품 분석 결과를 agent state로 로드합니다.",
            lambda: [
                item["product_analysis"]
                for item in raw_result.get("product_results", [])
            ],
        )

        match_bundle = call(
            "match_candidates",
            "각 후보를 기준 옷 memory와 비교하고 match report를 다시 생성합니다.",
            lambda: self._match_candidates(products, closet_item),
            {"candidate_count": len(products), "closet_item_id": closet_item.get("id")},
        )

        ranked = call(
            "rank_candidates",
            "판정 우선순위와 점수로 후보를 정렬합니다.",
            lambda: self._rank_candidates(match_bundle["candidates"]),
        )

        guardrail = call(
            "guardrail_check",
            "핵심 실측 누락 후보가 있으면 억지 추천하지 않고 정보 필요 상태로 둡니다.",
            lambda: self._guardrail_check(ranked),
        )
        decisions.append(guardrail)

        summary_table = [item["summary_row"] for item in ranked]
        return {
            "status": "completed",
            "closet_item": closet_item,
            "summary_table": summary_table,
            "candidates": ranked,
            "keyword_probe": keyword_probe,
            "agent_run": {
                "run_id": run_id,
                "agent_name": "ClothingPurchaseAgent",
                "mode": "tool_orchestrating_policy_agent",
                "objective": "사용자의 기준 옷 memory를 기준으로 웹 후보 의류의 구매 실패 위험을 검증합니다.",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "llm_status": get_llm_status(),
                "input_summary": _summarize_intake(merged_intake),
                "decisions": decisions,
                "tool_calls": [call_item.to_dict() for call_item in calls],
                "final_state": {
                    "candidate_count": len(ranked),
                    "recommendation_counts": _recommendation_counts(summary_table),
                    "top_candidate": summary_table[0] if summary_table else None,
                },
            },
        }

    def _decide_search_strategy(self, intake: dict[str, Any]) -> dict[str, Any]:
        refresh_requested = bool(intake.get("refresh_web"))
        cache_available = self.web_result_path.exists()
        should_refresh = refresh_requested or not cache_available
        return {
            "refresh_web": should_refresh,
            "cache_available": cache_available,
            "reason": "refresh_web requested"
            if refresh_requested
            else "candidate cache missing"
            if not cache_available
            else "reuse cached candidate specs and rematch only",
        }

    def _ensure_candidate_cache(self, strategy: dict[str, Any]) -> dict[str, Any]:
        if strategy["refresh_web"]:
            self.refresh_web_candidates()
        return json.loads(self.web_result_path.read_text(encoding="utf-8"))

    def _load_keyword_probe(self) -> dict[str, Any]:
        if not self.keyword_result_path.exists():
            return {}
        return json.loads(self.keyword_result_path.read_text(encoding="utf-8"))

    def _match_candidates(
        self,
        products: list[dict[str, Any]],
        closet_item: dict[str, Any],
    ) -> dict[str, Any]:
        candidates = []
        for product in products:
            match_result = calculate_match(product["normalized_spec"], closet_item)
            report = {
                "product_analysis_id": product["id"],
                "closet_item_ids": [closet_item.get("id")],
                "match_result": match_result,
                "summary": generate_match_report_summary(product, closet_item, match_result),
                "status": "completed",
            }
            summary_row = _summary_row_for_report(report, product)
            candidates.append(
                {
                    "product": product,
                    "report": report,
                    "summary_row": summary_row,
                }
            )
        return {"candidates": candidates}

    def _rank_candidates(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        order = {"buy": 0, "caution": 1, "need_more_info": 2, "avoid": 3}
        return sorted(
            candidates,
            key=lambda item: (
                order.get(item["summary_row"]["recommendation"], 9),
                -item["summary_row"]["overall"],
            ),
        )

    def _guardrail_check(self, candidates: list[dict[str, Any]]) -> dict[str, Any]:
        need_info = [
            item["summary_row"]["candidate_key"]
            for item in candidates
            if item["summary_row"]["recommendation"] == "need_more_info"
        ]
        return {
            "need_more_info_candidates": need_info,
            "guardrail_triggered": bool(need_info),
            "policy": "핵심 실측 누락 후보는 추천하지 않고 검색, 상세페이지 분석, OCR, 정규화 보강 후에도 남은 근거 부족 항목으로 표시합니다.",
        }


def _summary_row_for_report(report: dict[str, Any], product: dict[str, Any]) -> dict[str, Any]:
    match = report["match_result"]
    spec = product["normalized_spec"]
    return {
        "candidate_key": spec.get("candidate_key"),
        "name": spec.get("name"),
        "recommendation": match["recommendation"],
        "overall": match["score"]["overall"],
        "size": match["score"]["size"],
        "material": match["score"]["material"],
        "price": match["score"]["price"],
        "confidence": match["score"]["confidence"],
        "measurement_deltas": match["measurement_deltas"],
        "missing_fields": match["missing_fields"],
        "source_url": product["raw_input"].get("source_url"),
    }


def _apply_intake_snapshot(closet_item: dict[str, Any], intake: dict[str, Any]) -> dict[str, Any]:
    closet = deepcopy(closet_item)

    for source_key, target_key in [("name", "name"), ("brand", "brand"), ("finish_notes", "finish_notes")]:
        value = _clean_text(intake.get(source_key))
        if value:
            closet[target_key] = value

    measurements = _parse_measurements(intake.get("measurements") or {})
    if measurements:
        closet["measurements"] = measurements

    material = _parse_material_text(intake.get("material_text") or "")
    if material:
        closet["material"] = material

    price = _parse_price_text(intake.get("price_text") or "")
    if price is not None:
        closet["price"] = price

    preference_reasons = _parse_preference_reasons(intake.get("preference_reasons") or "")
    if preference_reasons:
        closet["preference_reasons"] = preference_reasons
        closet["preference_signals"] = _infer_preference_signals(
            " ".join(preference_reasons + [closet.get("finish_notes") or ""])
        )

    return closet


def _merge_intake_fields(raw_intake: dict[str, Any], llm_parse: dict[str, Any]) -> dict[str, Any]:
    free_text = raw_intake.get("free_text") or ""
    merged = _parse_free_text_rule_based(free_text)

    llm_fields = llm_parse.get("parsed") or {}
    merged = _merge_dicts(merged, llm_fields)

    explicit = {
        key: value
        for key, value in raw_intake.items()
        if key not in {"free_text", "refresh_web"} and _has_value(value)
    }
    merged = _merge_dicts(merged, explicit)
    merged["free_text"] = free_text
    if raw_intake.get("refresh_web"):
        merged["refresh_web"] = True
    return merged


def _merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if not _has_value(value):
            continue
        if key == "measurements":
            measurements = dict(merged.get("measurements") or {})
            measurements.update(value or {})
            merged["measurements"] = measurements
        else:
            merged[key] = value
    return merged


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def _parse_free_text_rule_based(text: str) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    if not text.strip():
        return parsed

    name_match = re.search(r"(?:제품명|상품명|옷이름)\s*[:：]\s*(.+)", text)
    if name_match:
        parsed["name"] = name_match.group(1).strip()

    brand_match = re.search(r"(?:브랜드)\s*[:：]\s*(.+)", text)
    if brand_match:
        parsed["brand"] = brand_match.group(1).strip()

    reason_match = re.search(r"(?:마음에 드는 이유|좋은 이유|선호 이유)\s*[:：]\s*(.+)", text)
    if reason_match:
        parsed["preference_reasons"] = reason_match.group(1).strip()

    finish_match = re.search(r"(?:마감|봉제|내구성)\s*[:：]\s*(.+)", text)
    if finish_match:
        parsed["finish_notes"] = finish_match.group(1).strip()

    material_match = re.search(
        r"((?<![가-힣A-Za-z])(?:면|코튼|cotton|폴리|폴리에스터|polyester|나일론|nylon|레이온|rayon|울|wool)\s*\d{1,3}\s*%?)",
        text,
        re.IGNORECASE,
    )
    if material_match:
        parsed["material_text"] = material_match.group(1).strip()

    price_match = re.search(r"(\d{1,3}(?:,\d{3})+|\d{4,}|\d+(?:\.\d+)?\s*만)\s*원?", text)
    if price_match:
        parsed["price_text"] = price_match.group(0).strip()

    labels = {
        "총장": "length",
        "기장": "length",
        "어깨": "shoulder",
        "어깨너비": "shoulder",
        "가슴": "chest",
        "가슴단면": "chest",
        "소매": "sleeve",
        "소매길이": "sleeve",
    }
    measurements: dict[str, float] = {}
    label_pattern = "|".join(sorted((re.escape(label) for label in labels), key=len, reverse=True))
    for label, value in re.findall(rf"({label_pattern})\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(?:cm)?", text):
        measurements[labels[label]] = float(value)
    if measurements:
        parsed["measurements"] = measurements

    return parsed


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _parse_measurements(raw: dict[str, Any]) -> dict[str, float]:
    measurements: dict[str, float] = {}
    for key in ["length", "shoulder", "chest", "sleeve"]:
        value = raw.get(key)
        if value is None:
            continue
        numeric = _parse_float(value)
        if numeric is not None and numeric > 0:
            measurements[key] = numeric
    return measurements


def _parse_float(value: object) -> float | None:
    match = re.search(r"\d+(?:\.\d+)?", str(value).replace(",", ""))
    if not match:
        return None
    return float(match.group(0))


def _parse_material_text(text: str) -> dict[str, int]:
    labels = {
        "면": "cotton",
        "코튼": "cotton",
        "cotton": "cotton",
        "폴리": "polyester",
        "폴리에스터": "polyester",
        "polyester": "polyester",
        "나일론": "nylon",
        "nylon": "nylon",
        "레이온": "rayon",
        "rayon": "rayon",
        "울": "wool",
        "wool": "wool",
    }
    label_pattern = "|".join(sorted((re.escape(label) for label in labels), key=len, reverse=True))
    material: dict[str, int] = {}
    for label, value in re.findall(rf"(?<![가-힣A-Za-z])({label_pattern})\s*(\d{{1,3}})\s*%?", text, re.IGNORECASE):
        key = labels[label.lower()] if label.lower() in labels else labels[label]
        material[key] = int(value)
    return material


def _parse_price_text(text: str) -> int | None:
    clean = text.replace(",", "").strip()
    if not clean:
        return None

    numbers = [float(value) for value in re.findall(r"\d+(?:\.\d+)?", clean)]
    if not numbers:
        return None

    if "만" in clean:
        return round(sum(numbers) / len(numbers) * 10000)
    return round(numbers[0])


def _parse_preference_reasons(text: str) -> list[str]:
    return [
        reason.strip()
        for reason in re.split(r"[,;/\n]+", text)
        if reason.strip()
    ]


def _infer_preference_signals(text: str) -> list[str]:
    signals = []
    if "핏" in text or "사이즈" in text or "품" in text:
        signals.append("fit")
    if any(keyword in text for keyword in ["오래", "튼튼", "내구", "마감", "봉제", "늘어"]):
        signals.append("durability")
    if any(keyword in text for keyword in ["디자인", "그래픽", "색", "색상", "차콜", "피그먼트", "워싱"]):
        signals.append("design")
    return signals


def _recommendation_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        key = row.get("recommendation") or "unknown"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _summarize_intake(intake: dict[str, Any]) -> dict[str, Any]:
    return {
        "has_user_override": bool(intake),
        "measurement_keys": sorted((intake.get("measurements") or {}).keys()),
        "material_text": intake.get("material_text"),
        "price_text": intake.get("price_text"),
        "free_text_chars": len(intake.get("free_text") or ""),
        "refresh_web": bool(intake.get("refresh_web")),
    }


def _summarize_tool_output(name: str, output: Any) -> dict[str, Any]:
    if name == "ensure_candidate_cache":
        return {
            "product_count": len(output.get("product_results", [])),
            "match_count": len(output.get("match_results", [])),
            "status": output.get("status"),
        }
    if name == "load_keyword_probe":
        return {
            "page_count": len(output.get("page_results", [])),
            "image_count": len(output.get("image_results", [])),
        }
    if name == "build_closet_memory":
        return {
            "name": output.get("name"),
            "measurements": output.get("measurements"),
            "material": output.get("material"),
            "price": output.get("price"),
            "preference_signals": output.get("preference_signals"),
        }
    if name == "normalize_free_text_with_llm":
        return {
            "status": output.get("status"),
            "provider": output.get("provider"),
            "model": output.get("model"),
            "external_api_called": output.get("external_api_called"),
            "parsed_keys": sorted((output.get("parsed") or {}).keys()),
            "error": output.get("error"),
        }
    if name == "merge_intake_fields":
        return _summarize_intake(output)
    if name == "load_candidate_products":
        return {
            "candidate_count": len(output),
            "candidate_keys": [item.get("candidate_key") for item in output],
        }
    if name == "match_candidates":
        rows = [item["summary_row"] for item in output.get("candidates", [])]
        return {
            "candidate_count": len(rows),
            "recommendation_counts": _recommendation_counts(rows),
        }
    if name == "rank_candidates":
        return {
            "ranked_keys": [item["summary_row"]["candidate_key"] for item in output],
        }
    if isinstance(output, dict):
        return output
    return {"value": output}
