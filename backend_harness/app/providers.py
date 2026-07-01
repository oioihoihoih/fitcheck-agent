from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from time import sleep
from typing import Any, Callable, Protocol
from urllib.parse import parse_qs, unquote, urlparse

import requests
from requests import exceptions as request_exceptions
from bs4 import BeautifulSoup

from .config_env import load_project_env


load_project_env()


RETRYABLE_HTTP_STATUS_CODES = {429, 500, 502, 503, 504}
GENERIC_PROVIDER_REQUEST_FAILURE_CODES = {
    "SEARCH_PROVIDER_REQUEST_FAILED",
    "PAGE_FETCH_REQUEST_FAILED",
}
DEFAULT_PREFERRED_SEARCH_DOMAINS = [
    "musinsa.com",
    "29cm.co.kr",
    "wconcept.co.kr",
    "smartstore.naver.com",
    "naver.com",
    "ably.co.kr",
    "zigzag.kr",
    "ssfshop.com",
    "eqlstore.com",
]
DEFAULT_BLOCKED_SEARCH_DOMAINS = [
    "instagram.com",
    "facebook.com",
    "youtube.com",
    "pinterest.com",
    "reddit.com",
    "tistory.com",
    "brunch.co.kr",
    "medium.com",
]
OCR_SPEC_KEYWORD_PATTERN = (
    r"(size|chest|shoulder|length|sleeve|material|fabric|cotton|poly|wool|linen|rayon|nylon|cm|"
    r"\uc0ac\uc774\uc988|\ucd1d\uc7a5|\uc5b4\uae68|\uac00\uc2b4|\uc18c\ub9e4|"
    r"\uc18c\uc7ac|\ud63c\uc6a9|\uba74|\ud3f4\ub9ac|\uc6b8|\ub9b0\ub128)"
)


class ProviderRequestFailure(Exception):
    def __init__(self, message: str, *, retry_count: int, retry_errors: list[str]) -> None:
        super().__init__(message)
        self.retry_count = retry_count
        self.retry_errors = retry_errors


def provider_error_metadata_from_result(result: dict[str, Any]) -> dict[str, Any]:
    retry_errors = _retry_errors_from_result(result)
    retry_codes = [_provider_error_code_from_text(item) for item in retry_errors]
    retry_codes = [code for code in retry_codes if code]
    latest_retry_code = retry_codes[-1] if retry_codes else None
    evidence_codes = _provider_evidence_error_codes(result)
    latest_evidence_code = evidence_codes[-1] if evidence_codes else None

    explicit_error = result.get("error") or {}
    explicit_error_dict = explicit_error if isinstance(explicit_error, dict) else {}
    explicit_code = explicit_error_dict.get("code")
    final_failure = (
        result.get("status") in {"failed", "configuration_required"}
        or result.get("fetch_status") == "failed"
        or bool(result.get("fetch_error"))
        or _provider_evidence_indicates_unusable(result)
    )
    normalized_error_code = None
    if final_failure:
        if explicit_code and explicit_code not in GENERIC_PROVIDER_REQUEST_FAILURE_CODES:
            normalized_error_code = explicit_code
        elif latest_retry_code:
            normalized_error_code = latest_retry_code
        elif latest_evidence_code:
            normalized_error_code = latest_evidence_code
        elif result.get("fetch_error"):
            normalized_error_code = _provider_error_code_from_text(str(result.get("fetch_error")))
        elif explicit_code:
            normalized_error_code = explicit_code
        else:
            normalized_error_code = "PROVIDER_UNKNOWN_ERROR"

    metadata: dict[str, Any] = {}
    if retry_codes:
        metadata["provider_retry_error_codes"] = retry_codes
        metadata["provider_latest_retry_error_code"] = latest_retry_code
        metadata["provider_latest_retry_error_category"] = _provider_error_category(latest_retry_code)
    if evidence_codes:
        metadata["provider_evidence_error_codes"] = evidence_codes
        metadata["provider_latest_evidence_error_code"] = latest_evidence_code
        metadata["provider_latest_evidence_error_category"] = _provider_error_category(latest_evidence_code)
    if normalized_error_code:
        metadata["provider_error_code"] = normalized_error_code
        metadata["provider_error_category"] = _provider_error_category(normalized_error_code)
        preview = _provider_error_preview(result)
        if preview:
            metadata["provider_error_message_preview"] = preview
    return metadata


def _retry_errors_from_result(result: dict[str, Any]) -> list[str]:
    retry_errors = result.get("retry_errors") or []
    if isinstance(retry_errors, list):
        return [str(item) for item in retry_errors if str(item)]
    return [str(retry_errors)] if str(retry_errors) else []


def _provider_error_preview(result: dict[str, Any]) -> str:
    explicit_error = result.get("error") or {}
    explicit_error_dict = explicit_error if isinstance(explicit_error, dict) else {}
    for value in [
        explicit_error_dict.get("message") or (explicit_error if isinstance(explicit_error, str) else None),
        result.get("fetch_error"),
        *(reversed(_retry_errors_from_result(result))),
    ]:
        if value:
            return str(value)[:240]
    return ""


def _provider_evidence_items(result: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = result.get("evidence") or result.get("ocr_evidence") or []
    if not isinstance(evidence, list):
        return []
    return [item for item in evidence if isinstance(item, dict)]


def _provider_evidence_indicates_unusable(result: dict[str, Any]) -> bool:
    evidence = _provider_evidence_items(result)
    if not evidence:
        return False
    if not _provider_evidence_error_codes(result):
        return False
    return not any(item.get("status") == "completed" and item.get("text") for item in evidence)


def _provider_evidence_error_codes(result: dict[str, Any]) -> list[str]:
    codes = []
    for item in _provider_evidence_items(result):
        code = _provider_error_code_from_evidence(item)
        if code:
            codes.append(code)
    return codes


def _provider_error_code_from_evidence(item: dict[str, Any]) -> str | None:
    reason = str(item.get("reason") or "")
    status = str(item.get("status") or "")
    if status == "skipped":
        return None
    reason_map = {
        "live_ocr_provider_not_selected": "OCR_PROVIDER_NOT_CONNECTED",
        "tesseract_cli_not_found": "OCR_TESSERACT_CLI_NOT_FOUND",
        "tesseract_cli_timeout": "OCR_TESSERACT_TIMEOUT",
        "tesseract_cli_failed": "OCR_TESSERACT_PROCESS_FAILED",
        "uploaded_file_not_found": "OCR_UPLOAD_FILE_NOT_FOUND",
        "no_local_asset_path_for_live_ocr": "OCR_LOCAL_ASSET_PATH_REQUIRED",
    }
    if reason in reason_map:
        return reason_map[reason]
    if status == "configuration_required":
        return "OCR_PROVIDER_CONFIGURATION_REQUIRED"
    if status == "not_connected":
        return "OCR_PROVIDER_NOT_CONNECTED"
    if status == "failed":
        return "OCR_PROVIDER_EVIDENCE_FAILED"
    return None


def _provider_error_code_from_text(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value)
    lowered = text.lower()
    status_match = re.search(r"http_status:(\d{3})", lowered)
    if not status_match:
        status_match = re.search(r"\b(\d{3})\s+(?:client|server)\s+error\b", lowered)
    if status_match:
        return _provider_http_status_error_code(int(status_match.group(1)))
    if "timeout" in lowered or "timed out" in lowered:
        return "REQUEST_TIMEOUT"
    if any(token in lowered for token in ["connection", "dns", "name resolution", "refused"]):
        return "REQUEST_CONNECTION_ERROR"
    if "too many redirects" in lowered:
        return "REQUEST_TOO_MANY_REDIRECTS"
    if "chunked" in lowered or "incomplete read" in lowered:
        return "REQUEST_CHUNKED_ENCODING_ERROR"
    return None


def _provider_http_status_error_code(status_code: int) -> str:
    if status_code == 429:
        return "HTTP_429_RATE_LIMITED"
    labels = {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        408: "REQUEST_TIMEOUT",
        500: "SERVER_ERROR",
        502: "BAD_GATEWAY",
        503: "SERVICE_UNAVAILABLE",
        504: "GATEWAY_TIMEOUT",
    }
    label = labels.get(status_code)
    if label:
        return f"HTTP_{status_code}_{label}"
    if 400 <= status_code < 500:
        return f"HTTP_{status_code}_CLIENT_ERROR"
    if 500 <= status_code < 600:
        return f"HTTP_{status_code}_SERVER_ERROR"
    return f"HTTP_{status_code}"


def _provider_error_category(error_code: str | None) -> str | None:
    if not error_code:
        return None
    if error_code in {"SEARCH_PROVIDER_API_KEY_REQUIRED", "SEARCH_PROVIDER_BASE_URL_REQUIRED", "LIVE_SEARCH_PROVIDER_REQUIRED"}:
        return "configuration"
    if error_code in {"OCR_TESSERACT_CLI_NOT_FOUND", "OCR_PROVIDER_CONFIGURATION_REQUIRED"}:
        return "configuration"
    if error_code == "OCR_PROVIDER_NOT_CONNECTED":
        return "provider_not_connected"
    if error_code == "OCR_TESSERACT_TIMEOUT":
        return "timeout"
    if error_code == "OCR_TESSERACT_PROCESS_FAILED":
        return "provider_process_failed"
    if error_code == "OCR_UPLOAD_FILE_NOT_FOUND":
        return "local_file_missing"
    if error_code == "OCR_LOCAL_ASSET_PATH_REQUIRED":
        return "asset_boundary"
    if error_code.startswith("OCR_"):
        return "ocr_error"
    if error_code == "DUCKDUCKGO_HTML_CHALLENGE":
        return "challenge"
    if error_code == "HTTP_429_RATE_LIMITED":
        return "rate_limited"
    if error_code.startswith("HTTP_5"):
        return "provider_server_error"
    if error_code.startswith("HTTP_4"):
        return "provider_client_error"
    if error_code == "REQUEST_TIMEOUT":
        return "timeout"
    if error_code == "REQUEST_CONNECTION_ERROR":
        return "connection_error"
    if error_code.startswith("REQUEST_"):
        return "request_error"
    if error_code.endswith("_REQUIRED"):
        return "configuration"
    return "provider_error"


def request_get_with_retries(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, Any] | None = None,
    timeout: float,
    max_retries: int = 0,
    **kwargs,
):
    attempts = max(0, int(max_retries)) + 1
    retry_errors: list[str] = []
    for attempt in range(attempts):
        try:
            response = requests.get(
                url,
                params=params,
                headers=headers,
                timeout=timeout,
                **kwargs,
            )
            status_code = getattr(response, "status_code", 200)
            if status_code in RETRYABLE_HTTP_STATUS_CODES and attempt < attempts - 1:
                retry_errors.append(f"http_status:{status_code}")
                sleep(_retry_delay(attempt))
                continue
            response.raise_for_status()
            return response, attempt, retry_errors
        except request_exceptions.RequestException as exc:
            retry_errors.append(str(exc)[:300])
            if attempt >= attempts - 1 or not _is_retryable_request_exception(exc):
                raise ProviderRequestFailure(
                    str(exc),
                    retry_count=attempt,
                    retry_errors=retry_errors,
                ) from exc
            sleep(_retry_delay(attempt))
    raise ProviderRequestFailure(
        "request retry loop exhausted",
        retry_count=max(0, attempts - 1),
        retry_errors=retry_errors,
    )


