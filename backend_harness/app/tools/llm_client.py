from __future__ import annotations

import json
import os
import re
from time import sleep
from typing import Any

import requests
from requests import exceptions as request_exceptions

from ..config_env import load_project_env

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - dependency may not be installed in minimal local checks.
    OpenAI = None  # type: ignore[assignment]


DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"
DEFAULT_MODEL = "llama3.1"
DEFAULT_TIMEOUT_SECONDS = 45.0
DEFAULT_MAX_RETRIES = 1
SUPPORTED_API_STYLES = {"responses", "chat_completions"}


load_project_env()


class LlmTransportError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.response = _TransportErrorResponse(status_code) if status_code is not None else None
        self.request_id = request_id
        self.provider_request_id = request_id


class _TransportErrorResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


def get_llm_config() -> dict[str, Any]:
    explicit_base_url = os.getenv("FITCHECK_LLM_BASE_URL")
    explicit_api_key = os.getenv("FITCHECK_LLM_API_KEY")
    openai_api_key = os.getenv("OPENAI_API_KEY")
    openai_base_url = os.getenv("OPENAI_BASE_URL")

    if explicit_base_url or explicit_api_key:
        base_url = (explicit_base_url or DEFAULT_BASE_URL).rstrip("/")
        return {
            "provider": os.getenv("FITCHECK_LLM_PROVIDER", "openai_compatible"),
            "base_url": base_url,
            "model": os.getenv("FITCHECK_LLM_MODEL", DEFAULT_MODEL),
            "api_key": explicit_api_key or "ollama",
            "api_style": _resolve_api_style(base_url),
            "timeout": _resolve_timeout(),
            "max_retries": _resolve_max_retries(),
            "configured_source": "fitcheck_env",
            "requires_user_config": False,
        }

    if openai_api_key:
        base_url = (openai_base_url or "https://api.openai.com/v1").rstrip("/")
        return {
            "provider": "openai_compatible",
            "base_url": base_url,
            "model": os.getenv("FITCHECK_LLM_MODEL", os.getenv("OPENAI_MODEL", "")),
            "api_key": openai_api_key,
            "api_style": _resolve_api_style(base_url),
            "timeout": _resolve_timeout(),
            "max_retries": _resolve_max_retries(),
            "configured_source": "openai_env",
            "requires_user_config": not bool(os.getenv("FITCHECK_LLM_MODEL") or os.getenv("OPENAI_MODEL")),
        }

    base_url = DEFAULT_BASE_URL
    return {
        "provider": os.getenv("FITCHECK_LLM_PROVIDER", "openai_compatible"),
        "base_url": base_url,
        "model": os.getenv("FITCHECK_LLM_MODEL", DEFAULT_MODEL),
        "api_key": "ollama",
        "api_style": _resolve_api_style(base_url),
        "timeout": _resolve_timeout(),
        "max_retries": _resolve_max_retries(),
        "configured_source": "default_local_ollama",
        "requires_user_config": True,
    }


