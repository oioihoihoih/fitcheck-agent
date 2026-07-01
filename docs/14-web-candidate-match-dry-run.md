# Web Candidate Match Dry Run Archive

이전 문서는 특정 후보 3개를 cached fixture로 만들어 기준 옷과 비교한 dry-run 기록이었다.

현재 production agent flow에서는 이 fixed candidate dry-run을 사용하지 않는다. `/api/agent/runs`는 LLM tool-calling loop와 backend-owned tools를 사용하며, live search provider가 없으면 cached 후보로 답하지 않고 `LIVE_SEARCH_PROVIDER_REQUIRED` / `configuration_required`로 멈춘다.