def request_post_with_retries(
    url: str,
    *,
    json_payload: dict[str, Any] | None = None,
    headers: dict[str, Any] | None = None,
    timeout: float,
    max_retries: int = 0,
    **kwargs,
):
    attempts = max(0, int(max_retries)) + 1
    retry_errors: list[str] = []
    for attempt in range(attempts):
        try:
            response = requests.post(
                url,
                json=json_payload,
                headers=headers,
                timeout=timeout,
                **kwargs,
            )
            status_code = getattr(response, "status_code", 200)
            if status_code in RETRYABLE_HTTP_STATUS_CODES and attempt < attempts - 1:
                retry_errors.append(f"http_status:{status_code}")
                sleep(_retry_delay(attempt))
                continue
            response.raise_for_status()
            return response, attempt, retry_errors
        except request_exceptions.RequestException as exc:
            retry_errors.append(str(exc)[:300])
            if attempt >= attempts - 1 or not _is_retryable_request_exception(exc):
                raise ProviderRequestFailure(
                    str(exc),
                    retry_count=attempt,
                    retry_errors=retry_errors,
                ) from exc
            sleep(_retry_delay(attempt))
    raise ProviderRequestFailure(
        "request retry loop exhausted",
        retry_count=max(0, attempts - 1),
        retry_errors=retry_errors,
    )


class SearchProvider(Protocol):
    provider_name: str
    mode: str

    def search(self, query: str, *, refresh: bool = False) -> dict[str, Any]:
        ...


class OcrProvider(Protocol):
    provider_name: str
    mode: str

    def extract_product_evidence(self, product: dict[str, Any]) -> dict[str, Any]:
        ...

    def extract_upload_evidence(
        self,
        attachments: list[dict[str, Any]],
        *,
        upload_dir: Path,
    ) -> dict[str, Any]:
        ...


class VisionProvider(Protocol):
    provider_name: str
    mode: str

    def inspect_uploads(
        self,
        attachments: list[dict[str, Any]],
        *,
        upload_dir: Path,
    ) -> dict[str, Any]:
        ...


@dataclass
class CachedHarnessSearchProvider:
    web_result_path: Path
    refresh_web_candidates: Callable[[], None]
    provider_name: str = "cached_harness_search"
    mode: str = "cached_harness"

    def search(self, query: str, *, refresh: bool = False) -> dict[str, Any]:
        if refresh or not self.web_result_path.exists():
            self.refresh_web_candidates()
        result = json.loads(self.web_result_path.read_text(encoding="utf-8"))
        rows = result.get("summary_table", [])
        return {
            "status": "completed",
            "provider": self.provider_name,
            "mode": self.mode,
            "external_api_called": False,
            "timeout_seconds": None,
            "retry_count": 0,
            "query": query,
            "candidate_count": len(rows),
            "candidates": [
                {
                    "candidate_key": row.get("candidate_key"),
                    "name": row.get("name"),
                    "recommendation_from_cache": row.get("recommendation"),
                    "overall_from_cache": row.get("overall"),
                    "source_url": row.get("source_url"),
                    "missing_fields": row.get("missing_fields", []),
                }
                for row in rows
            ],
        }


@dataclass
class DuckDuckGoHtmlSearchProvider:
    provider_name: str = "duckduckgo_html_search"
    mode: str = "live_provider"
    timeout_seconds: float = 10.0
    max_results: int = 5
    max_retries: int = 1

    def search(self, query: str, *, refresh: bool = False) -> dict[str, Any]:
        try:
            response, retry_count, retry_errors = request_get_with_retries(
                "https://duckduckgo.com/html/",
                params={"q": query},
                headers={"User-Agent": "FitCheckAgent/0.2"},
                timeout=self.timeout_seconds,
                max_retries=self.max_retries,
            )
        except ProviderRequestFailure as exc:
            return _live_search_request_failed(
                provider=self.provider_name,
                mode=self.mode,
                query=query,
                timeout_seconds=self.timeout_seconds,
                retry_count=exc.retry_count,
                retry_errors=exc.retry_errors,
                error_message=str(exc),
            )
        if _is_duckduckgo_challenge_page(response.text):
            result = {
                "status": "failed",
                "provider": self.provider_name,
                "mode": self.mode,
                "external_api_called": True,
                "timeout_seconds": self.timeout_seconds,
                "retry_count": retry_count,
                "query": query,
                "query_quality": _search_query_diagnostics(query),
                "domain_policy": _search_domain_policy_summary(),
                "candidate_count": 0,
                "candidates": [],
                "error": {
                    "code": "DUCKDUCKGO_HTML_CHALLENGE",
                    "message": "DuckDuckGo returned an anomaly/challenge page instead of search results.",
                },
            }
            if retry_errors:
                result["retry_errors"] = retry_errors
            result.update(provider_error_metadata_from_result(result))
            return result
        soup = BeautifulSoup(response.text, "html.parser")
        results = []
        for result_node in soup.select(".result"):
            link = result_node.select_one(".result__a")
            if not link:
                continue
            href = _normalize_duckduckgo_href(link.get("href") or "")
            title = link.get_text(" ", strip=True)
            snippet_node = result_node.select_one(".result__snippet")
            snippet = snippet_node.get_text(" ", strip=True) if snippet_node else ""
            if not href or not title:
                continue
            candidate = _annotate_search_candidate(
                {
                    "candidate_key": f"live_{_slugify(title)[:42]}",
                    "name": title,
                    "source_url": href,
                    "snippet": snippet,
                    "missing_fields": ["measurements", "material", "price"],
                    "provider": self.provider_name,
                    "mode": self.mode,
                    "recommendation_from_cache": None,
                    "overall_from_cache": None,
                }
            )
            results.append(candidate)
            if len(results) >= self.max_results * 4:
                break
        ranked_results = _rank_search_candidates(results)
        result = {
            "status": "completed",
            "provider": self.provider_name,
            "mode": self.mode,
            "external_api_called": True,
            "timeout_seconds": self.timeout_seconds,
            "retry_count": retry_count,
            "query": query,
            **_search_result_payload(query, ranked_results, self.max_results),
        }
        if retry_errors:
            result["retry_errors"] = retry_errors
        return result


@dataclass
class JinaSearchProvider:
    api_key: str | None
    provider_name: str = "jina_search"
    mode: str = "live_provider"
    timeout_seconds: float = 12.0
    max_results: int = 5
    max_retries: int = 1

    def search(self, query: str, *, refresh: bool = False) -> dict[str, Any]:
        if not self.api_key:
            return {
                "status": "configuration_required",
                "provider": self.provider_name,
                "mode": self.mode,
                "external_api_called": False,
                "timeout_seconds": self.timeout_seconds,
                "retry_count": 0,
                "query": query,
                "query_quality": _search_query_diagnostics(query),
                "domain_policy": _search_domain_policy_summary(),
                "candidate_count": 0,
                "candidates": [],
                "error": {
                    "code": "SEARCH_PROVIDER_API_KEY_REQUIRED",
                    "message": "Set JINA_API_KEY or FITCHECK_SEARCH_API_KEY for FITCHECK_SEARCH_PROVIDER=jina_search.",
                },
            }
        try:
            response, retry_count, retry_errors = request_get_with_retries(
                "https://s.jina.ai/",
                params={"q": query},
                headers={
                    "Accept": "text/plain",
                    "Authorization": f"Bearer {self.api_key}",
                    "User-Agent": "FitCheckAgent/0.2",
                },
                timeout=self.timeout_seconds,
                max_retries=self.max_retries,
            )
        except ProviderRequestFailure as exc:
            return _live_search_request_failed(
                provider=self.provider_name,
                mode=self.mode,
                query=query,
                timeout_seconds=self.timeout_seconds,
                retry_count=exc.retry_count,
                retry_errors=exc.retry_errors,
                error_message=str(exc),
            )
        candidates = _parse_jina_search_markdown(response.text, self.max_results)
        result = {
            "status": "completed",
            "provider": self.provider_name,
            "mode": self.mode,
            "external_api_called": True,
            "timeout_seconds": self.timeout_seconds,
            "retry_count": retry_count,
            "query": query,
            **_search_result_payload(query, candidates, self.max_results),
        }
        if retry_errors:
            result["retry_errors"] = retry_errors
        return result


@dataclass
class BraveSearchProvider:
    api_key: str | None
    provider_name: str = "brave_search"
    mode: str = "live_provider"
    timeout_seconds: float = 12.0
    max_results: int = 5
    country: str = "KR"
    search_lang: str = "ko"
    max_retries: int = 1

    def search(self, query: str, *, refresh: bool = False) -> dict[str, Any]:
        if not self.api_key:
            return {
                "status": "configuration_required",
                "provider": self.provider_name,
                "mode": self.mode,
                "external_api_called": False,
                "timeout_seconds": self.timeout_seconds,
                "retry_count": 0,
                "query": query,
                "query_quality": _search_query_diagnostics(query),
                "domain_policy": _search_domain_policy_summary(),
                "candidate_count": 0,
                "candidates": [],
                "error": {
                    "code": "SEARCH_PROVIDER_API_KEY_REQUIRED",
                    "message": "Set BRAVE_SEARCH_API_KEY or FITCHECK_SEARCH_API_KEY for FITCHECK_SEARCH_PROVIDER=brave_search.",
                },
            }
        try:
            response, retry_count, retry_errors = request_get_with_retries(
                "https://api.search.brave.com/res/v1/web/search",
                params={
                    "q": query,
                    "count": 20,
                    "country": self.country,
                    "search_lang": self.search_lang,
                    "safesearch": "moderate",
                    "result_filter": "web",
                    "extra_snippets": "true",
                    "operators": "true",
                },
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip",
                    "X-Subscription-Token": self.api_key,
                    "User-Agent": "FitCheckAgent/0.2",
                },
                timeout=self.timeout_seconds,
                max_retries=self.max_retries,
            )
        except ProviderRequestFailure as exc:
            return _live_search_request_failed(
                provider=self.provider_name,
                mode=self.mode,
                query=query,
                timeout_seconds=self.timeout_seconds,
                retry_count=exc.retry_count,
                retry_errors=exc.retry_errors,
                error_message=str(exc),
            )
        candidates = _parse_brave_search_json(response.json(), self.max_results)
        result = {
            "status": "completed",
            "provider": self.provider_name,
            "mode": self.mode,
            "external_api_called": True,
            "timeout_seconds": self.timeout_seconds,
            "retry_count": retry_count,
            "query": query,
            **_search_result_payload(query, candidates, self.max_results),
        }
        if retry_errors:
            result["retry_errors"] = retry_errors
        return result