def get_llm_status() -> dict[str, Any]:
    config = get_llm_config()
    if config["configured_source"] == "openai_env" and not config["model"]:
        return {
            "provider": config["provider"],
            "base_url": config["base_url"],
            "model": config["model"],
            "api_style": config["api_style"],
            "status": "configuration_required",
            "configured_source": config["configured_source"],
            "missing": ["FITCHECK_LLM_MODEL or OPENAI_MODEL"],
            "message": "OPENAI_API_KEY is present, but no model was configured.",
            "timeout_seconds": config["timeout"],
            "max_retries": config["max_retries"],
            "transport": _transport_name(config),
            **_tool_capability_status(config),
        }

    models_url = f"{config['base_url']}/models"
    try:
        if _use_openai_sdk(config):
            _openai_client(config, timeout=min(config["timeout"], 3)).models.list()
        else:
            response = requests.get(
                models_url,
                headers=_headers(config),
                timeout=min(config["timeout"], 3),
            )
            response.raise_for_status()
    except Exception as exc:
        if config["requires_user_config"]:
            return {
                "provider": config["provider"],
                "base_url": config["base_url"],
                "model": config["model"],
                "api_style": config["api_style"],
                "status": "configuration_required",
                "configured_source": config["configured_source"],
                "timeout_seconds": config["timeout"],
                "max_retries": config["max_retries"],
                "transport": _transport_name(config),
                "missing": [
                    "FITCHECK_LLM_BASE_URL + FITCHECK_LLM_API_KEY",
                    "or OPENAI_API_KEY + FITCHECK_LLM_MODEL",
                    "or a running local Ollama-compatible server at http://127.0.0.1:11434/v1",
                ],
                "error": str(exc),
                **_tool_capability_status(config),
            }
        return {
            "provider": config["provider"],
            "base_url": config["base_url"],
            "model": config["model"],
            "api_style": config["api_style"],
            "status": "unavailable",
            "configured_source": config["configured_source"],
            "timeout_seconds": config["timeout"],
            "max_retries": config["max_retries"],
            "transport": _transport_name(config),
            "error": str(exc),
            **_tool_capability_status(config),
        }

    return {
        "provider": config["provider"],
        "base_url": config["base_url"],
        "model": config["model"],
        "api_style": config["api_style"],
        "status": "connected",
        "configured_source": config["configured_source"],
        "timeout_seconds": config["timeout"],
        "max_retries": config["max_retries"],
        "transport": _transport_name(config),
        **_tool_capability_status(config),
    }


def extract_closet_intake_with_llm(free_text: str) -> dict[str, Any]:
    """Use an OpenAI-compatible local/API LLM to parse free-form clothing text."""
    text = free_text.strip()
    status = get_llm_status()
    if not text:
        return {
            "provider": status["provider"],
            "model": status["model"],
            "status": "skipped",
            "external_api_called": False,
            "reason": "free_text_empty",
            "parsed": {},
        }
    if status["status"] != "connected":
        return {
            **status,
            "external_api_called": False,
            "parsed": {},
        }

    schema_hint = {
        "name": "string or null",
        "brand": "string or null",
        "measurements": {"length": "number cm", "shoulder": "number cm", "chest": "number cm", "sleeve": "number cm"},
        "material_text": "string or null",
        "price_text": "string or null",
        "finish_notes": "string or null",
        "preference_reasons": "comma separated Korean string or null",
    }
    prompt = (
        "다음 사용자의 옷 설명에서 기준 옷 정보를 추출해 JSON 하나만 반환하세요. "
        "값을 모르면 null 또는 빈 객체를 쓰세요. cm와 원 단위는 원문에 가까운 숫자로 유지하세요.\n"
        f"JSON schema: {json.dumps(schema_hint, ensure_ascii=False)}\n\n"
        f"사용자 입력:\n{text}"
    )
    try:
        content = _chat(
            system="You extract structured clothing preference data. Return only JSON.",
            user=prompt,
        )
        parsed = _extract_json_object(content)
    except Exception as exc:
        return {
            **status,
            "status": "failed",
            "external_api_called": True,
            "error": str(exc),
            "parsed": {},
        }

    return {
        **status,
        "status": "completed",
        "external_api_called": True,
        "parsed": parsed,
    }


