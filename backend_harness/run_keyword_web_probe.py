from __future__ import annotations

import json
import os
import re
from pathlib import Path
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

import requests

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover - exercised in minimal local envs.
    BeautifulSoup = None


DEFAULT_KEYWORDS = ["핏", "재질", "사이즈", "반팔", "티셔츠"]


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    output_dir = root / "harness_outputs" / "web_keyword_probe"
    image_dir = output_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)

    keywords = _load_keywords()
    sources = _load_probe_items(
        json_env="FITCHECK_HARNESS_SOURCES_JSON",
        path_env="FITCHECK_HARNESS_SOURCES_PATH",
        item_type="source",
    )
    image_probes = _load_probe_items(
        json_env="FITCHECK_HARNESS_IMAGE_PROBES_JSON",
        path_env="FITCHECK_HARNESS_IMAGE_PROBES_PATH",
        item_type="image",
    )
    if not image_probes:
        for image_path in image_dir.iterdir():
            if image_path.is_file():
                image_path.unlink(missing_ok=True)

    page_results = [probe_page(source, keywords) for source in sources]
    image_results = [probe_image(image, image_dir) for image in image_probes]

    result = {
        "keywords": keywords,
        "note": (
            "No default product sources are hard-coded. Provide FITCHECK_HARNESS_SOURCES_JSON "
            "or FITCHECK_HARNESS_SOURCES_PATH for deterministic probe runs."
        ),
        "source_configured": bool(sources),
        "image_probe_configured": bool(image_probes),
        "page_results": page_results,
        "image_results": image_results,
    }

    result_path = output_dir / "keyword_web_probe_result.json"
    _write_json_atomic(result_path, result)

    print(json.dumps(_summary(result), ensure_ascii=False, indent=2))
    print(f"\nSaved: {result_path}")