@dataclass
class TavilySearchProvider:
    api_key: str | None
    provider_name: str = "tavily_search"
    mode: str = "live_provider"
    timeout_seconds: float = 15.0
    max_results: int = 5
    max_retries: int = 1
    search_depth: str = "basic"
    topic: str = "general"
    country: str | None = "south korea"

    def search(self, query: str, *, refresh: bool = False) -> dict[str, Any]:
        if not self.api_key:
            return {
                "status": "configuration_required",
                "provider": self.provider_name,
                "mode": self.mode,
                "external_api_called": False,
                "timeout_seconds": self.timeout_seconds,
                "retry_count": 0,
                "query": query,
                "query_quality": _search_query_diagnostics(query),
                "domain_policy": _search_domain_policy_summary(),
                "candidate_count": 0,
                "candidates": [],
                "error": {
                    "code": "SEARCH_PROVIDER_API_KEY_REQUIRED",
                    "message": "Set TAVILY_API_KEY or FITCHECK_SEARCH_API_KEY for FITCHECK_SEARCH_PROVIDER=tavily_search.",
                },
            }
        domain_policy = _search_domain_policy_summary()
        payload: dict[str, Any] = {
            "query": query,
            "search_depth": self.search_depth,
            "topic": self.topic,
            "max_results": min(max(self.max_results * 2, 1), 20),
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
        }
        if self.country:
            payload["country"] = self.country
        if domain_policy.get("allowed_domains"):
            payload["include_domains"] = domain_policy["allowed_domains"]
        if domain_policy.get("blocked_domains"):
            payload["exclude_domains"] = domain_policy["blocked_domains"]
        try:
            response, retry_count, retry_errors = request_post_with_retries(
                "https://api.tavily.com/search",
                json_payload=payload,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "FitCheckAgent/0.2",
                },
                timeout=self.timeout_seconds,
                max_retries=self.max_retries,
            )
        except ProviderRequestFailure as exc:
            return _live_search_request_failed(
                provider=self.provider_name,
                mode=self.mode,
                query=query,
                timeout_seconds=self.timeout_seconds,
                retry_count=exc.retry_count,
                retry_errors=exc.retry_errors,
                error_message=str(exc),
            )
        try:
            payload_json = response.json()
        except ValueError as exc:
            return _live_search_request_failed(
                provider=self.provider_name,
                mode=self.mode,
                query=query,
                timeout_seconds=self.timeout_seconds,
                retry_count=retry_count,
                retry_errors=retry_errors,
                error_message=f"Tavily returned non-JSON response: {exc}",
            )
        candidates = _parse_tavily_search_json(payload_json, self.max_results)
        result = {
            "status": "completed",
            "provider": self.provider_name,
            "mode": self.mode,
            "external_api_called": True,
            "timeout_seconds": self.timeout_seconds,
            "retry_count": retry_count,
            "query": query,
            "tavily_search_depth": self.search_depth,
            "tavily_topic": self.topic,
            **_search_result_payload(query, candidates, self.max_results),
        }
        if retry_errors:
            result["retry_errors"] = retry_errors
        return result


@dataclass
class GoogleCseSearchProvider:
    api_key: str | None
    cse_id: str | None
    provider_name: str = "google_cse_search"
    mode: str = "live_provider"
    timeout_seconds: float = 12.0
    max_results: int = 5
    max_retries: int = 1

    def search(self, query: str, *, refresh: bool = False) -> dict[str, Any]:
        if not self.api_key or not self.cse_id:
            missing = []
            if not self.api_key:
                missing.append("GOOGLE_API_KEY or FITCHECK_SEARCH_API_KEY")
            if not self.cse_id:
                missing.append("GOOGLE_CSE_ID or FITCHECK_GOOGLE_CSE_ID")
            return {
                "status": "configuration_required",
                "provider": self.provider_name,
                "mode": self.mode,
                "external_api_called": False,
                "timeout_seconds": self.timeout_seconds,
                "retry_count": 0,
                "query": query,
                "query_quality": _search_query_diagnostics(query),
                "domain_policy": _search_domain_policy_summary(),
                "candidate_count": 0,
                "candidates": [],
                "error": {
                    "code": "GOOGLE_CSE_CONFIGURATION_REQUIRED",
                    "message": "Set Google Programmable Search API key and search engine id for FITCHECK_SEARCH_PROVIDER=google_cse.",
                    "missing": missing,
                },
                "required_configuration": missing,
            }
        try:
            response, retry_count, retry_errors = request_get_with_retries(
                "https://www.googleapis.com/customsearch/v1",
                params={
                    "key": self.api_key,
                    "cx": self.cse_id,
                    "q": query,
                    "num": min(max(self.max_results * 2, 1), 10),
                    "safe": "active",
                },
                headers={
                    "Accept": "application/json",
                    "User-Agent": "FitCheckAgent/0.2",
                },
                timeout=self.timeout_seconds,
                max_retries=self.max_retries,
            )
        except ProviderRequestFailure as exc:
            return _live_search_request_failed(
                provider=self.provider_name,
                mode=self.mode,
                query=query,
                timeout_seconds=self.timeout_seconds,
                retry_count=exc.retry_count,
                retry_errors=exc.retry_errors,
                error_message=str(exc),
            )
        try:
            payload = response.json()
        except ValueError as exc:
            return _live_search_request_failed(
                provider=self.provider_name,
                mode=self.mode,
                query=query,
                timeout_seconds=self.timeout_seconds,
                retry_count=retry_count,
                retry_errors=retry_errors,
                error_message=f"Google CSE returned non-JSON response: {exc}",
            )
        candidates = _parse_google_cse_json(payload, self.max_results)
        result = {
            "status": "completed",
            "provider": self.provider_name,
            "mode": self.mode,
            "external_api_called": True,
            "timeout_seconds": self.timeout_seconds,
            "retry_count": retry_count,
            "query": query,
            **_search_result_payload(query, candidates, self.max_results),
        }
        if retry_errors:
            result["retry_errors"] = retry_errors
        return result


@dataclass
class SearXngSearchProvider:
    base_url: str | None
    provider_name: str = "searxng_search"
    mode: str = "live_provider"
    timeout_seconds: float = 12.0
    max_results: int = 5
    language: str = "ko"
    safesearch: int = 1
    max_retries: int = 1

    def search(self, query: str, *, refresh: bool = False) -> dict[str, Any]:
        if not self.base_url:
            return {
                "status": "configuration_required",
                "provider": self.provider_name,
                "mode": self.mode,
                "external_api_called": False,
                "timeout_seconds": self.timeout_seconds,
                "retry_count": 0,
                "query": query,
                "query_quality": _search_query_diagnostics(query),
                "domain_policy": _search_domain_policy_summary(),
                "candidate_count": 0,
                "candidates": [],
                "error": {
                    "code": "SEARCH_PROVIDER_BASE_URL_REQUIRED",
                    "message": "Set FITCHECK_SEARXNG_URL or SEARXNG_URL for FITCHECK_SEARCH_PROVIDER=searxng.",
                },
            }
        endpoint = self.base_url.rstrip("/") + "/search"
        try:
            response, retry_count, retry_errors = request_get_with_retries(
                endpoint,
                params={
                    "q": query,
                    "format": "json",
                    "categories": "general",
                    "language": self.language,
                    "safesearch": str(self.safesearch),
                },
                headers={
                    "Accept": "application/json",
                    "User-Agent": "FitCheckAgent/0.2",
                },
                timeout=self.timeout_seconds,
                max_retries=self.max_retries,
            )
        except ProviderRequestFailure as exc:
            return _live_search_request_failed(
                provider=self.provider_name,
                mode=self.mode,
                query=query,
                timeout_seconds=self.timeout_seconds,
                retry_count=exc.retry_count,
                retry_errors=exc.retry_errors,
                error_message=str(exc),
            )
        candidates = _parse_searxng_search_json(response.json(), self.max_results)
        result = {
            "status": "completed",
            "provider": self.provider_name,
            "mode": self.mode,
            "external_api_called": True,
            "timeout_seconds": self.timeout_seconds,
            "retry_count": retry_count,
            "query": query,
            "base_url": self.base_url.rstrip("/"),
            **_search_result_payload(query, candidates, self.max_results),
        }
        if retry_errors:
            result["retry_errors"] = retry_errors
        return result


@dataclass
class FixtureOcrProvider:
    provider_name: str = "fixture_ocr_boundary"
    mode: str = "fixture_result_boundary"

    def extract_product_evidence(self, product: dict[str, Any]) -> dict[str, Any]:
        assets = product.get("asset_results", [])
        evidence = []
        for asset in assets:
            if asset.get("image_type") not in {"size_chart", "material_chart", "detail_text", "review_text"}:
                continue
            ocr = asset.get("ocr") or {}
            status = ocr.get("status") or "not_connected"
            reason = ocr.get("reason")
            if status == "not_connected" and not reason:
                reason = "live_ocr_provider_not_selected"
            evidence.append(
                {
                    "asset_id": asset.get("asset_id"),
                    "image_type": asset.get("image_type"),
                    "status": status,
                    "reason": reason,
                    "provider": ocr.get("provider") or self.provider_name,
                    "provider_mode": self.mode,
                    "external_api_called": bool(ocr.get("external_api_called")),
                    "confidence": ocr.get("confidence") if ocr.get("confidence") is not None else 0.0,
                    "text": ocr.get("text"),
                }
            )
        return {
            "status": "completed",
            "provider": self.provider_name,
            "mode": self.mode,
            "external_api_called": False,
            "timeout_seconds": None,
            "retry_count": 0,
            "evidence": evidence,
        }

    def extract_upload_evidence(
        self,
        attachments: list[dict[str, Any]],
        *,
        upload_dir: Path,
    ) -> dict[str, Any]:
        return {
            "status": "completed",
            "provider": self.provider_name,
            "mode": self.mode,
            "external_api_called": False,
            "timeout_seconds": None,
            "retry_count": 0,
            "evidence": [
                {
                    "attachment_id": item.get("id"),
                    "filename": item.get("filename"),
                    "status": "not_connected",
                    "reason": "live_ocr_provider_not_selected",
                    "text": None,
                    "confidence": 0.0,
                }
                for item in attachments
            ],
        }