def generate_closet_summary_stub(closet_item: dict) -> dict:
    measurements = closet_item.get("measurements", {})
    name = closet_item.get("name") or "이 옷"
    brand = closet_item.get("brand") or "브랜드 미상"
    lookup = closet_item.get("lookup_enrichment", {})
    price_range = lookup.get("observed_price_range_krw")
    preference_reasons = closet_item.get("preference_reasons", [])

    lookup_sentence = ""
    if lookup.get("product_code"):
        lookup_sentence += f" 검색 보강 결과 상품 코드 후보는 {lookup['product_code']}입니다."
    if price_range:
        lookup_sentence += (
            f" 관측 가격대는 {price_range.get('min')}~{price_range.get('max')}원입니다."
        )

    preference_sentence = ""
    if preference_reasons:
        preference_sentence = f" 사용자가 마음에 든 이유는 {', '.join(preference_reasons)}입니다."

    summary = (
        f"기준 옷으로 '{brand} {name}'을 등록했습니다. "
        f"총장 {measurements.get('length')}cm, "
        f"어깨 {measurements.get('shoulder')}cm, "
        f"가슴단면 {measurements.get('chest')}cm, "
        f"소매 {measurements.get('sleeve')}cm 기준으로 후보 상품을 비교합니다."
        f"{lookup_sentence}"
        f"{preference_sentence}"
    )

    return {
        "provider": "llm_stub",
        "model": None,
        "external_api_called": False,
        "summary": summary,
    }


def generate_match_report_summary_stub(
    product: dict,
    closet_item: dict,
    match_result: dict,
) -> dict:
    return _build_match_report_summary(
        product=product,
        closet_item=closet_item,
        match_result=match_result,
        provider="llm_stub",
        model=None,
        status="fallback",
        external_api_called=False,
    )


def generate_match_report_summary(
    product: dict,
    closet_item: dict,
    match_result: dict,
) -> dict:
    status = get_llm_status()
    if status["status"] != "connected":
        return _build_match_report_summary(
            product=product,
            closet_item=closet_item,
            match_result=match_result,
            provider="rule_based_fallback",
            model=status["model"],
            status=status["status"],
            external_api_called=False,
            llm_status=status,
        )

    spec = product.get("normalized_spec", {})
    prompt_payload = {
        "candidate_name": spec.get("name"),
        "closet_name": closet_item.get("name"),
        "recommendation": match_result.get("recommendation"),
        "score": match_result.get("score"),
        "measurement_deltas": match_result.get("measurement_deltas"),
        "missing_fields": match_result.get("missing_fields"),
        "risks": match_result.get("risks", [])[:5],
        "preference_alignment": match_result.get("preference_alignment", [])[:5],
    }
    try:
        summary = _chat(
            system=(
                "You write concise Korean clothing match reports. "
                "Use only the provided scores, risks, and observed missing fields."
            ),
            user=(
                "다음 match 결과를 사용자가 이해하기 쉬운 한국어 2~3문장으로 요약하세요. "
                "판정, 기준 옷과의 차이, 근거가 아직 부족한 항목이 있으면 그 항목만 포함하세요.\n"
                f"{json.dumps(prompt_payload, ensure_ascii=False)}"
            ),
        ).strip()
    except Exception as exc:
        fallback = _build_match_report_summary(
            product=product,
            closet_item=closet_item,
            match_result=match_result,
            provider="rule_based_fallback",
            model=status["model"],
            status="failed",
            external_api_called=True,
            llm_status=status,
        )
        fallback["error"] = str(exc)
        return fallback

    return {
        "provider": status["provider"],
        "model": status["model"],
        "status": "completed",
        "external_api_called": True,
        "summary": summary,
    }


