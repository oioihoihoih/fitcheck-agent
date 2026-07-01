# Keyword Web OCR Probe Archive

이전 문서는 특정 쇼핑몰 상품 URL과 Codex visual OCR fixture를 기본값으로 사용한 deterministic probe 기록이었다.

현재 해당 fixed candidate demo는 제거됐다. `run_keyword_web_probe.py`는 기본 상품 source를 갖지 않으며, deterministic boundary를 따로 검증할 때만 `FITCHECK_HARNESS_SOURCES_JSON` 또는 `FITCHECK_HARNESS_SOURCES_PATH`로 명시적인 source list를 주입한다.

