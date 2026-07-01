from __future__ import annotations

import re
from typing import Any


OCR_TARGET_TYPES = {"size_chart", "material_chart", "detail_text", "review_text"}


MEASUREMENT_LABELS = {
    "총장": "length",
    "기장": "length",
    "length": "length",
    "어깨": "shoulder",
    "어깨너비": "shoulder",
    "shoulder": "shoulder",
    "가슴": "chest",
    "가슴단면": "chest",
    "chest": "chest",
    "소매": "sleeve",
    "소매길이": "sleeve",
    "sleeve": "sleeve",
}

MATERIAL_LABELS = {
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
    "린넨": "linen",
    "linen": "linen",
}

MEASUREMENT_LABELS.update(
    {
        "\ucd1d\uc7a5": "length",
        "\uae30\uc7a5": "length",
        "\uc5b4\uae68": "shoulder",
        "\uc5b4\uae68\ub108\ube44": "shoulder",
        "\uac00\uc2b4": "chest",
        "\uac00\uc2b4\ub2e8\uba74": "chest",
        "\ud488": "chest",
        "\uc18c\ub9e4": "sleeve",
        "\uc18c\ub9e4\uae38\uc774": "sleeve",
        "\ub125\ub108\ube44": "neck_width",
        "\ubaa9\ub108\ube44": "neck_width",
        "\ud6c4\ub4dc\ud3ed": "hood_width",
        "\ubc11\ub2e8": "hem_width",
        "\ud5c8\ub9ac": "waist",
        "\ud5c8\ub9ac\ub2e8\uba74": "waist",
        "\ubc11\uc704": "rise",
        "\ud5c8\ubc85\uc9c0": "thigh",
        "\ud5c8\ubc85\uc9c0\ub2e8\uba74": "thigh",
        "\uc5c9\ub369\uc774": "hip",
        "\ud799": "hip",
        "\uc778\uc2ec": "inseam",
        "\uc548\ucabd\uae38\uc774": "inseam",
        "\uc544\uc6c3\uc2ec": "outseam",
        "\uac00\ub85c": "width",
        "\uc138\ub85c": "height",
        "\ub192\uc774": "height",
        "\ud3ed": "depth",
        "\ub108\ube44": "width",
        "\ub450\uaed8": "depth",
        "\uc2a4\ud2b8\ub7a9": "strap_length",
        "\ub048\uae38\uc774": "strap_length",
        "\ub458\ub808": "circumference",
        "\uba38\ub9ac\ub458\ub808": "head_circumference",
        "\ubc1c\uae38\uc774": "foot_length",
        "\ubc1c\ubcfc": "foot_width",
        "\uad7d": "heel_height",
        "\uad7d\ub192\uc774": "heel_height",
        "neck width": "neck_width",
        "hood width": "hood_width",
        "hem": "hem_width",
        "waist": "waist",
        "rise": "rise",
        "thigh": "thigh",
        "hip": "hip",
        "inseam": "inseam",
        "outseam": "outseam",
        "width": "width",
        "height": "height",
        "depth": "depth",
        "strap": "strap_length",
        "circumference": "circumference",
        "head circumference": "head_circumference",
        "foot length": "foot_length",
        "foot width": "foot_width",
        "heel height": "heel_height",
    }
)

MATERIAL_LABELS.update(
    {
        "\uba74": "cotton",
        "\ucf54\ud2bc": "cotton",
        "\ud3f4\ub9ac": "polyester",
        "\ud3f4\ub9ac\uc5d0\uc2a4\ud130": "polyester",
        "\ub098\uc77c\ub860": "nylon",
        "\ub808\uc774\uc628": "rayon",
        "\uc6b8": "wool",
        "\ub9b0\ub128": "linen",
    }
)


