from __future__ import annotations

from pathlib import Path


MEASUREMENT_LABELS = {
    "총장": "length",
    "어깨너비": "shoulder",
    "어깨": "shoulder",
    "가슴단면": "chest",
    "가슴": "chest",
    "소매길이": "sleeve",
    "소매": "sleeve",
}


def read_utf8_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def parse_closet_text(raw_text: str) -> dict:
    """Parse mobile-form-like text into a normalized closet item draft."""
    result: dict = {"measurements": {}}
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]

    for index, line in enumerate(lines):
        normalized_line = line.replace("：", ":")
        if normalized_line.startswith("제품명"):
            result["name"] = normalized_line.split(":", 1)[1].strip()
        elif normalized_line.startswith("브랜드"):
            result["brand"] = normalized_line.split(":", 1)[1].strip()
        elif normalized_line.startswith("옷이 마음에 드는 이유"):
            raw_reasons = normalized_line.split(":", 1)[1].strip()
            result["preference_reasons"] = [
                reason.strip().rstrip(".")
                for reason in raw_reasons.split(",")
                if reason.strip()
            ]

        labels = line.split()
        if labels and all(label in MEASUREMENT_LABELS for label in labels):
            if index + 1 >= len(lines):
                continue
            values = lines[index + 1].split()
            for label, value in zip(labels, values):
                key = MEASUREMENT_LABELS[label]
                result["measurements"][key] = _to_float(value)

    return result


def infer_closet_traits(parsed: dict) -> dict:
    name = parsed.get("name") or ""
    measurements = parsed.get("measurements", {})
    preference_reasons = parsed.get("preference_reasons", [])

    category = "unknown"
    if "티셔츠" in name or "반팔" in name:
        category = "t_shirt"

    color = None
    if "차콜" in name:
        color = "charcoal"
    elif "블랙" in name or "검정" in name:
        color = "black"

    sleeve_type = None
    if "반팔" in name:
        sleeve_type = "short_sleeve"

    fit_guess = "unknown"
    shoulder = measurements.get("shoulder")
    chest = measurements.get("chest")
    if category == "t_shirt" and shoulder and chest:
        if shoulder >= 55 and chest >= 60:
            fit_guess = "relaxed_or_oversized"
        else:
            fit_guess = "regular"

    return {
        "category": category,
        "color": color,
        "sleeve_type": sleeve_type,
        "fit_guess": fit_guess,
        "preference_signals": _infer_preference_signals(preference_reasons),
    }


def _to_float(value: str) -> float:
    cleaned = value.strip().replace("cm", "").replace(",", ".")
    return float(cleaned)


def _infer_preference_signals(reasons: list[str]) -> list[str]:
    signals: list[str] = []
    joined = " ".join(reasons)
    if "핏" in joined:
        signals.append("fit")
    if "오래" in joined or "내구" in joined or "괜찮" in joined:
        signals.append("durability")
    if "디자인" in joined:
        signals.append("design")
    return signals