@dataclass
class TesseractCliOcrProvider:
    provider_name: str = "tesseract_cli_ocr"
    mode: str = "live_provider"
    languages: str = "kor+eng"
    timeout_seconds: float = 12.0

    def extract_product_evidence(self, product: dict[str, Any]) -> dict[str, Any]:
        evidence = []
        for asset in product.get("asset_results", []):
            if not _is_product_ocr_target(asset):
                continue
            image_path = _local_asset_path(asset)
            if not image_path:
                evidence.append(
                    {
                        "asset_id": asset.get("asset_id"),
                        "image_type": asset.get("image_type"),
                        "status": "skipped",
                        "reason": "no_local_asset_path_for_live_ocr",
                        "provider": self.provider_name,
                        "provider_mode": self.mode,
                        "external_api_called": False,
                        "text": None,
                        "confidence": 0.0,
                    }
                )
                continue
            evidence.append(
                self._extract_path(
                    image_path,
                    asset_id=asset.get("asset_id"),
                    image_type=asset.get("image_type"),
                )
            )
        return {
            "status": "completed",
            "provider": self.provider_name,
            "mode": self.mode,
            "external_api_called": any(item.get("external_api_called") for item in evidence),
            "timeout_seconds": self.timeout_seconds,
            "retry_count": 0,
            "evidence": evidence,
        }

    def extract_upload_evidence(
        self,
        attachments: list[dict[str, Any]],
        *,
        upload_dir: Path,
    ) -> dict[str, Any]:
        evidence = []
        for item in attachments:
            path = _upload_path_for_attachment(item, upload_dir)
            if not path:
                evidence.append(
                    {
                        "attachment_id": item.get("id"),
                        "filename": item.get("filename"),
                        "status": "failed",
                        "reason": "uploaded_file_not_found",
                        "provider": self.provider_name,
                        "provider_mode": self.mode,
                        "external_api_called": False,
                        "text": None,
                        "confidence": 0.0,
                    }
                )
                continue
            evidence.append(
                self._extract_path(
                    path,
                    asset_id=item.get("id"),
                    filename=item.get("filename"),
                )
            )
        return {
            "status": "completed",
            "provider": self.provider_name,
            "mode": self.mode,
            "external_api_called": any(item.get("external_api_called") for item in evidence),
            "timeout_seconds": self.timeout_seconds,
            "retry_count": 0,
            "evidence": evidence,
        }

    def _extract_path(
        self,
        image_path: Path,
        *,
        asset_id: str | None = None,
        filename: str | None = None,
        image_type: str | None = None,
    ) -> dict[str, Any]:
        executable = _tesseract_executable()
        base = {
            "asset_id": asset_id,
            "filename": filename or image_path.name,
            "image_type": image_type,
            "asset_path": str(image_path),
            "provider": self.provider_name,
            "provider_mode": self.mode,
        }
        if not executable:
            return {
                **base,
                "status": "configuration_required",
                "reason": "tesseract_cli_not_found",
                "external_api_called": False,
                "text": None,
                "confidence": 0.0,
            }
        variants = _ocr_image_variants(image_path)
        attempts: list[dict[str, Any]] = []
        best_completed: subprocess.CompletedProcess[str] | None = None
        best_variant = "original"
        completed_texts: list[dict[str, Any]] = []
        try:
            for variant_label, variant_path in variants:
                completed = subprocess.run(
                    [executable, str(variant_path), "stdout", "-l", self.languages],
                    check=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.timeout_seconds,
                )
                text = (completed.stdout or "").strip()
                quality = _ocr_text_quality(text)
                attempts.append(
                    {
                        "variant": variant_label,
                        "status": "completed" if completed.returncode == 0 else "failed",
                        "text_length": len(text),
                        "quality": quality,
                    }
                )
                if completed.returncode == 0 and text:
                    completed_texts.append(
                        {
                            "variant": variant_label,
                            "text": text,
                            "quality": quality,
                        }
                    )
                if completed.returncode != 0 and best_completed is None:
                    best_completed = completed
                    best_variant = variant_label
                    continue
                if completed.returncode != 0:
                    continue
                if best_completed is None or best_completed.returncode != 0:
                    best_completed = completed
                    best_variant = variant_label
                    continue
                current_text = (best_completed.stdout or "").strip()
                if _ocr_text_quality(text) > _ocr_text_quality(current_text):
                    best_completed = completed
                    best_variant = variant_label
        except subprocess.TimeoutExpired:
            return {
                **base,
                "status": "failed",
                "reason": "tesseract_cli_timeout",
                "external_api_called": True,
                "text": None,
                "confidence": 0.0,
            }
        finally:
            _remove_temporary_ocr_variants(variants)

        completed = best_completed
        if completed is None:
            return {
                **base,
                "status": "failed",
                "reason": "tesseract_cli_failed",
                "external_api_called": True,
                "text": None,
                "confidence": 0.0,
                "ocr_attempts": attempts,
            }
        text = (completed.stdout or "").strip()
        if completed.returncode != 0:
            return {
                **base,
                "status": "failed",
                "reason": "tesseract_cli_failed",
                "stderr": (completed.stderr or "")[:500],
                "external_api_called": True,
                "text": text or None,
                "confidence": 0.0,
                "ocr_attempts": attempts,
                "ocr_variant": best_variant,
            }
        relevant_texts = _dedupe_ocr_texts(
            [item for item in completed_texts if float(item.get("quality") or 0.0) >= 0.55]
        )
        if len(relevant_texts) > 1:
            text = "\n".join(str(item["text"]).strip() for item in relevant_texts)
            best_variant = "combined_relevant"
        elif len(relevant_texts) == 1:
            text = str(relevant_texts[0]["text"]).strip()
            best_variant = str(relevant_texts[0]["variant"])
        return {
            **base,
            "status": "completed" if text else "completed_empty",
            "reason": None if text else "no_text_detected",
            "external_api_called": True,
            "text": text or None,
            "confidence": _ocr_confidence(text),
            "text_quality": _ocr_text_quality(text),
            "ocr_attempts": attempts,
            "ocr_variant": best_variant,
        }


def _ocr_image_variants(image_path: Path) -> list[tuple[str, Path]]:
    variants: list[tuple[str, Path]] = [("original", image_path)]
    try:
        from PIL import Image, ImageOps
    except Exception:
        return variants

    temporary_variants: list[tuple[str, Path]] = []
    try:
        with Image.open(image_path) as image:
            processed = ImageOps.exif_transpose(image).convert("L")
            width, height = processed.size
            if width <= 0 or height <= 0:
                return variants
            scale = 3 if max(width, height) < 1400 else 2
            _append_preprocessed_ocr_variant(
                temporary_variants,
                "preprocessed_threshold",
                processed,
                scale=scale,
            )
            if height >= width * 1.25 and height >= 900:
                crop_boxes = [
                    ("crop_top", (0, 0, width, max(1, height // 3))),
                    ("crop_middle", (0, height // 3, width, max(height // 3 + 1, (height * 2) // 3))),
                    ("crop_bottom", (0, (height * 2) // 3, width, height)),
                ]
                for label, box in crop_boxes:
                    _append_preprocessed_ocr_variant(
                        temporary_variants,
                        f"preprocessed_{label}",
                        processed.crop(box),
                        scale=scale,
                    )
    except Exception:
        for _, tmp_path in temporary_variants:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
        return variants

    return variants + temporary_variants


def _append_preprocessed_ocr_variant(
    temporary_variants: list[tuple[str, Path]],
    label: str,
    image: Any,
    *,
    scale: int,
) -> None:
    from PIL import Image, ImageOps

    width, height = image.size
    if width <= 0 or height <= 0:
        return
    processed = image.resize((width * scale, height * scale), Image.Resampling.LANCZOS)
    processed = ImageOps.autocontrast(processed)
    processed = processed.point(lambda pixel: 255 if pixel > 170 else 0, mode="1")
    fd, tmp_name = tempfile.mkstemp(prefix=f"fitcheck_ocr_{label}_", suffix=".png")
    os.close(fd)
    tmp_path = Path(tmp_name)
    processed.save(tmp_path)
    temporary_variants.append((label, tmp_path))


def _remove_temporary_ocr_variants(variants: list[tuple[str, Path]]) -> None:
    for label, path in variants:
        if label == "original":
            continue
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass


def _dedupe_ocr_texts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in sorted(items, key=lambda value: float(value.get("quality") or 0.0), reverse=True):
        normalized = re.sub(r"\s+", " ", str(item.get("text") or "")).strip().lower()
        if not normalized or normalized in seen:
            continue
        if any(normalized in existing or existing in normalized for existing in seen):
            continue
        seen.add(normalized)
        output.append(item)
    return output[:3]


def _ocr_text_quality(text: str) -> float:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    if not cleaned:
        return 0.0
    digit_groups = re.findall(r"\d+(?:\.\d+)?", cleaned)
    has_digit = bool(digit_groups)
    has_spec_keyword = bool(re.search(OCR_SPEC_KEYWORD_PATTERN, cleaned, re.I))
    quality = min(len(cleaned) / 120.0, 0.25)
    if has_digit and (has_spec_keyword or len(digit_groups) >= 2):
        quality += 0.25
    elif has_digit:
        quality += 0.05
    if has_spec_keyword:
        quality += 0.7
    if len(cleaned) < 8:
        quality -= 0.4
    return max(0.0, round(quality, 3))


def _ocr_confidence(text: str) -> float:
    quality = _ocr_text_quality(text)
    if quality <= 0:
        return 0.0
    if quality < 0.55:
        return round(max(0.1, quality), 2)
    return min(0.85, max(0.45, round(0.45 + quality * 0.25, 2)))


@dataclass
class NotConnectedVisionProvider:
    provider_name: str = "vision_provider_not_connected"
    mode: str = "not_connected"

    def inspect_uploads(
        self,
        attachments: list[dict[str, Any]],
        *,
        upload_dir: Path,
    ) -> dict[str, Any]:
        return {
            "status": "not_connected",
            "provider": self.provider_name,
            "mode": self.mode,
            "external_api_called": False,
            "evidence": [
                {
                    "attachment_id": item.get("id"),
                    "filename": item.get("filename"),
                    "status": "not_connected",
                    "reason": "vision_provider_not_configured",
                    "parsed": {},
                    "confidence": 0.0,
                }
                for item in attachments
            ],
        }


@dataclass
class OpenAiResponsesVisionProvider:
    provider_name: str = "openai_responses_vision"
    mode: str = "live_provider"
    api_key: str | None = None
    base_url: str = "https://api.openai.com/v1"
    model: str = ""
    timeout_seconds: float = 45.0
    max_retries: int = 1

    def inspect_uploads(
        self,
        attachments: list[dict[str, Any]],
        *,
        upload_dir: Path,
    ) -> dict[str, Any]:
        if not self.api_key:
            return {
                "status": "configuration_required",
                "provider": self.provider_name,
                "mode": self.mode,
                "external_api_called": False,
                "error": {
                    "code": "VISION_OPENAI_API_KEY_REQUIRED",
                    "message": "OPENAI_API_KEY or FITCHECK_VISION_API_KEY is required.",
                },
                "evidence": [],
            }
        if not self.model:
            return {
                "status": "configuration_required",
                "provider": self.provider_name,
                "mode": self.mode,
                "external_api_called": False,
                "error": {
                    "code": "VISION_MODEL_REQUIRED",
                    "message": "FITCHECK_VISION_MODEL, FITCHECK_LLM_MODEL, or OPENAI_MODEL is required.",
                },
                "evidence": [],
            }
        evidence = []
        external_called = False
        retry_errors: list[str] = []
        max_attachments = _int_env("FITCHECK_VISION_MAX_UPLOADS", 2)
        for item in attachments[:max_attachments]:
            path = _upload_path_for_attachment(item, upload_dir)
            if not path:
                evidence.append(
                    {
                        "attachment_id": item.get("id"),
                        "filename": item.get("filename"),
                        "status": "failed",
                        "reason": "uploaded_file_not_found",
                        "provider": self.provider_name,
                        "provider_mode": self.mode,
                        "external_api_called": False,
                        "parsed": {},
                        "confidence": 0.0,
                    }
                )
                continue
            try:
                payload = {
                    "model": self.model,
                    "input": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": (
                                        "You inspect a user's clothing photo for a shopping recommendation agent. "
                                        "Return only JSON with broad visible clues. Do not infer brand, exact product, "
                                        "fabric composition, measurements, price, durability, or private facts. "
                                        "Allowed JSON keys: category, category_query_label, color_query_label, "
                                        "silhouette_keywords, design_terms, sleeve_length, visible_text, confidence, notes. "
                                        "Use null or [] when uncertain."
                                    ),
                                },
                                {"type": "input_image", "image_url": _image_data_url(path, str(item.get("media_type") or ""))},
                            ],
                        }
                    ],
                    "max_output_tokens": 350,
                }
                response, retry_count, errors = request_post_with_retries(
                    f"{self.base_url.rstrip('/')}/responses",
                    json_payload=payload,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    timeout=self.timeout_seconds,
                    max_retries=self.max_retries,
                )
                external_called = True
                retry_errors.extend(errors)
                body = response.json()
                text = _responses_output_text(body)
                parsed = _extract_json_object(text)
                evidence.append(
                    {
                        "attachment_id": item.get("id"),
                        "filename": item.get("filename"),
                        "status": "completed",
                        "provider": self.provider_name,
                        "provider_mode": self.mode,
                        "external_api_called": True,
                        "retry_count": retry_count,
                        "parsed": parsed,
                        "raw_text": text[:1000],
                        "confidence": _vision_confidence(parsed),
                    }
                )
            except ProviderRequestFailure as exc:
                retry_errors.extend(exc.retry_errors)
                evidence.append(
                    {
                        "attachment_id": item.get("id"),
                        "filename": item.get("filename"),
                        "status": "failed",
                        "reason": "vision_provider_request_failed",
                        "provider": self.provider_name,
                        "provider_mode": self.mode,
                        "external_api_called": external_called,
                        "retry_count": exc.retry_count,
                        "retry_errors": exc.retry_errors,
                        "parsed": {},
                        "confidence": 0.0,
                    }
                )
            except Exception as exc:
                evidence.append(
                    {
                        "attachment_id": item.get("id"),
                        "filename": item.get("filename"),
                        "status": "failed",
                        "reason": "vision_provider_failed",
                        "provider": self.provider_name,
                        "provider_mode": self.mode,
                        "external_api_called": external_called,
                        "error": str(exc)[:300],
                        "parsed": {},
                        "confidence": 0.0,
                    }
                )
        return {
            "status": "completed" if any(item.get("status") == "completed" for item in evidence) else "failed" if evidence else "completed",
            "provider": self.provider_name,
            "mode": self.mode,
            "external_api_called": external_called,
            "timeout_seconds": self.timeout_seconds,
            "retry_count": max([int(item.get("retry_count") or 0) for item in evidence] or [0]),
            "retry_errors": retry_errors,
            "evidence": evidence,
        }


def _image_data_url(path: Path, media_type: str) -> str:
    content_type = media_type if media_type.startswith("image/") else _image_media_type_from_path(path)
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


def _image_media_type_from_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    if suffix == ".webp":
        return "image/webp"
    if suffix == ".gif":
        return "image/gif"
    return "image/jpeg"


def _responses_output_text(body: dict[str, Any]) -> str:
    if body.get("output_text"):
        return str(body["output_text"])
    parts = []
    for item in body.get("output", []) or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []) or []:
            if isinstance(content, dict) and content.get("text"):
                parts.append(str(content["text"]))
    return "\n".join(parts).strip()


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.S)
        if not match:
            return {}
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _vision_confidence(parsed: dict[str, Any]) -> float:
    try:
        raw = float(parsed.get("confidence"))
        if 0 <= raw <= 1:
            return round(raw, 2)
        if 1 < raw <= 100:
            return round(raw / 100, 2)
    except (TypeError, ValueError):
        pass
    clues = sum(1 for key in ["category", "category_query_label", "color_query_label", "sleeve_length"] if parsed.get(key))
    if parsed.get("silhouette_keywords"):
        clues += 1
    if parsed.get("design_terms"):
        clues += 1
    return min(0.75, 0.25 + 0.1 * clues)