class ResponsesApiClient:
    """OpenAI-compatible tool-calling client.

    OpenAI cloud uses the Responses API. Local OpenAI-compatible servers usually
    expose chat completions, so this adapter converts chat tool calls into the
    Responses-like shape expected by the FitCheck runtime.
    """

    def __init__(self) -> None:
        self.external_api_called = False
        self._chat_histories: dict[str, list[dict[str, Any]]] = {}

    def get_status(self) -> dict[str, Any]:
        return get_llm_status()

    def prepare_input_items(
        self,
        *,
        run: dict[str, Any],
        instructions: str,
        input_items: str | list[dict[str, Any]],
    ) -> str | list[dict[str, Any]]:
        config = get_llm_config()
        if config["api_style"] != "chat_completions" or not isinstance(input_items, list):
            return input_items
        if not any(item.get("type") == "function_call_output" for item in input_items):
            return input_items
        messages = _reconstruct_chat_messages_from_run(instructions, run)
        if not messages:
            return input_items
        return [
            {
                "type": "fitcheck_chat_messages",
                "source": "durable_run_state",
                "messages": messages,
            }
        ]

    def create_response(
        self,
        *,
        instructions: str,
        input_items: str | list[dict[str, Any]],
        tools: list[dict[str, Any]],
        previous_response_id: str | None = None,
        delta_callback: Any | None = None,
    ) -> dict[str, Any]:
        config = get_llm_config()
        if config["api_style"] == "chat_completions":
            return self._create_chat_completion_response(
                config=config,
                instructions=instructions,
                input_items=input_items,
                tools=tools,
                previous_response_id=previous_response_id,
            )

        payload: dict[str, Any] = {
            "model": config["model"],
            "instructions": instructions,
            "input": input_items,
            "tools": tools,
            "tool_choice": "auto",
            "store": True,
        }
        if previous_response_id:
            payload["previous_response_id"] = previous_response_id

        self.external_api_called = True
        if _use_openai_sdk(config):
            return _responses_create_with_openai_sdk(config, payload, delta_callback)
        return _post_json(config, "/responses", payload)

    def _create_chat_completion_response(
        self,
        *,
        config: dict[str, Any],
        instructions: str,
        input_items: str | list[dict[str, Any]],
        tools: list[dict[str, Any]],
        previous_response_id: str | None,
    ) -> dict[str, Any]:
        messages = self._build_chat_messages(
            instructions=instructions,
            input_items=input_items,
            previous_response_id=previous_response_id,
        )
        self.external_api_called = True
        request_payload = {
            "model": config["model"],
            "messages": messages,
            "tools": _to_chat_completion_tools(tools),
            "tool_choice": "auto",
            "temperature": float(os.getenv("FITCHECK_LLM_TEMPERATURE", "0")),
        }
        payload = (
            _chat_completion_create_with_openai_sdk(config, request_payload)
            if _use_openai_sdk(config)
            else _post_json(config, "/chat/completions", request_payload)
        )
        response_id = payload.get("id") or f"chatcmpl_local_{len(self._chat_histories) + 1}"
        message = ((payload.get("choices") or [{}])[0].get("message") or {})
        messages.append(_assistant_message_for_history(message))
        self._chat_histories[response_id] = messages
        return _chat_completion_to_responses_like(response_id, payload)

    def _build_chat_messages(
        self,
        *,
        instructions: str,
        input_items: str | list[dict[str, Any]],
        previous_response_id: str | None,
    ) -> list[dict[str, Any]]:
        if isinstance(input_items, list) and len(input_items) == 1:
            item = input_items[0]
            if item.get("type") == "fitcheck_chat_messages" and isinstance(item.get("messages"), list):
                return [dict(message) for message in item["messages"]]

        if previous_response_id and previous_response_id in self._chat_histories:
            messages = [dict(item) for item in self._chat_histories[previous_response_id]]
        else:
            messages = [{"role": "system", "content": instructions}]

        if isinstance(input_items, str):
            messages.append({"role": "user", "content": input_items})
            return messages

        for item in input_items:
            if item.get("type") == "function_call_output":
                legacy_function_name = _legacy_function_name_for_output(
                    messages,
                    previous_response_id=previous_response_id,
                    call_id=str(item.get("call_id") or ""),
                )
                if legacy_function_name:
                    messages.append(
                        {
                            "role": "function",
                            "name": legacy_function_name,
                            "content": str(item.get("output", "")),
                        }
                    )
                else:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": item.get("call_id"),
                            "content": str(item.get("output", "")),
                        }
                    )
            elif item.get("type") == "reasoning":
                continue
            elif item.get("role") and "content" in item:
                messages.append(
                    {
                        "role": str(item.get("role")),
                        "content": str(item.get("content", "")),
                    }
                )
            else:
                messages.append(
                    {
                        "role": "user",
                        "content": json.dumps(item, ensure_ascii=False),
                    }
                )
        return messages