def probe_page(source: dict[str, str], keywords: list[str]) -> dict[str, Any]:
    try:
        response = requests.get(
            source["url"],
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        response.raise_for_status()
    except Exception as exc:
        return {
            **source,
            "status": "failed",
            "error": str(exc),
            "keyword_hits": {},
            "extracted_specs": {},
        }

    text = normalize_spaces(extract_text(response.text))
    return {
        **source,
        "status": "completed",
        "keyword_hits": {keyword: keyword in text for keyword in keywords},
        "extracted_specs": extract_specs_from_text(text),
        "text_sample": text[:600],
    }


def probe_image(image: dict[str, str], image_dir: Path) -> dict[str, Any]:
    parsed = urlparse(image["url"])
    suffix = Path(parsed.path).suffix or ".jpg"
    target = image_dir / f"{image['image_id']}{suffix}"
    status = "completed"
    error = None
    try:
        response = requests.get(
            image["url"],
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        response.raise_for_status()
        target.write_bytes(response.content)
    except Exception as exc:
        status = "failed"
        error = str(exc)

    return {
        **image,
        "status": status,
        "error": error,
        "local_path": str(target) if status == "completed" else None,
        "ocr_result": image.get("ocr_result") or image.get("fixture_ocr"),
    }


def _load_keywords() -> list[str]:
    raw = os.getenv("FITCHECK_HARNESS_KEYWORDS", "")
    if not raw.strip():
        return DEFAULT_KEYWORDS
    return [item.strip() for item in raw.split(",") if item.strip()]


def _load_probe_items(*, json_env: str, path_env: str, item_type: str) -> list[dict[str, Any]]:
    raw_json = os.getenv(json_env)
    raw_path = os.getenv(path_env)
    if raw_json:
        payload = json.loads(raw_json)
    elif raw_path:
        payload = json.loads(Path(raw_path).read_text(encoding="utf-8"))
    else:
        return []

    if isinstance(payload, dict):
        payload = payload.get("items") or payload.get("sources") or payload.get("image_probes") or []
    if not isinstance(payload, list):
        raise SystemExit(f"{json_env} or {path_env} must resolve to a list of probe items")
    return [_normalize_probe_item(item, index, item_type) for index, item in enumerate(payload)]


def _normalize_probe_item(item: dict[str, Any], index: int, item_type: str) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise SystemExit(f"{item_type} probe item #{index} must be an object")
    url = str(item.get("url") or "").strip()
    if not url:
        raise SystemExit(f"{item_type} probe item #{index} is missing url")
    title = str(item.get("title") or item.get("name") or urlparse(url).netloc or f"{item_type}_{index}").strip()
    normalized = dict(item)
    if item_type == "source":
        normalized["source_id"] = str(item.get("source_id") or _slugify(title) or f"source_{index}")
        normalized["title"] = title
    else:
        normalized["image_id"] = str(item.get("image_id") or _slugify(title) or f"image_{index}")
        normalized["source_id"] = str(item.get("source_id") or _slugify(title) or f"source_{index}")
    normalized["url"] = url
    return normalized


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9가-힣]+", "_", value).strip("_").lower()
    return slug or "item"


def extract_specs_from_text(text: str) -> dict[str, Any]:
    specs: dict[str, Any] = {}
    measurements = {}

    contextual = _extract_contextual_measurements(text)
    if contextual:
        measurements.update(contextual)

    label_map = {
        "어깨": "shoulder",
        "가슴": "chest",
        "가슴단면": "chest",
        "팔길이": "sleeve",
        "소매": "sleeve",
        "소매장": "sleeve",
        "총길이": "length",
        "총장": "length",
    }
    if not measurements:
        label_pattern = "|".join(sorted(label_map, key=len, reverse=True))
        for label, value in re.findall(rf"({label_pattern})\s*(?:단면)?\s*[:=]?\s*(\d+(?:\.\d+)?)\s*cm", text):
            measurements[label_map[label]] = float(value)
    if measurements:
        specs["measurements"] = measurements

    if re.search(r"(면|코튼|cotton)\s*100\s*%?", text, re.IGNORECASE):
        specs["material"] = {"cotton": 100}

    price_match = re.search(
        r"(?:PRICE|판매가|상품가|상품가격)\s*[:|]?\s*(\d{1,3}(?:,\d{3})+)\s*원",
        text,
        re.IGNORECASE,
    )
    if price_match:
        specs["price"] = int(price_match.group(1).replace(",", ""))

    return specs


def _extract_contextual_measurements(text: str) -> dict[str, float]:
    """Prefer product size-table contexts over model body measurements."""
    size_patterns = [
        r"FREE\s*[-:]\s*어깨\s*(\d+(?:\.\d+)?)\s*(?:cm)?\s*/\s*가슴\s*(\d+(?:\.\d+)?)\s*(?:cm)?\s*/\s*팔길이\s*(\d+(?:\.\d+)?)\s*(?:cm)?\s*/\s*총길이\s*(\d+(?:\.\d+)?)\s*(?:cm)?",
        r"SIZE\s*FREE\s*[:：]\s*어깨\s*(\d+(?:\.\d+)?)\s*/\s*가슴\s*(\d+(?:\.\d+)?)\s*/\s*팔길이\s*(\d+(?:\.\d+)?)\s*/\s*총길이\s*(\d+(?:\.\d+)?)",
    ]
    for pattern in size_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            shoulder, chest, sleeve, length = [float(value) for value in match.groups()]
            return {
                "shoulder": shoulder,
                "chest": chest,
                "sleeve": sleeve,
                "length": length,
            }

    kream_matches = re.findall(
        r"(?:M|L|XL|2XL)\s*[:：]\s*총장\s*(\d+(?:\.\d+)?)cm?,\s*어깨\s*(\d+(?:\.\d+)?)cm?,\s*가슴단면\s*(\d+(?:\.\d+)?)cm?,\s*소매장\s*(\d+(?:\.\d+)?)cm?",
        text,
        re.IGNORECASE,
    )
    if kream_matches:
        # Return the largest size row as a conservative comparable oversized fit candidate.
        length, shoulder, chest, sleeve = [float(value) for value in kream_matches[-1]]
        return {
            "length": length,
            "shoulder": shoulder,
            "chest": chest,
            "sleeve": sleeve,
        }

    return {}


def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def extract_text(html: str) -> str:
    if BeautifulSoup is not None:
        soup = BeautifulSoup(html, "html.parser")
        return soup.get_text(" ")

    parser = _TextExtractor()
    parser.feed(html)
    return parser.text()


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._parts.append(data)

    def text(self) -> str:
        return " ".join(self._parts)


def _summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "page_count": len(result["page_results"]),
        "image_count": len(result["image_results"]),
        "pages_with_extracted_specs": [
            item["source_id"]
            for item in result["page_results"]
            if item.get("extracted_specs")
        ],
        "images_with_visual_ocr": [
            item["image_id"]
            for item in result["image_results"]
            if item.get("ocr_result", {}).get("status") == "completed"
        ],
    }


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


if __name__ == "__main__":
    main()