def build_search_provider(
    *,
    web_result_path: Path,
    refresh_web_candidates: Callable[[], None],
    provider_override: str | None = None,
) -> SearchProvider:
    # docs/24 gap #11: allow a per-run caller override, falling back to env.
    if provider_override and provider_override.strip():
        provider = provider_override.strip().lower()
    else:
        provider = os.getenv("FITCHECK_SEARCH_PROVIDER", "cached_harness").strip().lower()
    if provider in {"jina", "jina_search", "jina_ai"}:
        return JinaSearchProvider(
            api_key=os.getenv("JINA_API_KEY") or os.getenv("FITCHECK_SEARCH_API_KEY"),
            timeout_seconds=_float_env("FITCHECK_SEARCH_TIMEOUT", 12.0),
            max_retries=_int_env("FITCHECK_SEARCH_MAX_RETRIES", 1),
        )
    if provider in {"brave", "brave_search", "brave_api"}:
        return BraveSearchProvider(
            api_key=os.getenv("BRAVE_SEARCH_API_KEY") or os.getenv("FITCHECK_SEARCH_API_KEY"),
            timeout_seconds=_float_env("FITCHECK_SEARCH_TIMEOUT", 12.0),
            country=os.getenv("FITCHECK_SEARCH_COUNTRY", "KR").strip().upper() or "KR",
            search_lang=os.getenv("FITCHECK_SEARCH_LANG", "ko").strip().lower() or "ko",
            max_retries=_int_env("FITCHECK_SEARCH_MAX_RETRIES", 1),
        )
    if provider in {"tavily", "tavily_search", "tavily_api"}:
        return TavilySearchProvider(
            api_key=os.getenv("TAVILY_API_KEY") or os.getenv("FITCHECK_SEARCH_API_KEY"),
            timeout_seconds=_float_env("FITCHECK_SEARCH_TIMEOUT", 15.0),
            max_retries=_int_env("FITCHECK_SEARCH_MAX_RETRIES", 1),
            search_depth=os.getenv("FITCHECK_TAVILY_SEARCH_DEPTH", "basic").strip().lower() or "basic",
            topic=os.getenv("FITCHECK_TAVILY_TOPIC", "general").strip().lower() or "general",
            country=os.getenv("FITCHECK_TAVILY_COUNTRY", "south korea").strip().lower() or None,
        )
    if provider in {"google", "google_cse", "google_custom_search", "programmable_search"}:
        return GoogleCseSearchProvider(
            api_key=os.getenv("GOOGLE_API_KEY") or os.getenv("FITCHECK_SEARCH_API_KEY"),
            cse_id=os.getenv("GOOGLE_CSE_ID") or os.getenv("FITCHECK_GOOGLE_CSE_ID"),
            timeout_seconds=_float_env("FITCHECK_SEARCH_TIMEOUT", 12.0),
            max_retries=_int_env("FITCHECK_SEARCH_MAX_RETRIES", 1),
        )
    if provider in {"searxng", "searxng_search", "searx"}:
        return SearXngSearchProvider(
            base_url=os.getenv("FITCHECK_SEARXNG_URL") or os.getenv("SEARXNG_URL"),
            timeout_seconds=_float_env("FITCHECK_SEARCH_TIMEOUT", 12.0),
            language=os.getenv("FITCHECK_SEARCH_LANG", "ko").strip().lower() or "ko",
            safesearch=_int_env("FITCHECK_SEARCH_SAFESEARCH", 1),
            max_retries=_int_env("FITCHECK_SEARCH_MAX_RETRIES", 1),
        )
    if provider in {"duckduckgo", "duckduckgo_html", "live_duckduckgo"}:
        return DuckDuckGoHtmlSearchProvider(
            timeout_seconds=_float_env("FITCHECK_SEARCH_TIMEOUT", 10.0),
            max_retries=_int_env("FITCHECK_SEARCH_MAX_RETRIES", 1),
        )
    return CachedHarnessSearchProvider(web_result_path, refresh_web_candidates)


def build_ocr_provider(provider_override: str | None = None) -> OcrProvider:
    # docs/24 gap #11: allow a per-run caller override, falling back to env.
    if provider_override and provider_override.strip():
        provider = provider_override.strip().lower()
    else:
        provider = os.getenv("FITCHECK_OCR_PROVIDER", "fixture_boundary").strip().lower()
    if provider in {"none", "off", "disabled"}:
        return FixtureOcrProvider()
    if provider in {"tesseract", "tesseract_cli", "live_tesseract"}:
        return TesseractCliOcrProvider(
            languages=os.getenv("FITCHECK_OCR_LANGS", "kor+eng").strip() or "kor+eng",
            timeout_seconds=_float_env("FITCHECK_OCR_TIMEOUT", 12.0),
        )
    return FixtureOcrProvider()