def _build_match_report_summary(
    product: dict,
    closet_item: dict,
    match_result: dict,
    provider: str,
    model: str | None,
    status: str,
    external_api_called: bool,
    llm_status: dict[str, Any] | None = None,
) -> dict:
    spec = product.get("normalized_spec", {})
    score = match_result["score"]
    recommendation = match_result["recommendation"]
    name = spec.get("name") or "후보 상품"
    closet_name = closet_item.get("name") or "기준 옷"

    recommendation_label = {
        "buy": "우선 확인할 만한 유사 후보",
        "caution": "조건부 후보",
        "avoid": "기준 요소와 거리가 있는 후보",
        "need_more_info": "정보 추가 필요 후보",
    }.get(recommendation, recommendation)

    risk_sentences = [risk["message"] for risk in match_result.get("risks", [])[:3]]
    if not risk_sentences:
        risk_sentences = ["큰 위험 요소는 발견되지 않았습니다."]
    preference_sentences = [
        item["evidence"]
        for item in match_result.get("preference_alignment", [])[:2]
    ]
    preference_text = ""
    if preference_sentences:
        preference_text = f" 선호 이유 반영: {' '.join(preference_sentences)}"

    summary = (
        f"후보 상품 '{name}'은 기준 옷 '{closet_name}'과 비교했을 때 "
        f"'{recommendation_label}'입니다. "
        f"전체 점수는 {score['overall']}점이며, 사이즈 {score['size']}점, "
        f"소재 {score['material']}점, 가격 {score['price']}점, "
        f"정보 신뢰도 {score['confidence']}점입니다. "
        f"주요 근거: {' '.join(risk_sentences)}"
        f"{preference_text}"
    )

    result = {
        "provider": provider,
        "model": model,
        "status": status,
        "external_api_called": external_api_called,
        "summary": summary,
    }
    if llm_status:
        result["llm_status"] = llm_status
    return result


def _chat(system: str, user: str) -> str:
    config = get_llm_config()
    request_payload = {
        "model": config["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
    }
    payload = (
        _chat_completion_create_with_openai_sdk(config, request_payload)
        if _use_openai_sdk(config)
        else _post_json(config, "/chat/completions", request_payload)
    )
    return payload["choices"][0]["message"]["content"]


def _headers(config: dict[str, Any]) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {config['api_key']}",
        "Content-Type": "application/json",
    }


def _post_json(config: dict[str, Any], path: str, payload: dict[str, Any]) -> dict[str, Any]:
    url = f"{config['base_url']}{path}"
    max_retries = int(config.get("max_retries") or 0)
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            response = requests.post(
                url,
                headers=_headers(config),
                json=payload,
                timeout=config["timeout"],
            )
            status_code = getattr(response, "status_code", 200)
            if status_code in {408, 409, 429, 500, 502, 503, 504} and attempt < max_retries:
                sleep(_retry_delay(attempt))
                continue
            response.raise_for_status()
            return response.json()
        except request_exceptions.RequestException as exc:
            last_error = exc
            if attempt >= max_retries or not _is_retryable_request_error(exc):
                raise
            sleep(_retry_delay(attempt))
    if last_error:
        raise last_error
    raise RuntimeError("LLM request failed without an exception.")


def _responses_create_with_openai_sdk(
    config: dict[str, Any],
    payload: dict[str, Any],
    delta_callback: Any | None = None,
) -> dict[str, Any]:
    # docs/24 #5: when a delta callback is provided, stream the Responses API so
    # the model's text appears progressively; still return the fully-assembled
    # final response so tool-call parsing downstream is unchanged.
    if delta_callback is not None:
        try:
            return _responses_stream_with_openai_sdk(config, payload, delta_callback)
        except _LlmStreamingUnsupported:
            pass  # fall back to buffered call below
    try:
        response = _openai_client(config).responses.create(**payload)
    except Exception as exc:
        _raise_llm_transport_error_if_sdk_exception(exc)
        raise
    request_id = _sdk_request_id(response)
    data = _model_to_dict(response)
    data.setdefault("api_style", "responses")
    data.setdefault("raw_response_id", data.get("id"))
    data.setdefault("transport", "openai_python_sdk")
    if request_id:
        data.setdefault("request_id", request_id)
        data.setdefault("provider_request_id", request_id)
    return data


