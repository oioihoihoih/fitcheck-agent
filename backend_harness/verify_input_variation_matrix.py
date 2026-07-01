from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from verify_production_agent_runtime import _assert_equal, _runtime, _tool_response


class FakeInputVariationLlmClient:
    external_api_called = False

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def get_status(self) -> dict[str, Any]:
        return {
            "provider": "fake_responses_api",
            "base_url": "memory://fake",
            "model": "fake-input-variation-model",
            "api_style": "responses",
            "tool_execution": "backend_owned_functions",
            "hosted_tools_supported": False,
            "local_function_tools_supported": True,
            "status": "connected",
            "configured_source": "test",
        }

    def create_response(
        self,
        *,
        instructions: str,
        input_items: str | list[dict[str, Any]],
        tools: list[dict[str, Any]],
        previous_response_id: str | None = None,
    ) -> dict[str, Any]:
        request = {
            "instructions": instructions,
            "input_items": input_items,
            "previous_response_id": previous_response_id,
            "tool_names": [tool.get("name") for tool in tools],
        }
        self.requests.append(request)
        self.external_api_called = True
        text = str(input_items)
        if "와이드 데님" in text or "바지" in text:
            return _tool_response(
                "resp_pants",
                "call_pants",
                "search_web_candidates",
                {"query": "무신사 와이드 데님 팬츠 M 디자인 색", "refresh_web": False},
            )
        if "후드" in text or "기모" in text:
            return _tool_response(
                "resp_hoodie",
                "call_hoodie",
                "search_web_candidates",
                {"query": "유니클로 후드 집업 XL 기모 여유핏", "refresh_web": False},
            )
        if "바라클라바" in text or "넥워머" in text:
            return _tool_response(
                "resp_balaclava",
                "call_balaclava",
                "search_web_candidates",
                {"query": "니트 바라클라바 넥워머 블랙 여유핏 귀 덮는 방한", "refresh_web": False},
            )
        return _tool_response(
            "resp_tshirt",
            "call_tshirt",
            "search_web_candidates",
            {"query": "테스트브랜드 반팔 티셔츠 L 면 소재 100% 핏 내구성", "refresh_web": False},
        )


CASES = [
    {
        "label": "tshirt",
        "text": "이거 테스트브랜드에서 산 반팔 티셔츠인데, 사이즈는 L이고, 면 100% 재질과 핏이 마음에 들고 오래 입어도 괜찮아서 비슷한 옷을 추천해줘.",
        "expected_query": "테스트브랜드 반팔 티셔츠 L 면 소재 100% 핏 내구성",
        "expected_tokens": ["반팔", "티셔츠", "면"],
    },
    {
        "label": "pants",
        "text": "이건 무신사에서 산 와이드 데님 팬츠인데, 사이즈는 M이고 디자인과 색이 마음에 들어서 비슷한 바지를 찾아줘.",
        "expected_query": "무신사 와이드 데님 팬츠 M 디자인 색",
        "expected_tokens": ["와이드", "데님", "팬츠"],
    },
    {
        "label": "hoodie",
        "text": "유니클로에서 산 후드집업 XL이 기모라 따뜻하고 여유핏이 좋아. 비슷한 후드류를 더 찾아줘.",
        "expected_query": "유니클로 후드 집업 XL 기모 여유핏",
        "expected_tokens": ["후드", "기모", "여유핏"],
    },
    {
        "label": "unseen_free_form_target",
        "text": "겨울에 쓰던 검은 니트 바라클라바가 귀까지 덮이고 목도 살짝 감싸줘서 좋아. 비슷한 넥워머 겸용 아이템을 찾아줘.",
        "expected_query": "니트 바라클라바 넥워머 블랙 여유핏 귀 덮는 방한",
        "expected_tokens": ["바라클라바", "넥워머", "방한"],
    },
]