def build_vision_provider() -> VisionProvider:
    provider = os.getenv("FITCHECK_VISION_PROVIDER", "not_connected").strip().lower()
    if provider in {"openai", "openai_responses", "responses"}:
        return OpenAiResponsesVisionProvider(
            api_key=os.getenv("FITCHECK_VISION_API_KEY") or os.getenv("OPENAI_API_KEY"),
            base_url=(os.getenv("FITCHECK_VISION_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/"),
            model=os.getenv("FITCHECK_VISION_MODEL") or os.getenv("FITCHECK_LLM_MODEL") or os.getenv("OPENAI_MODEL", ""),
            timeout_seconds=_float_env("FITCHECK_VISION_TIMEOUT", 45.0),
            max_retries=_int_env("FITCHECK_VISION_MAX_RETRIES", 1),
        )
    return NotConnectedVisionProvider()


def get_provider_status() -> dict[str, Any]:
    search_setting = os.getenv("FITCHECK_SEARCH_PROVIDER", "cached_harness").strip().lower()
    ocr_setting = os.getenv("FITCHECK_OCR_PROVIDER", "fixture_boundary").strip().lower()
    vision_setting = os.getenv("FITCHECK_VISION_PROVIDER", "not_connected").strip().lower()
    page_fetch_fallbacks = _page_fetch_fallbacks()
    search_jina = search_setting in {"jina", "jina_search", "jina_ai"}
    search_brave = search_setting in {"brave", "brave_search", "brave_api"}
    search_tavily = search_setting in {"tavily", "tavily_search", "tavily_api"}
    search_google = search_setting in {"google", "google_cse", "google_custom_search", "programmable_search"}
    search_searxng = search_setting in {"searxng", "searxng_search", "searx"}
    search_duckduckgo = search_setting in {"duckduckgo", "duckduckgo_html", "live_duckduckgo"}
    search_live = search_jina or search_brave or search_tavily or search_google or search_searxng or search_duckduckgo
    ocr_live = ocr_setting in {"tesseract", "tesseract_cli", "live_tesseract"}
    vision_live = vision_setting in {"openai", "openai_responses", "responses"}
    searxng_url = os.getenv("FITCHECK_SEARXNG_URL") or os.getenv("SEARXNG_URL")
    google_cse_id = os.getenv("GOOGLE_CSE_ID") or os.getenv("FITCHECK_GOOGLE_CSE_ID")
    return {
        "search": {
            "selected": search_setting,
            "provider": (
                "jina_search" if search_jina
                else "brave_search" if search_brave
                else "tavily_search" if search_tavily
                else "google_cse_search" if search_google
                else "searxng_search" if search_searxng
                else "duckduckgo_html_search" if search_duckduckgo
                else "cached_harness_search"
            ),
            "mode": "live_provider" if search_live else "cached_harness",
            "external_provider_configured": search_live,
            "max_retries": _int_env("FITCHECK_SEARCH_MAX_RETRIES", 1) if search_live else 0,
            "requires_api_key": search_jina or search_brave or search_tavily or search_google,
            "api_key_configured": (
                bool(os.getenv("JINA_API_KEY") or os.getenv("FITCHECK_SEARCH_API_KEY")) if search_jina
                else bool(os.getenv("BRAVE_SEARCH_API_KEY") or os.getenv("FITCHECK_SEARCH_API_KEY")) if search_brave
                else bool(os.getenv("TAVILY_API_KEY") or os.getenv("FITCHECK_SEARCH_API_KEY")) if search_tavily
                else bool(os.getenv("GOOGLE_API_KEY") or os.getenv("FITCHECK_SEARCH_API_KEY")) if search_google
                else None
            ),
            "requires_search_engine_id": search_google,
            "search_engine_id_configured": bool(google_cse_id) if search_google else None,
            "requires_base_url": search_searxng,
            "base_url_configured": bool(searxng_url) if search_searxng else None,
            "base_url": searxng_url.rstrip("/") if search_searxng and searxng_url else None,
            "country": (
                os.getenv("FITCHECK_SEARCH_COUNTRY", "KR") if search_brave
                else os.getenv("FITCHECK_TAVILY_COUNTRY", "south korea") if search_tavily
                else None
            ),
            "search_lang": os.getenv("FITCHECK_SEARCH_LANG", "ko") if search_brave or search_searxng else None,
            "search_depth": os.getenv("FITCHECK_TAVILY_SEARCH_DEPTH", "basic") if search_tavily else None,
            "topic": os.getenv("FITCHECK_TAVILY_TOPIC", "general") if search_tavily else None,
            "domain_policy": _search_domain_policy_summary(),
        },
        "ocr": {
            "selected": ocr_setting,
            "provider": "tesseract_cli_ocr" if ocr_live else "fixture_ocr_boundary",
            "mode": "live_provider" if ocr_live else "fixture_result_boundary",
            "external_provider_configured": ocr_live,
            "tesseract_cli_available": bool(_tesseract_executable()),
            "languages": os.getenv("FITCHECK_OCR_LANGS", "kor+eng"),
        },
        "vision": {
            "selected": vision_setting,
            "provider": "openai_responses_vision" if vision_live else "vision_provider_not_connected",
            "mode": "live_provider" if vision_live else "not_connected",
            "external_provider_configured": vision_live,
            "requires_api_key": vision_live,
            "api_key_configured": bool(os.getenv("FITCHECK_VISION_API_KEY") or os.getenv("OPENAI_API_KEY")) if vision_live else None,
            "model": (os.getenv("FITCHECK_VISION_MODEL") or os.getenv("FITCHECK_LLM_MODEL") or os.getenv("OPENAI_MODEL")) if vision_live else None,
        },
        "page_fetch": {
            "selected": ",".join(sorted(page_fetch_fallbacks)) if page_fetch_fallbacks else "direct_fetch_only",
            "provider": "live_page_fetch",
            "mode": "live_provider",
            "external_provider_configured": True,
            "fallbacks": sorted(page_fetch_fallbacks),
            "fallback_enabled": bool(page_fetch_fallbacks),
            "jina_reader_enabled": "jina_reader" in page_fetch_fallbacks,
            "jina_reader_api_key_configured": bool(os.getenv("JINA_API_KEY") or os.getenv("FITCHECK_JINA_API_KEY")),
            "timeout_seconds": _int_env("FITCHECK_JINA_READER_TIMEOUT_SECONDS", 20) if "jina_reader" in page_fetch_fallbacks else None,
            "max_retries": _int_env("FITCHECK_JINA_READER_MAX_RETRIES", 0) if "jina_reader" in page_fetch_fallbacks else None,
        },
    }


def _page_fetch_fallbacks() -> set[str]:
    raw = os.getenv("FITCHECK_PAGE_FETCH_FALLBACK") or os.getenv("FITCHECK_PAGE_FETCH_FALLBACKS") or ""
    return {
        item.strip().lower()
        for item in raw.split(",")
        if item.strip()
    }


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9가-힣]+", "_", value).strip("_").lower()
    return slug or "candidate"


def _normalize_duckduckgo_href(href: str) -> str:
    if not href:
        return ""
    parsed = urlparse(href)
    if parsed.query:
        params = parse_qs(parsed.query)
        uddg = params.get("uddg")
        if uddg:
            return unquote(uddg[0])
    return href


def _is_duckduckgo_challenge_page(html: str) -> bool:
    text = html.lower()
    return (
        "duckduckgo.com/anomaly.js" in text
        or "id=\"challenge-form\"" in text
        or "anomaly" in text and "challenge" in text and "duckduckgo" in text
    )


def _parse_jina_search_markdown(markdown: str, max_results: int) -> list[dict[str, Any]]:
    candidates = []
    blocks = re.split(r"\n(?=\s*(?:Title:|\[\d+\]|#{1,3}\s))", markdown)
    for block in blocks:
        title = _first_match(block, r"(?im)^\s*Title:\s*(.+)$")
        url = _first_match(block, r"(?im)^\s*URL(?: Source)?:\s*(https?://\S+)")
        if not url:
            link = re.search(r"\[([^\]]+)\]\((https?://[^)]+)\)", block)
            if link:
                title = title or link.group(1)
                url = link.group(2)
        if not title:
            title = _first_match(block, r"(?im)^\s*#{1,3}\s*(.+)$")
        if not title or not url:
            continue
        snippet = re.sub(r"(?im)^(Title|URL Source|URL):.*$", "", block)
        snippet = re.sub(r"\[[^\]]+\]\(https?://[^)]+\)", "", snippet)
        snippet = re.sub(r"\s+", " ", snippet).strip()[:500]
        candidates.append(
            _annotate_search_candidate(
                {
                    "candidate_key": f"live_{_slugify(title)[:42]}",
                    "name": title.strip(),
                    "source_url": url.strip(),
                    "snippet": snippet,
                    "missing_fields": ["measurements", "material", "price"],
                    "provider": "jina_search",
                    "mode": "live_provider",
                    "recommendation_from_cache": None,
                    "overall_from_cache": None,
                }
            )
        )
        if len(candidates) >= max_results * 4:
            break
    return _rank_search_candidates(candidates)


def _parse_brave_search_json(payload: dict[str, Any], max_results: int) -> list[dict[str, Any]]:
    candidates = []
    for index, item in enumerate((payload.get("web") or {}).get("results", []) or [], start=1):
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        snippet_parts = [
            str(item.get("description") or item.get("snippet") or "").strip(),
            *[
                str(value or "").strip()
                for value in (item.get("extra_snippets") or [])
                if str(value or "").strip()
            ],
        ]
        snippet = " ".join(part for part in snippet_parts if part)
        if not title or not url.startswith(("http://", "https://")):
            continue
        candidates.append(
            _annotate_search_candidate(
                {
                    "candidate_key": f"live_{_slugify(title)[:42]}",
                    "name": title,
                    "source_url": url,
                    "snippet": re.sub(r"\s+", " ", snippet)[:500],
                    "missing_fields": ["measurements", "material", "price"],
                    "provider": "brave_search",
                    "mode": "live_provider",
                    "recommendation_from_cache": None,
                    "overall_from_cache": None,
                    "brave_result_rank": index,
                }
            )
        )
        if len(candidates) >= max_results * 4:
            break
    return _rank_search_candidates(candidates)