class _LlmStreamingUnsupported(Exception):
    """Raised when the SDK streaming path is unavailable; caller falls back."""


def _responses_stream_with_openai_sdk(
    config: dict[str, Any], payload: dict[str, Any], delta_callback: Any
) -> dict[str, Any]:
    client = _openai_client(config)
    stream_ctx = getattr(getattr(client, "responses", None), "stream", None)
    if stream_ctx is None:
        raise _LlmStreamingUnsupported()
    try:
        with client.responses.stream(**payload) as stream:
            for event in stream:
                event_type = getattr(event, "type", "")
                if event_type == "response.output_text.delta":
                    delta = getattr(event, "delta", "") or ""
                    if delta:
                        try:
                            delta_callback(delta)
                        except Exception:
                            pass  # never let a UI callback break the model turn
            final = stream.get_final_response()
    except _LlmStreamingUnsupported:
        raise
    except Exception as exc:
        _raise_llm_transport_error_if_sdk_exception(exc)
        raise
    request_id = _sdk_request_id(final)
    data = _model_to_dict(final)
    data.setdefault("api_style", "responses")
    data.setdefault("raw_response_id", data.get("id"))
    data.setdefault("transport", "openai_python_sdk_stream")
    if request_id:
        data.setdefault("request_id", request_id)
        data.setdefault("provider_request_id", request_id)
    return data