def main() -> None:
    saved_cached_search = os.environ.get("FITCHECK_ALLOW_CACHED_AGENT_SEARCH")
    try:
        os.environ["FITCHECK_ALLOW_CACHED_AGENT_SEARCH"] = "1"
        summaries = [_run_case(case) for case in CASES]
    finally:
        if saved_cached_search is None:
            os.environ.pop("FITCHECK_ALLOW_CACHED_AGENT_SEARCH", None)
        else:
            os.environ["FITCHECK_ALLOW_CACHED_AGENT_SEARCH"] = saved_cached_search

    queries = [item["search_query"] for item in summaries]
    if len(set(queries)) != len(queries):
        raise SystemExit(f"input variation did not produce distinct search queries: {queries!r}")
    print(json.dumps({"status": "passed", "cases": summaries}, ensure_ascii=False, indent=2))


def _run_case(case: dict[str, Any]) -> dict[str, Any]:
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeInputVariationLlmClient()
        runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm, max_turns=1)
        result = runtime.start_run({"goal": case["text"], "free_text": case["text"]})

    _assert_equal(result["status"], "failed", f"{case['label']} bounded run status")
    _assert_equal(result["error"]["code"], "MAX_AGENT_TURNS_EXCEEDED", f"{case['label']} bounded run code")
    _assert_equal(len(fake_llm.requests), 1, f"{case['label']} LLM request count")
    request = fake_llm.requests[0]
    if case["text"] not in str(request["input_items"]):
        raise SystemExit(f"{case['label']} raw input was not passed to LLM input")
    if "Do not treat parse_user_goal as a mandatory first step" not in request["instructions"]:
        raise SystemExit(f"{case['label']} instructions lost optional parser guidance")
    if "call search_web_candidates directly" not in request["instructions"]:
        raise SystemExit(f"{case['label']} instructions lost direct search guidance")
    for expected_instruction in [
        "Preserve unfamiliar target words exactly",
        "Treat size labels such as S/M/L/XL",
        "garment measurement evidence",
        "Distinguish body/model measurements",
    ]:
        if expected_instruction not in request["instructions"]:
            raise SystemExit(
                f"{case['label']} instructions lost domain grounding guidance: {expected_instruction}"
            )
    if "parse_user_goal" not in request["tool_names"]:
        raise SystemExit(f"{case['label']} optional parse_user_goal tool was not exposed")
    if "search_web_candidates" not in request["tool_names"]:
        raise SystemExit(f"{case['label']} search_web_candidates tool was not exposed")
    input_payload = json.loads(str(request["input_items"]))
    agent_memory = input_payload.get("agent_memory") or {}
    if agent_memory.get("source") != "backend_owned_progressive_disclosure_skills":
        raise SystemExit(f"{case['label']} initial context lost backend-owned skill memory source")
    if not agent_memory.get("available_skills"):
        raise SystemExit(f"{case['label']} initial context did not expose the skill catalog")
    if "goal" not in input_payload or "input" not in input_payload:
        raise SystemExit(f"{case['label']} initial context lost goal/input separation")

    observations = result["state"]["observations"]
    observed_tools = [item["tool"] for item in observations]
    if "parse_user_goal" in observed_tools:
        raise SystemExit(f"{case['label']} direct raw-input run unexpectedly called parse_user_goal")
    _assert_equal(observed_tools[0], "search_web_candidates", f"{case['label']} first selected tool")
    search_observation = observations[0]
    search_query = search_observation["arguments"]["query"]
    _assert_equal(search_query, case["expected_query"], f"{case['label']} search query")
    for token in case["expected_tokens"]:
        if token not in search_query:
            raise SystemExit(f"{case['label']} search query missed token {token!r}: {search_query!r}")

    tool_call_names = [
        step["name"]
        for step in result["trace"]
        if step["step_type"] == "tool_call"
    ]
    _assert_equal(tool_call_names[0], "search_web_candidates", f"{case['label']} first tool trace")
    max_turn_guardrails = [
        step
        for step in result["trace"]
        if step["step_type"] == "guardrail" and step["name"] == "stop_max_agent_turns_exceeded"
    ]
    if not max_turn_guardrails:
        raise SystemExit(f"{case['label']} bounded run did not record max-turn guardrail trace")
    return {
        "label": case["label"],
        "raw_input_chars": len(case["text"]),
        "first_tool": observed_tools[0],
        "search_query": search_query,
        "case_status": "bounded_after_first_tool",
        "runtime_status": result["status"],
        "expected_error_code": result["error"]["code"],
    }


if __name__ == "__main__":
    main()