def _parse_tavily_search_json(payload: dict[str, Any], max_results: int) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for index, item in enumerate(payload.get("results") or [], start=1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        content = str(item.get("content") or item.get("raw_content") or "").strip()
        if not title or not url:
            continue
        candidate = {
            "candidate_key": f"tavily_{_slugify(title)[:42]}",
            "name": title,
            "source_url": url,
            "snippet": re.sub(r"\s+", " ", content)[:500],
            "missing_fields": ["measurements", "material", "price"],
            "provider": "tavily_search",
            "mode": "live_provider",
            "recommendation_from_cache": None,
            "overall_from_cache": None,
            "tavily_result_rank": index,
            "tavily_score": item.get("score"),
            "published_date": item.get("published_date"),
        }
        candidates.append(_annotate_search_candidate(candidate))
        if len(candidates) >= max_results * 4:
            break
    return _rank_search_candidates(candidates)


def _parse_google_cse_json(payload: dict[str, Any], max_results: int) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    candidates = []
    for index, item in enumerate(payload.get("items", []) or [], start=1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("link") or "").strip()
        snippet = str(item.get("snippet") or item.get("htmlSnippet") or "").strip()
        if not title or not url.startswith(("http://", "https://")):
            continue
        pagemap = item.get("pagemap") if isinstance(item.get("pagemap"), dict) else {}
        thumbnail_url = _first_google_pagemap_image(pagemap, "cse_thumbnail") or _first_google_pagemap_image(
            pagemap,
            "cse_image",
        )
        candidate = {
            "candidate_key": f"live_google_{_slugify(title)[:36]}",
            "name": title,
            "source_url": url,
            "snippet": re.sub(r"\s+", " ", snippet)[:500],
            "missing_fields": ["measurements", "material", "price"],
            "provider": "google_cse_search",
            "mode": "live_provider",
            "recommendation_from_cache": None,
            "overall_from_cache": None,
            "google_cse_result_rank": index,
            "display_link": item.get("displayLink"),
        }
        if thumbnail_url:
            candidate["thumbnail_url"] = thumbnail_url
        candidates.append(_annotate_search_candidate(candidate))
        if len(candidates) >= max_results * 4:
            break
    return _rank_search_candidates(candidates)


def _first_google_pagemap_image(pagemap: dict[str, Any], key: str) -> str:
    values = pagemap.get(key) or []
    if not isinstance(values, list):
        return ""
    for value in values:
        if not isinstance(value, dict):
            continue
        src = str(value.get("src") or "").strip()
        if src.startswith(("http://", "https://")):
            return src
    return ""


def _parse_searxng_search_json(payload: dict[str, Any], max_results: int) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    candidates = []
    for index, item in enumerate(payload.get("results", []) or [], start=1):
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        snippet = str(item.get("content") or item.get("description") or "").strip()
        if not title or not url.startswith(("http://", "https://")):
            continue
        candidates.append(
            _annotate_search_candidate(
                {
                    "candidate_key": f"live_{_slugify(title)[:42]}",
                    "name": title,
                    "source_url": url,
                    "snippet": re.sub(r"\s+", " ", snippet)[:500],
                    "missing_fields": ["measurements", "material", "price"],
                    "provider": "searxng_search",
                    "mode": "live_provider",
                    "recommendation_from_cache": None,
                    "overall_from_cache": None,
                    "searxng_result_rank": index,
                    "engines": item.get("engines", []),
                }
            )
        )
        if len(candidates) >= max_results * 4:
            break
    return _rank_search_candidates(candidates)


def _rank_search_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(
        enumerate(candidates, start=1),
        key=lambda item: (-int(item[1].get("search_match_score") or item[1].get("commerce_score") or 0), item[0]),
    )
    result = []
    for priority, (original_rank, candidate) in enumerate(ranked, start=1):
        result.append(
            {
                **candidate,
                "search_result_rank": int(candidate.get("search_result_rank") or original_rank),
                "search_priority": priority,
            }
        )
    return result


def _search_result_payload(query: str, ranked_candidates: list[dict[str, Any]], max_results: int) -> dict[str, Any]:
    ranked_candidates = _rank_search_candidates(
        [_apply_query_relevance(query, candidate) for candidate in ranked_candidates]
    )
    return {
        "query_quality": _search_query_diagnostics(query),
        "domain_policy": _search_domain_policy_summary(),
        **_candidate_filter_payload(ranked_candidates, max_results),
    }


def _apply_query_relevance(query: str, candidate: dict[str, Any]) -> dict[str, Any]:
    query_text = query.lower()
    candidate_text = " ".join(
        str(candidate.get(key) or "") for key in ("name", "source_url", "snippet")
    ).lower()
    candidate_title_url_text = " ".join(
        str(candidate.get(key) or "") for key in ("name", "source_url")
    ).lower()
    score = int(candidate.get("search_match_score") or candidate.get("commerce_score") or 0)
    signals = list(candidate.get("commerce_signals") or [])
    risks = list(candidate.get("content_risk_flags") or [])
    if _looks_like_tshirt_query(query_text):
        if _looks_like_tshirt_candidate(candidate_text):
            score += 15
            signals.append("query_category_match_t_shirt")
        else:
            score -= 35
            risks.append("query_category_mismatch_t_shirt")
    query_category = _fashion_category_from_text(query_text)
    candidate_category = _fashion_category_from_text(candidate_text)
    query_has_fashion_context = _has_human_fashion_context(query_text)
    if query_has_fashion_context:
        if _has_human_fashion_context(candidate_text):
            score += 12
            signals.append("human_fashion_context_match")
        if _has_pet_product_context(candidate_text):
            score -= 45
            risks.append("pet_product_context_mismatch")
        if _has_nonfashion_gear_context(candidate_text):
            score -= 25
            risks.append("nonfashion_gear_context_mismatch")
    if query_category:
        if candidate_category == query_category:
            score += 20
            signals.append(f"query_category_match_{query_category}")
            candidate_title_url_category = _fashion_category_from_text(candidate_title_url_text)
            if candidate_title_url_category != query_category:
                score = min(score, 40)
                risks.append(f"query_category_title_mismatch_{query_category}")
        elif candidate_category:
            score -= 40
            risks.append(f"query_category_mismatch_{query_category}")
    if _looks_like_denim_jeans_query(query_text):
        if _looks_like_denim_jeans_candidate(candidate_text):
            score += 15
            signals.append("query_subcategory_match_denim_jeans")
            if not _looks_like_denim_jeans_candidate(candidate_title_url_text):
                score = min(score, 40)
                risks.append("query_subcategory_title_mismatch_denim_jeans")
        else:
            score -= 45
            risks.append("query_subcategory_mismatch_denim_jeans")
        if _looks_like_shorts_candidate(candidate_text):
            score = min(score, 35)
            risks.append("query_subcategory_mismatch_shorts")
    if "product_url_path" not in signals and "commerce_query_param" not in signals:
        score = min(score, 40)
        risks.append("missing_product_detail_url")
    if (
        {"blog_or_article", "community_or_social"} & set(risks)
        and "product_url_path" not in signals
        and "commerce_query_param" not in signals
    ):
        score = min(score, 30)
    if "search_or_listing" in risks:
        score = min(score, 40)
        risks.append("listing_page_not_product_detail")
    if "pet_product_context_mismatch" in risks:
        score = min(score, 35)
    if "nonfashion_gear_context_mismatch" in risks:
        score = min(score, 40)
    score = max(0, min(100, score))
    return {
        **candidate,
        "commerce_score": score,
        "search_match_score": score,
        "commerce_signals": sorted(set(signals)),
        "content_risk_flags": sorted(set(risks)),
        "candidate_type": "product_like" if score >= 45 else "content_or_uncertain",
    }


def _looks_like_tshirt_query(text: str) -> bool:
    return bool(re.search(r"(반팔|티셔츠|t-?shirt|tshirt|tee\b|short[- ]sleeve)", text))


def _looks_like_tshirt_candidate(text: str) -> bool:
    return bool(re.search(r"(반팔|티셔츠|t-?shirt|tshirt|tee\b|크루\s*넥\s*티)", text))


def _looks_like_denim_jeans_query(text: str) -> bool:
    return bool(re.search(r"(청바지|데님|jeans?|denim)", text, re.IGNORECASE))


def _looks_like_denim_jeans_candidate(text: str) -> bool:
    return bool(re.search(r"(청바지|데님|jeans?|denim)", text, re.IGNORECASE))


def _looks_like_shorts_candidate(text: str) -> bool:
    return bool(re.search(r"(숏팬츠|반바지|쇼츠|shorts?\b)", text, re.IGNORECASE))


def _has_human_fashion_context(text: str) -> bool:
    return bool(
        re.search(
            r"(패션|의류|옷|착용|코디|스타일링|룩북|남성|여성|공용|유니섹스|"
            r"무신사|29cm|wconcept|ably|zigzag|fashion|apparel|wear|outfit|styling|lookbook|unisex|men'?s|women'?s)",
            text,
            re.IGNORECASE,
        )
    )


def _has_pet_product_context(text: str) -> bool:
    return bool(
        re.search(
            r"(강아지|반려견|애견|펫|고양이|반려묘|루이독|louisdog|puppy|dog|dogs|pet|pets|cat|cats)",
            text,
            re.IGNORECASE,
        )
    )


def _has_nonfashion_gear_context(text: str) -> bool:
    return bool(
        re.search(
            r"(전술|군용|군사|서바이벌|산업용|tactical|military|survival|industrial)",
            text,
            re.IGNORECASE,
        )
    )


def _fashion_category_from_text(text: str) -> str:
    category_patterns = [
        ("t_shirt", r"(반팔|티셔츠|t-?shirt|tshirt|tee\b)"),
        ("shirt", r"(셔츠|남방|\bshirt\b)"),
        ("pants", r"(청바지|데님|팬츠|바지|슬랙스|jeans?|denim|pants|trousers)"),
        ("shoes", r"(신발|운동화|스니커즈|로퍼|부츠|샌들|구두|슈즈|sneakers?|shoes?|loafer|boots?|sandals?)"),
        ("bag", r"(가방|백팩|토트백|크로스백|숄더백|핸드백|bag|backpack|tote)"),
        ("hat", r"(모자|캡|비니|버킷햇|hat|cap|beanie)"),
        ("dress", r"(원피스|드레스|\bdress\b)"),
        ("skirt", r"(스커트|치마|\bskirt\b)"),
        ("jacket", r"(자켓|재킷|블루종|패딩|jacket|blouson)"),
        ("coat", r"(코트|트렌치|\bcoat\b)"),
        ("knit", r"(니트|스웨터|knit|sweater)"),
        ("hoodie", r"(후드티|후디|hoodie)"),
    ]
    for category, pattern in category_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return category
    return ""


def _candidate_filter_payload(ranked_candidates: list[dict[str, Any]], max_results: int) -> dict[str, Any]:
    product_like = []
    rejected = []
    for candidate in ranked_candidates:
        if candidate.get("candidate_type") == "product_like":
            product_like.append(candidate)
        else:
            rejected.append(candidate)
    selected = product_like[:max_results]
    rejected_limit = max(max_results * 2, 3)
    return {
        "candidate_filter_policy": "product_like_only",
        "search_result_count": len(ranked_candidates),
        "product_like_candidate_count": len(product_like),
        "rejected_candidate_count": len(rejected),
        "rejected_candidates": [_rejected_candidate_summary(candidate) for candidate in rejected[:rejected_limit]],
        "candidate_count": len(selected),
        "candidates": selected,
    }


def _rejected_candidate_summary(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_key": candidate.get("candidate_key"),
        "name": candidate.get("name"),
        "source_url": candidate.get("source_url"),
        "candidate_type": candidate.get("candidate_type"),
        "commerce_score": candidate.get("commerce_score"),
        "search_match_score": candidate.get("search_match_score"),
        "commerce_signals": candidate.get("commerce_signals", []),
        "content_risk_flags": candidate.get("content_risk_flags", []),
        "domain_policy": candidate.get("domain_policy", {}),
        "search_result_rank": candidate.get("search_result_rank"),
        "search_priority": candidate.get("search_priority"),
    }


def _annotate_search_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    title = str(candidate.get("name") or "")
    url = str(candidate.get("source_url") or "")
    snippet = str(candidate.get("snippet") or "")
    score, signals, risks = _commerce_score(title=title, url=url, snippet=snippet)
    domain_policy = _domain_policy_for_url(url)
    if domain_policy["action"] == "blocked":
        score = 0
        risks.append("domain_blocked")
    elif domain_policy["action"] == "not_allowed":
        score = 0
        risks.append("domain_not_allowed")
    elif domain_policy["action"] == "preferred":
        score += 10
        signals.append("preferred_domain")
    search_match_score = max(0, min(100, score))
    return {
        **candidate,
        "commerce_score": search_match_score,
        "search_match_score": search_match_score,
        "commerce_signals": sorted(set(signals)),
        "content_risk_flags": sorted(set(risks)),
        "domain_policy": domain_policy,
        "candidate_type": "product_like" if search_match_score >= 45 else "content_or_uncertain",
    }


def _commerce_score(*, title: str, url: str, snippet: str) -> tuple[int, list[str], list[str]]:
    text = f"{title} {url} {snippet}".lower()
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path_and_query = f"{parsed.path}?{parsed.query}".lower()
    score = 0
    signals: list[str] = []
    risks: list[str] = []

    positive_patterns = {
        "product_url_path": r"(/(?:product|products|goods|item|items|detail|prd)(?:/|\.|_|-|\?|=|$)|product_no=|goodsno=|itemid=|item_id=|sku=)",
        "commerce_query_param": r"(product_no|goodsno|itemid|item_id|sku|option|cate_no)=",
        "price_or_currency": r"(₩|원|krw|[0-9][0-9,]{3,}\s*원)",
        "spec_signal": r"(총장|어깨|가슴단면|소매|cotton|면\s*100|폴리|소재|사이즈)",
        "clothing_keyword": r"(티셔츠|반팔|셔츠|후드|맨투맨|니트|팬츠|슬랙스|데님|자켓|재킷|원피스|스커트|피그먼트|신발|운동화|스니커즈|로퍼|부츠|샌들|구두|슈즈|가방|백팩|토트백|크로스백|모자|캡|비니|발라클라바|바라클라바|sneakers?|shoes?|loafer|boots?|sandals?|bag|backpack|hat|cap|balaclava)",
        "human_fashion_context": r"(패션|의류|옷|착용|코디|스타일링|룩북|남성|여성|공용|유니섹스|fashion|apparel|wear|outfit|styling|lookbook|unisex)",
    }
    for label, pattern in positive_patterns.items():
        if re.search(pattern, text):
            score += 20 if label in {"product_url_path", "commerce_query_param"} else 15
            signals.append(label)

    commerce_host_patterns = [
        "shop",
        "store",
        "mall",
        "smartstore",
        "cafe24",
        "musinsa",
        "29cm",
        "wconcept",
        "ably",
        "zigzag",
        "naver",
    ]
    if any(pattern in host for pattern in commerce_host_patterns):
        score += 15
        signals.append("commerce_host")
    if _has_human_fashion_context(text):
        score += 12
        signals.append("human_fashion_context")
    if _has_pet_product_context(text):
        score -= 45
        risks.append("pet_product_context")
    if _has_nonfashion_gear_context(text):
        score -= 20
        risks.append("nonfashion_gear_context")
    if "product_url_path" in signals and "commerce_host" in signals:
        score += 10
        signals.append("commerce_product_page")

    content_risk_patterns = {
        "blog_or_article": r"(blog|tistory|brunch|medium|post|article|news|magazine)",
        "community_or_social": r"(cafe\.naver|reddit|instagram|facebook|youtube|pinterest|threads)",
        "search_or_listing": (
            r"(/search|/tag|/tags|/category(?:/|$)|/categories(?:/|$)|/collections?(?:/|$)|"
            r"/menu(?:/|$)|/like(?:/|$)|/brand(?:/|$)|/brands(?:/|$)|/shop(?:/|$)|/store(?:/|$)|"
            r"/list(?:/|\.|\?|$)|product[_-]?list|item[_-]?list|itemlist|goods[_-]?list|prd[_-]?list|"
            r"searchsect\.|search\.ssg|dsearch\.php)"
        ),
        "navigation_or_account": r"(/choose-location|/login|/signin|/signup|/cart|/wishlist|/mypage|/account|/help|/event|/events|/ranking|/best(?:/|$))",
        "auxiliary_asset_page": r"(^image[-.]|/image|/images|/img|/static|/assets|sizetable|size_table|sizechart|size_chart|_size\.html)",
    }
    risk_text = f"{host} {path_and_query}"
    for label, pattern in content_risk_patterns.items():
        if re.search(pattern, risk_text):
            score -= 35 if label in {"search_or_listing", "navigation_or_account"} else 25
            risks.append(label)
    if ("search_or_listing" in risks or "navigation_or_account" in risks) and "product_url_path" not in signals:
        score = min(score, 30)
        risks.append("missing_product_detail_url")
    if "search_or_listing" in risks:
        score = min(score, 40)
        risks.append("listing_page_not_product_detail")
    if "auxiliary_asset_page" in risks:
        score = min(score, 30)

    return max(0, min(100, score)), signals, risks


def _search_query_diagnostics(query: str) -> dict[str, Any]:
    cleaned = re.sub(r"\s+", " ", query).strip()
    lowered = cleaned.lower()
    tokens = [token for token in re.split(r"\s+", cleaned) if token]
    clothing_patterns = [
        r"티셔츠|반팔|셔츠|후드|맨투맨|니트|팬츠|슬랙스|데님|자켓|재킷|원피스|스커트|코트|가디건|발라클라바|바라클라바",
        r"t-?shirt|shirt|hoodie|knit|pants|denim|jacket|coat|cardigan|skirt|dress|balaclava",
    ]
    product_intent_patterns = [
        r"상품|제품|구매|추천|사이즈표|소재|총장|어깨|가슴단면|소매|핏|오버핏|박시|와이드",
        r"product|buy|size chart|material|cotton|fit|oversized|wide",
    ]
    style_content_patterns = [r"코디|착장|후기|블로그|리뷰", r"styling|outfit|lookbook|review|blog"]
    specificity_patterns = [
        r"\b(?:xs|s|m|l|xl|xxl|free)\b",
        r"\d{2,3}\s*cm",
        r"면|코튼|cotton|폴리|poly|울|wool|데님|denim",
        r"무신사|29cm|wconcept|브랜드|brand",
    ]
    has_clothing_keyword = any(re.search(pattern, lowered) for pattern in clothing_patterns)
    has_product_intent = any(re.search(pattern, lowered) for pattern in product_intent_patterns)
    has_style_content_risk = any(re.search(pattern, lowered) for pattern in style_content_patterns)
    has_specificity = any(re.search(pattern, lowered) for pattern in specificity_patterns)
    score = 0
    if has_clothing_keyword:
        score += 35
    if has_product_intent:
        score += 30
    if has_specificity:
        score += 20
    if len(tokens) >= 3:
        score += 10
    if has_style_content_risk and not has_product_intent:
        score -= 20
    quality = "product_discovery_ready" if score >= 55 else "needs_query_expansion" if score >= 30 else "too_broad"
    return {
        "query": cleaned,
        "quality": quality,
        "score": max(0, min(100, score)),
        "token_count": len(tokens),
        "has_clothing_keyword": has_clothing_keyword,
        "has_product_intent_signal": has_product_intent,
        "has_specificity_signal": has_specificity,
        "style_content_risk": has_style_content_risk,
    }


def _search_domain_policy_summary() -> dict[str, Any]:
    allowed = _domain_patterns_from_env("FITCHECK_SEARCH_ALLOWED_DOMAINS", [])
    preferred = _domain_patterns_from_env("FITCHECK_SEARCH_PREFERRED_DOMAINS", DEFAULT_PREFERRED_SEARCH_DOMAINS)
    blocked = _domain_patterns_from_env("FITCHECK_SEARCH_BLOCKED_DOMAINS", DEFAULT_BLOCKED_SEARCH_DOMAINS)
    return {
        "allowed_domains_configured": bool(allowed),
        "allowed_domains": allowed,
        "preferred_domains": preferred,
        "blocked_domains": blocked,
        "policy": "allowlist_then_blocklist_then_preference",
    }


def _domain_policy_for_url(url: str) -> dict[str, Any]:
    host = urlparse(url).netloc.lower()
    allowed = _domain_patterns_from_env("FITCHECK_SEARCH_ALLOWED_DOMAINS", [])
    preferred = _domain_patterns_from_env("FITCHECK_SEARCH_PREFERRED_DOMAINS", DEFAULT_PREFERRED_SEARCH_DOMAINS)
    blocked = _domain_patterns_from_env("FITCHECK_SEARCH_BLOCKED_DOMAINS", DEFAULT_BLOCKED_SEARCH_DOMAINS)
    matched_blocked = _matching_domain_pattern(host, blocked)
    if matched_blocked:
        return {"host": host, "action": "blocked", "matched_domain": matched_blocked}
    if allowed:
        matched_allowed = _matching_domain_pattern(host, allowed)
        if not matched_allowed:
            return {"host": host, "action": "not_allowed", "matched_domain": None}
        matched_preferred = _matching_domain_pattern(host, preferred)
        return {
            "host": host,
            "action": "preferred" if matched_preferred else "allowed",
            "matched_domain": matched_preferred or matched_allowed,
        }
    matched_preferred = _matching_domain_pattern(host, preferred)
    return {
        "host": host,
        "action": "preferred" if matched_preferred else "allowed",
        "matched_domain": matched_preferred,
    }


def _domain_patterns_from_env(name: str, defaults: list[str]) -> list[str]:
    raw = os.getenv(name)
    if raw is None:
        return list(defaults)
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


def _matching_domain_pattern(host: str, patterns: list[str]) -> str | None:
    for pattern in patterns:
        cleaned = pattern.lower().removeprefix("*.").strip()
        if not cleaned:
            continue
        if "." not in cleaned and cleaned in host:
            return pattern
        if host == cleaned or host.endswith(f".{cleaned}"):
            return pattern
    return None


def _first_match(text: str, pattern: str) -> str:
    match = re.search(pattern, text)
    return match.group(1).strip() if match else ""


def _live_search_request_failed(
    *,
    provider: str,
    mode: str,
    query: str,
    timeout_seconds: float,
    retry_count: int,
    retry_errors: list[str],
    error_message: str,
) -> dict[str, Any]:
    result = {
        "status": "failed",
        "provider": provider,
        "mode": mode,
        "external_api_called": True,
        "timeout_seconds": timeout_seconds,
        "retry_count": retry_count,
        "retry_errors": retry_errors,
        "query": query,
        "query_quality": _search_query_diagnostics(query),
        "domain_policy": _search_domain_policy_summary(),
        "candidate_count": 0,
        "candidates": [],
        "error": {
            "code": "SEARCH_PROVIDER_REQUEST_FAILED",
            "message": error_message,
        },
    }
    result.update(provider_error_metadata_from_result(result))
    return result


def _is_retryable_request_exception(exc: request_exceptions.RequestException) -> bool:
    if isinstance(
        exc,
        (
            request_exceptions.Timeout,
            request_exceptions.ConnectionError,
            request_exceptions.ChunkedEncodingError,
        ),
    ):
        return True
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    return status_code in RETRYABLE_HTTP_STATUS_CODES


def _retry_delay(attempt: int) -> float:
    return min(0.1 * (2 ** attempt), 0.5)


def _upload_path_for_attachment(item: dict[str, Any], upload_dir: Path) -> Path | None:
    stored_name = item.get("stored_name")
    if stored_name:
        candidate = upload_dir / Path(str(stored_name)).name
        return candidate if candidate.exists() else None
    url = item.get("url") or ""
    if "/outputs/uploads/" in str(url):
        candidate = upload_dir / Path(str(url).rsplit("/", 1)[-1]).name
        return candidate if candidate.exists() else None
    return None


def _local_asset_path(asset: dict[str, Any]) -> Path | None:
    for key in ["asset_path", "local_path", "path"]:
        value = asset.get(key)
        if value and Path(str(value)).exists():
            return Path(str(value))
    return None


def _is_product_ocr_target(asset: dict[str, Any]) -> bool:
    image_type = str(asset.get("image_type") or "")
    if asset.get("ocr_target") is False:
        return False
    return image_type in {"size_chart", "material_chart", "detail_text", "review_text"}


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return max(0, int(value))
    except ValueError:
        return default


def _tesseract_executable() -> str | None:
    configured = os.getenv("FITCHECK_TESSERACT_CMD") or os.getenv("TESSERACT_CMD")
    if configured:
        path = Path(configured)
        if path.exists():
            return str(path)
        found = shutil.which(configured)
        if found:
            return found
    found = shutil.which("tesseract")
    if found:
        return found
    for candidate in [
        Path("C:/Program Files/Tesseract-OCR/tesseract.exe"),
        Path("C:/Program Files (x86)/Tesseract-OCR/tesseract.exe"),
    ]:
        if candidate.exists():
            return str(candidate)
    return None