def _chat_completion_create_with_openai_sdk(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    try:
        response = _openai_client(config).chat.completions.create(**payload)
    except Exception as exc:
        _raise_llm_transport_error_if_sdk_exception(exc)
        raise
    request_id = _sdk_request_id(response)
    data = _model_to_dict(response)
    data.setdefault("transport", "openai_python_sdk")
    if request_id:
        data.setdefault("request_id", request_id)
        data.setdefault("provider_request_id", request_id)
    return data


def _openai_client(config: dict[str, Any], *, timeout: float | None = None) -> Any:
    if OpenAI is None:
        raise RuntimeError("openai Python package is not installed. Run `pip install -r requirements.txt`.")
    return OpenAI(
        api_key=config["api_key"],
        base_url=config["base_url"],
        timeout=timeout if timeout is not None else config["timeout"],
        max_retries=int(config.get("max_retries") or 0),
    )


def _model_to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if hasattr(value, "dict"):
        return value.dict()
    raise TypeError(f"Unsupported OpenAI SDK response type: {type(value)!r}")


def _sdk_request_id(value: Any) -> str | None:
    for attr in ("_request_id", "request_id", "provider_request_id"):
        request_id = getattr(value, attr, None)
        if request_id:
            return str(request_id)
    response = getattr(value, "response", None)
    headers = getattr(response, "headers", None)
    if headers:
        for key in ("x-request-id", "request-id"):
            request_id = headers.get(key)
            if request_id:
                return str(request_id)
    return None


def _use_openai_sdk(config: dict[str, Any]) -> bool:
    preference = os.getenv("FITCHECK_LLM_USE_OPENAI_SDK", "").strip().lower()
    if preference in {"0", "false", "no", "off"}:
        return False
    if OpenAI is None:
        return False
    return True


def _transport_name(config: dict[str, Any]) -> str:
    return "openai_python_sdk" if _use_openai_sdk(config) else "requests_http_fallback"


def _raise_llm_transport_error_if_sdk_exception(exc: Exception) -> None:
    status_code = getattr(getattr(exc, "response", None), "status_code", None)
    request_id = _sdk_request_id(exc)
    class_name = type(exc).__name__
    module_name = type(exc).__module__
    is_openai_exception = module_name.startswith("openai") or status_code is not None
    if not is_openai_exception:
        return
    if status_code is not None:
        raise LlmTransportError(
            f"HTTP {status_code} from OpenAI SDK: {exc}",
            status_code=int(status_code),
            request_id=request_id,
        ) from exc
    lowered_name = class_name.lower()
    if "timeout" in lowered_name:
        raise LlmTransportError(f"OpenAI SDK timeout: {exc}", request_id=request_id) from exc
    if "connection" in lowered_name:
        raise LlmTransportError(f"OpenAI SDK connection error: {exc}", request_id=request_id) from exc
    raise LlmTransportError(f"OpenAI SDK error: {exc}", request_id=request_id) from exc


def _is_retryable_request_error(exc: request_exceptions.RequestException) -> bool:
    if isinstance(
        exc,
        (
            request_exceptions.Timeout,
            request_exceptions.ConnectionError,
        ),
    ):
        return True
    response = getattr(exc, "response", None)
    return bool(response is not None and response.status_code in {408, 409, 429, 500, 502, 503, 504})


def _retry_delay(attempt: int) -> float:
    return min(1.5 * (attempt + 1), 4.0)


def _resolve_timeout() -> float:
    raw = os.getenv("FITCHECK_LLM_TIMEOUT")
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        return max(1.0, float(raw))
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS


def _resolve_max_retries() -> int:
    raw = os.getenv("FITCHECK_LLM_MAX_RETRIES")
    if not raw:
        return DEFAULT_MAX_RETRIES
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_MAX_RETRIES


def _resolve_api_style(base_url: str) -> str:
    explicit = os.getenv("FITCHECK_LLM_API", "").strip().lower().replace("-", "_")
    if explicit:
        if explicit not in SUPPORTED_API_STYLES:
            raise ValueError(
                f"Unsupported FITCHECK_LLM_API={explicit!r}; "
                f"expected one of {sorted(SUPPORTED_API_STYLES)}"
            )
        return explicit
    if "api.openai.com" in base_url:
        return "responses"
    return "chat_completions"


def _tool_capability_status(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "tool_execution": "backend_owned_functions",
        "local_function_tools_supported": True,
        "hosted_tools_supported": bool(
            config.get("api_style") == "responses"
            and "api.openai.com" in str(config.get("base_url", ""))
        ),
    }


def _to_chat_completion_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chat_tools: list[dict[str, Any]] = []
    for tool in tools:
        if tool.get("type") != "function":
            continue
        if "function" in tool:
            chat_tools.append(tool)
            continue
        function: dict[str, Any] = {
            "name": tool.get("name"),
            "description": tool.get("description", ""),
            "parameters": tool.get("parameters", {"type": "object", "properties": {}}),
        }
        if "strict" in tool:
            function["strict"] = tool["strict"]
        chat_tools.append({"type": "function", "function": function})
    return chat_tools


def _assistant_message_for_history(message: dict[str, Any]) -> dict[str, Any]:
    history_message: dict[str, Any] = {
        "role": "assistant",
        "content": message.get("content"),
    }
    if message.get("tool_calls"):
        history_message["tool_calls"] = message["tool_calls"]
    if message.get("function_call"):
        history_message["function_call"] = message["function_call"]
    return history_message


def _chat_completion_to_responses_like(response_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    message = ((payload.get("choices") or [{}])[0].get("message") or {})
    output: list[dict[str, Any]] = []
    tool_calls = message.get("tool_calls") or []
    for tool_call in tool_calls:
        function = tool_call.get("function") or {}
        output.append(
            {
                "type": "function_call",
                "id": tool_call.get("id"),
                "call_id": tool_call.get("id"),
                "name": function.get("name"),
                "arguments": function.get("arguments") or "{}",
            }
        )
    if not tool_calls and message.get("function_call"):
        function_call = message.get("function_call") or {}
        output.append(
            {
                "type": "function_call",
                "id": _legacy_function_call_id(response_id),
                "call_id": _legacy_function_call_id(response_id),
                "name": function_call.get("name"),
                "arguments": function_call.get("arguments") or "{}",
                "compatibility": "deprecated_chat_function_call",
            }
        )

    content = message.get("content") or ""
    if content and not output:
        output.append(
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": content,
                    }
                ],
            }
        )

    return {
        "id": response_id,
        "api_style": "chat_completions",
        "output_text": content if content and not tool_calls else "",
        "output": output,
        "raw_response_id": payload.get("id"),
        "request_id": payload.get("request_id") or payload.get("provider_request_id"),
        "provider_request_id": payload.get("provider_request_id") or payload.get("request_id"),
        "transport": payload.get("transport"),
    }