def analyze_product_assets(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for index, asset in enumerate(assets, start=1):
        image_type = asset.get("asset_type_hint") or _infer_image_type(asset)
        classification = {
            "image_type": image_type,
            "confidence": 0.95 if asset.get("asset_type_hint") else 0.65,
            "method": "fixture_or_rule_based_harness",
        }

        ocr = _extract_fixture_ocr(asset, image_type)
        results.append(
            {
                "asset_id": f"candidate_asset_{index:03d}",
                "name": asset.get("name") or f"asset_{index}",
                "image_type": image_type,
                "classification": classification,
                "ocr": ocr,
            }
        )
    return results


def normalize_product_spec(fixture: dict[str, Any], asset_results: list[dict[str, Any]]) -> dict[str, Any]:
    text_by_type: dict[str, list[str]] = {}
    confidence_by_type: dict[str, list[float]] = {}
    for asset in asset_results:
        ocr = asset["ocr"]
        if ocr["status"] != "completed" or not ocr.get("text"):
            continue
        text_by_type.setdefault(asset["image_type"], []).append(ocr["text"])
        confidence_by_type.setdefault(asset["image_type"], []).append(ocr.get("confidence") or 0.0)

    size_text = " ".join(text_by_type.get("size_chart", []))
    target_size_label = fixture.get("target_size_label")
    size_measurement_rows = _parse_size_measurement_rows(size_text)
    measurement_selection = _measurement_selection_metadata(
        size_measurement_rows,
        target_size_label=target_size_label,
    )
    measurements = _parse_measurements(size_text, target_size_label=target_size_label)
    material = _parse_material(" ".join(text_by_type.get("material_chart", [])))
    review_keywords = _parse_review_keywords(" ".join(text_by_type.get("review_text", [])))

    explicit = fixture.get("explicit_fields", {})
    measurements.update(explicit.get("measurements", {}))
    material.update(explicit.get("material", {}))

    category = fixture.get("category") or _infer_category(fixture.get("name", ""))
    missing_fields = []
    for field in _required_measurement_fields_for_category_or_name(category, str(fixture.get("name") or "")):
        if field not in measurements:
            missing_fields.append(f"measurements.{field}")
    if not material:
        missing_fields.append("material")
    if fixture.get("price") is None:
        missing_fields.append("price")

    explicit_confidence = explicit.get("field_confidence", {})
    field_confidence = {
        "measurements": _average(confidence_by_type.get("size_chart", [])) if measurements else 0.0,
        "material": _average(confidence_by_type.get("material_chart", [])) if material else 0.0,
        "price": 0.95 if fixture.get("price") is not None else 0.0,
        "category": 0.9 if fixture.get("category") else 0.5,
        "review": _average(confidence_by_type.get("review_text", [])) if review_keywords else 0.0,
    }
    for field, confidence in explicit_confidence.items():
        if field in field_confidence:
            field_confidence[field] = max(field_confidence[field], confidence)
    critical_confidences = [
        field_confidence["measurements"],
        field_confidence["material"],
        field_confidence["price"],
        field_confidence["category"],
    ]

    return {
        "candidate_key": fixture.get("candidate_key"),
        "category": category,
        "brand": fixture.get("brand"),
        "name": fixture.get("name"),
        "price": fixture.get("price"),
        "size_label": fixture.get("target_size_label"),
        "measurements": measurements,
        "size_measurement_rows": size_measurement_rows,
        "measurement_selection": measurement_selection,
        "material": material,
        "fit_keywords": fixture.get("fit_keywords", []),
        "review_keywords": review_keywords,
        "missing_fields": missing_fields,
        "field_confidence": field_confidence,
        "overall_confidence": round(_average(critical_confidences), 2),
    }


def _infer_image_type(asset: dict[str, Any]) -> str:
    name = (asset.get("name") or "").lower()
    if "size" in name or "사이즈" in name:
        return "size_chart"
    if "material" in name or "소재" in name:
        return "material_chart"
    if "review" in name or "리뷰" in name:
        return "review_text"
    if "model" in name or "착장" in name:
        return "outfit_image"
    return "product_image"


def _required_measurement_fields_for_category_or_name(category: str, name: str) -> list[str]:
    category = str(category or "").lower()
    name = str(name or "").lower()
    profile_by_category = {
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
    if re.search(r"(발라클라바|바라클라바|balaclava)", name, re.IGNORECASE):
        return ["length", "neck_width", "hood_width", "hem_width"]
    name_profiles = [
        (r"(발라클라바|바라클라바|balaclava)", ["length", "neck_width", "hood_width", "hem_width"]),
        (r"(청바지|데님|팬츠|바지|슬랙스|jeans?|denim|pants|trousers)", profile_by_category["pants"]),
        (r"(가방|백팩|토트백|크로스백|bag|backpack|tote)", profile_by_category["bag"]),
        (r"(신발|운동화|스니커즈|로퍼|부츠|샌들|구두|슈즈|sneakers?|shoes?|loafer|boots?|sandals?)", profile_by_category["shoes"]),
        (r"(모자|캡|비니|버킷햇|hat|cap|beanie)", profile_by_category["hat"]),
        (r"(스커트|치마|skirt)", profile_by_category["skirt"]),
        (r"(원피스|드레스|dress)", profile_by_category["dress"]),
        (r"(티셔츠|반팔|셔츠|남방|니트|후드|맨투맨|자켓|재킷|코트|shirt|t-?shirt|knit|hoodie|jacket|coat)", profile_by_category["shirt"]),
    ]
    for pattern, fields in name_profiles:
        if re.search(pattern, name, re.IGNORECASE):
            return list(fields)
    if category in profile_by_category:
        return profile_by_category[category]
    return profile_by_category["shirt"]


def _extract_fixture_ocr(asset: dict[str, Any], image_type: str) -> dict[str, Any]:
    if image_type not in OCR_TARGET_TYPES:
        return {
            "status": "skipped",
            "reason": "not_text_heavy_image",
            "provider": "ocr_fixture_stub",
            "external_api_called": False,
            "text": None,
            "confidence": None,
        }
    if "ocr_text" not in asset:
        return {
            "status": "failed",
            "reason": "ocr_text_fixture_missing",
            "provider": "ocr_fixture_stub",
            "external_api_called": False,
            "text": None,
            "confidence": 0.0,
        }
    return {
        "status": "completed",
        "provider": "ocr_fixture_stub",
        "external_api_called": False,
        "text": asset["ocr_text"],
        "confidence": asset.get("ocr_confidence", 0.8),
    }


def _parse_measurements(text: str, *, target_size_label: str | None = None) -> dict[str, float]:
    if not text.strip():
        return {}

    size_rows = _parse_size_measurement_rows(text)
    if size_rows:
        selected = _select_size_measurement_row(size_rows, target_size_label=target_size_label)
        if selected:
            return dict(selected.get("measurements") or {})
        if target_size_label:
            return {}

    measurements: dict[str, float] = {}
    for field, value in _parse_pipe_table_measurements(text).items():
        measurements.setdefault(field, value)
    for field, value in _parse_whitespace_table_measurements(text).items():
        measurements.setdefault(field, value)

    label_pattern = "|".join(re.escape(label) for label in sorted(MEASUREMENT_LABELS, key=len, reverse=True))
    adjacent_pattern = (
        rf"(?<![0-9A-Za-z\uac00-\ud7a3])({label_pattern})"
        rf"\s*[:=]?\s*(\d+(?:\.\d+)?)(?:\s*cm)?"
    )
    for line in text.splitlines() or [text]:
        if _line_looks_like_model_body_stats(line):
            continue
        for match in re.finditer(adjacent_pattern, line, re.IGNORECASE):
            label, value = match.groups()
            numeric_value = float(value)
            key = _measurement_key_from_label(label)
            if key and _measurement_value_is_plausible(key, numeric_value):
                measurements.setdefault(key, numeric_value)
    return measurements


def _parse_size_measurement_rows(text: str) -> list[dict[str, Any]]:
    if not text.strip():
        return []
    rows: list[dict[str, Any]] = []
    rows.extend(_parse_pipe_size_measurement_rows(text))
    rows.extend(_parse_whitespace_size_measurement_rows(text))
    rows.extend(_parse_selected_size_measurement_rows(text))
    return _dedupe_size_measurement_rows(rows)


def _measurement_selection_metadata(
    rows: list[dict[str, Any]],
    *,
    target_size_label: str | None,
) -> dict[str, Any]:
    selected = _select_size_measurement_row(rows, target_size_label=target_size_label)
    status = "no_size_table"
    if rows and target_size_label and selected:
        status = "matched_target_size"
    elif rows and target_size_label and not selected:
        status = "target_size_not_found"
    elif rows and len(rows) == 1 and selected:
        status = "single_size_row"
    elif rows and selected:
        status = "default_first_row_no_target_size"
    return {
        "status": status,
        "requested_size_label": _normalize_size_label(target_size_label),
        "selected_size_label": selected.get("size_label") if selected else None,
        "selected_raw_size_label": selected.get("raw_size_label") if selected else None,
        "available_size_labels": [row.get("size_label") for row in rows],
        "row_count": len(rows),
        "policy": (
            "Size labels are used only to select the observed size-chart row. "
            "Fit claims still require the selected row's garment measurements."
        ),
    }


def _select_size_measurement_row(
    rows: list[dict[str, Any]],
    *,
    target_size_label: str | None = None,
) -> dict[str, Any] | None:
    if not rows:
        return None
    normalized_target = _normalize_size_label(target_size_label)
    if normalized_target:
        for row in rows:
            if normalized_target in _size_label_aliases(str(row.get("raw_size_label") or row.get("size_label") or "")):
                return row
        return None
    return rows[0] if len(rows) == 1 else rows[0]


def _parse_pipe_size_measurement_rows(text: str) -> list[dict[str, Any]]:
    if "|" not in text:
        return []
    tokens = [_clean_table_cell(token) for token in text.split("|")]
    rows: list[dict[str, Any]] = []
    for index, token in enumerate(tokens):
        if not _cell_is_size_header(token):
            continue
        keys: list[str] = []
        cursor = index + 1
        while cursor < len(tokens) and len(keys) < 8:
            cell = tokens[cursor]
            if not cell:
                cursor += 1
                continue
            key = _measurement_key_from_table_cell(cell)
            if not key:
                break
            keys.append(key)
            cursor += 1
        if len(keys) < 2:
            continue
        cursor = _skip_table_separators(tokens, cursor)
        while cursor < len(tokens):
            raw_size = tokens[cursor]
            cursor += 1
            if not raw_size or _is_table_separator(raw_size):
                continue
            if not _cell_looks_like_size_value(raw_size):
                continue
            values: list[float] = []
            while cursor < len(tokens) and len(values) < len(keys):
                cell = tokens[cursor]
                cursor += 1
                if not cell or _is_table_separator(cell):
                    continue
                numeric = _numeric_value_from_cell(cell)
                if numeric is None:
                    break
                values.append(numeric)
            if len(values) < len(keys):
                continue
            measurements = {
                key: value
                for key, value in zip(keys, values)
                if _measurement_value_is_plausible(key, value)
            }
            if len(measurements) >= 2:
                rows.append(_size_measurement_row(raw_size, measurements, source="pipe_table"))
    return rows


def _parse_whitespace_size_measurement_rows(text: str) -> list[dict[str, Any]]:
    lines = [_clean_table_row_preserving_spacing(line) for line in re.split(r"[\r\n]+", text)]
    lines = [line for line in lines if line.strip()]
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        header_cells = [_clean_table_cell(cell) for cell in re.split(r"\s{2,}|\t+", line)]
        if len(header_cells) < 2:
            header_cells = [_clean_table_cell(cell) for cell in re.split(r"\s+", line)]
        if not header_cells:
            continue
        has_size_header = _cell_is_size_header(header_cells[0])
        key_cells = header_cells[1:] if has_size_header else header_cells
        keys = [_measurement_key_from_table_cell(cell) for cell in key_cells]
        keys = [key for key in keys if key]
        if len(keys) < 2 or not _measurement_header_line_is_product_table(line, keys):
            continue
        for row_line in lines[index + 1 : index + 12]:
            if _line_looks_like_model_body_stats(row_line):
                continue
            row_cells = [_clean_table_cell(cell) for cell in re.split(r"\s{2,}|\t+", row_line)]
            if len(row_cells) < 2:
                row_cells = [_clean_table_cell(cell) for cell in re.split(r"\s+", row_line)]
            row_cells = [cell for cell in row_cells if cell]
            if len(row_cells) < len(keys) + 1:
                continue
            raw_size = row_cells[0]
            if not _cell_looks_like_size_value(raw_size):
                continue
            values: list[float] = []
            for cell in row_cells[1:]:
                numeric = _numeric_value_from_cell(cell)
                if numeric is not None:
                    values.append(numeric)
                if len(values) >= len(keys):
                    break
            if len(values) < len(keys):
                continue
            measurements = {
                key: value
                for key, value in zip(keys, values)
                if _measurement_value_is_plausible(key, value)
            }
            if len(measurements) >= 2:
                rows.append(_size_measurement_row(raw_size, measurements, source="whitespace_table"))
        if rows:
            break
    return rows


def _parse_selected_size_measurement_rows(text: str) -> list[dict[str, Any]]:
    selected_size = _selected_size_label_from_text(text)
    if not selected_size:
        return []
    measurements: dict[str, float] = {}
    for field, value in _parse_pipe_table_measurements(text).items():
        measurements.setdefault(field, value)
    for field, value in _parse_whitespace_table_measurements(text).items():
        measurements.setdefault(field, value)
    if len(measurements) < 2:
        return []
    return [_size_measurement_row(selected_size, measurements, source="selected_size_table")]


def _selected_size_label_from_text(text: str) -> str | None:
    patterns = [
        r"(?:내\s*사이즈|선택\s*사이즈|구매\s*사이즈|주문\s*사이즈)\s*[:*]?\s*([A-Za-z0-9]{1,5})",
        r"(?:my\s*size|selected\s*size|purchased\s*size)\s*[:*]?\s*([A-Za-z0-9]{1,5})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _size_measurement_row(raw_size: str, measurements: dict[str, float], *, source: str) -> dict[str, Any]:
    return {
        "size_label": _normalize_size_label(raw_size),
        "raw_size_label": raw_size,
        "measurements": measurements,
        "source": source,
    }


def _dedupe_size_measurement_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[tuple[str, float], ...]]] = set()
    for row in rows:
        measurements = row.get("measurements") if isinstance(row.get("measurements"), dict) else {}
        key = (
            str(row.get("size_label") or ""),
            tuple(sorted((str(field), float(value)) for field, value in measurements.items())),
        )
        if key in seen:
            continue
        seen.add(key)
        output.append(row)
    return output


def _skip_table_separators(tokens: list[str], cursor: int) -> int:
    while cursor < len(tokens):
        cell = tokens[cursor]
        if not cell or _is_table_separator(cell):
            cursor += 1
            continue
        return cursor
    return cursor


def _cell_is_size_header(value: str) -> bool:
    normalized = _normalize_measurement_header_cell(value)
    return normalized in {"size", "sizes", "사이즈", "호수", "옵션", "구분"}


def _cell_looks_like_size_value(value: str) -> bool:
    if not value:
        return False
    normalized = _normalize_size_label(value)
    if not normalized:
        return False
    if re.fullmatch(r"\d+(?:\.\d+)?", normalized):
        return False
    return bool(
        re.search(r"(?:^|[^A-Z0-9])(XS|S|M|L|XL|XXL|XXXL|FREE|OS|ONE|ONE SIZE)(?:[^A-Z0-9]|$)", normalized)
        or re.search(r"\d+\s*호", value)
        or re.fullmatch(r"\d{2,3}", normalized)
    )


def _normalize_size_label(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.upper().replace("프리", "FREE").replace("사이즈", "")
    text = re.sub(r"\s+", "", text)
    text = text.replace("ONE-SIZE", "ONESIZE").replace("ONE_SIZE", "ONESIZE")
    return text or None


def _size_label_aliases(value: str) -> set[str]:
    normalized = _normalize_size_label(value)
    if not normalized:
        return set()
    aliases = {normalized}
    aliases.update(match.group(1) for match in re.finditer(r"(XS|S|M|L|XL|XXL|XXXL|FREE|OS|ONESIZE)", normalized))
    aliases.update(match.group(1) for match in re.finditer(r"(\d+)\s*호", value, re.IGNORECASE))
    aliases.update(match.group(1) for match in re.finditer(r"\b(\d{2,3})\b", normalized))
    if "ONE" in normalized and "SIZE" in normalized:
        aliases.add("ONESIZE")
    return {alias for alias in aliases if alias}


def _is_table_separator(value: str) -> bool:
    return bool(re.fullmatch(r":?-{2,}:?", value.strip()))


def _numeric_value_from_cell(value: str) -> float | None:
    match = re.search(r"(?<!\d)(\d{1,3}(?:\.\d+)?)(?:\s*cm)?(?!\d)", value, re.IGNORECASE)
    if not match:
        return None
    numeric = float(match.group(1))
    if not 1 <= numeric <= 200:
        return None
    return numeric


def _measurement_key_from_label(label: str) -> str | None:
    if label in MEASUREMENT_LABELS:
        return MEASUREMENT_LABELS[label]
    return MEASUREMENT_LABELS.get(label.lower())


def _line_looks_like_model_body_stats(line: str) -> bool:
    lower = line.lower()
    if re.search(r"(model|모델|height|bust|hips|body\s*size|착용\s*모델|모델\s*사이즈)", lower):
        return True
    body_stat_hits = len(re.findall(r"\b(top|bottom|waist|hip|hips|bust)\b", lower))
    return body_stat_hits >= 2


def _parse_pipe_table_measurements(text: str) -> dict[str, float]:
    if "|" not in text:
        return {}

    measurements: dict[str, float] = {}
    tokens = [_clean_table_cell(token) for token in text.split("|")]
    for index, token in enumerate(tokens):
        first_key = _measurement_key_from_table_cell(token)
        if not first_key:
            continue

        keys: list[str] = []
        cursor = index
        while cursor < len(tokens) and len(keys) < 6:
            cell = tokens[cursor]
            if not cell:
                cursor += 1
                continue
            key = _measurement_key_from_table_cell(cell)
            if not key:
                break
            keys.append(key)
            cursor += 1

        if len(keys) < 3:
            continue

        values = _next_plausible_numeric_cells(tokens, start=cursor, count=len(keys))
        if len(values) < len(keys):
            continue

        for key, value in zip(keys, values):
            if _measurement_value_is_plausible(key, value):
                measurements.setdefault(key, value)
        if {"length", "shoulder", "chest", "sleeve"}.issubset(measurements):
            break
    return measurements


def _parse_whitespace_table_measurements(text: str) -> dict[str, float]:
    measurements: dict[str, float] = {}
    lines = [_clean_table_row_preserving_spacing(line) for line in re.split(r"[\r\n]+", text)]
    lines = [line for line in lines if line.strip()]
    for index, line in enumerate(lines):
        header_cells = [_clean_table_cell(cell) for cell in re.split(r"\s{2,}|\t+", line)]
        keys = [_measurement_key_from_table_cell(cell) for cell in header_cells]
        keys = [key for key in keys if key]
        if len(keys) < 2:
            continue
        if not _measurement_header_line_is_product_table(line, keys):
            continue
        for value_line in lines[index + 1 : index + 5]:
            values = _numeric_values_from_table_row(value_line)
            if len(values) < len(keys):
                continue
            for key, value in zip(keys, values):
                if _measurement_value_is_plausible(key, value):
                    measurements.setdefault(key, value)
            break
        if measurements:
            break
    return measurements


def _clean_table_row_preserving_spacing(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"[*_`#\[\]()]|&nbsp;", " ", value)
    return value.strip()


def _measurement_header_line_is_product_table(line: str, keys: list[str]) -> bool:
    lower = line.lower()
    if re.search(r"(model|height|bust|hips|bottom|top|모델|키|몸무게)", lower):
        return False
    return any(key in {"length", "shoulder", "chest", "sleeve"} for key in keys) or len(keys) >= 3


def _numeric_values_from_table_row(line: str) -> list[float]:
    if re.search(r"(model|height|bust|hips|bottom|top|모델|키|몸무게)", line, re.IGNORECASE):
        return []
    values: list[float] = []
    for match in re.finditer(r"(?<!\d)(\d{2,3}(?:\.\d+)?)(?:\s*cm)?(?!\d)", line, re.IGNORECASE):
        value = float(match.group(1))
        if 10 <= value <= 200:
            values.append(value)
    return values


def _measurement_value_is_plausible(field: str, value: float) -> bool:
    ranges = {
        "length": (10, 200),
        "shoulder": (15, 80),
        "chest": (20, 100),
        "sleeve": (5, 100),
        "neck_width": (5, 50),
        "hood_width": (10, 80),
        "waist": (20, 80),
        "rise": (10, 60),
        "thigh": (10, 60),
        "hem_width": (5, 80),
        "hip": (20, 90),
        "inseam": (10, 120),
        "outseam": (20, 140),
        "width": (1, 200),
        "height": (1, 200),
        "depth": (1, 100),
        "strap_length": (5, 150),
        "circumference": (10, 200),
        "head_circumference": (30, 80),
        "foot_length": (15, 35),
        "foot_width": (5, 15),
        "heel_height": (0, 20),
    }
    lower, upper = ranges.get(field, (1, 200))
    return lower <= value <= upper


def _clean_table_cell(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"[*_`#\[\]()]|&nbsp;", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _measurement_key_from_table_cell(cell: str) -> str | None:
    if not cell:
        return None
    lower_cell = cell.lower()
    for label in sorted(MEASUREMENT_LABELS, key=len, reverse=True):
        key = MEASUREMENT_LABELS[label]
        lower_label = label.lower()
        if _table_cell_matches_measurement_label(lower_cell, lower_label):
            return key
    return None


def _table_cell_matches_measurement_label(cell: str, label: str) -> bool:
    normalized_cell = _normalize_measurement_header_cell(cell)
    normalized_label = _normalize_measurement_header_cell(label)
    if not normalized_cell or not normalized_label:
        return False
    if normalized_cell == normalized_label:
        return True
    if normalized_label not in normalized_cell:
        return False
    # Pipe-table headers may include units like "cm", but long descriptive
    # cells such as "short-sleeve T-shirt measurement guide" are not headers.
    return len(normalized_cell) <= len(normalized_label) + 4


def _normalize_measurement_header_cell(value: str) -> str:
    value = value.lower().replace("centimeter", "cm")
    value = re.sub(r"\bcm\b", "", value)
    value = re.sub(r"[^0-9a-z\uac00-\ud7a3]+", "", value)
    return value


def _next_plausible_numeric_cells(tokens: list[str], *, start: int, count: int) -> list[float]:
    values: list[float] = []
    for token in tokens[start : start + 60]:
        if not token:
            continue
        if re.fullmatch(r"-{2,}|:?-{2,}:?", token):
            continue
        match = re.search(r"(?<!\d)(\d{2,3}(?:\.\d+)?)(?:\s*cm)?(?!\d)", token, re.IGNORECASE)
        if not match:
            continue
        value = float(match.group(1))
        if 10 <= value <= 200:
            values.append(value)
        if len(values) >= count:
            break
    return values


def _parse_material(text: str) -> dict[str, int]:
    if not text.strip():
        return {}

    material: dict[str, int] = {}
    label_pattern = "|".join(re.escape(label) for label in sorted(MATERIAL_LABELS, key=len, reverse=True))
    for match in re.finditer(rf"(?<![A-Za-z가-힣])({label_pattern})\s*[:=]?\s*(\d+)\s*%?", text, re.IGNORECASE):
        label, value = match.groups()
        key = MATERIAL_LABELS[label.lower()] if label.lower() in MATERIAL_LABELS else MATERIAL_LABELS[label]
        material[key] = int(value)
    for match in re.finditer(rf"(\d+)\s*%\s*({label_pattern})(?![A-Za-z가-힣])", text, re.IGNORECASE):
        value, label = match.groups()
        key = MATERIAL_LABELS[label.lower()] if label.lower() in MATERIAL_LABELS else MATERIAL_LABELS[label]
        material[key] = int(value)
    return material


def _parse_review_keywords(text: str) -> list[str]:
    keywords = []
    candidates = [
        "정사이즈",
        "작아요",
        "커요",
        "짧아요",
        "길어요",
        "얇아요",
        "비쳐요",
        "구김",
        "탄탄",
        "두꺼움",
    ]
    for keyword in candidates:
        if keyword in text:
            keywords.append(keyword)
    return keywords


def _infer_category(name: str) -> str:
    lowered = name.lower()
    if "티셔츠" in name or "반팔" in name or "t-shirt" in lowered or "tee" in lowered:
        return "t_shirt"
    if (
        "pants" in lowered
        or "denim" in lowered
        or "jeans" in lowered
        or "청바지" in name
        or "데님" in name
        or "팬츠" in name
        or "바지" in name
    ):
        return "pants"
    category_patterns = [
        ("hoodie", ["hoodie", "후드티", "후디"]),
        ("shirt", ["shirt", "셔츠", "남방"]),
        ("knit", ["knit", "sweater", "니트", "스웨터"]),
        ("jacket", ["jacket", "자켓", "재킷", "점퍼", "아우터"]),
        ("coat", ["coat", "코트", "트렌치"]),
        ("skirt", ["skirt", "스커트", "치마"]),
        ("dress", ["dress", "원피스", "드레스"]),
        ("shoes", ["shoes", "sneakers", "loafer", "boots", "신발", "운동화", "스니커즈", "로퍼", "부츠", "샌들"]),
        ("bag", ["bag", "backpack", "tote", "가방", "백팩", "토트백", "크로스백", "숄더백"]),
        ("hat", ["hat", "cap", "beanie", "모자", "캡", "비니", "버킷햇"]),
    ]
    for category, keywords in category_patterns:
        if any(keyword in lowered or keyword in name for keyword in keywords):
            return category
    return "unknown"


def _average(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 2)