def _legacy_function_call_id(response_id: str | None) -> str:
    return f"{response_id or 'chat_completion'}_function_call"


def _legacy_function_name_for_output(
    messages: list[dict[str, Any]],
    *,
    previous_response_id: str | None,
    call_id: str,
) -> str | None:
    if call_id != _legacy_function_call_id(previous_response_id):
        return None
    for message in reversed(messages):
        function_call = message.get("function_call")
        if message.get("role") == "assistant" and isinstance(function_call, dict):
            name = function_call.get("name")
            return str(name) if name else None
    return None


def _reconstruct_chat_messages_from_run(instructions: str, run: dict[str, Any]) -> list[dict[str, Any]]:
    observations_by_call_id = {
        str(observation.get("call_id")): observation
        for observation in (run.get("state") or {}).get("observations", []) or []
        if observation.get("call_id")
    }
    messages: list[dict[str, Any]] = [{"role": "system", "content": instructions}]
    initial_user_content = _initial_user_content_from_run(run)
    if initial_user_content:
        messages.append({"role": "user", "content": initial_user_content})

    pending_tool_calls: list[dict[str, Any]] = []
    emitted_call_ids: set[str] = set()
    for step in run.get("trace", []) or []:
        step_type = step.get("step_type")
        if step_type == "llm_response":
            _flush_reconstructed_tool_calls(messages, pending_tool_calls, observations_by_call_id, emitted_call_ids)
            pending_tool_calls = []
        elif step_type == "user_reply":
            _flush_reconstructed_tool_calls(messages, pending_tool_calls, observations_by_call_id, emitted_call_ids)
            pending_tool_calls = []
            reply_content = _user_reply_content_from_payload(step.get("input") or {})
            if reply_content:
                messages.append({"role": "user", "content": reply_content})
        elif step_type == "tool_call":
            call_id = str((step.get("input") or {}).get("call_id") or "").strip()
            name = str(step.get("name") or "").strip()
            if call_id and name:
                pending_tool_calls.append(
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": json.dumps(
                                (step.get("input") or {}).get("arguments") or {},
                                ensure_ascii=False,
                            ),
                        },
                    }
                )
    _flush_reconstructed_tool_calls(messages, pending_tool_calls, observations_by_call_id, emitted_call_ids)
    return messages


def _flush_reconstructed_tool_calls(
    messages: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
    observations_by_call_id: dict[str, dict[str, Any]],
    emitted_call_ids: set[str],
) -> None:
    if not tool_calls:
        return
    messages.append({"role": "assistant", "content": "", "tool_calls": tool_calls})
    for call in tool_calls:
        call_id = str(call.get("id") or "")
        observation = observations_by_call_id.get(call_id)
        if not observation or call_id in emitted_call_ids:
            continue
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call_id,
                "content": json.dumps(observation.get("result") or {}, ensure_ascii=False),
            }
        )
        emitted_call_ids.add(call_id)


def _initial_user_content_from_run(run: dict[str, Any]) -> str:
    payload = dict(run.get("input") or {})
    payload.pop("session_id", None)
    payload.pop("user_replies", None)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _user_reply_content_from_payload(payload: dict[str, Any]) -> str:
    cleaned = dict(payload)
    cleaned.pop("session_id", None)
    return json.dumps(cleaned, ensure_ascii=False, sort_keys=True)


def _extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))
