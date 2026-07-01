from __future__ import annotations

import json
import os
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from typing import Any

import app.providers as providers_module
from app.agent_runtime import (
    FITCHECK_AGENT_INSTRUCTIONS,
    FitCheckAgentRuntime,
    _baseline_identity_rejection_from_latest_reply,
    _baseline_identity_confirmation_args_from_latest_reply,
)
from app.agent_run_store import SqliteAgentRunStore, append_trace
from app.schemas import (
    AgentClientTranscriptResponse,
    AgentRunEvidenceResponse,
    AgentRunResponse,
    AgentRunSessionInfoResponse,
    AgentSdkEventsResponse,
    AgentStateEventResponse,
)
from app.report_grounding import validate_report_grounding
from app.tool_registry import (
    ToolContext,
    ToolDefinition,
    ToolRegistry,
    build_fitcheck_tool_registry,
    _confirm_baseline_identity,
    _baseline_identity_confirmation_questions,
    _baseline_name_is_weak,
    _critical_missing_fields_for_spec,
    _goal_mode_from_text,
    _coverage_follow_up_questions,
    _coverage_report_sentence,
    _enrich_closet_item_from_web,
    _filter_listing_page_candidates,
    _load_closet_memory,
    _measurement_profile_from_category_or_name,
    _match_has_close_enough_measurements,
    _normalize_live_product_spec,
    _plan_context_and_sources,
    _parse_liked_item_text,
    _recommendation_label,
    _risk_summary_text,
    _recompute_missing_fields,
    _score_candidate_match,
    _target_category_validation_for_candidate,
    _write_purchase_report,
    _tool_label,
)
from app.tools.match_tools import calculate_match


class FakeToolCallingLlmClient:
    external_api_called = False

    def __init__(self) -> None:
        self.index = 0
        self.responses = [
            _tool_response("resp_1", "call_1", "load_closet_memory", {}),
            _tool_response(
                "resp_2",
                "call_2",
                "search_web_candidates",
                {"query": "segment short sleeve tee", "refresh_web": False},
            ),
            _tool_response(
                "resp_3",
                "call_3a",
                "fetch_product_page",
                {"candidate_key": "web_test_candidate"},
            ),
            _tool_response(
                "resp_4",
                "call_4a",
                "classify_product_assets",
                {"candidate_key": "web_test_candidate"},
            ),
            _tool_response(
                "resp_5",
                "call_5a",
                "extract_size_material_info",
                {"candidate_key": "web_test_candidate"},
            ),
            _tool_response(
                "resp_6",
                "call_6a",
                "normalize_product_spec",
                {"candidate_key": "web_test_candidate"},
            ),
            _tool_response(
                "resp_7",
                "call_7a",
                "score_candidate_match",
                {"candidate_key": "web_test_candidate"},
            ),
            _tool_response(
                "resp_8",
                "call_8a",
                "write_purchase_report",
                {"candidate_key": "web_test_candidate"},
            ),
            {
                "id": "resp_9",
                "output_text": "?뚯뒪???꾨낫???꾩껜 100?먯씤 tool 寃곌낵 湲곗? ?좎궗 ?꾨낫?낅땲??",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "?뚯뒪???꾨낫???꾩껜 100?먯씤 tool 寃곌낵 湲곗? ?좎궗 ?꾨낫?낅땲??",
                            }
                        ],
                    }
                ],
            },
        ]

    def get_status(self) -> dict[str, Any]:
        return {
            "provider": "fake_responses_api",
            "base_url": "memory://fake",
            "model": "fake-tool-calling-model",
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
        if not hasattr(self, "requests"):
            self.requests = []
        self.requests.append(
            {
                "instructions": instructions,
                "input_items": input_items,
                "previous_response_id": previous_response_id,
                "tool_names": [tool.get("name") for tool in tools],
            }
        )
        self.external_api_called = True
        if self.index >= len(self.responses):
            raise AssertionError("fake LLM was called more times than expected")
        response = self.responses[self.index]
        self.index += 1
        return response


class FakeSkillAwareLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        super().__init__()
        self.responses.insert(
            0,
            _tool_response(
                "resp_skill_0",
                "call_skill_0",
                "load_agent_skill",
                {"skill_name": "discover-similar-products"},
            ),
        )


class FakeSingleReportAfterMultiCandidateSearchLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        self.index = 0
        self.responses = [
            _tool_response("resp_1", "call_1", "load_closet_memory", {}),
            _tool_response(
                "resp_2",
                "call_2",
                "search_web_candidates",
                {"query": "relaxed charcoal tee", "refresh_web": False},
            ),
            _tool_response("resp_3", "call_3a", "fetch_product_page", {"candidate_key": "web_test_candidate"}),
            _tool_response("resp_4", "call_4a", "classify_product_assets", {"candidate_key": "web_test_candidate"}),
            _tool_response("resp_5", "call_5a", "extract_size_material_info", {"candidate_key": "web_test_candidate"}),
            _tool_response("resp_6", "call_6a", "normalize_product_spec", {"candidate_key": "web_test_candidate"}),
            _tool_response("resp_7", "call_7b", "fetch_product_page", {"candidate_key": "web_second_candidate"}),
            _tool_response("resp_8", "call_8b", "classify_product_assets", {"candidate_key": "web_second_candidate"}),
            _tool_response("resp_9", "call_9b", "extract_size_material_info", {"candidate_key": "web_second_candidate"}),
            _tool_response("resp_10", "call_10b", "normalize_product_spec", {"candidate_key": "web_second_candidate"}),
            _tool_response("resp_11", "call_11a", "score_candidate_match", {"candidate_key": "web_test_candidate"}),
            _tool_response("resp_12", "call_12a", "write_purchase_report", {"candidate_key": "web_test_candidate"}),
            _message_response("resp_13", "single candidate report done"),
        ]


class FakeFinalWithoutToolsLlmClient:
    external_api_called = False

    def get_status(self) -> dict[str, Any]:
        return {
            "provider": "fake_local_llm",
            "base_url": "memory://fake",
            "model": "fake-no-tools-model",
            "api_style": "chat_completions",
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
        self.external_api_called = True
        return {
            "id": "chat_1",
            "api_style": "chat_completions",
            "output_text": "tool ?놁씠 諛붾줈 ?듬??⑸땲??",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "tool ?놁씠 諛붾줈 ?듬??⑸땲??",
                        }
                    ],
                }
            ],
        }


class FakeSlowToolProgressLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        self.index = 0
        self.responses = [
            _tool_response("resp_slow_1", "call_slow_1", "slow_probe", {}),
            {
                "id": "resp_slow_2",
                "output_text": "slow probe finished",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "slow probe finished"}],
                    }
                ],
            },
        ]


class FakeRawInputDirectSearchLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        self.index = 0
        self.responses = [
            _tool_response(
                "resp_1",
                "call_1",
                "search_web_candidates",
                {"query": "musinsa wide denim pants M design", "refresh_web": False},
            ),
            _tool_response("resp_2", "call_2", "load_closet_memory", {}),
            _tool_response(
                "resp_3",
                "call_3",
                "enrich_closet_item_from_web",
                {"query": "?얜똻???????諭??怨뺣뻷 ????M", "refresh_web": False},
            ),
            _tool_response(
                "resp_4",
                "call_4",
                "fetch_product_page",
                {"candidate_key": "web_test_candidate"},
            ),
            _tool_response(
                "resp_5",
                "call_5",
                "classify_product_assets",
                {"candidate_key": "web_test_candidate"},
            ),
            _tool_response(
                "resp_6",
                "call_6",
                "extract_size_material_info",
                {"candidate_key": "web_test_candidate"},
            ),
            _tool_response(
                "resp_7",
                "call_7",
                "normalize_product_spec",
                {"candidate_key": "web_test_candidate"},
            ),
            _tool_response(
                "resp_8",
                "call_8",
                "score_candidate_match",
                {"candidate_key": "web_test_candidate"},
            ),
            _tool_response(
                "resp_9",
                "call_9",
                "write_purchase_report",
                {"candidate_key": "web_test_candidate"},
            ),
            {
                "id": "resp_10",
                "output_text": "?뚯뒪???꾨낫???꾩껜 100?먯씤 tool 寃곌낵 湲곗? ?좎궗 ?꾨낫?낅땲??",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "?뚯뒪???꾨낫???꾩껜 100?먯씤 tool 寃곌낵 湲곗? ?좎궗 ?꾨낫?낅땲??",
                            }
                        ],
                    }
                ],
            },
        ]


class FakeOptionalParserFreeTextOnlyLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        self.index = 0
        self.responses = [
            _tool_response(
                "resp_parse_1",
                "call_parse_1",
                "parse_user_goal",
                {"free_text": "I bought a testbrand short sleeve t-shirt size L cotton 100 and want similar items."},
            ),
            _tool_response(
                "resp_parse_2",
                "call_search_2",
                "search_web_candidates",
                {"query": "?뚯뒪?몃툕?쒕뱶 諛섑뙏 ?곗뀛痢?L 硫?100%", "refresh_web": False},
            ),
            {
                "id": "resp_parse_3",
                "output_text": "optional parser ?댄썑 final???쒕룄?⑸땲??",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "optional parser ?댄썑 final???쒕룄?⑸땲??",
                            }
                        ],
                    }
                ],
            },
        ]


class FakeInvalidCandidateThenCorrectedLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        self.index = 0
        self.responses = [
            _tool_response(
                "resp_1",
                "call_1",
                "search_web_candidates",
                {"query": "segment short sleeve tee", "refresh_web": False},
            ),
            _tool_response(
                "resp_2",
                "call_bad_fetch",
                "fetch_product_page",
                {"candidate_key": "missing_candidate"},
            ),
            _tool_response(
                "resp_3",
                "call_3",
                "fetch_product_page",
                {"candidate_key": "web_test_candidate"},
            ),
            _tool_response(
                "resp_4",
                "call_4",
                "classify_product_assets",
                {"candidate_key": "web_test_candidate"},
            ),
            _tool_response(
                "resp_5",
                "call_5",
                "extract_size_material_info",
                {"candidate_key": "web_test_candidate"},
            ),
            _tool_response(
                "resp_6",
                "call_6",
                "normalize_product_spec",
                {"candidate_key": "web_test_candidate"},
            ),
            _tool_response(
                "resp_7",
                "call_7",
                "score_candidate_match",
                {"candidate_key": "web_test_candidate"},
            ),
            _tool_response(
                "resp_8",
                "call_8",
                "write_purchase_report",
                {"candidate_key": "web_test_candidate"},
            ),
            {
                "id": "resp_9",
                "output_text": "?뚯뒪???꾨낫???꾩껜 100?먯씤 tool 寃곌낵 湲곗? ?좎궗 ?꾨낫?낅땲??",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "?뚯뒪???꾨낫???꾩껜 100?먯씤 tool 寃곌낵 湲곗? ?좎궗 ?꾨낫?낅땲??",
                            }
                        ],
                    }
                ],
            },
        ]


class FakeWaitingThenReplySearchLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        self.index = 0
        self.responses = [
            _tool_response("resp_1", "call_1", "load_closet_memory", {}),
            _tool_response(
                "resp_wait",
                "call_wait",
                "ask_missing_info",
                {"candidate_key": "all", "missing_fields": ["unresolved_evidence"]},
            ),
            _tool_response("resp_3", "call_3", "inspect_uploaded_images", {"role": "current_clothing_photo"}),
            _tool_response(
                "resp_4",
                "call_4",
                "search_web_candidates",
                {"query": "union bridge hoodie XL cotton", "refresh_web": False},
            ),
            _tool_response("resp_5", "call_5", "load_closet_memory", {}),
            _tool_response(
                "resp_6",
                "call_6",
                "enrich_closet_item_from_web",
                {"query": "?醫딅빍??고닏?깆슦? ?袁⑤굡??XL", "refresh_web": False},
            ),
            _tool_response(
                "resp_7",
                "call_7",
                "fetch_product_page",
                {"candidate_key": "web_test_candidate"},
            ),
            _tool_response(
                "resp_8",
                "call_8",
                "classify_product_assets",
                {"candidate_key": "web_test_candidate"},
            ),
            _tool_response(
                "resp_9",
                "call_9",
                "extract_size_material_info",
                {"candidate_key": "web_test_candidate"},
            ),
            _tool_response(
                "resp_10",
                "call_10",
                "normalize_product_spec",
                {"candidate_key": "web_test_candidate"},
            ),
            _tool_response(
                "resp_11",
                "call_11",
                "score_candidate_match",
                {"candidate_key": "web_test_candidate"},
            ),
            _tool_response(
                "resp_12",
                "call_12",
                "write_purchase_report",
                {"candidate_key": "web_test_candidate"},
            ),
            {
                "id": "resp_13",
                "output_text": "?뚯뒪???꾨낫???꾩껜 100?먯씤 tool 寃곌낵 湲곗? ?좎궗 ?꾨낫?낅땲??",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "?뚯뒪???꾨낫???꾩껜 100?먯씤 tool 寃곌낵 湲곗? ?좎궗 ?꾨낫?낅땲??",
                            }
                        ],
                    }
                ],
            },
        ]


class FakeCompletedThenFinalWithoutReplyToolsLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        super().__init__()
        self.responses.append(
            {
                "id": "resp_after_reply",
                "output_text": "?댁쟾 異붿쿇??洹몃?濡??좎??⑸땲??",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "?댁쟾 異붿쿇??洹몃?濡??좎??⑸땲??",
                            }
                        ],
                    }
                ],
            }
        )


class FakeWaitingThenSparseWebSearchReplyLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        self.index = 0
        self.responses = [
            _tool_response("resp_1", "call_1", "load_closet_memory", {}),
            _tool_response(
                "resp_wait_1",
                "call_wait_1",
                "ask_missing_info",
                {"candidate_key": "all", "missing_fields": ["baseline_evidence"]},
            ),
            _tool_response(
                "resp_wait_2",
                "call_wait_2",
                "ask_missing_info",
                {"candidate_key": "all", "missing_fields": ["baseline_evidence"]},
            ),
            _tool_response(
                "resp_search",
                "call_search",
                "search_web_candidates",
                {"query": "\uc6f9\uc11c\uce58\ub85c \ucc3e\uc544\ubd10", "refresh_web": False},
            ),
            _tool_response(
                "resp_wait_3",
                "call_wait_3",
                "ask_missing_info",
                {"candidate_key": "all", "missing_fields": ["baseline_evidence"]},
            ),
        ]


class FakeWaitingThenDirectSearchWithoutPlanLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        self.index = 0
        self.responses = [
            _tool_response("resp_1", "call_1", "load_closet_memory", {}),
            _tool_response(
                "resp_wait",
                "call_wait",
                "ask_missing_info",
                {"candidate_key": "all", "missing_fields": ["baseline_evidence"]},
            ),
            _tool_response(
                "resp_search_without_plan",
                "call_search_without_plan",
                "search_web_candidates",
                {"query": "\uc6f9\uc11c\uce58\ub85c \ucc3e\uc544\ubd10", "refresh_web": False},
            ),
            _tool_response(
                "resp_wait_after_search",
                "call_wait_after_search",
                "ask_missing_info",
                {"candidate_key": "all", "missing_fields": ["baseline_evidence"]},
            ),
        ]


class FakePlanThenSparseWebSearchReplyLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        self.index = 0
        self.responses = [
            _tool_response("resp_1", "call_1", "load_closet_memory", {}),
            _tool_response(
                "resp_wait_1",
                "call_wait_1",
                "ask_missing_info",
                {"candidate_key": "all", "missing_fields": ["baseline_evidence"]},
            ),
            _tool_response(
                "resp_plan",
                "call_plan",
                "plan_context_and_sources",
                {"free_text": "\uc6f9\uc11c\uce58\ub85c \ub2e4\uc2dc \ucc3e\uc544\ubd10"},
            ),
            _tool_response(
                "resp_search",
                "call_search",
                "search_web_candidates",
                {"query": "\uc6f9\uc11c\uce58\ub85c \ub2e4\uc2dc \ucc3e\uc544\ubd10", "refresh_web": True},
            ),
            _tool_response(
                "resp_wait_2",
                "call_wait_2",
                "ask_missing_info",
                {"candidate_key": "all", "missing_fields": ["baseline_evidence"]},
            ),
        ]


class FakeCachedSearchOnlyLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        self.index = 0
        self.responses = [
            _tool_response(
                "resp_1",
                "call_1",
                "search_web_candidates",
                {"query": "segment short sleeve tee", "refresh_web": False},
            ),
        ]


class FakeBackendTerminalThenReplyLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        self.index = 0
        self.responses = [
            _tool_response(
                "resp_terminal_score_1",
                "call_terminal_score_1",
                "score_candidate_match",
                {"candidate_key": "web_test_candidate"},
            ),
            _tool_response(
                "resp_terminal_score_2",
                "call_terminal_score_2",
                "score_candidate_match",
                {"candidate_key": "web_test_candidate"},
            ),
        ]


class FakeInterruptedToolCallResumeLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        self.index = 0
        self.responses = [
            _tool_response(
                "resp_after_interrupted_tool_calls",
                "call_after_interrupted_tool_calls",
                "ask_missing_info",
                {"candidate_key": "all", "missing_fields": ["baseline_evidence"]},
            )
        ]


class FakeMaxTurnsToolLoopLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        self.index = 0
        self.responses = [
            _tool_response("resp_loop_1", "call_loop_1", "load_closet_memory", {}),
            _tool_response("resp_loop_2", "call_loop_2", "load_closet_memory", {}),
        ]


class FakeToolThenLlmFailureClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        self.index = 0
        self.responses = [
            _tool_response("resp_1", "call_1", "load_closet_memory", {}),
        ]

    def create_response(
        self,
        *,
        instructions: str,
        input_items: str | list[dict[str, Any]],
        tools: list[dict[str, Any]],
        previous_response_id: str | None = None,
    ) -> dict[str, Any]:
        if not hasattr(self, "requests"):
            self.requests = []
        self.requests.append(
            {
                "instructions": instructions,
                "input_items": input_items,
                "previous_response_id": previous_response_id,
                "tool_names": [tool.get("name") for tool in tools],
            }
        )
        self.external_api_called = True
        if self.index == 0:
            self.index += 1
            return self.responses[0]
        self.index += 1
        raise TimeoutError("fake LLM timeout after tool output")


class FakeToolThenRateLimitedLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        self.index = 0
        self.responses = [
            _tool_response("resp_1", "call_1", "load_closet_memory", {}),
        ]

    def create_response(
        self,
        *,
        instructions: str,
        input_items: str | list[dict[str, Any]],
        tools: list[dict[str, Any]],
        previous_response_id: str | None = None,
    ) -> dict[str, Any]:
        if not hasattr(self, "requests"):
            self.requests = []
        self.requests.append(
            {
                "instructions": instructions,
                "input_items": input_items,
                "previous_response_id": previous_response_id,
                "tool_names": [tool.get("name") for tool in tools],
            }
        )
        self.external_api_called = True
        if self.index == 0:
            self.index += 1
            return self.responses[0]
        self.index += 1
        raise RuntimeError("429 Client Error: Too Many Requests for url: https://api.openai.com/v1/responses")


class FakeScoreThenRateLimitedLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        self.index = 0

    def create_response(
        self,
        *,
        instructions: str,
        input_items: str | list[dict[str, Any]],
        tools: list[dict[str, Any]],
        previous_response_id: str | None = None,
    ) -> dict[str, Any]:
        if not hasattr(self, "requests"):
            self.requests = []
        self.requests.append(
            {
                "instructions": instructions,
                "input_items": input_items,
                "previous_response_id": previous_response_id,
                "tool_names": [tool.get("name") for tool in tools],
            }
        )
        self.external_api_called = True
        if self.index == 0:
            self.index += 1
            return _tool_response(
                "resp_score",
                "call_score",
                "score_candidate_match",
                {"candidate_key": "all"},
            )
        self.index += 1
        raise RuntimeError("429 Client Error: Too Many Requests for url: https://api.openai.com/v1/responses")


class FakeToolThenRateLimitedThenRecoverLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        self.index = 0
        self.responses = [
            _tool_response("resp_1", "call_1", "load_closet_memory", {}),
            _tool_response(
                "resp_3",
                "call_3",
                "search_web_candidates",
                {"query": "recovered relaxed charcoal t-shirt", "refresh_web": False},
            ),
            _tool_response(
                "resp_4",
                "call_4",
                "ask_missing_info",
                {"candidate_key": "all", "missing_fields": ["baseline_evidence"]},
            ),
        ]

    def create_response(
        self,
        *,
        instructions: str,
        input_items: str | list[dict[str, Any]],
        tools: list[dict[str, Any]],
        previous_response_id: str | None = None,
    ) -> dict[str, Any]:
        if not hasattr(self, "requests"):
            self.requests = []
        self.requests.append(
            {
                "instructions": instructions,
                "input_items": input_items,
                "previous_response_id": previous_response_id,
                "tool_names": [tool.get("name") for tool in tools],
            }
        )
        self.external_api_called = True
        if self.index == 0:
            self.index += 1
            return self.responses[0]
        if self.index == 1:
            self.index += 1
            raise RuntimeError("429 Client Error: Too Many Requests for url: https://api.openai.com/v1/responses")
        response = self.responses[self.index - 1]
        self.index += 1
        return response


class FakeMultiToolCallLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        self.index = 0
        self.responses = [
            {
                "id": "resp_multi_1",
                "provider_request_id": "req_multi_1",
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "total_tokens": 120,
                    "input_tokens_details": {"cached_tokens": 10},
                },
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "call_multi_closet",
                        "name": "load_closet_memory",
                        "arguments": "{}",
                    },
                    {
                        "type": "function_call",
                        "call_id": "call_multi_search",
                        "name": "search_web_candidates",
                        "arguments": json.dumps(
                            {"query": "segment short sleeve tee", "refresh_web": False},
                            ensure_ascii=False,
                        ),
                    },
                ],
            },
            {
                "id": "resp_multi_2",
                "provider_request_id": "req_multi_2",
                "usage": {
                    "input_tokens": 80,
                    "output_tokens": 30,
                    "total_tokens": 110,
                },
                "output_text": "??tool output??諛쏆? ??final???쒕룄?⑸땲??",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "??tool output??諛쏆? ??final???쒕룄?⑸땲??",
                            }
                        ],
                    }
                ],
            },
        ]


class FakeParallelCandidateBatchLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        self.index = 0
        self.responses = [
            {
                "id": "resp_parallel_1",
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "call_parallel_a",
                        "name": "fetch_product_page",
                        "arguments": json.dumps({"candidate_key": "candidate_a"}, ensure_ascii=False),
                    },
                    {
                        "type": "function_call",
                        "call_id": "call_parallel_b",
                        "name": "fetch_product_page",
                        "arguments": json.dumps({"candidate_key": "candidate_b"}, ensure_ascii=False),
                    },
                ],
            },
            {
                "id": "resp_parallel_2",
                "output_text": "final text without report",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "final text without report"}],
                    }
                ],
            },
        ]


class FakeScoreAllThenFinalLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        self.index = 0
        self.responses = [
            _tool_response("resp_score_all_1", "call_score_all", "score_candidate_match", {"candidate_key": "all"}),
            _message_response("resp_score_all_2", "premature final after score all"),
        ]


class FakeBaselineEnrichThenPrematureReportLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        super().__init__()
        self.responses = [
            _tool_response("resp_baseline_recovery_1", "call_score_before_baseline", "score_candidate_match", {"candidate_key": "all"}),
            _tool_response("resp_baseline_recovery_2", "call_enrich_baseline", "enrich_closet_item_from_web", {"query": "gakkai unions t-shirt"}),
            _tool_response("resp_baseline_recovery_3", "call_report_too_early", "write_purchase_report", {"candidate_key": "all"}),
            _message_response("resp_baseline_recovery_4", "리포트 작성 완료"),
            _tool_response("resp_baseline_recovery_5", "call_report_after_recovery", "write_purchase_report", {"candidate_key": "all"}),
            _message_response("resp_baseline_recovery_6", "완료"),
        ]


class FakeBaselineEvidenceReportRecoveryLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        self.index = 0
        self.responses = [
            _tool_response(
                "resp_baseline_report_recovery_1",
                "call_initial_score",
                "score_candidate_match",
                {"candidate_key": "all"},
            ),
            _tool_response(
                "resp_baseline_report_recovery_2",
                "call_blocked_report",
                "write_purchase_report",
                {"candidate_key": "all"},
            ),
            _tool_response(
                "resp_baseline_report_recovery_3",
                "call_recovered_report",
                "write_purchase_report",
                {"candidate_key": "all"},
            ),
            _message_response("resp_baseline_report_recovery_4", "grounded report checked"),
        ]


class FakeFinalBeforeBackendRequiredBaselineRecoveryLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        self.index = 0
        self.responses = [
            _tool_response(
                "resp_backend_required_baseline_1",
                "call_initial_score",
                "score_candidate_match",
                {"candidate_key": "all"},
            ),
            _message_response("resp_backend_required_baseline_2", "도구 리포트 없이 최종 답변하려는 응답입니다."),
            _tool_response(
                "resp_backend_required_baseline_3",
                "call_recovered_report",
                "write_purchase_report",
                {"candidate_key": "all"},
            ),
            _message_response("resp_backend_required_baseline_4", "복구 리포트 확인"),
        ]


class FakeNestedChatStyleToolCallLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        self.index = 0
        self.responses = [
            {
                "id": "resp_nested_1",
                "output": [
                    {
                        "id": "call_nested_search",
                        "type": "function",
                        "function": {
                            "name": "search_web_candidates",
                            "arguments": json.dumps(
                                {"query": "臾댁떊????대뱶 ?곕떂 ?ъ툩 M", "refresh_web": False},
                                ensure_ascii=False,
                            ),
                        },
                    }
                ],
            },
            {
                "id": "resp_nested_2",
                "output_text": "nested chat-style tool call output??諛쏆? ??final???쒕룄?⑸땲??",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "nested chat-style tool call output??諛쏆? ??final???쒕룄?⑸땲??",
                            }
                        ],
                    }
                ],
            },
        ]


class FakeReasoningContextToolCallLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        self.index = 0
        self.responses = [
            {
                "id": "resp_reasoning_1",
                "output": [
                    {
                        "id": "rs_fitcheck_1",
                        "type": "reasoning",
                        "summary": [
                            {
                                "type": "summary_text",
                                "text": "Need candidate search before final report.",
                            }
                        ],
                        "encrypted_content": "enc_test_reasoning",
                    },
                    {
                        "type": "function_call",
                        "call_id": "call_reasoning_search",
                        "name": "search_web_candidates",
                        "arguments": json.dumps(
                            {"query": "unions short sleeve tee L cotton", "refresh_web": False},
                            ensure_ascii=False,
                        ),
                    },
                ],
            },
            {
                "id": "resp_reasoning_2",
                "output_text": "reasoning context? tool output??諛쏆? ??final???쒕룄?⑸땲??",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "reasoning context? tool output??諛쏆? ??final???쒕룄?⑸땲??",
                            }
                        ],
                    }
                ],
            },
        ]


class FakeRepeatedEmptySearchLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        self.index = 0
        self.responses = [
            _tool_response(
                "resp_1",
                "call_1",
                "search_web_candidates",
                {"query": "寃?됯껐怨쇱뾾???뚯뒪??荑쇰━", "refresh_web": False},
            ),
            _tool_response(
                "resp_2",
                "call_2",
                "search_web_candidates",
                {"query": "寃?됯껐怨쇱뾾???뚯뒪??荑쇰━", "refresh_web": False},
            ),
        ]


class FakeBaselineIdentityContinuedSearchLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        self.index = 0
        self.responses = [
            _tool_response(
                "resp_baseline_block_1",
                "call_baseline_block_1",
                "plan_context_and_sources",
                {},
            ),
            _tool_response(
                "resp_baseline_block_2",
                "call_baseline_block_2",
                "load_closet_memory",
                {"closet_item_id": "current_liked_item"},
            ),
            _tool_response(
                "resp_baseline_block_3",
                "call_baseline_block_3",
                "enrich_closet_item_from_web",
                {
                    "query": "brand Gakkai Unions short sleeve tee L product detail size chart material",
                    "closet_item_id": "current_liked_item",
                    "refresh_web": False,
                },
            ),
            _tool_response(
                "resp_baseline_block_4",
                "call_baseline_block_4",
                "search_web_candidates",
                {"query": "short sleeve tee", "refresh_web": True},
            ),
            _tool_response(
                "resp_baseline_block_5",
                "call_baseline_block_5",
                "write_purchase_report",
                {"candidate_key": "all"},
            ),
            _message_response("resp_baseline_block_6", "검색과 기준 옷 lookup 근거 기반으로 완료했습니다."),
        ]


class FakeEmptySearchThenFinalLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        self.index = 0
        self.responses = [
            _tool_response(
                "resp_empty_final_1",
                "call_empty_final_1",
                "search_web_candidates",
                {"query": "메리제인 상품 상세", "refresh_web": False},
            ),
            {
                "id": "resp_empty_final_2",
                "output_text": "검색 결과가 없어 조건을 더 받아야 합니다.",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "검색 결과가 없어 조건을 더 받아야 합니다.",
                            }
                        ],
                    }
                ],
            },
        ]


class FakeExpandingEmptySearchLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        self.index = 0
        self.responses = [
            _tool_response(
                f"resp_{index}",
                f"call_{index}",
                "search_web_candidates",
                {"query": f"寃?됯껐怨쇱뾾???뺤옣 荑쇰━ {index}", "refresh_web": False},
            )
            for index in range(1, 6)
        ]


class FakeSearchFailureLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        self.index = 0
        self.responses = [
            _tool_response(
                "resp_1",
                "call_1",
                "search_web_candidates",
                {"query": "duckduckgo challenge test", "refresh_web": False},
            ),
        ]


class FakePrematureAskLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        self.index = 0
        self.responses = [
            _tool_response("resp_1", "call_1", "load_closet_memory", {}),
            _tool_response(
                "resp_2",
                "call_2",
                "search_web_candidates",
                {"query": "segment short sleeve tee", "refresh_web": False},
            ),
            _tool_response(
                "resp_3",
                "call_3",
                "ask_missing_info",
                {
                    "candidate_key": "web_test_candidate",
                    "missing_fields": ["measurements.length", "material"],
                },
            ),
            _tool_response(
                "resp_4",
                "call_4",
                "fetch_product_page",
                {"candidate_key": "web_test_candidate"},
            ),
            _tool_response(
                "resp_5",
                "call_5",
                "classify_product_assets",
                {"candidate_key": "web_test_candidate"},
            ),
            _tool_response(
                "resp_6",
                "call_6",
                "extract_size_material_info",
                {"candidate_key": "web_test_candidate"},
            ),
            _tool_response(
                "resp_7",
                "call_7",
                "normalize_product_spec",
                {"candidate_key": "web_test_candidate"},
            ),
            _tool_response(
                "resp_8",
                "call_8",
                "score_candidate_match",
                {"candidate_key": "web_test_candidate"},
            ),
            _tool_response(
                "resp_9",
                "call_9",
                "write_purchase_report",
                {"candidate_key": "web_test_candidate"},
            ),
            {
                "id": "resp_10",
                "output_text": "?뚯뒪???꾨낫???꾩껜 100?먯씤 tool 寃곌낵 湲곗? ?좎궗 ?꾨낫?낅땲??",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "?뚯뒪???꾨낫???꾩껜 100?먯씤 tool 寃곌낵 湲곗? ?좎궗 ?꾨낫?낅땲??",
                            }
                        ],
                    }
                ],
            },
        ]


class FakeAskBeforeSearchLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        self.index = 0
        self.responses = [
            _tool_response(
                "resp_1",
                "call_1",
                "ask_missing_info",
                {
                    "candidate_key": "all",
                    "missing_fields": ["measurements.length", "material"],
                },
            ),
            _tool_response("resp_2", "call_2", "load_closet_memory", {}),
            _tool_response(
                "resp_3",
                "call_3",
                "search_web_candidates",
                {"query": "segment short sleeve tee", "refresh_web": False},
            ),
            _tool_response(
                "resp_4",
                "call_4",
                "fetch_product_page",
                {"candidate_key": "web_test_candidate"},
            ),
            _tool_response(
                "resp_5",
                "call_5",
                "classify_product_assets",
                {"candidate_key": "web_test_candidate"},
            ),
            _tool_response(
                "resp_6",
                "call_6",
                "extract_size_material_info",
                {"candidate_key": "web_test_candidate"},
            ),
            _tool_response(
                "resp_7",
                "call_7",
                "normalize_product_spec",
                {"candidate_key": "web_test_candidate"},
            ),
            _tool_response(
                "resp_8",
                "call_8",
                "score_candidate_match",
                {"candidate_key": "web_test_candidate"},
            ),
            _tool_response(
                "resp_9",
                "call_9",
                "write_purchase_report",
                {"candidate_key": "web_test_candidate"},
            ),
            {
                "id": "resp_10",
                "output_text": "?뚯뒪???꾨낫???꾩껜 100?먯씤 tool 寃곌낵 湲곗? ?좎궗 ?꾨낫?낅땲??",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "?뚯뒪???꾨낫???꾩껜 100?먯씤 tool 寃곌낵 湲곗? ?좎궗 ?꾨낫?낅땲??",
                            }
                        ],
                    }
                ],
            },
        ]


class FakeRepeatedPrematureAskLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        self.index = 0
        ask_args = {
            "candidate_key": "web_test_candidate",
            "missing_fields": ["measurements.length", "material"],
        }
        self.responses = [
            _tool_response("resp_1", "call_1", "load_closet_memory", {}),
            _tool_response(
                "resp_2",
                "call_2",
                "search_web_candidates",
                {"query": "segment short sleeve tee", "refresh_web": False},
            ),
            _tool_response("resp_3", "call_3", "ask_missing_info", ask_args),
            _tool_response("resp_4", "call_4", "ask_missing_info", ask_args),
        ]


class FakePrematureScoreThenReportLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        self.index = 0
        self.responses = [
            _tool_response("resp_1", "call_1", "load_closet_memory", {}),
            _tool_response(
                "resp_2",
                "call_2",
                "search_web_candidates",
                {"query": "segment short sleeve tee", "refresh_web": False},
            ),
            _tool_response(
                "resp_3",
                "call_3",
                "score_candidate_match",
                {"candidate_key": "all"},
            ),
            _tool_response(
                "resp_4",
                "call_4",
                "write_purchase_report",
                {"candidate_key": "all"},
            ),
            {
                "id": "resp_5",
                "output_text": "enrichment ?놁씠 理쒖쥌 異붿쿇???대┰?덈떎.",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "enrichment ?놁씠 理쒖쥌 異붿쿇???대┰?덈떎.",
                            }
                        ],
                    }
                ],
            },
            _tool_response(
                "resp_6",
                "call_6",
                "fetch_product_page",
                {"candidate_key": "web_test_candidate"},
            ),
            _tool_response(
                "resp_7",
                "call_7",
                "classify_product_assets",
                {"candidate_key": "web_test_candidate"},
            ),
            _tool_response(
                "resp_8",
                "call_8",
                "extract_size_material_info",
                {"candidate_key": "web_test_candidate"},
            ),
            _tool_response(
                "resp_9",
                "call_9",
                "normalize_product_spec",
                {"candidate_key": "web_test_candidate"},
            ),
            _tool_response(
                "resp_10",
                "call_10",
                "score_candidate_match",
                {"candidate_key": "all"},
            ),
            _tool_response(
                "resp_11",
                "call_11",
                "write_purchase_report",
                {"candidate_key": "all"},
            ),
            {
                "id": "resp_12",
                "output_text": "tool observation 湲곕컲 理쒖쥌 異붿쿇?낅땲??",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "tool observation 湲곕컲 理쒖쥌 異붿쿇?낅땲??",
                            }
                        ],
                    }
                ],
            },
        ]


class FakeIgnoresGuidanceAfterPrematureAskLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        self.index = 0
        self.responses = [
            _tool_response("resp_1", "call_1", "load_closet_memory", {}),
            _tool_response(
                "resp_2",
                "call_2",
                "search_web_candidates",
                {"query": "segment short sleeve tee", "refresh_web": False},
            ),
            _tool_response(
                "resp_3",
                "call_3",
                "ask_missing_info",
                {
                    "candidate_key": "web_test_candidate",
                    "missing_fields": ["measurements.length", "material"],
                },
            ),
            _message_response("resp_4", "도구 없이 답변하려고 했습니다."),
            _tool_response(
                "resp_5",
                "call_5",
                "score_candidate_match",
                {"candidate_key": "all"},
            ),
            _tool_response(
                "resp_6",
                "call_6",
                "write_purchase_report",
                {"candidate_key": "all"},
            ),
            _message_response("resp_7", "복구된 tool observation 기반 최종 답변입니다."),
        ]


class FakeIgnoresCloseFitGuidanceLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        self.index = 0
        self.responses = [
            _tool_response(
                "resp_close_1",
                "call_close_score_1",
                "score_candidate_match",
                {"candidate_key": "all"},
            ),
            _message_response("resp_close_2", "가까운 후보를 더 찾지 않고 멈추려는 답변입니다."),
            _tool_response(
                "resp_close_3",
                "call_close_score_2",
                "score_candidate_match",
                {"candidate_key": "all"},
            ),
            _tool_response(
                "resp_close_4",
                "call_close_report",
                "write_purchase_report",
                {"candidate_key": "all"},
            ),
            _message_response("resp_close_5", "복구 검색 이후 grounded report를 확인했습니다."),
        ]


class FakeAlwaysStopsDuringCloseFitRecoveryLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        self.index = 0
        self.requests = []

    def create_response(
        self,
        *,
        instructions: str,
        input_items: str | list[dict[str, Any]],
        tools: list[dict[str, Any]],
        previous_response_id: str | None = None,
    ) -> dict[str, Any]:
        self.requests.append(
            {
                "instructions": instructions,
                "input_items": input_items,
                "previous_response_id": previous_response_id,
                "tool_names": [tool.get("name") for tool in tools],
            }
        )
        self.external_api_called = True
        self.index += 1
        if self.index == 1:
            return _tool_response(
                "resp_close_loop_1",
                "call_close_loop_score",
                "score_candidate_match",
                {"candidate_key": "all"},
            )
        return _message_response(
            f"resp_close_loop_{self.index}",
            "모델이 또 close-fit recovery를 무시하고 멈추려는 답변입니다.",
        )


class FakeWeakScoreThenDeferredAllAtMaxTurnsLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        self.index = 0
        self.requests = []
        self.responses = [
            {
                "id": "resp_weak_then_deferred_all",
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "call_weak_score",
                        "name": "score_candidate_match",
                        "arguments": json.dumps({"candidate_key": "weak_candidate"}),
                    },
                    {
                        "type": "function_call",
                        "call_id": "call_all_score",
                        "name": "score_candidate_match",
                        "arguments": json.dumps({"candidate_key": "all"}),
                    },
                ],
            }
        ]


class FakeWeakSingleScoreAtMaxTurnsLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        self.index = 0
        self.requests = []
        self.responses = [
            _tool_response(
                "resp_weak_single_score",
                "call_weak_single_score",
                "score_candidate_match",
                {"candidate_key": "weak_candidate"},
            )
        ]


class FakeMalformedToolArgsLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        self.index = 0
        self.responses = [
            _tool_response(
                "resp_1",
                "call_1",
                "fetch_product_page",
                {},
            ),
            {
                "id": "resp_2",
                "output_text": "?섎せ??tool args ?댄썑 諛붾줈 理쒖쥌 ?듬??⑸땲??",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "?섎せ??tool args ?댄썑 諛붾줈 理쒖쥌 ?듬??⑸땲??",
                            }
                        ],
                    }
                ],
            },
        ]


class FakeMalformedJsonToolArgsLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        self.index = 0
        self.responses = [
            {
                "id": "resp_1",
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "call_1",
                        "name": "fetch_product_page",
                        "arguments": "{\"candidate_key\":",
                    }
                ],
            },
            {
                "id": "resp_2",
                "output_text": "JSON parse ?ㅽ뙣 ?댄썑 諛붾줈 理쒖쥌 ?듬??⑸땲??",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "JSON parse ?ㅽ뙣 ?댄썑 諛붾줈 理쒖쥌 ?듬??⑸땲??",
                            }
                        ],
                    }
                ],
            },
        ]


class FakeUnknownToolLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        self.index = 0
        self.responses = [
            _tool_response(
                "resp_1",
                "call_1",
                "invent_product_specs",
                {"candidate_key": "web_test_candidate"},
            ),
            {
                "id": "resp_2",
                "output_text": "unknown tool ?댄썑 諛붾줈 理쒖쥌 ?듬??⑸땲??",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "unknown tool ?댄썑 諛붾줈 理쒖쥌 ?듬??⑸땲??",
                            }
                        ],
                    }
                ],
            },
        ]


class FakeBlockedUserUrlLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        self.index = 0
        self.responses = [
            _tool_response(
                "resp_1",
                "call_1",
                "register_user_candidate_url",
                {"source_url": "http://127.0.0.1:8000/admin"},
            ),
            _tool_response(
                "resp_2",
                "call_2",
                "register_user_candidate_url",
                {"source_url": "http://127.0.0.1:8000/admin"},
            ),
        ]


class FakeRepeatedSlowToolLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        self.index = 0
        self.responses = [
            _tool_response("resp_slow_1", "call_slow_1", "slow_backend_tool", {}),
            _tool_response("resp_slow_2", "call_slow_2", "slow_backend_tool", {}),
        ]


class FakeRepeatedMalformedToolArgsLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        self.index = 0
        self.responses = [
            _tool_response(
                "resp_1",
                "call_1",
                "fetch_product_page",
                {},
            ),
            _tool_response(
                "resp_2",
                "call_2",
                "fetch_product_page",
                {},
            ),
        ]


class FakeFailedReportThenFinalLlmClient(FakeToolCallingLlmClient):
    def __init__(self) -> None:
        self.index = 0
        self.responses = [
            _tool_response("resp_1", "call_1", "load_closet_memory", {}),
            _tool_response(
                "resp_2",
                "call_2",
                "search_web_candidates",
                {"query": "segment short sleeve tee", "refresh_web": False},
            ),
            _tool_response(
                "resp_3",
                "call_3",
                "write_purchase_report",
                {"candidate_key": "web_test_candidate"},
            ),
            {
                "id": "resp_4",
                "output_text": "洹쇨굅 ?놁씠 理쒖쥌 異붿쿇???대┰?덈떎.",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "洹쇨굅 ?놁씠 理쒖쥌 異붿쿇???대┰?덈떎.",
                            }
                        ],
                    }
                ],
            },
        ]


class FakeDuckDuckGoChallengeRequests:
    @staticmethod
    def get(*args, **kwargs):
        return FakeHtmlResponse(
            "<html><script src='https://duckduckgo.com/anomaly.js'></script><form id='challenge-form'></form></html>"
        )


class FakeNoSearchResultsRequests:
    @staticmethod
    def get(*args, **kwargs):
        return FakeHtmlResponse("<html><body><main>No product results</main></body></html>")


class FakeHtmlResponse:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        return None


def main() -> None:
    _assert_user_visible_plan_and_instructions_are_readable()
    _assert_user_visible_report_helpers_are_korean()
    _assert_empty_start_payload_rejected_before_llm()
    _assert_configuration_required_without_llm()
    _assert_backend_tool_schema_strictness_keywords_are_validated()
    _assert_prepared_run_checkpoints_visible_during_background_execution()
    _assert_cached_agent_search_requires_explicit_harness_opt_in()
    _assert_search_provider_failure_stops_run()
    _assert_empty_search_final_becomes_waiting_for_user()
    _assert_repeated_empty_search_stops_run()
    _assert_unresolved_baseline_search_continues_with_grounded_recovery()
    _assert_expanding_empty_search_attempt_limit_stops_run()
    _assert_max_agent_turns_exceeded_records_guardrail()
    _assert_llm_response_failure_clears_pending_tool_outputs()
    _assert_pending_tool_result_pairing_mismatch_fails_before_llm()
    _assert_llm_rate_limit_failure_is_structured_and_resumable()
    _assert_rate_limit_after_completed_score_recovers_with_backend_report()
    _assert_rate_limit_after_baseline_identity_planning_recovers_with_enrichment()
    _assert_failed_rate_limited_run_can_resume_with_pending_outputs()
    _assert_failed_rate_limited_run_can_continue_without_user_reply()
    _assert_saved_closet_reference_preloads_durable_memory()
    saved_cached_search = os.environ.get("FITCHECK_ALLOW_CACHED_AGENT_SEARCH")
    saved_provider = os.environ.get("FITCHECK_SEARCH_PROVIDER")
    saved_ocr_provider = os.environ.get("FITCHECK_OCR_PROVIDER")
    try:
        os.environ["FITCHECK_ALLOW_CACHED_AGENT_SEARCH"] = "1"
        os.environ["FITCHECK_SEARCH_PROVIDER"] = "cached_harness"
        os.environ["FITCHECK_OCR_PROVIDER"] = "fixture_boundary"
        _assert_progressive_disclosure_skill_loading()
        _assert_tool_calling_loop_with_fake_llm()
        _assert_agent_run_evidence_preserves_sdk_like_pairing()
        _assert_context_compaction_summary_is_recorded_when_budget_threshold_is_crossed()
        _assert_response_context_reset_compacts_pending_tool_outputs()
        _assert_agent_sse_event_ids_are_monotonic_and_resumable()
        _assert_multiple_tool_calls_are_executed_and_returned_together()
        _assert_parallel_candidate_tool_batch_is_recorded_and_ordered()
        _assert_backend_required_report_preserves_all_candidate_score()
        _assert_open_world_bag_measurement_profile()
        _assert_similar_shoes_context_not_overridden_by_pairing_item()
        _assert_listing_page_candidates_are_filtered_before_enrichment()
        _assert_latest_reply_overrides_prior_search_context()
        _assert_partial_baseline_identity_uses_confirmation_flow()
        _assert_baseline_lookup_without_candidate_clears_confirmation_requirement()
        _assert_baseline_identity_confirmation_reply_completes_without_llm_ack()
        _assert_model_cannot_confirm_baseline_identity_without_user_reply()
        _assert_baseline_identity_rejects_mismatched_and_user_denied_candidates()
        _assert_brand_only_baseline_identity_search_does_not_pick_arbitrary_candidate()
        _assert_nested_chat_style_tool_call_arguments_are_preserved()
        _assert_reasoning_context_items_are_forwarded_with_tool_outputs()
        _assert_optional_parser_accepts_free_text_only_tool_call()
        _assert_raw_input_can_drive_direct_search_without_parser()
        _assert_invalid_candidate_key_is_structured_tool_failure()
        _assert_user_reply_resumes_agent_with_reply_context()
        _assert_sparse_web_search_reply_uses_accumulated_context()
        _assert_latest_reply_context_is_backend_preplanned_before_model_tool_choice()
        _assert_sparse_web_search_reply_planner_recipe_suggests_search()
        _assert_runtime_reply_rejects_session_mismatch()
        _assert_empty_runtime_reply_rejected_before_llm()
        _assert_reply_requires_new_report_after_latest_user_input()
        _assert_malformed_json_tool_arguments_are_parse_failure()
        _assert_malformed_tool_arguments_are_observed()
        _assert_unknown_tool_call_is_structured_failure()
        _assert_blocked_user_url_tool_call_is_structured_failure()
        _assert_repeated_failed_tool_call_stops_run()
        _assert_tool_timeout_is_observed_and_stops_on_repeat()
        _assert_missing_info_before_search_is_tool_first_guidance()
        _assert_premature_missing_info_is_deferred()
        _assert_repeated_premature_missing_info_is_blocked()
        _assert_premature_score_report_recovers_with_enrichment_plan()
        _assert_report_after_baseline_enrichment_requires_rescore()
        _assert_baseline_evidence_report_recovery_runs_backend_enrichment()
        _assert_backend_required_report_recovery_runs_baseline_enrichment()
        _assert_repeated_baseline_evidence_recovery_waits_for_user()
        _assert_backend_recovery_executes_guidance_when_model_ignores_tools()
        _assert_close_fit_guidance_runs_backend_search_recovery_when_model_stops()
        _assert_close_fit_max_turn_recovery_rescores_before_report_fallback()
        _assert_close_fit_recovery_continues_across_refreshed_score_guidance()
        _assert_close_fit_recovery_outruns_llm_turn_limit_until_close_match()
        _assert_latest_deferred_score_recovery_preempts_weak_max_turn_report()
        _assert_report_blocker_guidance_recovers_before_weak_max_turn_report()
        _assert_precise_similarity_report_recovers_or_waits_for_baseline_evidence()
        _assert_reply_after_backend_terminal_completion_resets_provider_context()
        _assert_interrupted_tool_call_resume_resets_provider_context()
        _assert_url_size_phrase_does_not_pollute_brand_or_name()
        _assert_unknown_target_category_and_material_terms_are_not_false_blockers()
        _assert_ambiguous_lower_body_measurement_semantics_do_not_hard_fail_fit()
        _assert_related_product_material_does_not_pollute_current_product_spec()
        _assert_urlencoded_content_price_is_extracted_for_current_product()
        _assert_numeric_grounding_ignores_image_label_numbers()
        _assert_material_grounding_ignores_search_arguments()
        _assert_description_only_baseline_does_not_require_measurement_comparison()
        _assert_single_candidate_report_after_multi_candidate_search_is_aggregated()
        _assert_failed_report_tool_blocks_final()
        _assert_deferred_score_question_blocks_premature_final_as_waiting()
        _assert_final_without_tools_is_rejected()
    finally:
        if saved_cached_search is None:
            os.environ.pop("FITCHECK_ALLOW_CACHED_AGENT_SEARCH", None)
        else:
            os.environ["FITCHECK_ALLOW_CACHED_AGENT_SEARCH"] = saved_cached_search
        if saved_provider is None:
            os.environ.pop("FITCHECK_SEARCH_PROVIDER", None)
        else:
            os.environ["FITCHECK_SEARCH_PROVIDER"] = saved_provider
        if saved_ocr_provider is None:
            os.environ.pop("FITCHECK_OCR_PROVIDER", None)
        else:
            os.environ["FITCHECK_OCR_PROVIDER"] = saved_ocr_provider
    print("Production agent runtime verification passed.")


def _assert_progressive_disclosure_skill_loading() -> None:
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeSkillAwareLlmClient()
        runtime = _runtime(Path(temp_dir) / "runs.sqlite3", llm_client=fake_llm)
        result = runtime.start_run(
            {
                "goal": "자주 입는 반팔티와 비슷한 상품을 찾아줘",
                "free_text": "핏과 소재가 비슷한 반팔티를 여러 개 비교해줘",
            }
        )

        _assert_equal(result["status"], "completed", "skill-aware agent status")
        _assert_equal(
            result["state"].get("loaded_agent_skills"),
            ["discover-similar-products"],
            "loaded skill persistence",
        )
        first_request = fake_llm.requests[0]
        initial_payload = json.loads(first_request["input_items"])
        catalog_names = {
            item.get("name")
            for item in initial_payload.get("agent_memory", {}).get("available_skills", [])
        }
        if "discover-similar-products" not in catalog_names:
            raise SystemExit("initial model context did not include the skill catalog")
        if "# Similar Product Discovery" in first_request["input_items"]:
            raise SystemExit("initial model context disclosed full skill instructions before loading")
        if "load_agent_skill" not in first_request["tool_names"]:
            raise SystemExit("load_agent_skill was not exposed as a backend-owned tool")
        if "# Similar Product Discovery" not in fake_llm.requests[1]["instructions"]:
            raise SystemExit("loaded skill instructions were not added to the next model request")
        skill_observations = [
            item
            for item in result["state"]["observations"]
            if item.get("tool") == "load_agent_skill"
        ]
        if not skill_observations:
            raise SystemExit("loaded skill was not persisted as a tool observation")
        skill_traces = [
            step
            for step in result["trace"]
            if step.get("step_type") == "context" and step.get("name") == "match_agent_skills"
        ]
        if not skill_traces:
            raise SystemExit("skill matching was not recorded in trace")


def _assert_open_world_bag_measurement_profile() -> None:
    expected = ["width", "height", "depth"]
    by_category = _measurement_profile_from_category_or_name("bag", "")
    by_name = _measurement_profile_from_category_or_name("unknown", "블랙 나일론 크로스백 가방")
    for label, profile in [("category", by_category), ("name", by_name)]:
        _assert_equal(profile, expected, f"bag measurement profile from {label}")
        forbidden = {"shoulder", "chest", "sleeve", "waist", "rise", "thigh", "hem_width"}
        overlap = forbidden.intersection(profile)
        if overlap:
            raise SystemExit(f"bag measurement profile included garment fields from {label}: {sorted(overlap)}")


def _assert_similar_shoes_context_not_overridden_by_pairing_item() -> None:
    text = (
        "내가 자주 신는 신발은 화이트 레더 스니커즈야. "
        "발볼이 너무 좁지 않고 청바지랑 잘 어울려서 좋아했어. "
        "비슷한 미니멀한 스니커즈를 웹검색으로 찾아 비교해줘."
    )
    _assert_equal(
        _goal_mode_from_text(text),
        "similar_clothing_discovery",
        "similar shoes text should not become outfit pairing just because it says 어울려서",
    )
    parsed = _parse_liked_item_text(text)
    _assert_equal(parsed.get("category"), "shoes", "similar shoes text category")
    profile = _measurement_profile_from_category_or_name(str(parsed.get("category") or ""), text)
    _assert_equal(profile, ["foot_length", "foot_width"], "similar shoes measurement profile")


def _assert_listing_page_candidates_are_filtered_before_enrichment() -> None:
    result = {
        "status": "completed",
        "candidate_count": 2,
        "candidates": [
            {
                "candidate_key": "listing_farfetch_shoes",
                "name": "Off-White shoes listing",
                "source_url": "https://www.farfetch.com/kr/shopping/men/off-white/shoes-2/items.aspx",
            },
            {
                "candidate_key": "product_musinsa_shoes",
                "name": "화이트 스니커즈",
                "source_url": "https://www.musinsa.com/products/1234567",
            },
        ],
    }
    filtered = _filter_listing_page_candidates(result)
    _assert_equal(filtered["candidate_count"], 1, "listing page filter candidate count")
    _assert_equal(filtered["candidates"][0]["candidate_key"], "product_musinsa_shoes", "listing page filter kept product")
    if not filtered.get("listing_page_filter") or filtered["listing_page_filter"].get("rejected_count") != 1:
        raise SystemExit("listing page filter did not record rejection metadata")


def _assert_latest_reply_overrides_prior_search_context() -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        web_result_path = root / "web_result.json"
        keyword_result_path = root / "keyword_result.json"
        web_result_path.write_text(json.dumps({"closet_result": {"closet_item": {}}}), encoding="utf-8")
        keyword_result_path.write_text("{}", encoding="utf-8")
        run = {
            "run_id": "run_reply_context",
            "goal": "평소 신는 화이트 레더 스니커즈와 비슷한 신발 추천",
            "input": {
                "goal": "평소 신는 화이트 레더 스니커즈와 비슷한 신발 추천",
                "free_text": (
                    "내가 자주 신는 신발은 화이트 레더 스니커즈야. "
                    "발볼이 너무 좁지 않고 청바지랑 잘 어울려서 좋아했어. "
                    "비슷한 미니멀한 스니커즈를 웹검색으로 찾아 비교해줘."
                ),
                "user_replies": [
                    {
                        "message": "이번에는 검정색 스니커즈로 다시 찾아봐. 발볼은 너무 좁지 않은 걸로.",
                        "answers": [
                            {
                                "field": "user_reply",
                                "value": "이번에는 검정색 스니커즈로 다시 찾아봐. 발볼은 너무 좁지 않은 걸로.",
                            }
                        ],
                    }
                ],
            },
            "state": {},
            "trace": [],
        }
        context = ToolContext(
            web_result_path=web_result_path,
            keyword_result_path=keyword_result_path,
            refresh_web_candidates=lambda: None,
            run=run,
        )
        result = _plan_context_and_sources({}, context)
        constraints = result.get("search_constraints") or {}
        _assert_equal(constraints.get("category"), "shoes", "latest reply retained shoes category")
        _assert_equal(constraints.get("color_query_label"), "블랙", "latest reply black color override")
        query_hint = str(constraints.get("query_hint") or "")
        if "블랙" not in query_hint or "신발" not in query_hint:
            raise SystemExit(f"latest reply query hint did not use black shoes: {query_hint}")


def _assert_user_visible_plan_and_instructions_are_readable() -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        web_result_path = root / "web_result.json"
        keyword_result_path = root / "keyword_result.json"
        web_result_path.write_text(json.dumps({"closet_result": {"closet_item": {}}}), encoding="utf-8")
        keyword_result_path.write_text("{}", encoding="utf-8")
        run = {
            "run_id": "run_readable_plan",
            "goal": "예전에 산 무탠다드 청바지 L과 비슷한 팬츠를 찾아줘",
            "input": {
                "goal": "예전에 산 무탠다드 청바지 L과 비슷한 팬츠를 찾아줘",
                "free_text": "무탠다드 청바지 L을 자주 입었고 비슷한 핏의 바지를 찾고 싶어.",
                "attachments": [],
            },
            "state": {},
            "trace": [],
        }
        context = ToolContext(
            web_result_path=web_result_path,
            keyword_result_path=keyword_result_path,
            refresh_web_candidates=lambda: None,
            run=run,
        )
        result = _plan_context_and_sources({}, context)
        plan = result.get("execution_plan") or run["state"].get("execution_plan") or {}
        if not plan.get("steps"):
            raise SystemExit("execution plan readability check did not create plan steps")
        readable_payload = json.dumps(
            {
                "instructions": FITCHECK_AGENT_INSTRUCTIONS,
                "execution_plan": plan,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        mojibake_markers = ["�", "Ã", "Â", "횄", "횂", "占", "筌", "疫", "野", "鍮", "留", "湲", "寃"]
        found = [marker for marker in mojibake_markers if marker in readable_payload]
        if found:
            raise SystemExit(f"user-visible agent plan/instructions contain mojibake markers: {found}")
        for expected in ["맥락 정리", "스킬/SOP 선택", "후보 검색", "후보별 상세 분석", "근거 기반 리포트"]:
            if expected not in readable_payload:
                raise SystemExit(f"user-visible execution plan is missing readable label: {expected}")
        if "비슷한 옷 찾아줘" not in FITCHECK_AGENT_INSTRUCTIONS:
            raise SystemExit("agent instructions are missing readable Korean follow-up examples")
        source_text = (Path(__file__).resolve().parent / "app" / "tool_registry.py").read_text(encoding="utf-8")
        if "_build_user_visible_execution_plan_legacy" in source_text:
            raise SystemExit("unused legacy execution plan builder should not remain in tool_registry.py")


class FakeBaselineIdentitySearchProvider:
    def search(self, query: str, refresh: bool = False) -> dict[str, Any]:
        return {
            "status": "completed",
            "provider": "fake_identity_search",
            "mode": "test",
            "external_api_called": False,
            "query": query,
            "candidates": [
                {
                    "candidate_key": "identity_candidate_1",
                    "name": "가까이 유니언즈 반팔티 L 후보",
                    "source_url": "https://search.example/products/gakkai-unions-l-tee",
                    "snippet": "사용자 힌트와 유사한 기준 옷 후보",
                    "provider": "fake_identity_search",
                }
            ],
        }


class FakeNoBaselineIdentitySearchProvider:
    def search(self, query: str, refresh: bool = False) -> dict[str, Any]:
        return {
            "status": "completed",
            "provider": "fake_no_identity_search",
            "mode": "test",
            "external_api_called": False,
            "query": query,
            "candidates": [],
        }


class FakeMixedBaselineIdentitySearchProvider:
    def search(self, query: str, refresh: bool = False) -> dict[str, Any]:
        return {
            "status": "completed",
            "provider": "fake_mixed_identity_search",
            "mode": "test",
            "external_api_called": False,
            "query": query,
            "candidates": [
                {
                    "candidate_key": "wrong_uniqlo_candidate",
                    "name": "남성 AIRism코튼오버사이즈스트라이프T(반팔)",
                    "source_url": "https://www.uniqlo.com/kr/ko/products/example",
                    "snippet": "유니클로 AIRism 반팔 티셔츠",
                    "provider": "fake_mixed_identity_search",
                },
                {
                    "candidate_key": "gakkai_candidate",
                    "name": "가까이 유니언즈 GAKKAI UNIONS 포켓 게이머 반팔 티셔츠 차콜",
                    "source_url": "https://search.example/products/gakkai-unions-charcoal-tee",
                    "snippet": "가까이 유니언즈 반팔티 L 후보",
                    "provider": "fake_mixed_identity_search",
                },
            ],
        }


def _assert_partial_baseline_identity_uses_confirmation_flow() -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        web_result_path = root / "web_result.json"
        keyword_result_path = root / "keyword_result.json"
        run_store = SqliteAgentRunStore(root / "agent_runs.sqlite3")
        web_result_path.write_text(json.dumps({"closet_result": {"closet_item": {}}}), encoding="utf-8")
        keyword_result_path.write_text("{}", encoding="utf-8")
        run = {
            "run_id": "run_partial_baseline_identity",
            "session_id": "partial_baseline_session",
            "goal": "자주 입는 차콜 반팔티랑 비슷한 옷을 찾아줘",
            "input": {
                "session_id": "partial_baseline_session",
                "goal": "자주 입는 차콜 반팔티랑 비슷한 옷을 찾아줘",
                "free_text": "자주 입는 차콜 반팔티랑 비슷한 옷을 찾아줘. 핏은 여유롭고 오래 입어도 괜찮았어.",
                "user_replies": [
                    {
                        "message": "가까이 유니언즈의 사이즈 L 인데, 찾아봐",
                        "answers": [
                            {
                                "field": "user_reply",
                                "value": "가까이 유니언즈의 사이즈 L 인데, 찾아봐",
                            }
                        ],
                    }
                ],
            },
            "state": {},
            "trace": [],
        }
        context = ToolContext(
            web_result_path=web_result_path,
            keyword_result_path=keyword_result_path,
            refresh_web_candidates=lambda: None,
            run=run,
            search_provider=FakeBaselineIdentitySearchProvider(),
            memory_store=run_store,
        )
        plan = _plan_context_and_sources({}, context)
        baseline_identity = plan["grounding_policy"]["baseline_identity"]
        if baseline_identity.get("can_identify_exact_product"):
            raise SystemExit("partial baseline identity should not be treated as exact product")
        if baseline_identity.get("status") not in {"needs_identity_confirmation", "description_only"}:
            raise SystemExit(f"unexpected baseline identity status: {baseline_identity}")
        if "baseline.identity" not in [item.get("field") for item in plan["grounding_policy"]["evidence_required_fields"]]:
            raise SystemExit("grounding policy did not mark baseline identity as evidence-required")

        memory = _load_closet_memory({}, context)
        if not memory.get("baseline_identity"):
            raise SystemExit("load_closet_memory did not expose baseline identity policy")

        enriched = _enrich_closet_item_from_web({"query": "가까이 유니언즈 반팔티 L"}, context)
        if not enriched.get("identity_candidates"):
            raise SystemExit("partial baseline lookup did not preserve identity candidates")
        _assert_equal(
            enriched.get("identity_confirmation_status"),
            "needs_user_confirmation_before_durable_memory",
            "partial baseline identity confirmation status",
        )
        _assert_equal(
            enriched.get("database_write_policy"),
            "do_not_store_search_result_as_confirmed_closet_item_until_user_confirms",
            "partial baseline database write policy",
        )
        closet = run["state"].get("closet_item") or {}
        _assert_equal(
            closet.get("memory_source"),
            "web_lookup_unconfirmed_baseline",
            "partial baseline closet memory source",
        )
        if (closet.get("baseline_identity") or {}).get("database_write_policy") != "store_as_unconfirmed_identity_candidate_until_user_confirms":
            raise SystemExit("partial baseline closet identity was not kept unconfirmed")

        run["input"].setdefault("user_replies", []).append(
            {
                "message": "첫 번째 후보가 내가 말한 가까이 유니언즈 L 반팔티가 맞아.",
                "answers": [
                    {
                        "field": "user_reply",
                        "value": "첫 번째 후보가 내가 말한 가까이 유니언즈 L 반팔티가 맞아.",
                    }
                ],
            }
        )
        confirmation_args = _baseline_identity_confirmation_args_from_latest_reply(run)
        if not confirmation_args:
            raise SystemExit("latest confirmation reply did not resolve to confirm_baseline_identity arguments")
        _assert_equal(
            confirmation_args.get("candidate_key"),
            "identity_candidate_1",
            "confirmation reply candidate key",
        )
        if "source_url" in confirmation_args and not confirmation_args["source_url"]:
            raise SystemExit("confirmation args should omit empty optional source_url")

        confirmed = _confirm_baseline_identity(
            {
                "candidate_key": "identity_candidate_1",
                "confirmation_text": "이 후보가 내가 말한 가까이 유니언즈 L 반팔티가 맞아.",
            },
            context,
        )
        _assert_equal(confirmed["status"], "completed", "baseline identity confirmation status")
        _assert_equal(
            confirmed["baseline_identity"]["status"],
            "memory_identity_available",
            "confirmed baseline identity status",
        )
        _assert_equal(
            confirmed["database_write_policy"],
            "promote_to_confirmed_closet_memory",
            "confirmed baseline database write policy",
        )
        _assert_equal(
            confirmed["durable_memory_status"],
            "stored",
            "confirmed baseline durable memory status",
        )
        _assert_equal(
            run["state"]["closet_item"]["memory_source"],
            "user_confirmed_baseline_identity",
            "confirmed baseline closet memory source",
        )
        stored_items = run_store.list_closet_items("partial_baseline_session")
        if len(stored_items) != 1:
            raise SystemExit(f"confirmed baseline was not persisted once: {stored_items}")
        _assert_equal(
            stored_items[0]["baseline_identity"]["status"],
            "memory_identity_available",
            "persisted confirmed baseline identity status",
        )
        next_run = {
            "run_id": "run_partial_baseline_identity_next",
            "session_id": "partial_baseline_session",
            "goal": "저장한 가까이 유니언즈 반팔티랑 비슷한 옷을 찾아줘",
            "input": {
                "session_id": "partial_baseline_session",
                "goal": "저장한 가까이 유니언즈 반팔티랑 비슷한 옷을 찾아줘",
                "free_text": "저장한 가까이 유니언즈 L 반팔티랑 비슷한 옷을 다시 찾아줘.",
            },
            "state": {},
            "trace": [],
        }
        next_context = ToolContext(
            web_result_path=web_result_path,
            keyword_result_path=keyword_result_path,
            refresh_web_candidates=lambda: None,
            run=next_run,
            search_provider=FakeBaselineIdentitySearchProvider(),
            memory_store=run_store,
        )
        loaded = _load_closet_memory({}, next_context)
        _assert_equal(
            loaded["durable_memory_status"],
            "loaded",
            "durable closet memory load status",
        )
        if not loaded.get("durable_memory_match"):
            raise SystemExit("confirmed baseline was not matched from durable memory in the next run")
        _assert_equal(
            loaded["closet_item"]["memory_source"],
            "durable_closet_memory",
            "next run closet memory source",
        )
        _assert_equal(
            loaded["closet_item"]["baseline_identity"]["status"],
            "memory_identity_available",
            "next run durable baseline identity status",
        )
        vague_run = {
            "run_id": "run_partial_baseline_identity_vague_next",
            "session_id": "partial_baseline_session",
            "goal": "저장한 반팔티랑 비슷한 옷을 찾아줘",
            "input": {
                "session_id": "partial_baseline_session",
                "goal": "저장한 반팔티랑 비슷한 옷을 찾아줘",
                "free_text": "저장한 반팔티랑 비슷한 옷을 다시 찾아줘.",
            },
            "state": {},
            "trace": [],
        }
        vague_context = ToolContext(
            web_result_path=web_result_path,
            keyword_result_path=keyword_result_path,
            refresh_web_candidates=lambda: None,
            run=vague_run,
            search_provider=FakeBaselineIdentitySearchProvider(),
            memory_store=run_store,
        )
        vague_loaded = _load_closet_memory({}, vague_context)
        if not vague_loaded.get("durable_memory_match"):
            raise SystemExit("single confirmed closet memory was not matched from a vague saved-item reference")
        _assert_equal(
            vague_loaded["closet_item"]["memory_source"],
            "durable_closet_memory",
            "vague next run closet memory source",
        )


def _assert_baseline_lookup_without_candidate_clears_confirmation_requirement() -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        web_result_path = root / "web_result.json"
        keyword_result_path = root / "keyword_result.json"
        web_result_path.write_text(json.dumps({"closet_result": {"closet_item": {}}}), encoding="utf-8")
        keyword_result_path.write_text("{}", encoding="utf-8")
        run = {
            "run_id": "run_no_baseline_identity_candidate",
            "session_id": "no_baseline_candidate_session",
            "goal": "자주 입는 차콜 반팔티랑 비슷한 옷을 찾아줘",
            "input": {
                "session_id": "no_baseline_candidate_session",
                "goal": "자주 입는 차콜 반팔티랑 비슷한 옷을 찾아줘",
                "free_text": "자주 입는 차콜 반팔티랑 비슷한 옷을 찾아줘.",
                "user_replies": [
                    {
                        "message": "가까이 유니언즈의 사이즈 L 인데, 웹서치로 찾아봐",
                        "answers": [
                            {
                                "field": "user_reply",
                                "value": "가까이 유니언즈의 사이즈 L 인데, 웹서치로 찾아봐",
                            }
                        ],
                    }
                ],
            },
            "state": {
                "baseline_identity": {
                    "status": "needs_identity_confirmation",
                    "database_write_policy": "store_as_unconfirmed_identity_candidates_until_user_confirms",
                }
            },
            "trace": [],
        }
        context = ToolContext(
            web_result_path=web_result_path,
            keyword_result_path=keyword_result_path,
            refresh_web_candidates=lambda: None,
            run=run,
            search_provider=FakeNoBaselineIdentitySearchProvider(),
        )
        _plan_context_and_sources({}, context)
        result = _enrich_closet_item_from_web(
            {"query": "가까이 유니언즈 반팔 티셔츠 L", "closet_item_id": "current_liked_item"},
            context,
        )
        _assert_equal(result.get("baseline_enrichment_status"), "no_baseline_candidate", "no baseline candidate status")
        baseline_identity = run["state"].get("baseline_identity") or {}
        _assert_equal(
            baseline_identity.get("status"),
            "no_baseline_candidate",
            "no baseline candidate clears confirmation requirement",
        )
        if run["state"].get("unconfirmed_baseline_identity_candidates"):
            raise SystemExit("no baseline candidate lookup should clear unconfirmed identity candidates")
        closet_identity = (run["state"].get("closet_item") or {}).get("baseline_identity") or {}
        _assert_equal(
            closet_identity.get("status"),
            "no_baseline_candidate",
            "closet no baseline candidate status",
        )


def _assert_model_cannot_confirm_baseline_identity_without_user_reply() -> None:
    with TemporaryDirectory() as temp_dir:
        output_dir = Path(temp_dir)
        web_result_path = output_dir / "web.json"
        keyword_result_path = output_dir / "keyword.json"
        web_result_path.write_text(json.dumps({"closet_result": {"closet_item": {}}}), encoding="utf-8")
        keyword_result_path.write_text("{}", encoding="utf-8")
        run = {
            "run_id": "model_confirm_without_user",
            "goal": "가까이 유니언즈에서 산 반팔티와 비슷한 옷 찾아줘",
            "input": {
                "goal": "가까이 유니언즈에서 산 반팔티와 비슷한 옷 찾아줘",
                "free_text": "가까이 유니언즈에서 산 반팔티와 비슷한 옷 찾아줘",
            },
            "state": {
                "unconfirmed_baseline_identity_candidates": [
                    {
                        "candidate_key": "identity_candidate_1",
                        "name": "가까이 유니언즈 반팔티 후보",
                        "source_url": "https://www.musinsa.com/products/example",
                    }
                ]
            },
            "trace": [],
        }
        context = ToolContext(
            web_result_path=web_result_path,
            keyword_result_path=keyword_result_path,
            refresh_web_candidates=lambda: None,
            run=run,
        )
        result = _confirm_baseline_identity(
            {
                "candidate_key": "identity_candidate_1",
                "source_url": "https://www.musinsa.com/products/example",
                "confirmation_text": "모델이 이 후보를 기준 옷으로 확정합니다.",
            },
            context,
        )
        _assert_equal(result["status"], "waiting_for_user", "model baseline confirmation without user reply status")
        _assert_equal(
            result["reason"],
            "baseline_identity_user_confirmation_required",
            "model baseline confirmation without user reply reason",
        )
        if run["state"].get("confirmed_baseline_identity"):
            raise SystemExit("model-only baseline confirmation should not store confirmed identity")


def _assert_baseline_identity_confirmation_reply_completes_without_llm_ack() -> None:
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeToolCallingLlmClient()
        fake_llm.responses = []
        fake_llm.index = 0
        runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm)
        payload = {
            "session_id": "baseline_confirm_session",
            "goal": "가까이 유니언즈 반팔티 후보 확인",
            "free_text": "검색된 기준 옷 후보가 내가 말한 옷인지 확인하고 싶어.",
        }
        llm_status = fake_llm.get_status()
        run = runtime.store.create_run(payload, llm_status)
        run["status"] = "waiting_for_user"
        run["state"]["previous_response_id"] = "resp_waiting_for_baseline_confirmation"
        run["state"]["missing_info"] = [
            {
                "field": "baseline_identity_confirmation",
                "question": "검색된 기준 옷 후보가 실제로 평소 입는 옷인지 확인해 주세요.",
            }
        ]
        run["state"]["closet_item"] = {
            "id": "current_liked_item",
            "name": "가까이 유니언즈 반팔티 후보",
            "size_label": "L",
            "memory_source": "web_lookup_unconfirmed_baseline",
            "baseline_identity": {
                "status": "needs_identity_confirmation",
                "selected_candidate_key": "identity_candidate_1",
                "database_write_policy": "store_as_unconfirmed_identity_candidate_until_user_confirms",
            },
        }
        run["state"]["unconfirmed_baseline_identity_candidates"] = [
            {
                "candidate_key": "identity_candidate_1",
                "name": "가까이 유니언즈 반팔티 L 후보",
                "source_url": "https://search.example/products/gakkai-unions-tee",
                "provider": "fake_identity_search",
            }
        ]
        run = runtime.store.update_run(run)

        result = runtime.reply(
            run["run_id"],
            {
                "session_id": "baseline_confirm_session",
                "message": "맞아. identity_candidate_1 이 내가 말한 기준 옷이야.",
                "answers": [
                    {
                        "field": "baseline_identity_candidate_key",
                        "value": "identity_candidate_1",
                    }
                ],
            },
        )
        _assert_equal(result["status"], "completed", "baseline confirmation reply runtime status")
        if getattr(fake_llm, "requests", []):
            raise SystemExit("baseline confirmation reply should complete from backend tool evidence without another LLM call")
        confirmation_observations = [
            item
            for item in result.get("state", {}).get("observations", [])
            if item.get("tool") == "confirm_baseline_identity"
        ]
        if not confirmation_observations:
            raise SystemExit("baseline confirmation reply did not execute confirm_baseline_identity")
        confirmation = confirmation_observations[-1]
        if not confirmation.get("call_id"):
            raise SystemExit("baseline confirmation observation is missing call_id")
        reset = result.get("state", {}).get("provider_response_context_reset") or {}
        _assert_equal(
            reset.get("reason"),
            "baseline_identity_confirmation_completed_without_model_ack",
            "baseline confirmation provider context reset reason",
        )
        _assert_equal(
            reset.get("terminal_backend_call_id"),
            confirmation.get("call_id"),
            "baseline confirmation terminal backend call id",
        )
        final_report = result.get("final_report") or {}
        _assert_equal(final_report.get("grounding"), "confirm_baseline_identity_tool", "baseline confirmation final grounding")
        if "기준 옷 후보를 확인" not in str(final_report.get("summary") or ""):
            raise SystemExit(f"baseline confirmation final report summary was not user-visible Korean: {final_report}")
        stored_items = runtime.store.list_closet_items("baseline_confirm_session")
        if not stored_items:
            raise SystemExit("baseline confirmation did not persist closet memory")
        _assert_equal(
            (stored_items[0].get("baseline_identity") or {}).get("status"),
            "memory_identity_available",
            "stored baseline identity status after runtime confirmation reply",
        )


def _assert_baseline_identity_rejects_mismatched_and_user_denied_candidates() -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        web_result_path = root / "web_result.json"
        keyword_result_path = root / "keyword_result.json"
        run_store = SqliteAgentRunStore(root / "agent_runs.sqlite3")
        web_result_path.write_text(json.dumps({"closet_result": {"closet_item": {}}}), encoding="utf-8")
        keyword_result_path.write_text("{}", encoding="utf-8")
        run = {
            "run_id": "run_baseline_identity_rejection",
            "session_id": "baseline_rejection_session",
            "goal": "가까이 유니언즈 반팔티랑 비슷한 옷 찾아봐",
            "input": {
                "session_id": "baseline_rejection_session",
                "goal": "가까이 유니언즈 반팔티랑 비슷한 옷 찾아봐",
                "free_text": "가까이 유니언즈에서 산 반팔티랑 비슷한 옷 찾아봐. 재질, 핏, 오래가는 점이 마음에 들어",
            },
            "state": {},
            "trace": [],
        }
        context = ToolContext(
            web_result_path=web_result_path,
            keyword_result_path=keyword_result_path,
            refresh_web_candidates=lambda: None,
            run=run,
            search_provider=FakeMixedBaselineIdentitySearchProvider(),
            memory_store=run_store,
        )
        enriched = _enrich_closet_item_from_web({"query": "비슷한 옷 찾아봐"}, context)
        selected = enriched.get("selected_candidate") or {}
        _assert_equal(
            selected.get("name"),
            "가까이 유니언즈 GAKKAI UNIONS 포켓 게이머 반팔 티셔츠 차콜",
            "baseline identity brand-matched candidate selection",
        )
        rejected = enriched.get("rejected_identity_candidates") or []
        if not any(item.get("candidate_key") == "wrong_uniqlo_candidate" for item in rejected):
            raise SystemExit("brand-mismatched AIRism baseline candidate was not rejected")
        identity_candidates = enriched.get("identity_candidates") or []
        if any(item.get("candidate_key") == "wrong_uniqlo_candidate" for item in identity_candidates):
            raise SystemExit("brand-mismatched AIRism baseline candidate remained confirmable")
        questions = _baseline_identity_confirmation_questions(context)
        if not questions:
            raise SystemExit("baseline identity confirmation question was not generated")
        _assert_equal(
            questions[0].get("source_url"),
            "https://search.example/products/gakkai-unions-charcoal-tee",
            "baseline identity confirmation question source_url",
        )
        if "후보 key:" not in questions[0].get("question", ""):
            raise SystemExit("baseline identity confirmation question does not include candidate key")

        run["state"]["unconfirmed_baseline_identity_candidates"] = [
            {
                "candidate_key": "wrong_uniqlo_candidate",
                "name": "남성 AIRism코튼오버사이즈스트라이프T(반팔)",
                "source_url": "https://www.uniqlo.com/kr/ko/products/example",
                "provider": "fake_mixed_identity_search",
            }
        ]
        run["input"].setdefault("user_replies", []).append(
            {
                "message": "아니야. 가까이 유니언즈에서 샀다니깐",
                "answers": [
                    {
                        "field": "user_reply",
                        "value": "아니야. 가까이 유니언즈에서 샀다니깐",
                    }
                ],
            }
        )
        rejection = _baseline_identity_rejection_from_latest_reply(run)
        if not rejection:
            raise SystemExit("negative baseline identity reply was not detected")
        rejected_candidates = rejection.get("rejected_candidates") or []
        _assert_equal(
            rejected_candidates[0].get("candidate_key"),
            "wrong_uniqlo_candidate",
            "negative reply rejected candidate key",
        )
        if _baseline_identity_confirmation_args_from_latest_reply(run):
            raise SystemExit("negative baseline reply should not be parsed as confirmation")


def _assert_brand_only_baseline_identity_search_does_not_pick_arbitrary_candidate() -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        web_result_path = root / "web_result.json"
        keyword_result_path = root / "keyword_result.json"
        run_store = SqliteAgentRunStore(root / "agent_runs.sqlite3")
        web_result_path.write_text(json.dumps({"closet_result": {"closet_item": {}}}), encoding="utf-8")
        keyword_result_path.write_text("{}", encoding="utf-8")
        run = {
            "run_id": "run_brand_only_baseline_identity",
            "session_id": "brand_only_baseline_session",
            "goal": "내 옷 사진.jpg 이 옷과 비슷한 옷 찾아봐",
            "input": {
                "session_id": "brand_only_baseline_session",
                "goal": "내 옷 사진.jpg 이 옷과 비슷한 옷 찾아봐",
                "free_text": "내 옷 사진.jpg 이 옷과 비슷한 옷 찾아봐. 가까이 유니언즈에서 구매했어. 재질, 핏, 오래가는 점이 마음에 들어",
            },
            "state": {},
            "trace": [],
        }
        context = ToolContext(
            web_result_path=web_result_path,
            keyword_result_path=keyword_result_path,
            refresh_web_candidates=lambda: None,
            run=run,
            search_provider=FakeMixedBaselineIdentitySearchProvider(),
            memory_store=run_store,
        )
        enriched = _enrich_closet_item_from_web({"query": "가까이 유니언즈 product detail size chart material"}, context)
        _assert_equal(
            enriched.get("baseline_enrichment_status"),
            "baseline_identity_constraints_too_weak",
            "brand-only baseline enrichment status",
        )
        if enriched.get("selected_candidate"):
            raise SystemExit("brand-only baseline search should not select an arbitrary candidate")
        if enriched.get("identity_candidates"):
            raise SystemExit("brand-only baseline search should not create confirmable identity candidates")
        weak_candidates = enriched.get("weak_identity_candidates") or []
        if not weak_candidates:
            raise SystemExit("brand-only baseline search should preserve weak candidates as diagnostics")
        questions = enriched.get("next_questions") or []
        if not questions or questions[0].get("question_type") != "baseline_identity_clarification":
            raise SystemExit("brand-only baseline search should ask for an identity clue")
        scored = _score_candidate_match({"candidate_key": "candidate_after_weak_baseline"}, context)
        _assert_equal(
            scored.get("reason"),
            "baseline_identity_clarification_required_before_scoring",
            "brand-only baseline score defer reason",
        )
        run["state"].setdefault("observations", []).append(
            {
                "tool": "score_candidate_match",
                "call_id": "test_score_weak_baseline",
                "arguments": {"candidate_key": "candidate_after_weak_baseline"},
                "result": scored,
            }
        )
        report = _write_purchase_report({"candidate_key": "candidate_after_weak_baseline"}, context)
        _assert_equal(
            report.get("status"),
            "waiting_for_user",
            "brand-only baseline report status",
        )
        _assert_equal(
            report.get("reason"),
            "baseline_identity_clarification_required_before_report",
            "brand-only baseline report defer reason",
        )


def _assert_user_visible_report_helpers_are_korean() -> None:
    samples = [
        _recommendation_label("need_more_info"),
        _recommendation_label("buy"),
        _tool_label("fetch_product_page"),
        _risk_summary_text(
            [
                {"type": "missing_waist"},
                {"type": "candidate_material_missing"},
                {"type": "price_low"},
            ]
        ),
        _coverage_report_sentence(
            {
                "completed_tools": ["search_web_candidates"],
                "observed_fields": ["material"],
                "critical_missing_fields": ["measurements.waist", "price"],
            }
        ),
        _coverage_follow_up_questions(
            "candidate_1",
            {"critical_missing_fields": ["measurements.waist", "material"]},
        )[0]["question"],
    ]
    joined = "\n".join(samples)
    forbidden_fragments = [
        "a candidate",
        "is missing evidence",
        "No additional risk",
        "page fetch",
        "Material evidence",
        "Candidate price",
    ]
    found = [fragment for fragment in forbidden_fragments if fragment in joined]
    if found:
        raise SystemExit(f"user-visible report helper returned English fragments: {found}")
    for expected in ["정보가 더 필요한 후보", "상세페이지", "허리 실측 근거", "실측 사이즈, 소재 근거"]:
        if expected not in joined:
            raise SystemExit(f"user-visible report helper missing Korean fragment: {expected}")


def _write_test_cached_web_result(result_path: Path) -> None:
    result_path.parent.mkdir(parents=True, exist_ok=True)
    product = {
        "id": "product_test_candidate",
        "candidate_key": "web_test_candidate",
        "provider": "test_cached_fixture",
        "raw_input": {
            "provider": "test_cached_fixture",
            "source_url": "https://shop.example/test-candidate",
            "brand": "?뚯뒪?몃툕?쒕뱶",
            "name": "Test candidate tee",
            "price": 35000,
            "description": "珥앹옣 76 ?닿묠 57 媛?대떒硫?62 ?뚮ℓ 25 硫?100% ?뚯뒪???꾨낫",
            "assets": [],
            "retry_count": 0,
            "retry_errors": [],
        },
        "asset_results": [],
        "normalized_spec": {
            "candidate_key": "web_test_candidate",
            "category": "t_shirt",
            "brand": "?뚯뒪?몃툕?쒕뱶",
            "name": "Test candidate tee",
            "source_url": "https://shop.example/test-candidate",
            "price": 35000,
            "size_label": "L",
            "measurements": {
                "length": 76,
                "shoulder": 57,
                "chest": 62,
                "sleeve": 25,
            },
            "material": {"cotton": 100},
            "fit_keywords": ["test_fit"],
            "review_keywords": [],
            "missing_fields": [],
            "field_confidence": {
                "measurements": 0.9,
                "material": 0.9,
                "price": 0.9,
                "category": 0.9,
                "review": 0.0,
            },
            "overall_confidence": 0.72,
        },
    }
    result = {
        "status": "completed",
        "probe_path": "",
        "generated_candidate_paths": [],
        "closet_result": {
            "closet_item": {
                "id": "closet_test_item",
                "category": "t_shirt",
                "brand": "?뚯뒪?몃툕?쒕뱶",
                "name": "Test baseline tee",
                "measurements": {
                    "length": 76,
                    "shoulder": 57,
                    "chest": 62,
                    "sleeve": 25,
                },
                "material": {"cotton": 100},
                "price": 35000,
                "preference_reasons": ["fit is preferred", "material is preferred"],
                "preference_signals": ["fit", "material"],
                "preference_score": 5,
            }
        },
        "product_results": [{"product_analysis": product}],
        "match_results": [],
        "summary_table": [
            {
                "candidate_key": "web_test_candidate",
                "name": "Test candidate tee",
                "recommendation": "buy",
                "overall": 100,
                "size": 100,
                "material": 100,
                "price": 100,
                "confidence": 100,
                "measurement_deltas": {
                    "length": 0,
                    "shoulder": 0,
                    "chest": 0,
                    "sleeve": 0,
                },
                "missing_fields": [],
                "source_url": "https://shop.example/test-candidate",
            }
        ],
    }
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_multi_candidate_cached_web_result(result_path: Path) -> None:
    _write_test_cached_web_result(result_path)
    result = json.loads(result_path.read_text(encoding="utf-8"))
    first = result["product_results"][0]["product_analysis"]
    for index, key in enumerate(["web_second_candidate", "web_third_candidate", "web_fourth_candidate"], start=2):
        product = json.loads(json.dumps(first, ensure_ascii=False))
        product["id"] = f"product_{index}_candidate"
        product["candidate_key"] = key
        product["raw_input"]["source_url"] = f"https://shop.example/{index}-candidate"
        product["raw_input"]["name"] = f"Candidate {index} tee"
        product["raw_input"]["price"] = 35000 + index * 2000
        product["normalized_spec"]["candidate_key"] = key
        product["normalized_spec"]["name"] = f"Candidate {index} tee"
        product["normalized_spec"]["source_url"] = f"https://shop.example/{index}-candidate"
        product["normalized_spec"]["price"] = 35000 + index * 2000
        product["normalized_spec"]["measurements"] = {
            "length": 75 + index,
            "shoulder": 56 + index,
            "chest": 61 + index,
            "sleeve": 25,
        }
        result["product_results"].append({"product_analysis": product})
        result["summary_table"].append(
            {
                "candidate_key": key,
                "name": f"Candidate {index} tee",
                "recommendation": "caution",
                "overall": 90 - index,
                "size": 92 - index,
                "material": 100,
                "price": 70,
                "confidence": 100,
                "measurement_deltas": {
                    "length": index - 1,
                    "shoulder": index - 1,
                    "chest": index - 1,
                    "sleeve": 0,
                },
                "missing_fields": [],
                "source_url": f"https://shop.example/{index}-candidate",
            }
        )
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def _assert_empty_start_payload_rejected_before_llm() -> None:
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeToolCallingLlmClient()
        runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm)
        result = runtime.start_run({})
        _assert_equal(result["status"], "invalid_request", "empty start payload status")
        _assert_equal(result["run_id"], None, "empty start payload run id")
        _assert_equal(
            result["error"]["code"],
            "INITIAL_USER_INPUT_REQUIRED",
            "empty start payload error code",
        )
        if getattr(fake_llm, "requests", []):
            raise SystemExit("empty start payload should not call LLM")
        if runtime.store.list_runs():
            raise SystemExit("empty start payload should not create run")


def _assert_configuration_required_without_llm() -> None:
    saved_env = {
        key: os.environ.get(key)
        for key in [
            "FITCHECK_LLM_BASE_URL",
            "FITCHECK_LLM_API_KEY",
            "FITCHECK_LLM_MODEL",
            "FITCHECK_LLM_API",
            "FITCHECK_LLM_TIMEOUT",
            "FITCHECK_LLM_MAX_RETRIES",
            "FITCHECK_LLM_TEMPERATURE",
            "OPENAI_API_KEY",
            "OPENAI_MODEL",
            "OPENAI_BASE_URL",
            "OLLAMA_HOST",
        ]
    }
    try:
        for key in saved_env:
            os.environ.pop(key, None)
        with TemporaryDirectory() as temp_dir:
            runtime = _runtime(Path(temp_dir) / "runs.json")
            result = runtime.start_run({"goal": "?????щ룄 ?섎뒗吏 ?먮떒"})
        _assert_equal(result["status"], "configuration_required", "missing LLM gate")
        _assert_equal(result["run_id"], None, "missing LLM run id")
        if not result["llm"].get("missing"):
            raise SystemExit("missing LLM gate failed: missing list was empty")
    finally:
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _assert_backend_tool_schema_strictness_keywords_are_validated() -> None:
    tool = ToolDefinition(
        name="strict_probe",
        description="Strict schema validation probe.",
        parameters={
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["fast", "deep"]},
                "query": {"type": "string", "minLength": 3, "maxLength": 8},
                "limit": {"type": "integer", "minimum": 1, "maximum": 3},
                "tags": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 2,
                    "items": {"type": "string", "minLength": 2},
                },
            },
            "required": ["mode", "query", "limit", "tags"],
            "additionalProperties": False,
        },
        handler=lambda args, context: {"status": "completed"},
    )
    result = tool.execute(
        {
            "mode": "wide",
            "query": "ab",
            "limit": 4,
            "tags": ["x", "ok", "extra"],
            "unexpected": True,
        },
        ToolContext(
            web_result_path=Path("unused_web.json"),
            keyword_result_path=Path("unused_keyword.json"),
            refresh_web_candidates=lambda: None,
            run={},
        ),
    )
    _assert_equal(result["status"], "failed", "strict schema validation status")
    _assert_equal(
        result["schema_strictness"]["backend_validation"],
        "strict_json_schema_subset",
        "strict schema backend contract",
    )
    codes = {item["code"] for item in result["validation_errors"]}
    for expected_code in {
        "enum_value_not_allowed",
        "string_too_short",
        "number_too_large",
        "array_too_long",
        "additional_property_not_allowed",
    }:
        if expected_code not in codes:
            raise SystemExit(f"strict schema validation did not report {expected_code}")
    item_paths = {item["path"] for item in result["validation_errors"]}
    if "$.tags[0]" not in item_paths:
        raise SystemExit("strict schema validation did not validate array item schema")
    strict_tool = ToolDefinition(
        name="provider_strict_eligible",
        description="Provider strict eligibility probe.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": ["integer", "null"]},
            },
            "required": ["query", "limit"],
            "additionalProperties": False,
        },
        handler=lambda args, context: {"status": "completed"},
    )
    optional_tool = ToolDefinition(
        name="provider_strict_ineligible",
        description="Provider strict ineligibility probe.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        handler=lambda args, context: {"status": "completed"},
    )
    saved_strict_env = {
        "FITCHECK_LLM_PROVIDER_STRICT_TOOLS": os.environ.get(
            "FITCHECK_LLM_PROVIDER_STRICT_TOOLS"
        ),
        "FITCHECK_LLM_BASE_URL": os.environ.get("FITCHECK_LLM_BASE_URL"),
        "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY"),
    }
    try:
        os.environ.pop("FITCHECK_LLM_PROVIDER_STRICT_TOOLS", None)
        if strict_tool.to_responses_tool()["strict"] is not False:
            raise SystemExit("provider strict default must remain disabled")
        os.environ["FITCHECK_LLM_PROVIDER_STRICT_TOOLS"] = "1"
        os.environ["FITCHECK_LLM_BASE_URL"] = "http://local-llm.test/v1"
        os.environ["OPENAI_API_KEY"] = "test-key"
        if strict_tool.to_responses_tool()["strict"] is not False:
            raise SystemExit("provider strict must not be enabled for local endpoints")
        os.environ["FITCHECK_LLM_BASE_URL"] = "https://api.openai.com/v1"
        if strict_tool.to_responses_tool()["strict"] is not True:
            raise SystemExit("provider strict-compatible schema did not export strict=true")
        if optional_tool.to_responses_tool()["strict"] is not False:
            raise SystemExit("provider strict-incompatible schema exported strict=true")
    finally:
        for key, value in saved_strict_env.items():
            _restore_env(key, value)
    registry = build_fitcheck_tool_registry()
    context = ToolContext(
        web_result_path=Path("unused_web.json"),
        keyword_result_path=Path("unused_keyword.json"),
        refresh_web_candidates=lambda: None,
        run={},
    )
    search_result = registry.get("search_web_candidates").execute(
        {"query": "x"},
        context,
    )
    _assert_equal(search_result["status"], "failed", "short search query schema status")
    _assert_equal(
        search_result["validation_errors"][0]["code"],
        "string_too_short",
        "short search query schema error",
    )
    fetch_result = registry.get("fetch_product_page").execute(
        {"candidate_key": ""},
        context,
    )
    _assert_equal(fetch_result["status"], "failed", "empty candidate key schema status")
    ask_result = registry.get("ask_missing_info").execute(
        {
            "candidate_key": "web_test_candidate",
            "missing_fields": [f"field_{index}" for index in range(13)],
        },
        context,
    )
    _assert_equal(ask_result["status"], "failed", "missing fields max items schema status")
    if not any(item["code"] == "array_too_long" for item in ask_result["validation_errors"]):
        raise SystemExit("missing fields schema did not enforce maxItems")
    register_result = registry.get("register_user_candidate_url").execute(
        {"source_url": "x"},
        context,
    )
    _assert_equal(register_result["status"], "failed", "short source url schema status")


def _assert_prepared_run_checkpoints_visible_during_background_execution() -> None:
    tool_started = Event()
    release_tool = Event()

    def slow_probe(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        tool_started.set()
        if not release_tool.wait(timeout=5):
            return {"status": "failed", "error": {"code": "TEST_RELEASE_TIMEOUT"}}
        return {"status": "completed", "probe": "released"}

    registry = ToolRegistry(
        [
            ToolDefinition(
                name="slow_probe",
                description="Slow checkpoint probe",
                parameters={
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
                handler=slow_probe,
                timeout_seconds=10,
            )
        ]
    )
    with TemporaryDirectory() as temp_dir:
        runtime = _runtime(
            Path(temp_dir) / "runs.json",
            llm_client=FakeSlowToolProgressLlmClient(),
            tool_registry=registry,
        )
        prepared = runtime.prepare_run(
            {
                "goal": "Verify background checkpoints",
                "free_text": "Run the slow probe tool",
                "session_id": "background_checkpoint_session",
            }
        )
        _assert_equal(prepared["status"], "running", "prepared background run status")
        stored_prepared = runtime.get_run(prepared["run_id"])
        _assert_equal(len(stored_prepared.get("trace") or []), 1, "prepared background trace count")

        worker = Thread(target=runtime.continue_run, args=(prepared["run_id"],), daemon=True)
        worker.start()
        if not tool_started.wait(timeout=5):
            raise SystemExit("background slow tool did not start")
        in_progress = runtime.get_run(prepared["run_id"])
        in_progress_trace = in_progress.get("trace") or []
        if not any(
            step.get("step_type") == "tool_call"
            and step.get("name") == "slow_probe"
            and step.get("status") == "started"
            for step in in_progress_trace
        ):
            raise SystemExit("background checkpoint did not persist started tool call")
        if any(
            step.get("step_type") == "tool_output" and step.get("name") == "slow_probe"
            for step in in_progress_trace
        ):
            raise SystemExit("background checkpoint exposed tool output before tool completion")

        release_tool.set()
        worker.join(timeout=10)
        if worker.is_alive():
            raise SystemExit("background checkpoint worker did not finish")
        finished = runtime.get_run(prepared["run_id"])
        if not any(
            step.get("step_type") == "tool_output"
            and step.get("name") == "slow_probe"
            and step.get("status") == "completed"
            for step in finished.get("trace") or []
        ):
            raise SystemExit("background checkpoint did not persist completed tool output")


def _assert_cached_agent_search_requires_explicit_harness_opt_in() -> None:
    saved = os.environ.get("FITCHECK_ALLOW_CACHED_AGENT_SEARCH")
    saved_provider = os.environ.get("FITCHECK_SEARCH_PROVIDER")
    try:
        os.environ.pop("FITCHECK_ALLOW_CACHED_AGENT_SEARCH", None)
        os.environ["FITCHECK_SEARCH_PROVIDER"] = "cached_harness"
        with TemporaryDirectory() as temp_dir:
            fake_llm = FakeCachedSearchOnlyLlmClient()
            runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm)
            result = runtime.start_run(
                {
                    "goal": "Find similar short sleeve tee candidates",
                    "free_text": "Find a similar relaxed short sleeve tee size L",
                }
            )

        _assert_equal(result["status"], "configuration_required", "cached search production gate status")
        _assert_equal(
            result["error"]["code"],
            "LIVE_SEARCH_PROVIDER_REQUIRED",
            "cached search production gate code",
        )
        _assert_equal(
            result["error"]["provider_error_code"],
            "LIVE_SEARCH_PROVIDER_REQUIRED",
            "cached search production gate provider error code",
        )
        _assert_equal(
            result["error"]["provider_error_category"],
            "configuration",
            "cached search production gate provider error category",
        )
        observations = result["state"]["observations"]
        _assert_equal(observations[0]["tool"], "search_web_candidates", "cached search gate tool")
        search_result = observations[0]["result"]
        _assert_equal(search_result["provider"], "cached_harness_search", "cached search gate provider")
        _assert_equal(search_result["candidate_count"], 0, "cached search gate candidate count")
        if search_result.get("candidates"):
            raise SystemExit("cached search gate should not expose fixture candidates")
        guardrails = [
            step
            for step in result["trace"]
            if step["name"] == "stop_tool_configuration_required"
        ]
        if not guardrails:
            raise SystemExit("cached search configuration gate trace was not recorded")
    finally:
        if saved is None:
            os.environ.pop("FITCHECK_ALLOW_CACHED_AGENT_SEARCH", None)
        else:
            os.environ["FITCHECK_ALLOW_CACHED_AGENT_SEARCH"] = saved
        if saved_provider is None:
            os.environ.pop("FITCHECK_SEARCH_PROVIDER", None)
        else:
            os.environ["FITCHECK_SEARCH_PROVIDER"] = saved_provider


def _assert_search_provider_failure_stops_run() -> None:
    saved_provider = os.environ.get("FITCHECK_SEARCH_PROVIDER")
    saved_requests = providers_module.requests
    try:
        os.environ["FITCHECK_SEARCH_PROVIDER"] = "duckduckgo_html"
        providers_module.requests = FakeDuckDuckGoChallengeRequests()
        with TemporaryDirectory() as temp_dir:
            fake_llm = FakeSearchFailureLlmClient()
            runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm)
            result = runtime.start_run(
                {
                    "goal": "Find similar short sleeve tee candidates",
                    "free_text": "search failure guardrail test",
                }
            )

        _assert_equal(result["status"], "failed", "search provider failure status")
        _assert_equal(result["error"]["code"], "SEARCH_PROVIDER_FAILED", "search provider failure code")
        _assert_equal(
            result["error"]["provider_error"]["code"],
            "DUCKDUCKGO_HTML_CHALLENGE",
            "search provider nested error code",
        )
        _assert_equal(
            result["error"]["provider_error_code"],
            "DUCKDUCKGO_HTML_CHALLENGE",
            "search provider normalized error code",
        )
        _assert_equal(
            result["error"]["provider_error_category"],
            "challenge",
            "search provider normalized error category",
        )
        search_observation = [
            item for item in result["state"]["observations"] if item["tool"] == "search_web_candidates"
        ][0]
        _assert_equal(
            search_observation["provider_error_code"],
            "DUCKDUCKGO_HTML_CHALLENGE",
            "search observation normalized error code",
        )
        search_trace = [
            step for step in result["trace"] if step["step_type"] == "tool_output" and step["name"] == "search_web_candidates"
        ][0]
        _assert_equal(
            search_trace["output"]["provider_error_code"],
            "DUCKDUCKGO_HTML_CHALLENGE",
            "search trace normalized error code",
        )
        guardrails = [
            step
            for step in result["trace"]
            if step["name"] == "stop_search_provider_failed"
        ]
        if not guardrails:
            raise SystemExit("search provider failure guardrail trace was not recorded")
    finally:
        providers_module.requests = saved_requests
        if saved_provider is None:
            os.environ.pop("FITCHECK_SEARCH_PROVIDER", None)
        else:
            os.environ["FITCHECK_SEARCH_PROVIDER"] = saved_provider


def _assert_repeated_empty_search_stops_run() -> None:
    saved_provider = os.environ.get("FITCHECK_SEARCH_PROVIDER")
    saved_requests = providers_module.requests
    try:
        os.environ["FITCHECK_SEARCH_PROVIDER"] = "duckduckgo_html"
        providers_module.requests = FakeNoSearchResultsRequests()
        with TemporaryDirectory() as temp_dir:
            fake_llm = FakeRepeatedEmptySearchLlmClient()
            runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm)
            result = runtime.start_run(
                {
                    "goal": "Find similar short sleeve tee candidates",
                    "free_text": "empty search repeat guardrail test",
                }
            )

        _assert_equal(result["status"], "completed", "repeated empty search status")
        _assert_equal(result.get("error"), None, "repeated empty search error")
        _assert_equal(
            (result.get("final_report") or {}).get("report_type"),
            "no_result",
            "repeated empty search report type",
        )
        observations = result["state"]["observations"]
        search_results = [item["result"] for item in observations if item["tool"] == "search_web_candidates"]
        _assert_equal(len(search_results), 2, "repeated empty search observation count")
        if not search_results[0].get("search_deferred"):
            raise SystemExit("empty search did not produce search_deferred guidance")
        if not search_results[0].get("next_tool_suggestions"):
            raise SystemExit("empty search did not include retry suggestions")
        first_attempt = search_results[0].get("search_attempt") or {}
        second_attempt = search_results[1].get("search_attempt") or {}
        _assert_equal(first_attempt.get("attempt_index"), 1, "first empty search attempt index")
        _assert_equal(first_attempt.get("repeated_query"), False, "first empty search repeated flag")
        _assert_equal(second_attempt.get("attempt_index"), 2, "second empty search attempt index")
        _assert_equal(second_attempt.get("repeated_query"), True, "second empty search repeated flag")
        repeated_query = first_attempt.get("current_query")
        repeated_suggestion = [
            item
            for item in search_results[0].get("next_tool_suggestions", [])
            if (item.get("arguments") or {}).get("query") == repeated_query
        ]
        if repeated_suggestion:
            raise SystemExit("empty search retry suggestions repeated the failed query")
        guidance = [
            step
            for step in result["trace"]
            if step["name"] == "continue_candidate_enrichment"
        ]
        if not guidance:
            raise SystemExit("empty search guidance trace was not recorded")
        guardrails = [
            step
            for step in result["trace"]
            if step["name"] == "stop_repeated_empty_search"
        ]
        if not guardrails or guardrails[-1].get("status") != "completed":
            raise SystemExit("repeated empty search guardrail trace was not recorded")
    finally:
        providers_module.requests = saved_requests
        if saved_provider is None:
            os.environ.pop("FITCHECK_SEARCH_PROVIDER", None)
        else:
            os.environ["FITCHECK_SEARCH_PROVIDER"] = saved_provider


def _assert_unresolved_baseline_search_continues_with_grounded_recovery() -> None:
    saved_provider = os.environ.get("FITCHECK_SEARCH_PROVIDER")
    saved_requests = providers_module.requests
    try:
        os.environ["FITCHECK_SEARCH_PROVIDER"] = "duckduckgo_html"
        providers_module.requests = FakeNoSearchResultsRequests()
        with TemporaryDirectory() as temp_dir:
            fake_llm = FakeBaselineIdentityContinuedSearchLlmClient()
            runtime = _runtime(Path(temp_dir) / "runs.sqlite3", llm_client=fake_llm)
            result = runtime.start_run(
                {
                    "goal": "Brand: Gakkai Unions. I bought a short sleeve tee size L and want similar clothes.",
                    "free_text": "Brand: Gakkai Unions. I bought a short sleeve tee size L. I liked the material and fit. Find similar clothes.",
                }
            )

        observations = result["state"]["observations"]
        blocked_searches = [
            item
            for item in observations
            if item.get("tool") == "search_web_candidates"
            and (item.get("result") or {}).get("reason") == "baseline_identity_required_before_candidate_search"
        ]
        if blocked_searches:
            raise SystemExit("unresolved baseline candidate search should not be blocked")
        candidate_searches = [
            item
            for item in observations
            if item.get("tool") == "search_web_candidates"
            and (item.get("result") or {}).get("reason") != "baseline_identity_required_before_candidate_search"
        ]
        if not candidate_searches:
            raise SystemExit("unresolved baseline flow did not continue to candidate search")
        recovery_enrichments = [
            item
            for item in observations
            if item.get("tool") == "enrich_closet_item_from_web"
            and str(item.get("call_id") or "").startswith("backend_recovery_")
        ]
        if not recovery_enrichments:
            raise SystemExit("unresolved baseline flow did not execute backend enrichment recovery")
        recovery_traces = [
            step
            for step in result["trace"]
            if step.get("step_type") == "backend_recovery"
            and step.get("name") == "execute_guidance_suggestions_after_model_no_tool_call"
        ]
        if not recovery_traces:
            raise SystemExit("backend recovery trace was not recorded for unresolved baseline search")
        if result.get("status") != "completed":
            raise SystemExit(f"unexpected unresolved baseline recovery status: {result.get('status')}")
        if (result.get("error") or {}).get("code") == "REPEATED_BACKEND_GUIDANCE_RECOVERY":
            raise SystemExit("unresolved baseline recovery should end as grounded no-result, not repeated recovery failure")
        if (result.get("final_report") or {}).get("report_type") != "no_result":
            raise SystemExit("unresolved baseline recovery should produce grounded no-result report")
    finally:
        providers_module.requests = saved_requests
        if saved_provider is None:
            os.environ.pop("FITCHECK_SEARCH_PROVIDER", None)
        else:
            os.environ["FITCHECK_SEARCH_PROVIDER"] = saved_provider


def _assert_empty_search_final_becomes_waiting_for_user() -> None:
    saved_provider = os.environ.get("FITCHECK_SEARCH_PROVIDER")
    saved_requests = providers_module.requests
    try:
        os.environ["FITCHECK_SEARCH_PROVIDER"] = "duckduckgo_html"
        providers_module.requests = FakeNoSearchResultsRequests()
        with TemporaryDirectory() as temp_dir:
            fake_llm = FakeEmptySearchThenFinalLlmClient()
            runtime = _runtime(Path(temp_dir) / "runs.sqlite3", llm_client=fake_llm)
            result = runtime.start_run(
                {
                    "goal": "내 셔츠와 어울리는 메리제인을 추천해줘",
                    "free_text": "웹검색으로 메리제인 후보를 찾아줘",
                }
            )

        _assert_equal(result["status"], "completed", "empty search final status")
        _assert_equal(result.get("error"), None, "empty search final error")
        _assert_equal(result["state"].get("missing_info") or [], [], "empty search final questions")
        final_report = result.get("final_report") or {}
        _assert_equal(final_report.get("report_type"), "no_result", "empty search final report type")
        if not final_report.get("grounded"):
            raise SystemExit("empty search no-result report was not grounded")
        if "메리제인" not in final_report.get("summary", ""):
            raise SystemExit("empty search no-result report did not preserve unseen target text")
        guardrails = [
            step
            for step in result["trace"]
            if step.get("name") == "complete_grounded_no_result_report"
        ]
        if not guardrails or guardrails[-1].get("status") != "completed":
            raise SystemExit("empty search grounded no-result trace was not recorded")
    finally:
        providers_module.requests = saved_requests
        if saved_provider is None:
            os.environ.pop("FITCHECK_SEARCH_PROVIDER", None)
        else:
            os.environ["FITCHECK_SEARCH_PROVIDER"] = saved_provider


def _assert_expanding_empty_search_attempt_limit_stops_run() -> None:
    saved_provider = os.environ.get("FITCHECK_SEARCH_PROVIDER")
    saved_requests = providers_module.requests
    try:
        os.environ["FITCHECK_SEARCH_PROVIDER"] = "duckduckgo_html"
        providers_module.requests = FakeNoSearchResultsRequests()
        with TemporaryDirectory() as temp_dir:
            fake_llm = FakeExpandingEmptySearchLlmClient()
            runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm)
            result = runtime.start_run(
                {
                    "goal": "Find broader short sleeve tee candidates",
                    "free_text": "different empty search query guardrail test",
                }
            )

        _assert_equal(result["status"], "completed", "expanding empty search status")
        _assert_equal(result.get("error"), None, "expanding empty search error")
        _assert_equal(
            (result.get("final_report") or {}).get("report_type"),
            "no_result",
            "expanding empty search report type",
        )
        observations = result["state"]["observations"]
        search_results = [item["result"] for item in observations if item["tool"] == "search_web_candidates"]
        _assert_equal(len(search_results), 5, "expanding empty search observation count")
        last_attempt = search_results[-1].get("search_attempt") or {}
        _assert_equal(last_attempt.get("attempt_index"), 5, "expanding empty search final attempt index")
        _assert_equal(last_attempt.get("previous_query_count"), 4, "expanding empty search previous query count")
        _assert_equal(last_attempt.get("previous_empty_attempt_count"), 4, "expanding empty search previous empty count")
        guardrails = [
            step
            for step in result["trace"]
            if step["name"] == "stop_empty_search_attempt_limit"
        ]
        if not guardrails or guardrails[-1].get("status") != "completed":
            raise SystemExit("expanding empty search attempt limit guardrail trace was not recorded")
        _assert_equal(
            guardrails[-1]["output"].get("attempt_count"),
            5,
            "expanding empty search attempt count",
        )
        _assert_equal(
            len(guardrails[-1]["output"].get("queries") or []),
            5,
            "expanding empty search query count",
        )
    finally:
        providers_module.requests = saved_requests
        if saved_provider is None:
            os.environ.pop("FITCHECK_SEARCH_PROVIDER", None)
        else:
            os.environ["FITCHECK_SEARCH_PROVIDER"] = saved_provider


def _assert_max_agent_turns_exceeded_records_guardrail() -> None:
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeMaxTurnsToolLoopLlmClient()
        runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm, max_turns=2)
        result = runtime.start_run(
            {
                "goal": "鍮꾩듂?????꾨낫瑜?怨꾩냽 ?먯깋?댁쨾",
                "free_text": "max turn guardrail test",
            }
        )

    _assert_equal(result["status"], "failed", "max turns status")
    _assert_equal(result["error"]["code"], "MAX_AGENT_TURNS_EXCEEDED", "max turns error code")
    _assert_equal(result["error"]["max_turns"], 2, "max turns error metadata")
    _assert_equal(len(fake_llm.requests), 2, "max turns LLM request count")
    if result["state"].get("pending_tool_outputs"):
        raise SystemExit("max turns should clear pending_tool_outputs")
    guardrails = [
        step
        for step in result["trace"]
        if step["step_type"] == "guardrail"
        and step["name"] == "stop_max_agent_turns_exceeded"
    ]
    if not guardrails:
        raise SystemExit("max turns guardrail trace was not recorded")
    _assert_equal(
        guardrails[-1]["output"]["code"],
        "MAX_AGENT_TURNS_EXCEEDED",
        "max turns guardrail output code",
    )
    llm_steps = [
        step for step in result["trace"] if step["step_type"] == "llm_response"
    ]
    _assert_equal(len(llm_steps), 2, "max turns LLM trace count")


def _assert_reply_after_backend_terminal_completion_resets_provider_context() -> None:
    def score_handler(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        return {
            "status": "completed",
            "candidate_key": arguments.get("candidate_key") or "web_test_candidate",
            "score": {"overall": 72, "size": 70, "material": 75, "confidence": 80},
            "recommendation": "conditional_candidate",
            "missing_fields": [],
            "risks": [],
        }

    def report_handler(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        return {
            "status": "completed",
            "candidate_key": arguments.get("candidate_key") or "web_test_candidate",
            "grounded": True,
            "final_report": "Observed score evidence supports a bounded recommendation.",
            "report_type": "shortlist",
            "grounding_validation": {"ok": True},
        }

    registry = ToolRegistry(
        [
            ToolDefinition(
                name="score_candidate_match",
                description="Test score tool for backend terminal completion",
                parameters={
                    "type": "object",
                    "properties": {"candidate_key": {"type": "string"}},
                    "required": ["candidate_key"],
                    "additionalProperties": False,
                },
                handler=score_handler,
            ),
            ToolDefinition(
                name="write_purchase_report",
                description="Test report tool for backend terminal completion",
                parameters={
                    "type": "object",
                    "properties": {"candidate_key": {"type": "string"}},
                    "required": ["candidate_key"],
                    "additionalProperties": False,
                },
                handler=report_handler,
            ),
        ]
    )
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeBackendTerminalThenReplyLlmClient()
        runtime = _runtime(
            Path(temp_dir) / "runs.json",
            llm_client=fake_llm,
            tool_registry=registry,
            max_turns=1,
        )
        result = runtime.start_run(
            {
                "goal": "Find clothing similar to a product URL after backend terminal completion.",
                "free_text": "https://www.musinsa.com/products/4844733 size L similar fit material",
                "session_id": "backend_terminal_reply_session",
            }
        )

        _assert_equal(result["status"], "completed", "backend terminal initial status")
        _assert_equal(result["state"].get("previous_response_id"), None, "backend terminal clears previous response id")
        reset = result["state"].get("provider_response_context_reset") or {}
        _assert_equal(
            reset.get("previous_response_id"),
            "resp_terminal_score_1",
            "backend terminal reset records old previous response id",
        )
        _assert_equal(
            fake_llm.requests[0]["previous_response_id"],
            None,
            "initial backend terminal request previous response id",
        )

        resumed = runtime.reply(
            result["run_id"],
            {
                "message": "Use the recorded URL and inspect it again with OCR.",
                "answers": [{"field": "search_instruction", "value": "Use the recorded URL and inspect it again with OCR."}],
                "attachments": [],
                "session_id": "backend_terminal_reply_session",
            },
        )

        _assert_equal(resumed["status"], "completed", "backend terminal reply status")
        _assert_equal(len(fake_llm.requests), 2, "backend terminal reply request count")
        _assert_equal(
            fake_llm.requests[1]["previous_response_id"],
            None,
            "reply after backend terminal completion previous response id",
        )
        if resumed.get("error"):
            raise SystemExit("reply after backend terminal completion should not fail")


def _assert_interrupted_tool_call_resume_resets_provider_context() -> None:
    def missing_info_handler(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        return {
            "status": "waiting_for_user",
            "candidate_key": arguments.get("candidate_key") or "all",
            "missing_fields": arguments.get("missing_fields") or ["baseline_evidence"],
            "questions": [
                {
                    "field": "baseline_evidence",
                    "question": "기준 옷의 실측이나 소재 근거를 알려주세요.",
                }
            ],
        }

    registry = ToolRegistry(
        [
            ToolDefinition(
                name="ask_missing_info",
                description="Test missing-info tool for interrupted tool-call resume",
                parameters={
                    "type": "object",
                    "properties": {
                        "candidate_key": {"type": "string"},
                        "missing_fields": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["candidate_key", "missing_fields"],
                    "additionalProperties": False,
                },
                handler=missing_info_handler,
            )
        ]
    )
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeInterruptedToolCallResumeLlmClient()
        runtime = _runtime(
            Path(temp_dir) / "runs.json",
            llm_client=fake_llm,
            tool_registry=registry,
            max_turns=2,
        )
        run = runtime.prepare_run(
            {
                "goal": "Resume an interrupted parallel tool batch.",
                "free_text": "차콜 반팔티와 비슷한 상품을 계속 찾아줘",
                "session_id": "interrupted_resume_session",
            }
        )
        run.setdefault("state", {})["previous_response_id"] = "resp_interrupted_tool_calls"
        run.setdefault("state", {})["pending_tool_outputs"] = []
        append_trace(
            run,
            step_type="llm_response",
            name="responses.create",
            status="completed",
            output_payload={
                "response_id": "resp_interrupted_tool_calls",
                "tool_call_count": 2,
            },
        )
        append_trace(
            run,
            step_type="runtime",
            name="execute_parallel_tool_batch",
            status="started",
            input_payload={"tool": "fetch_product_page", "call_count": 2},
        )
        for call_id, candidate_key in [
            ("call_interrupted_1", "candidate_one"),
            ("call_interrupted_2", "candidate_two"),
        ]:
            append_trace(
                run,
                step_type="tool_call",
                name="fetch_product_page",
                status="started",
                input_payload={
                    "arguments": {"candidate_key": candidate_key},
                    "call_id": call_id,
                    "parallel_group": "candidate_analysis",
                },
            )
        run = runtime.store.update_run(run)

        resumed = runtime.continue_run(run["run_id"])

        _assert_equal(resumed["status"], "waiting_for_user", "interrupted resume status")
        _assert_equal(len(fake_llm.requests), 1, "interrupted resume request count")
        _assert_equal(
            fake_llm.requests[0]["previous_response_id"],
            None,
            "interrupted resume previous response id",
        )
        reset = resumed.get("state", {}).get("provider_response_context_reset") or {}
        _assert_equal(
            reset.get("reason"),
            "interrupted_tool_calls_without_outputs",
            "interrupted resume reset reason",
        )
        _assert_equal(
            reset.get("missing_provider_tool_call_ids"),
            ["call_interrupted_1", "call_interrupted_2"],
            "interrupted resume missing call ids",
        )
        if not any(
            step.get("name") == "reset_response_context_after_interrupted_tool_calls"
            for step in resumed.get("trace") or []
        ):
            raise SystemExit("interrupted resume reset trace missing")


def _assert_url_size_phrase_does_not_pollute_brand_or_name() -> None:
    parsed = _parse_liked_item_text(
        "https://www.musinsa.com/products/4844733 의 L 사이즈 와 비슷한 핏, 재질의 옷을 찾아봐"
    )
    if parsed.get("brand") == "의 L":
        raise SystemExit("URL-adjacent Korean particle and size label should not become brand")
    if parsed.get("name") == "의 L":
        raise SystemExit("URL-adjacent Korean particle and size label should not become item name")
    _assert_equal(parsed.get("size_label"), "L", "URL size phrase still extracts size label")
    if not _baseline_name_is_weak("의 L"):
        raise SystemExit("URL-adjacent size residue should be treated as weak baseline name")


def _assert_unknown_target_category_and_material_terms_are_not_false_blockers() -> None:
    spec = {
        "category": "t_shirt",
        "name": "Double cotton oversized tee",
        "measurements": {"length": 75.5, "shoulder": 63.0, "chest": 59.0, "sleeve": 22.0},
        "material": {},
        "material_terms": ["cotton"],
        "missing_fields": ["material", "price"],
    }
    missing = _recompute_missing_fields(spec)
    if "material" not in missing:
        raise SystemExit("composition should remain missing when only material family terms are observed")
    critical = _critical_missing_fields_for_spec(spec, missing)
    if "material" in critical:
        raise SystemExit("material family terms should prevent material from blocking as a critical missing field")

    with TemporaryDirectory() as temp_dir:
        runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=FakeToolCallingLlmClient())
        run = runtime.prepare_run(
            {
                "goal": "Find similar clothing from a product URL.",
                "free_text": "https://www.musinsa.com/products/4844733 size L similar fit material",
                "session_id": "unknown_target_category_session",
            }
        )
        run.setdefault("state", {})["search_constraints"] = {
            "goal_mode": "similar_clothing_discovery",
            "target_category": "unknown",
            "target_item_query": "",
        }
        context = ToolContext(
            web_result_path=runtime.web_result_path,
            keyword_result_path=runtime.keyword_result_path,
            refresh_web_candidates=runtime.refresh_web_candidates,
            run=run,
        )
        validation = _target_category_validation_for_candidate(
            context,
            {"normalized_spec": {"category": "t_shirt", "name": "Double cotton oversized tee"}},
        )
        if validation.get("status") == "mismatch":
            raise SystemExit("unknown target category should not create a target category mismatch")


def _assert_ambiguous_lower_body_measurement_semantics_do_not_hard_fail_fit() -> None:
    candidate = {
        "category": "pants",
        "name": "Bermuda shorts",
        "measurements": {
            "length": 62.0,
            "waist": 39.0,
            "rise": 33.0,
            "thigh": 33.0,
            "hem_width": 63.0,
        },
        "material": {},
        "material_terms": ["cotton"],
        "price": 39000,
        "field_confidence": {"measurements": 0.8, "material_terms": 0.5, "price": 0.8, "category": 0.8},
        "overall_confidence": 0.72,
        "missing_fields": ["material"],
    }
    baseline = {
        "category": "pants",
        "name": "Liked shorts",
        "measurements": {
            "length": 62.0,
            "waist": 39.0,
            "rise": 33.5,
            "thigh": 33.3,
            "hem_width": 33.5,
        },
        "material": {},
        "material_terms": ["cotton"],
    }
    match = calculate_match(
        candidate,
        baseline,
        required_measurement_fields=["length", "waist", "rise", "thigh", "hem_width"],
    )
    if match["measurement_deltas"].get("hem_width") is not None:
        raise SystemExit("ambiguous hem width semantics should not produce a hard numeric delta")
    risks = match.get("risks") or []
    if not any(item.get("type") == "ambiguous_hem_width_semantics" for item in risks):
        raise SystemExit("ambiguous hem width comparison should be recorded as an evidence risk")
    if any(item.get("type") == "hem_width" and item.get("level") == "high" for item in risks):
        raise SystemExit("ambiguous hem width semantics should not be treated as a high fit mismatch")
    if int(match["score"].get("size") or 0) < 80:
        raise SystemExit("ambiguous hem width should not dominate the lower-body fit score")
    if not _match_has_close_enough_measurements(match):
        raise SystemExit("close-fit policy should accept close comparable fields when only ambiguous hem is skipped")
    baseline_with_circumference_like_hem = {
        **baseline,
        "measurements": {
            **baseline["measurements"],
            "hem_width": 80.0,
        },
    }
    match = calculate_match(
        candidate,
        baseline_with_circumference_like_hem,
        required_measurement_fields=["length", "waist", "rise", "thigh", "hem_width"],
    )
    if match["measurement_deltas"].get("hem_width") is not None:
        raise SystemExit("large lower-body hem values should be treated as ambiguous circumference-like evidence")
    if int(match["score"].get("size") or 0) < 80:
        raise SystemExit("large ambiguous hem values should not dominate lower-body fit scoring")
    if not _match_has_close_enough_measurements(match):
        raise SystemExit("close-fit policy should accept close comparable fields when large ambiguous hem is skipped")


def _assert_related_product_material_does_not_pollute_current_product_spec() -> None:
    product = {
        "candidate_key": "live_current_cotton_shorts",
        "raw_input": {
            "provider": "live_page_fetch+jina_reader",
            "fetch_status": "completed",
            "source_url": "https://www.musinsa.com/products/6154431",
            "name": "바이오워싱 카펜터 버뮤다 코튼 팬츠_블랙",
            "structured_text": (
                "Title: 바이오워싱 카펜터 버뮤다 코튼 팬츠_블랙 "
                "총장 62 허리 39 밑위 33 허벅지 33 밑단 63"
            ),
            "spec_table_text": "",
            "raw_text": (
                "Title: 바이오워싱 카펜터 버뮤다 코튼 팬츠_블랙 "
                "총장 62 허리 39 밑위 33 허벅지 33 밑단 63 "
                "[관련상품](https://www.musinsa.com/products/6238770) "
                "[10주년기획] 모두의 린넨 100% 커브드 팬츠_3 COLOR 33,900원"
            ),
            "price_candidates": [{"value": 33900, "currency": "KRW", "source": "won_suffix"}],
        },
    }
    spec = _normalize_live_product_spec(product)
    if spec.get("material"):
        raise SystemExit(f"related product exact material polluted current product spec: {spec.get('material')}")
    if "linen" in set(spec.get("material_terms") or []):
        raise SystemExit("related product linen term should not pollute current product material terms")
    if "cotton" not in set(spec.get("material_terms") or []):
        raise SystemExit("current product cotton term from title should remain available")
    if spec.get("price") == 33900:
        raise SystemExit("related product price should not be selected as current product price")
    if spec.get("price_candidates"):
        raise SystemExit("related product price candidates should not remain in normalized current product spec")


def _assert_urlencoded_content_price_is_extracted_for_current_product() -> None:
    product = {
        "candidate_key": "live_current_content_price",
        "raw_input": {
            "provider": "live_page_fetch+jina_reader",
            "fetch_status": "completed",
            "source_url": "https://www.musinsa.com/products/6154430",
            "name": "바이오워싱 카펜터 버뮤다 코튼 팬츠_베이지",
            "structured_text": (
                "Title: 바이오워싱 카펜터 버뮤다 코튼 팬츠_베이지 "
                "총장 62 허리 39 밑위 33 허벅지 33 밑단 63"
            ),
            "spec_table_text": "총장 62 허리 39 밑위 33 허벅지 33 밑단 63",
            "raw_text": (
                "event payload content_name%22%3A%22바이오워싱%20카펜터%20버뮤다%20코튼%20팬츠_%EB%B2%A0%EC%9D%B4%EC%A7%80%22"
                "%2C%22content_price%22%3A26900%2C%22content_group_id%22%3A%22Clothing%22 "
                "https://www.musinsa.com/products/6238770 related product 39,900원"
            ),
            "price_candidates": [],
        },
    }
    spec = _normalize_live_product_spec(product)
    if spec.get("price") != 26900:
        raise SystemExit("urlencoded current-product content_price should be selected as normalized price")
    prices = [item.get("value") for item in spec.get("price_candidates") or []]
    if 26900 not in prices:
        raise SystemExit("urlencoded current-product content_price should remain in price_candidates")
    if 39900 in prices:
        raise SystemExit("related-product won price after another product URL should not pollute current price candidates")


def _assert_numeric_grounding_ignores_image_label_numbers() -> None:
    run = {
        "state": {
            "observations": [
                {
                    "tool": "classify_product_assets",
                    "result": {
                        "status": "completed",
                        "assets": [
                            {"name": "Image 36: Thumbnail 35"},
                            {"name": "Image 37: content-img-36"},
                        ],
                    },
                },
                {
                    "tool": "score_candidate_match",
                    "result": {
                        "status": "completed",
                        "candidate_key": "all",
                        "ranked_matches": [
                            {
                                "candidate_key": "candidate_a",
                                "score": {
                                    "overall": 79,
                                    "size": 100,
                                    "material": 65,
                                    "price": 90,
                                    "confidence": 36,
                                },
                            }
                        ],
                    },
                },
                {
                    "tool": "write_purchase_report",
                    "result": {
                        "status": "completed",
                        "grounded": True,
                        "final_report": "전체 79점, 사이즈 100점, 소재 65점, 신뢰도 36점입니다.",
                    },
                },
            ]
        }
    }
    grounding = validate_report_grounding(
        report_text="전체 79점, 사이즈 100점, 소재 65점, 신뢰도 36점입니다.",
        run=run,
    )
    if not grounding.get("valid"):
        raise SystemExit(f"numeric grounding should remain valid with score evidence: {grounding}")
    evidence_by_claim = {
        item.get("claim"): item.get("evidence") or []
        for item in grounding.get("grounding_evidence") or []
        if item.get("claim_type") == "score"
    }
    confidence_sources = evidence_by_claim.get("confidence:36") or []
    if not confidence_sources:
        raise SystemExit("confidence score 36 should have score-field evidence")
    if any("assets" in str(item.get("path") or "") for item in confidence_sources):
        raise SystemExit("image asset labels must not be used as numeric score grounding evidence")
    if not any(".score.confidence" in str(item.get("path") or "") for item in confidence_sources):
        raise SystemExit("confidence score should cite score.confidence evidence")


def _assert_material_grounding_ignores_search_arguments() -> None:
    run = {
        "state": {
            "observations": [
                {
                    "tool": "search_web_candidates",
                    "arguments": {"query": "cotton tee 면 소재 티셔츠"},
                    "result": {
                        "status": "completed",
                        "candidate_count": 1,
                        "candidates": [{"candidate_key": "candidate_a", "name": "Basic Tee"}],
                    },
                },
                {
                    "tool": "score_candidate_match",
                    "arguments": {"candidate_key": "all"},
                    "result": {
                        "status": "completed",
                        "candidate_key": "all",
                        "ranked_matches": [
                            {
                                "candidate_key": "candidate_a",
                                "name": "Basic Tee",
                                "score": {
                                    "overall": 70,
                                    "size": 50,
                                    "material": 0,
                                    "price": 50,
                                    "confidence": 30,
                                },
                            }
                        ],
                    },
                },
            ]
        }
    }
    ungrounded = validate_report_grounding(
        report_text="Basic Tee는 면 소재입니다. 전체 70점입니다.",
        run=run,
    )
    if ungrounded.get("valid"):
        raise SystemExit(f"search query must not ground candidate material claims: {ungrounded}")
    if "cotton" not in set(ungrounded.get("suspicious_material_claims") or []):
        raise SystemExit(f"ungrounded material claim should be flagged as cotton: {ungrounded}")

    run["state"]["observations"][1]["result"]["ranked_matches"][0]["normalized_spec"] = {
        "material": {"cotton": 100},
        "missing_fields": [],
    }
    grounded = validate_report_grounding(
        report_text="Basic Tee는 면 소재입니다. 전체 70점입니다.",
        run=run,
    )
    if not grounded.get("valid"):
        raise SystemExit(f"normalized material evidence should ground cotton claim: {grounded}")
    material_sources = [
        item.get("evidence") or []
        for item in grounded.get("grounding_evidence") or []
        if item.get("claim_type") == "material" and item.get("claim") == "cotton"
    ]
    if not material_sources:
        raise SystemExit("grounded material claim should cite normalized material evidence")
    flattened_paths = [str(row.get("path") or "") for rows in material_sources for row in rows]
    if any(".arguments." in path for path in flattened_paths):
        raise SystemExit(f"material evidence must not cite tool arguments: {flattened_paths}")
    if not any(".normalized_spec.material" in path for path in flattened_paths):
        raise SystemExit(f"material evidence should cite normalized_spec.material: {flattened_paths}")


def _assert_description_only_baseline_does_not_require_measurement_comparison() -> None:
    with TemporaryDirectory() as tmp:
        run = {
            "run_id": "run_description_only_measurement_policy",
            "input": {
                "free_text": "자주 입는 차콜 반팔티랑 비슷한 옷을 찾아줘. 핏은 여유롭고 오래 입어도 괜찮았어.",
                "attachments": [],
            },
            "state": {
                "search_constraints": {
                    "goal_mode": "similar_clothing_discovery",
                    "category": "t_shirt",
                    "category_query_label": "반팔 티셔츠",
                    "color_query_label": "차콜",
                    "preference_signals": ["fit", "durability"],
                },
                "closet_item": {
                    "id": "current_liked_item",
                    "category": "t_shirt",
                    "name": "차콜 반팔 티셔츠",
                    "measurements": {},
                    "material": {},
                    "material_terms": [],
                    "preference_signals": ["fit", "durability"],
                    "baseline_identity": {
                        "status": "description_only_baseline",
                        "can_identify_exact_product": False,
                    },
                },
                "live_products": {
                    "candidate_a": {
                        "candidate_key": "candidate_a",
                        "normalized_spec": {
                            "candidate_key": "candidate_a",
                            "category": "t_shirt",
                            "name": "차콜 오버핏 반팔 티셔츠",
                            "source_url": "https://example.com/products/candidate-a",
                            "measurements": {},
                            "material": {},
                            "material_terms": ["cotton"],
                            "price": 29000,
                            "missing_fields": [
                                "measurements.length",
                                "measurements.shoulder",
                                "measurements.chest",
                                "measurements.sleeve",
                                "material",
                            ],
                            "field_confidence": {"price": 0.8, "material_terms": 0.5, "category": 0.8},
                            "overall_confidence": 0.5,
                        },
                    }
                },
                "observations": [
                    {
                        "tool": "search_web_candidates",
                        "call_id": "search_1",
                        "result": {
                            "status": "completed",
                            "candidate_count": 1,
                            "candidates": [
                                {
                                    "candidate_key": "candidate_a",
                                    "name": "차콜 오버핏 반팔 티셔츠",
                                    "source_url": "https://example.com/products/candidate-a",
                                }
                            ],
                        },
                    },
                    {
                        "tool": "fetch_product_page",
                        "call_id": "fetch_1",
                        "arguments": {"candidate_key": "candidate_a"},
                        "result": {"status": "completed", "candidate_key": "candidate_a"},
                    },
                    {
                        "tool": "classify_product_assets",
                        "call_id": "classify_1",
                        "arguments": {"candidate_key": "candidate_a"},
                        "result": {"status": "completed", "candidate_key": "candidate_a"},
                    },
                    {
                        "tool": "extract_size_material_info",
                        "call_id": "extract_1",
                        "arguments": {"candidate_key": "candidate_a"},
                        "result": {"status": "completed", "candidate_key": "candidate_a"},
                    },
                    {
                        "tool": "normalize_product_spec",
                        "call_id": "normalize_1",
                        "arguments": {"candidate_key": "candidate_a"},
                        "result": {"status": "completed", "candidate_key": "candidate_a"},
                    },
                ],
            },
            "trace": [],
        }
        context = ToolContext(
            web_result_path=Path(tmp) / "web.json",
            keyword_result_path=Path(tmp) / "keyword.json",
            refresh_web_candidates=lambda: None,
            run=run,
            memory_store=SqliteAgentRunStore(Path(tmp) / "runs.sqlite3"),
        )
        result = _score_candidate_match({"candidate_key": "all"}, context)
    if result.get("score_deferred"):
        raise SystemExit(f"description-only baseline should not defer scoring for missing measurements: {result}")
    ranked = result.get("ranked_matches") or []
    if not ranked:
        raise SystemExit("description-only baseline should produce a conditional ranked match")
    top = ranked[0]
    risks = top.get("risks") or top.get("top_risks") or []
    if not any(item.get("type") == "measurement_comparison_not_applicable" for item in risks):
        raise SystemExit("description-only baseline should record measurement comparison as not applicable")
    if top.get("measurement_deltas"):
        raise SystemExit("description-only baseline should not produce hard measurement deltas")


def _assert_llm_response_failure_clears_pending_tool_outputs() -> None:
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeToolThenLlmFailureClient()
        runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm)
        result = runtime.start_run(
            {
                "goal": "General agent runtime failure guardrail",
                "free_text": "LLM response failure guardrail test without clothing lookup clues",
            }
        )

    _assert_equal(result["status"], "failed", "LLM failure status")
    _assert_equal(result["error"]["code"], "LLM_RESPONSE_FAILED", "LLM failure error code")
    _assert_equal(result["error"]["provider_error_code"], "LLM_TIMEOUT", "LLM failure provider error code")
    _assert_equal(result["error"]["provider_error_category"], "timeout", "LLM failure provider error category")
    _assert_equal(result["error"]["retryable"], True, "LLM failure retryable")
    _assert_equal(result["error"]["resume_available"], True, "LLM failure resume availability")
    _assert_equal(len(fake_llm.requests), 2, "LLM failure request count")
    pending = result["state"].get("pending_tool_outputs") or []
    if len(pending) != 1 or pending[0].get("call_id") != "call_1":
        raise SystemExit("LLM response failure should preserve undelivered pending_tool_outputs")

    failed_llm_steps = [
        step
        for step in result["trace"]
        if step["step_type"] == "llm_response" and step["status"] == "failed"
    ]
    if not failed_llm_steps:
        raise SystemExit("LLM response failure trace was not recorded")
    _assert_equal(
        failed_llm_steps[-1]["output"]["code"],
        "LLM_RESPONSE_FAILED",
        "LLM failure trace output code",
    )
    _assert_equal(
        failed_llm_steps[-1]["input"]["pending_tool_output_count"],
        1,
        "LLM failure trace input pending count",
    )
    _assert_equal(
        failed_llm_steps[-1]["output"]["pending_tool_output_count"],
        1,
        "LLM failure trace output pending count",
    )

    guardrails = [
        step
        for step in result["trace"]
        if step["step_type"] == "guardrail" and step["name"] == "stop_llm_response_failed"
    ]
    if not guardrails:
        raise SystemExit("LLM response failure guardrail trace was not recorded")
    _assert_equal(
        guardrails[-1]["output"]["code"],
        "LLM_RESPONSE_FAILED",
        "LLM failure guardrail output code",
    )
    _assert_equal(
        guardrails[-1]["output"]["pending_tool_output_count"],
        1,
        "LLM failure guardrail pending count",
    )


def _assert_pending_tool_result_pairing_mismatch_fails_before_llm() -> None:
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeToolCallingLlmClient()
        runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm)
        run = runtime.store.create_run(
            {
                "goal": "resume with mismatched pending tool result",
                "free_text": "tool result pairing mismatch guardrail test",
            },
            fake_llm.get_status(),
        )
        run["status"] = "running"
        run["trace"] = [
            {
                "step_index": 1,
                "step_type": "llm_response",
                "name": "responses_api",
                "status": "completed",
                "input": {},
                "output": {"tool_call_count": 1},
                "created_at": run["created_at"],
            },
            {
                "step_index": 2,
                "step_type": "tool_call",
                "name": "search_web_candidates",
                "status": "started",
                "input": {"call_id": "call_expected", "arguments": {"query": "pants"}},
                "output": {},
                "created_at": run["created_at"],
            },
        ]
        run["state"]["pending_tool_outputs"] = [
            {
                "type": "function_call_output",
                "call_id": "call_orphan",
                "output": '{"status": "completed"}',
            }
        ]
        runtime.store.update_run(run)

        resumed = runtime.continue_run(run["run_id"])

    _assert_equal(resumed["status"], "failed", "pairing mismatch status")
    _assert_equal(
        resumed["error"]["code"],
        "TOOL_RESULT_PAIRING_MISMATCH",
        "pairing mismatch error code",
    )
    _assert_equal(
        resumed["error"]["missing_results"],
        ["call_expected"],
        "pairing mismatch missing result ids",
    )
    _assert_equal(
        resumed["error"]["orphan_results"],
        ["call_orphan"],
        "pairing mismatch orphan result ids",
    )
    _assert_equal(fake_llm.index, 0, "pairing mismatch should fail before LLM call")
    if resumed["state"].get("pending_tool_outputs"):
        raise SystemExit("pairing mismatch should clear pending_tool_outputs")
    guardrail_steps = [
        step
        for step in resumed["trace"]
        if step["step_type"] == "guardrail"
        and step["name"] == "stop_tool_result_pairing_mismatch"
    ]
    if not guardrail_steps:
        raise SystemExit("tool result pairing mismatch guardrail trace was not recorded")


def _assert_llm_rate_limit_failure_is_structured_and_resumable() -> None:
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeToolThenRateLimitedLlmClient()
        runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm)
        result = runtime.start_run(
            {
                "goal": "General runtime rate limit guardrail",
                "free_text": "LLM rate limit guardrail test without clothing lookup clues",
            }
        )

    _assert_equal(result["status"], "failed", "LLM rate limit status")
    _assert_equal(result["error"]["code"], "LLM_RESPONSE_FAILED", "LLM rate limit error code")
    _assert_equal(
        result["error"]["provider_error_code"],
        "HTTP_429_RATE_LIMITED",
        "LLM rate limit provider error code",
    )
    _assert_equal(
        result["error"]["provider_error_category"],
        "rate_limit",
        "LLM rate limit provider error category",
    )
    _assert_equal(result["error"]["retryable"], True, "LLM rate limit retryable")
    _assert_equal(result["error"]["resume_available"], True, "LLM rate limit resume availability")
    pending = result["state"].get("pending_tool_outputs") or []
    if len(pending) != 1 or pending[0].get("call_id") != "call_1":
        raise SystemExit("LLM rate limit should preserve undelivered pending_tool_outputs")
    guardrail = [
        step
        for step in result["trace"]
        if step["step_type"] == "guardrail" and step["name"] == "stop_llm_response_failed"
    ][-1]
    _assert_equal(
        guardrail["output"]["provider_error_code"],
        "HTTP_429_RATE_LIMITED",
        "LLM rate limit guardrail provider code",
    )
    _assert_equal(
        guardrail["output"]["resume_available"],
        True,
        "LLM rate limit guardrail resume availability",
    )


def _assert_rate_limit_after_completed_score_recovers_with_backend_report() -> None:
    def score_handler(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        context.run.setdefault("state", {})["missing_info"] = [
            {"field": "stale_baseline_evidence", "question": "stale question"}
        ]
        return {
            "status": "completed",
            "candidate_key": arguments.get("candidate_key") or "all",
            "ranked_matches": [
                {
                    "candidate_key": "candidate_recovery",
                    "recommendation": "consider",
                    "score": {"overall": 72},
                }
            ],
        }

    def report_handler(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        return {
            "status": "completed",
            "candidate_key": arguments.get("candidate_key"),
            "grounded": True,
            "final_report": "저장된 score observation을 사용한 grounded recovery report",
            "grounding_validation": {"status": "completed", "valid": True},
        }

    registry = ToolRegistry(
        [
            ToolDefinition(
                name="score_candidate_match",
                description="Test score tool",
                parameters={
                    "type": "object",
                    "properties": {"candidate_key": {"type": "string"}},
                    "required": ["candidate_key"],
                    "additionalProperties": False,
                },
                handler=score_handler,
            ),
            ToolDefinition(
                name="write_purchase_report",
                description="Test report tool",
                parameters={
                    "type": "object",
                    "properties": {"candidate_key": {"type": "string"}},
                    "required": ["candidate_key"],
                    "additionalProperties": False,
                },
                handler=report_handler,
            ),
        ]
    )
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeScoreThenRateLimitedLlmClient()
        runtime = _runtime(
            Path(temp_dir) / "runs.json",
            llm_client=fake_llm,
            tool_registry=registry,
        )
        result = runtime.start_run(
            {
                "goal": "Recover a grounded report after score",
                "free_text": "rate limited only after backend score completed",
            }
        )

    _assert_equal(result["status"], "completed", "post-score rate limit recovery status")
    _assert_equal(result["error"], None, "post-score rate limit recovery error")
    _assert_equal(
        result["final_report"]["grounding"],
        "write_purchase_report_after_retryable_llm_failure",
        "post-score rate limit recovery grounding",
    )
    _assert_equal(
        result["final_report"]["recovered_from_llm_error"]["provider_error_code"],
        "HTTP_429_RATE_LIMITED",
        "post-score rate limit recovery provider code",
    )
    if result["state"].get("pending_tool_outputs"):
        raise SystemExit("post-score rate limit recovery should clear pending tool outputs")
    if result["state"].get("missing_info"):
        raise SystemExit("post-score rate limit recovery should clear stale missing_info")
    tools = [item.get("tool") for item in result["state"].get("observations", [])]
    _assert_equal(
        tools,
        ["score_candidate_match", "write_purchase_report"],
        "post-score rate limit recovery tools",
    )
    recovery_trace = [
        step
        for step in result["trace"]
        if step.get("name") == "recover_retryable_llm_failure_with_grounded_report"
    ]
    if not recovery_trace:
        raise SystemExit("post-score rate limit recovery guardrail trace missing")


def _assert_rate_limit_after_baseline_identity_planning_recovers_with_enrichment() -> None:
    def load_memory_handler(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        context.run.setdefault("state", {})["closet_item"] = {
            "id": "current_liked_item",
            "size_label": "L",
            "memory_source": "user_text_partial_memory",
            "baseline_identity": {
                "status": "needs_identity_confirmation",
                "can_identify_exact_product": False,
            },
        }
        context.run.setdefault("state", {})["baseline_identity"] = {
            "status": "needs_identity_confirmation",
            "can_identify_exact_product": False,
        }
        return {
            "status": "completed",
            "baseline_identity": context.run["state"]["baseline_identity"],
            "closet_item": context.run["state"]["closet_item"],
        }

    def enrich_baseline_handler(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        candidate = {
            "candidate_key": "identity_candidate_recovery",
            "name": "가까이 유니언즈 기준 옷 후보",
            "source_url": "https://shop.example/gakkai-recovery",
            "provider": "fake_identity_recovery",
            "confirmation_status": "unconfirmed",
        }
        state = context.run.setdefault("state", {})
        state["unconfirmed_baseline_identity_candidates"] = [candidate]
        closet = state.setdefault("closet_item", {"id": "current_liked_item"})
        closet["memory_source"] = "web_lookup_unconfirmed_baseline"
        closet["baseline_identity"] = {
            "status": "needs_identity_confirmation",
            "selected_candidate_key": candidate["candidate_key"],
            "selected_candidate_name": candidate["name"],
            "selected_candidate_source_url": candidate["source_url"],
        }
        state["baseline_identity"] = closet["baseline_identity"]
        return {
            "status": "completed",
            "baseline_enrichment_status": "completed",
            "candidate_count": 1,
            "identity_candidates": [candidate],
            "provider": "fake_identity_recovery",
            "provider_mode": "test",
            "external_api_called": False,
        }

    registry = ToolRegistry(
        [
            ToolDefinition(
                name="load_closet_memory",
                description="Test closet memory loader",
                parameters={"type": "object", "properties": {}, "additionalProperties": False},
                handler=load_memory_handler,
            ),
            ToolDefinition(
                name="enrich_closet_item_from_web",
                description="Test baseline identity enrichment",
                parameters={"type": "object", "properties": {}, "additionalProperties": True},
                handler=enrich_baseline_handler,
            ),
        ]
    )
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeToolThenRateLimitedLlmClient()
        runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm, tool_registry=registry)
        result = runtime.start_run(
            {
                "goal": "자주 입는 차콜 반팔티랑 비슷한 옷을 찾아줘",
                "free_text": "자주 입는 차콜 반팔티랑 비슷한 옷을 찾아줘. 가까이 유니언즈 사이즈 L 이야.",
                "session_id": "baseline_rate_limit_recovery_session",
            }
        )

    _assert_equal(result["status"], "waiting_for_user", "baseline rate limit recovery status")
    if result.get("error"):
        raise SystemExit(f"baseline rate limit recovery should clear error: {result['error']}")
    tools = [item.get("tool") for item in result["state"].get("observations", [])]
    if "load_closet_memory" not in tools:
        raise SystemExit("baseline rate limit recovery did not load closet memory")
    if "enrich_closet_item_from_web" not in tools:
        raise SystemExit("baseline rate limit recovery did not execute baseline enrichment")
    if not result["state"].get("unconfirmed_baseline_identity_candidates"):
        raise SystemExit("baseline rate limit recovery did not preserve unconfirmed identity candidates")
    missing_info = result["state"].get("missing_info") or []
    if not missing_info or missing_info[0].get("field") != "baseline_identity_confirmation":
        raise SystemExit("baseline rate limit recovery did not ask for baseline identity confirmation")
    guardrail_steps = [
        step
        for step in result["trace"]
        if step.get("step_type") == "guardrail"
        and step.get("name") == "recover_retryable_llm_failure_with_baseline_identity_candidates"
    ]
    if not guardrail_steps:
        raise SystemExit("baseline rate limit recovery guardrail trace missing")


def _assert_failed_rate_limited_run_can_resume_with_pending_outputs() -> None:
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeToolThenRateLimitedThenRecoverLlmClient()
        runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm)
        failed = runtime.start_run(
            {
                "goal": "runtime resumable rate limit test",
                "free_text": "continue a generic tool loop after rate limit",
                "session_id": "resume_rate_limit_session",
            }
        )

        _assert_equal(failed["status"], "failed", "resumable rate limit setup status")
        _assert_equal(failed["error"]["provider_error_code"], "HTTP_429_RATE_LIMITED", "resumable rate limit setup code")
        _assert_equal(failed["error"]["resume_available"], True, "resumable rate limit setup resume flag")
        pending = failed["state"].get("pending_tool_outputs") or []
        if len(pending) != 1 or pending[0].get("call_id") != "call_1":
            raise SystemExit("resumable rate limit setup should preserve pending tool output")

        resumed = runtime.reply(
            failed["run_id"],
            {
                "message": "retry after rate limit",
                "answers": [{"field": "retry_instruction", "value": "continue"}],
                "attachments": [],
                "session_id": "resume_rate_limit_session",
            },
        )

        _assert_equal(resumed["status"], "waiting_for_user", "resumed rate limit final status")
        AgentRunResponse.model_validate(resumed)
        _assert_equal(fake_llm.requests[2]["previous_response_id"], "resp_1", "resumed rate limit previous response id")
        request_items = fake_llm.requests[2]["input_items"]
        if not isinstance(request_items, list):
            raise SystemExit("resumed rate limit request should use pending input item list")
        request_text = json.dumps(request_items, ensure_ascii=False)
        if "function_call_output" not in request_text or "call_1" not in request_text:
            raise SystemExit("resumed rate limit request did not include preserved tool output")
        if "retry after rate limit" not in request_text:
            raise SystemExit("resumed rate limit request did not include user reply")
        observed_tools = [item["tool"] for item in resumed["state"]["observations"]]
        if observed_tools[:1] != ["load_closet_memory"]:
            raise SystemExit(f"resumed rate limit should preserve initial tool output first: {observed_tools}")
        if "plan_context_and_sources" not in observed_tools:
            raise SystemExit(f"resumed rate limit should preplan the latest user reply: {observed_tools}")
        if "search_web_candidates" not in observed_tools:
            raise SystemExit(f"resumed rate limit should continue search after retry: {observed_tools}")
        if observed_tools[-1] != "ask_missing_info":
            raise SystemExit(f"resumed rate limit should still end in missing-info state: {observed_tools}")
        user_reply_traces = [
            step for step in resumed["trace"] if step["step_type"] == "user_reply"
        ]
        if not user_reply_traces:
            raise SystemExit("resumed rate limit run should record user_reply trace")


def _assert_failed_rate_limited_run_can_continue_without_user_reply() -> None:
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeToolThenRateLimitedThenRecoverLlmClient()
        runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm)
        failed = runtime.start_run(
            {
                "goal": "runtime continue after rate limit test",
                "free_text": "continue a generic tool loop after rate limit",
                "session_id": "continue_rate_limit_session",
            }
        )

        _assert_equal(failed["status"], "failed", "continuable rate limit setup status")
        _assert_equal(failed["error"]["resume_available"], True, "continuable rate limit resume flag")
        continued = runtime.continue_run(failed["run_id"])
        _assert_equal(continued["status"], "waiting_for_user", "continued rate limit final status")
        AgentRunResponse.model_validate(continued)
        _assert_equal(fake_llm.requests[2]["previous_response_id"], "resp_1", "continued rate limit previous response id")
        request_items = fake_llm.requests[2]["input_items"]
        if not isinstance(request_items, list):
            raise SystemExit("continued rate limit request should use pending input item list")
        request_text = json.dumps(request_items, ensure_ascii=False)
        if "function_call_output" not in request_text or "call_1" not in request_text:
            raise SystemExit("continued rate limit request did not include preserved tool output")
        if "retry after rate limit" in request_text:
            raise SystemExit("continued rate limit request should not require a user reply")
        resume_steps = [
            step for step in continued["trace"] if step.get("name") == "resume_retryable_failed_run"
        ]
        if not resume_steps:
            raise SystemExit("continued rate limit run did not record resume trace")


def _assert_saved_closet_reference_preloads_durable_memory() -> None:
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeToolCallingLlmClient()
        runtime = _runtime(Path(temp_dir) / "runs.sqlite3", llm_client=fake_llm)
        runtime.store.upsert_closet_item(
            "saved_memory_session",
            {
                "id": "confirmed_tee",
                "status": "confirmed",
                "category": "t_shirt",
                "brand": "가까이 유니언즈",
                "name": "가까이 유니언즈 차콜 반팔티",
                "size_label": "L",
                "source_url": "https://example.com/confirmed-tee",
                "measurements": {"shoulder": 52, "chest": 60},
                "material": {},
                "preference_reasons": ["자주 입음"],
                "preference_signals": ["fit", "durability"],
                "memory_source": "user_confirmed_baseline_identity",
                "baseline_identity": {
                    "status": "memory_identity_available",
                    "can_identify_exact_product": True,
                    "database_write_policy": "promote_to_confirmed_closet_memory",
                },
            },
        )
        result = runtime.start_run(
            {
                "goal": "저장한 반팔티랑 비슷한 옷을 찾아줘",
                "free_text": "저장한 반팔티랑 비슷한 옷을 다시 찾아줘. 색은 꼭 같지 않아도 돼.",
                "session_id": "saved_memory_session",
            }
        )

    observations = result["state"].get("observations") or []
    if not observations or observations[0].get("tool") != "load_closet_memory":
        raise SystemExit(f"saved closet reference did not preload memory first: {[item.get('tool') for item in observations[:3]]}")
    load_result = observations[0].get("result") or {}
    _assert_equal(load_result.get("durable_memory_status"), "loaded", "saved memory preload status")
    if not load_result.get("durable_memory_match"):
        raise SystemExit("saved closet reference preload did not match durable memory")
    _assert_equal(
        load_result["closet_item"]["memory_source"],
        "durable_closet_memory",
        "saved memory preload closet source",
    )
    backend_recovery_trace = [
        step
        for step in result["trace"]
        if step.get("step_type") == "tool_call"
        and step.get("name") == "load_closet_memory"
        and (step.get("input") or {}).get("backend_recovery")
    ]
    if not backend_recovery_trace:
        raise SystemExit("saved closet reference preload did not record backend recovery tool call")


def _assert_multiple_tool_calls_are_executed_and_returned_together() -> None:
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeMultiToolCallLlmClient()
        runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm)
        result = runtime.start_run(
            {
                "goal": "Find candidates similar to my baseline tee",
                "free_text": "?쇨렇癒쇳듃 諛섑뙏?곗쿂???ъ쭏怨??륁씠 鍮꾩듂???룹쓣 李얠븘以?",
            }
        )

    _assert_equal(result["status"], "failed", "multi-tool final guardrail status")
    _assert_equal(
        result["error"]["code"],
        "FINAL_REPORT_TOOL_REQUIRED",
        "multi-tool final guardrail code",
    )
    _assert_equal(len(fake_llm.requests), 2, "multi-tool LLM request count")

    second_input = fake_llm.requests[1]["input_items"]
    if not isinstance(second_input, list):
        raise SystemExit("multi-tool second request should receive function_call_output list")
    function_outputs = [
        item for item in second_input if item.get("type") == "function_call_output"
    ]
    _assert_equal(len(function_outputs), 2, "multi-tool function output count")
    _assert_equal(
        [item["call_id"] for item in function_outputs],
        ["call_multi_closet", "call_multi_search"],
        "multi-tool function output call_id order",
    )
    output_payloads = [json.loads(item["output"]) for item in function_outputs]
    _assert_equal(output_payloads[0]["status"], "completed", "multi-tool closet output status")
    _assert_equal(output_payloads[1]["status"], "completed", "multi-tool search output status")
    _assert_equal(
        output_payloads[1]["provider"],
        "cached_harness_search",
        "multi-tool search provider",
    )

    observations = result["state"]["observations"]
    _assert_equal(
        [item["tool"] for item in observations[:2]],
        ["load_closet_memory", "search_web_candidates"],
        "multi-tool observation order",
    )
    _assert_equal(
        [item["call_id"] for item in observations[:2]],
        ["call_multi_closet", "call_multi_search"],
        "multi-tool observation call ids",
    )
    llm_steps = [step for step in result["trace"] if step["step_type"] == "llm_response"]
    _assert_equal(llm_steps[0]["output"]["tool_call_count"], 2, "multi-tool trace call count")
    tool_call_steps = [step for step in result["trace"] if step["step_type"] == "tool_call"]
    _assert_equal(
        [step["name"] for step in tool_call_steps[:2]],
        ["load_closet_memory", "search_web_candidates"],
        "multi-tool trace order",
    )
    _assert_equal(
        [step["input"]["call_id"] for step in tool_call_steps[:2]],
        ["call_multi_closet", "call_multi_search"],
        "multi-tool trace call ids",
    )
    tool_output_steps = [step for step in result["trace"] if step["step_type"] == "tool_output"]
    _assert_equal(
        [step["output"]["call_id"] for step in tool_output_steps[:2]],
        ["call_multi_closet", "call_multi_search"],
        "multi-tool output trace call ids",
    )
    multi_tool_event = AgentStateEventResponse.model_validate(
        {
            "status": result["status"],
            "run_id": result["run_id"],
            "trace_count": len(result["trace"]),
            "latest_trace": [tool_output_steps[1]],
            "missing_info": result["state"].get("missing_info", []),
        }
    )
    _assert_equal(
        multi_tool_event.latest_trace[0].output["call_id"],
        "call_multi_search",
        "multi-tool SSE schema call id",
    )
    guardrail_steps = [
        step
        for step in result["trace"]
        if step["name"] == "require_grounded_report_tool_before_final"
    ]
    if not guardrail_steps:
        raise SystemExit("multi-tool final guardrail trace was not recorded")


def _assert_parallel_candidate_tool_batch_is_recorded_and_ordered() -> None:
    def fetch_handler(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        candidate_key = str(arguments["candidate_key"])
        time.sleep(0.05)
        context.run.setdefault("state", {}).setdefault("live_products", {})[candidate_key] = {
            "candidate_key": candidate_key,
            "raw_input": {"fetch_status": "completed", "provider": "fake_parallel_fetch"},
            "normalized_spec": {"candidate_key": candidate_key, "name": f"Parallel {candidate_key}"},
        }
        context.run.setdefault("state", {}).setdefault("match_results", []).append(
            {
                "candidate_key": candidate_key,
                "recommendation": "need_more_info",
                "score": {"overall": 10, "size": 10, "material": 10, "price": 10, "confidence": 10},
                "missing_fields": [],
            }
        )
        return {
            "status": "completed",
            "candidate_key": candidate_key,
            "provider": "fake_parallel_fetch",
            "fetch_status": "completed",
        }

    registry = ToolRegistry(
        [
            ToolDefinition(
                name="fetch_product_page",
                description="Test-only candidate fetch tool.",
                parameters={
                    "type": "object",
                    "properties": {"candidate_key": {"type": "string"}},
                    "required": ["candidate_key"],
                    "additionalProperties": False,
                },
                handler=fetch_handler,
                timeout_seconds=1.0,
            )
        ]
    )
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeParallelCandidateBatchLlmClient()
        runtime = _runtime(
            Path(temp_dir) / "runs.sqlite3",
            llm_client=fake_llm,
            tool_registry=registry,
            max_turns=2,
        )
        result = runtime.start_run(
            {
                "goal": "parallel candidate fetch smoke",
                "free_text": "fetch two independent product candidates",
            }
        )

    _assert_equal(result["status"], "failed", "parallel batch final guardrail status")
    _assert_equal(
        result["error"]["code"],
        "FINAL_REPORT_TOOL_REQUIRED",
        "parallel batch final guardrail code",
    )
    runtime_traces = [
        step
        for step in result["trace"]
        if step["step_type"] == "runtime" and step["name"] == "execute_parallel_tool_batch"
    ]
    _assert_equal([step["status"] for step in runtime_traces], ["started", "completed"], "parallel runtime trace statuses")
    _assert_equal(runtime_traces[0]["input"]["call_count"], 2, "parallel runtime call count")
    _assert_equal(runtime_traces[0]["input"]["parallel_group"], "candidate_analysis", "parallel runtime group")
    observations = result["state"]["observations"]
    _assert_equal(
        [item["call_id"] for item in observations[:2]],
        ["call_parallel_a", "call_parallel_b"],
        "parallel observation call_id order",
    )
    _assert_equal(
        [item.get("parallel_group") for item in observations[:2]],
        ["candidate_analysis", "candidate_analysis"],
        "parallel observation group metadata",
    )
    live_products = result["state"].get("live_products") or {}
    if not {"candidate_a", "candidate_b"}.issubset(set(live_products)):
        raise SystemExit(f"parallel worker state merge failed: {sorted(live_products)}")
    merged_match_keys = {
        item.get("candidate_key")
        for item in result["state"].get("match_results", [])
        if isinstance(item, dict)
    }
    if not {"candidate_a", "candidate_b"}.issubset(merged_match_keys):
        raise SystemExit(f"parallel worker match_results merge failed: {sorted(merged_match_keys)}")
    second_input = fake_llm.requests[1]["input_items"]
    function_outputs = [
        item for item in second_input if item.get("type") == "function_call_output"
    ]
    _assert_equal(
        [item["call_id"] for item in function_outputs],
        ["call_parallel_a", "call_parallel_b"],
        "parallel function output call_id order",
    )


def _assert_backend_required_report_preserves_all_candidate_score() -> None:
    def score_handler(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        return {
            "status": "completed",
            "candidate_key": arguments.get("candidate_key"),
            "ranked_matches": [
                {
                    "candidate_key": "candidate_a",
                    "recommendation": "need_more_info",
                    "score": {"overall": 31, "size": 10, "material": 40, "price": 70, "confidence": 20},
                    "missing_fields": ["measurements.length"],
                }
            ],
        }

    def report_handler(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        return {
            "status": "completed",
            "candidate_key": arguments.get("candidate_key"),
            "grounded": True,
            "final_report": f"backend report for {arguments.get('candidate_key')}",
            "grounding_validation": {"valid": True},
        }

    registry = ToolRegistry(
        [
            ToolDefinition(
                name="score_candidate_match",
                description="Test-only score all tool.",
                parameters={
                    "type": "object",
                    "properties": {"candidate_key": {"type": "string"}},
                    "required": ["candidate_key"],
                    "additionalProperties": False,
                },
                handler=score_handler,
                timeout_seconds=1.0,
            ),
            ToolDefinition(
                name="write_purchase_report",
                description="Test-only report tool.",
                parameters={
                    "type": "object",
                    "properties": {"candidate_key": {"type": "string"}},
                    "required": ["candidate_key"],
                    "additionalProperties": False,
                },
                handler=report_handler,
                timeout_seconds=1.0,
            ),
        ]
    )
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeScoreAllThenFinalLlmClient()
        runtime = _runtime(
            Path(temp_dir) / "runs.sqlite3",
            llm_client=fake_llm,
            tool_registry=registry,
            max_turns=3,
        )
        result = runtime.start_run(
            {
                "goal": "score all report smoke",
                "free_text": "rank all candidates then report",
            }
        )

    _assert_equal(result["status"], "completed", "score all backend report final status")
    observations = result["state"]["observations"]
    _assert_equal(
        [item["tool"] for item in observations],
        ["score_candidate_match", "write_purchase_report"],
        "score all backend report tool path",
    )
    _assert_equal(
        observations[-1]["arguments"]["candidate_key"],
        "all",
        "backend required report should preserve all candidate score",
    )
    _assert_equal(
        result["final_report"]["summary"],
        "backend report for all",
        "backend required all report summary",
    )


def _assert_nested_chat_style_tool_call_arguments_are_preserved() -> None:
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeNestedChatStyleToolCallLlmClient()
        runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm)
        result = runtime.start_run(
            {
                "goal": "Find candidates similar to musinsa wide denim pants",
                "free_text": "臾댁떊?ъ뿉??????대뱶 ?곕떂 ?ъ툩 M ?륁씠 醫뗭븘.",
            }
        )

    _assert_equal(result["status"], "failed", "nested chat-style final guardrail status")
    _assert_equal(
        result["error"]["code"],
        "FINAL_REPORT_TOOL_REQUIRED",
        "nested chat-style final guardrail code",
    )
    _assert_equal(len(fake_llm.requests), 2, "nested chat-style LLM request count")
    observations = result["state"]["observations"]
    _assert_equal(observations[0]["tool"], "search_web_candidates", "nested chat-style tool")
    _assert_equal(
        observations[0]["call_id"],
        "call_nested_search",
        "nested chat-style call id",
    )
    _assert_equal(
        observations[0]["arguments"]["query"],
        "臾댁떊????대뱶 ?곕떂 ?ъ툩 M",
        "nested chat-style arguments query",
    )
    if not observations[0]["result"].get("query"):
        raise SystemExit("nested chat-style search result query missing")

    tool_call_steps = [
        step
        for step in result["trace"]
        if step["step_type"] == "tool_call" and step["name"] == "search_web_candidates"
    ]
    if not tool_call_steps:
        raise SystemExit("nested chat-style tool_call trace was not recorded")
    _assert_equal(
        tool_call_steps[0]["input"]["arguments"]["query"],
        "臾댁떊????대뱶 ?곕떂 ?ъ툩 M",
        "nested chat-style trace arguments",
    )

    second_input = fake_llm.requests[1]["input_items"]
    if not isinstance(second_input, list):
        raise SystemExit("nested chat-style second request should receive function_call_output list")
    function_outputs = [
        item for item in second_input if item.get("type") == "function_call_output"
    ]
    _assert_equal(len(function_outputs), 1, "nested chat-style function output count")
    _assert_equal(
        function_outputs[0]["call_id"],
        "call_nested_search",
        "nested chat-style function output call id",
    )


def _assert_reasoning_context_items_are_forwarded_with_tool_outputs() -> None:
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeReasoningContextToolCallLlmClient()
        runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm)
        result = runtime.start_run(
            {
                "goal": "Find candidates similar to unions short sleeve tee",
                "free_text": "L ?ъ씠利덇퀬 ?뤾낵 ?ъ쭏??醫뗭븘??鍮꾩듂???룹쓣 李얘퀬 ?띠뼱.",
            }
        )

    _assert_equal(result["status"], "failed", "reasoning context final guardrail status")
    _assert_equal(
        result["error"]["code"],
        "FINAL_REPORT_TOOL_REQUIRED",
        "reasoning context final guardrail code",
    )
    _assert_equal(len(fake_llm.requests), 2, "reasoning context LLM request count")

    observations = result["state"]["observations"]
    _assert_equal(observations[0]["tool"], "search_web_candidates", "reasoning context tool")
    _assert_equal(
        observations[0]["arguments"]["query"],
        "unions short sleeve tee L cotton",
        "reasoning context search query",
    )

    second_input = fake_llm.requests[1]["input_items"]
    if not isinstance(second_input, list):
        raise SystemExit("reasoning context second request should receive input item list")
    reasoning_items = [
        item for item in second_input if item.get("type") == "reasoning"
    ]
    _assert_equal(len(reasoning_items), 1, "reasoning context item count")
    _assert_equal(reasoning_items[0]["id"], "rs_fitcheck_1", "reasoning context item id")
    _assert_equal(
        reasoning_items[0]["encrypted_content"],
        "enc_test_reasoning",
        "reasoning context encrypted content",
    )
    function_outputs = [
        item for item in second_input if item.get("type") == "function_call_output"
    ]
    _assert_equal(len(function_outputs), 1, "reasoning context function output count")
    _assert_equal(
        function_outputs[0]["call_id"],
        "call_reasoning_search",
        "reasoning context function output call id",
    )

    llm_steps = [step for step in result["trace"] if step["step_type"] == "llm_response"]
    _assert_equal(
        llm_steps[0]["output"]["reasoning_context_item_count"],
        1,
        "reasoning context trace count",
    )


def _assert_optional_parser_accepts_free_text_only_tool_call() -> None:
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeOptionalParserFreeTextOnlyLlmClient()
        runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm)
        result = runtime.start_run(
            {
                "free_text": "I bought a testbrand short sleeve t-shirt size L cotton 100 and want similar items.",
            }
        )

    _assert_equal(result["status"], "failed", "optional parser final guardrail status")
    _assert_equal(
        result["error"]["code"],
        "FINAL_REPORT_TOOL_REQUIRED",
        "optional parser final guardrail code",
    )
    _assert_equal(len(fake_llm.requests), 3, "optional parser LLM request count")

    observations = result["state"]["observations"]
    _assert_equal(
        [item["tool"] for item in observations[:2]],
        ["parse_user_goal", "search_web_candidates"],
        "optional parser observation order",
    )
    parse_result = observations[0]["result"]
    _assert_equal(parse_result["status"], "completed", "optional parser result status")
    _assert_equal(parse_result["has_free_text"], True, "optional parser free text flag")
    if not isinstance(parse_result["liked_item"], dict):
        raise SystemExit("optional parser liked item missing")
    _assert_equal(parse_result["liked_item"].get("size_label"), "L", "optional parser size")
    if observations[0]["arguments"].get("goal"):
        raise SystemExit("optional parser test unexpectedly supplied goal argument")

    second_input = fake_llm.requests[1]["input_items"]
    if not isinstance(second_input, list):
        raise SystemExit("optional parser second request should receive function output list")
    function_outputs = [
        item for item in second_input if item.get("type") == "function_call_output"
    ]
    _assert_equal(len(function_outputs), 1, "optional parser function output count")
    _assert_equal(
        function_outputs[0]["call_id"],
        "call_parse_1",
        "optional parser function output call id",
    )
    output_payload = json.loads(function_outputs[0]["output"])
    _assert_equal(output_payload["has_free_text"], True, "optional parser output payload")

    llm_steps = [step for step in result["trace"] if step["step_type"] == "llm_response"]
    _assert_equal(llm_steps[0]["output"]["tool_call_count"], 1, "optional parser first call count")
    tool_call_steps = [step for step in result["trace"] if step["step_type"] == "tool_call"]
    _assert_equal(
        [step["name"] for step in tool_call_steps[:2]],
        ["parse_user_goal", "search_web_candidates"],
        "optional parser trace order",
    )


def _assert_tool_calling_loop_with_fake_llm() -> None:
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeToolCallingLlmClient()
        runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm)
        result = runtime.start_run(
            {
                "goal": "?닿? 醫뗭븘?섎뒗 諛섑뙏?곗? 鍮꾩듂???꾨낫瑜?李얠븘 鍮꾧탳?댁쨾",
                "free_text": "?쇨렇癒쇳듃 諛섑뙏???꾨낫瑜?湲곗? ?룰낵 鍮꾧탳?댁쨾",
            }
        )

        _assert_equal(result["status"], "completed", "fake loop status")
        AgentRunResponse.model_validate(result)
        _assert_equal(result["llm"]["external_api_called"], True, "external call trace")
        tool_outputs = [
            step
            for step in result["trace"]
            if step["step_type"] == "tool_output"
        ]
        observed_tools = [step["name"] for step in tool_outputs]
        _assert_equal(
            observed_tools,
            [
                "load_closet_memory",
                "search_web_candidates",
                "fetch_product_page",
                "classify_product_assets",
                "extract_size_material_info",
                "normalize_product_spec",
                "score_candidate_match",
                "write_purchase_report",
            ],
            "model-selected tool outputs",
        )
        observations = result["state"]["observations"]
        _assert_equal(len(observations), 8, "stored observations")
        search_observation = [
            item for item in observations if item["tool"] == "search_web_candidates"
        ][0]
        _assert_equal(search_observation["provider"], "cached_harness_search", "search observation provider")
        _assert_equal(search_observation["external_api_called"], False, "search observation external flag")
        _assert_equal(search_observation["tool_timeout_seconds"], 20, "search observation tool timeout")
        _assert_equal(search_observation["tool_risk"], "external_search_io", "search observation tool risk")
        _assert_equal(search_observation["provider_retry_count"], 0, "search observation provider retry count")
        _assert_equal(search_observation["retry_badges"][0]["status"], "not_retried", "search observation retry badge status")
        _assert_equal(search_observation["retry_badges"][0]["provider"], "cached_harness_search", "search observation retry badge provider")
        if not isinstance(search_observation.get("duration_ms"), int):
            raise SystemExit("search observation duration_ms was not recorded")
        search_call_trace = [step for step in result["trace"] if step["step_type"] == "tool_call" and step["name"] == "search_web_candidates"][0]
        _assert_equal(search_call_trace["input"]["tool_timeout_seconds"], 20, "search call trace tool timeout")
        _assert_equal(search_call_trace["input"]["tool_risk"], "external_search_io", "search call trace tool risk")
        search_trace = [step for step in tool_outputs if step["name"] == "search_web_candidates"][0]
        _assert_equal(
            search_call_trace["input"]["call_id"],
            search_observation["call_id"],
            "search call trace call id",
        )
        _assert_equal(
            search_trace["output"]["call_id"],
            search_observation["call_id"],
            "search output trace call id",
        )
        search_event = AgentStateEventResponse.model_validate(
            {
                "status": result["status"],
                "run_id": result["run_id"],
                "trace_count": len(result["trace"]),
                "latest_trace": [search_trace],
                "missing_info": result["state"].get("missing_info", []),
            }
        )
        _assert_equal(
            search_event.latest_trace[0].output["call_id"],
            search_observation["call_id"],
            "search SSE schema call id",
        )
        _assert_equal(search_trace["output"]["provider"], "cached_harness_search", "search trace provider")
        _assert_equal(search_trace["output"]["external_api_called"], False, "search trace external flag")
        _assert_equal(search_trace["output"]["timeout_seconds"], None, "search trace provider timeout")
        _assert_equal(search_trace["output"]["retry_count"], 0, "search trace provider retry count")
        _assert_equal(search_trace["output"]["retry_badges"][0]["status"], "not_retried", "search trace retry badge status")
        _assert_equal(search_trace["output"]["tool_timeout_seconds"], 20, "search trace tool timeout")
        _assert_equal(search_trace["output"]["tool_risk"], "external_search_io", "search trace tool risk")
        if not isinstance(search_trace["output"].get("duration_ms"), int):
            raise SystemExit("search trace duration_ms was not recorded")
        ocr_trace = [step for step in tool_outputs if step["name"] == "extract_size_material_info"][0]
        _assert_equal(ocr_trace["output"]["provider"], "fixture_ocr_boundary", "OCR trace provider")
        _assert_equal(ocr_trace["output"]["provider_mode"], "fixture_result_boundary", "OCR trace provider mode")
        _assert_equal(ocr_trace["output"]["retry_count"], 0, "OCR trace provider retry count")
        _assert_equal(ocr_trace["output"]["retry_badges"][0]["status"], "not_retried", "OCR trace retry badge status")
        _assert_equal(ocr_trace["output"]["tool_timeout_seconds"], 30, "OCR trace tool timeout")
        _assert_equal(ocr_trace["output"]["tool_risk"], "ocr_or_vision_io", "OCR trace tool risk")
        score_observation = [
            item for item in observations if item["tool"] == "score_candidate_match"
        ][0]
        if score_observation["result"].get("score_deferred"):
            raise SystemExit("score_candidate_match ran before enrichment completed")
        _assert_equal(
            result["final_report"].get("grounding"),
            "write_purchase_report_tool",
            "final report grounding source",
        )
        if not result["final_report"].get("report_type"):
            raise SystemExit("final report did not preserve write_purchase_report report_type")
        if not result["final_report"].get("summary"):
            raise SystemExit("final report was not set from write_purchase_report output")
        _assert_no_user_visible_english_fragments(
            result["final_report"]["summary"],
            "fake loop final report summary",
        )
        if "tool 寃곌낵 湲곗?" not in result["final_report"].get("llm_final_text", ""):
            raise SystemExit("final LLM text was not retained as acknowledgement text")
        _assert_equal(result["final_report"].get("grounded"), True, "final report grounded flag")
        AgentStateEventResponse.model_validate(
            {
                "status": result["status"],
                "run_id": result["run_id"],
                "trace_count": len(result["trace"]),
                "latest_trace": result["trace"][-1:],
                "final_report": result["final_report"],
                "missing_info": result["state"].get("missing_info", []),
            }
        )


def _assert_single_candidate_report_after_multi_candidate_search_is_aggregated() -> None:
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeSingleReportAfterMultiCandidateSearchLlmClient()
        runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm)
        _write_multi_candidate_cached_web_result(runtime.web_result_path)
        result = runtime.start_run(
            {
                "goal": "여러 후보를 비교해서 비슷한 반팔티를 추천해줘",
                "free_text": "차콜 반팔티와 비슷한 핏과 소재의 후보를 여러 개 비교해줘",
            }
        )

        _assert_equal(result["status"], "completed", "single report aggregation status")
        if not str(result["final_report"].get("grounding") or "").startswith("write_purchase_report_all"):
            raise SystemExit(f"single report aggregation did not finish with all-candidate report: {result['final_report'].get('grounding')}")
        _assert_equal(result["final_report"].get("report_type"), "shortlist", "single report aggregation type")
        audit = result["final_report"].get("candidate_audit") or {}
        if not audit.get("excluded_candidates"):
            raise SystemExit("all-candidate report did not expose excluded candidate audit")
        if int(audit.get("searched_candidate_count") or 0) < 4:
            raise SystemExit("candidate audit did not preserve searched candidate count")
        if not result["final_report"].get("excluded_candidates"):
            raise SystemExit("final_report did not copy excluded_candidates metadata")
        observations = result["state"]["observations"]
        score_all = [
            item
            for item in observations
            if item["tool"] == "score_candidate_match"
            and str(item.get("arguments", {}).get("candidate_key") or "").lower() == "all"
        ]
        if not score_all:
            raise SystemExit("single-candidate report was not rescored as all candidates")
        report_all = [
            item
            for item in observations
            if item["tool"] == "write_purchase_report"
            and str(item.get("arguments", {}).get("candidate_key") or "").lower() == "all"
        ]
        if not report_all:
            raise SystemExit("single-candidate report was not rewritten as all-candidate shortlist")
        guardrails = [
            step
            for step in result["trace"]
            if step.get("step_type") == "guardrail"
            and str(step.get("name") or "").startswith("complete_all_report")
        ]
        if not guardrails:
            raise SystemExit("single-candidate aggregation guardrail trace missing")


def _assert_agent_run_evidence_preserves_sdk_like_pairing() -> None:
    from app.routes import agent as agent_routes

    saved_input_cost = os.environ.get("FITCHECK_LLM_INPUT_COST_PER_1M")
    saved_output_cost = os.environ.get("FITCHECK_LLM_OUTPUT_COST_PER_1M")
    saved_cache_read_cost = os.environ.get("FITCHECK_LLM_CACHE_READ_INPUT_COST_PER_1M")
    saved_agent_run_store = agent_routes.agent_run_store
    os.environ["FITCHECK_LLM_INPUT_COST_PER_1M"] = "1"
    os.environ["FITCHECK_LLM_OUTPUT_COST_PER_1M"] = "2"
    os.environ["FITCHECK_LLM_CACHE_READ_INPUT_COST_PER_1M"] = "0.25"
    with TemporaryDirectory() as temp_dir:
        try:
            fake_llm = FakeMultiToolCallLlmClient()
            runtime = _runtime(Path(temp_dir) / "runs.sqlite3", llm_client=fake_llm)
            result = runtime.start_run(
                {
                    "session_id": "session_transcript_test",
                    "goal": "segment tee compare",
                    "free_text": "compare the current top with a segment tee",
                }
            )
            agent_routes.agent_run_store = lambda: runtime.store
            evidence = AgentRunEvidenceResponse.model_validate(agent_routes._agent_run_evidence_for_run(result))
            transcript = AgentClientTranscriptResponse.model_validate(
                agent_routes._agent_client_transcript_for_run(result)
            )
            session_info = AgentRunSessionInfoResponse.model_validate(
                agent_routes._agent_session_info_for_run(result)
            )
            sdk_events = AgentSdkEventsResponse.model_validate(agent_routes._agent_sdk_events_for_run(result))
            sdk_events_after_first = AgentSdkEventsResponse.model_validate(
                agent_routes._agent_sdk_events_for_run(result, after=1)
            )
            sdk_events_terminal_delta = AgentSdkEventsResponse.model_validate(
                agent_routes._agent_sdk_events_for_run(result, after=len(result["trace"]) - 1)
            )
            sdk_events_caught_up = AgentSdkEventsResponse.model_validate(
                agent_routes._agent_sdk_events_for_run(result, after=len(result["trace"]))
            )
        finally:
            agent_routes.agent_run_store = saved_agent_run_store
            _restore_env("FITCHECK_LLM_INPUT_COST_PER_1M", saved_input_cost)
            _restore_env("FITCHECK_LLM_OUTPUT_COST_PER_1M", saved_output_cost)
            _restore_env("FITCHECK_LLM_CACHE_READ_INPUT_COST_PER_1M", saved_cache_read_cost)
        _assert_equal(evidence.status, "completed", "run evidence status")
        _assert_equal(evidence.run_id, result["run_id"], "run evidence id")
        _assert_equal(evidence.run_status, result["status"], "run evidence run status")
        _assert_equal(evidence.counts["trace"], len(result["trace"]), "run evidence trace count")
        _assert_equal(
            evidence.counts["observations"],
            len(result["state"]["observations"]),
            "run evidence observation count",
        )
        _assert_equal(evidence.counts["tool_calls"], 2, "run evidence tool call count")
        _assert_equal(evidence.counts["tool_outputs"], 2, "run evidence tool output count")
        _assert_equal(evidence.counts["hook_lifecycle"], 4, "run evidence hook lifecycle count")
        _assert_equal(evidence.counts["context_budget"], 2, "run evidence context budget count")
        _assert_equal(
            evidence.counts["normalized_events"],
            len(result["trace"]),
            "run evidence normalized event count",
        )
        normalized_events = evidence.sdk_like["normalized_events"]
        _assert_equal(
            [item["seq"] for item in normalized_events],
            list(range(1, len(result["trace"]) + 1)),
            "run evidence normalized event sequence",
        )
        _assert_equal(
            [item["payload"]["step_index"] for item in normalized_events],
            list(range(1, len(result["trace"]) + 1)),
            "run evidence normalized event payload step indexes",
        )
        _assert_equal(
            [record.input_kind for record in evidence.context_budget],
            ["initial_model_input", "pending_tool_outputs"],
            "run evidence context budget input kinds",
        )
        if evidence.context_budget[0].measurement != "estimated":
            raise SystemExit("run evidence context budget should be explicitly estimated")
        if evidence.context_budget[0].estimated_total_tokens <= 0:
            raise SystemExit("run evidence context budget should estimate positive token count")
        _assert_equal(
            evidence.context_budget[1].pending_tool_output_count,
            2,
            "run evidence context budget pending output count",
        )
        _assert_equal(
            [record.hook_event for record in evidence.hook_lifecycle[:2]],
            ["PreToolUse", "PostToolUse"],
            "run evidence hook lifecycle first tool events",
        )
        _assert_equal(
            evidence.hook_lifecycle[0].mode,
            "record_only",
            "run evidence hook lifecycle mode",
        )
        pairing = evidence.sdk_like["tool_result_pairing"]
        _assert_equal(pairing["status"], "paired", "run evidence tool pairing status")
        _assert_equal(pairing["missing_results"], [], "run evidence missing tool results")
        _assert_equal(pairing["orphan_results"], [], "run evidence orphan tool results")
        _assert_equal(
            evidence.sdk_like["provider_request_ids"],
            ["req_multi_1", "req_multi_2"],
            "run evidence provider request ids",
        )
        usage_accounting = evidence.sdk_like["usage_accounting"]
        _assert_equal(usage_accounting["request_count"], 2, "run evidence usage request count")
        _assert_equal(usage_accounting["provider_input_tokens"], 180, "run evidence input tokens")
        _assert_equal(usage_accounting["provider_output_tokens"], 50, "run evidence output tokens")
        _assert_equal(usage_accounting["provider_total_tokens"], 230, "run evidence total tokens")
        _assert_equal(
            usage_accounting["provider_cache_read_input_tokens"],
            10,
            "run evidence cache read tokens",
        )
        _assert_equal(usage_accounting["cost_status"], "calculated", "run evidence cost status")
        _assert_equal(usage_accounting["cost_usd"], 0.0002725, "run evidence cost")
        _assert_equal(
            evidence.tool_use_summary["type"],
            "streamlined_tool_use_summary",
            "run evidence tool summary type",
        )
        _assert_equal(
            evidence.tool_use_summary["tool_summary"],
            evidence.tool_use_summary["summary"],
            "run evidence SDK tool_summary alias",
        )
        if evidence.tool_use_summary["tool_uses"] <= 0:
            raise SystemExit("run evidence tool summary should count tool use")
        if not evidence.compact_tool_use_messages:
            raise SystemExit("run evidence should expose compact tool-use messages")
        if not any(item.get("type") == "tool_use_summary" for item in evidence.compact_tool_use_messages):
            raise SystemExit("run evidence compact messages should include tool_use_summary rows")
        _assert_equal(transcript.status, "completed", "client transcript status")
        _assert_equal(transcript.session_id, "session_transcript_test", "client transcript session id")
        _assert_equal(session_info.status, "completed", "session info status")
        _assert_equal(session_info.source, "fitcheck_agent_session_info", "session info source")
        _assert_equal(session_info.run_id, result["run_id"], "session info run id")
        _assert_equal(session_info.session_id, "session_transcript_test", "session info session id")
        _assert_equal(session_info.latest_event_seq, len(result["trace"]), "session info latest event seq")
        _assert_equal(session_info.counts["trace"], len(result["trace"]), "session info trace count")
        _assert_equal(session_info.counts["tool_calls"], 2, "session info tool call count")
        if session_info.resumability["waiting_for_user"]:
            raise SystemExit("completed session info should not wait for user")
        if "sdk_events" not in session_info.source_endpoints:
            raise SystemExit("session info source endpoints omitted SDK events")
        _assert_equal(
            transcript.status_line["provider_total_tokens"],
            230,
            "client transcript token status line",
        )
        _assert_equal(
            transcript.evidence_summary["normalized_events"],
            len(result["trace"]),
            "client transcript normalized event count",
        )
        _assert_equal(
            transcript.tool_use_summary["type"],
            "streamlined_tool_use_summary",
            "client transcript tool summary type",
        )
        _assert_equal(
            transcript.tool_use_summary["tool_summary"],
            transcript.tool_use_summary["summary"],
            "client transcript SDK tool_summary alias",
        )
        if not transcript.messages:
            raise SystemExit("client transcript should include streamlined messages")
        first_message = transcript.messages[0]
        _assert_equal(first_message.type, "streamlined_text", "client transcript first message type")
        _assert_equal(first_message.session_id, "session_transcript_test", "client transcript message session id")
        if not first_message.uuid or not first_message.uuid.startswith(f"{result['run_id']}:client:"):
            raise SystemExit("client transcript message uuid was not stable")
        if "client_transcript" not in transcript.source_endpoints:
            raise SystemExit("client transcript source endpoints omitted the transcript route")
        if "session_info" not in transcript.source_endpoints:
            raise SystemExit("client transcript source endpoints omitted the session-info route")
        if "sdk_events" not in transcript.source_endpoints:
            raise SystemExit("client transcript source endpoints omitted the SDK events route")
        _assert_equal(sdk_events.status, "completed", "SDK events status")
        _assert_equal(sdk_events.run_id, result["run_id"], "SDK events run id")
        _assert_equal(sdk_events.source, "fitcheck_agent_sdk_event_projection", "SDK events source")
        _assert_equal(sdk_events.next_after, len(result["trace"]), "SDK events next cursor")
        _assert_equal(
            sdk_events.projected_event_count,
            len(sdk_events.events),
            "SDK events projected event count",
        )
        _assert_equal(
            sdk_events.cursor_policy["cursor_basis"],
            "raw_source_event_seq",
            "SDK events cursor basis",
        )
        _assert_equal(
            sdk_events.cursor_policy["cursor_semantics"],
            "exclusive",
            "SDK events cursor semantics",
        )
        _assert_equal(
            sdk_events.terminal_event_seq,
            len(result["trace"]),
            "SDK events terminal seq",
        )
        _assert_equal(sdk_events.terminal_event_included, True, "SDK events terminal included")
        _assert_equal(sdk_events.final_output_included, False, "SDK events no-report final output flag")
        if sdk_events_after_first.raw_event_count != len(result["trace"]) - 1:
            raise SystemExit("SDK events after cursor did not filter raw events")
        _assert_equal(
            sdk_events_after_first.final_output_included,
            False,
            "SDK events after-first no-report final output flag",
        )
        _assert_equal(
            sdk_events_terminal_delta.raw_event_count,
            1,
            "SDK events terminal delta raw count",
        )
        _assert_equal(
            sdk_events_terminal_delta.next_after,
            len(result["trace"]),
            "SDK events terminal delta next cursor",
        )
        _assert_equal(
            sdk_events_terminal_delta.terminal_event_included,
            True,
            "SDK events terminal delta terminal included",
        )
        _assert_equal(
            sdk_events_terminal_delta.final_output_included,
            False,
            "SDK events terminal delta no-report final output flag",
        )
        _assert_equal(sdk_events_caught_up.raw_event_count, 0, "SDK events caught-up raw count")
        _assert_equal(sdk_events_caught_up.events, [], "SDK events caught-up events")
        _assert_equal(
            sdk_events_caught_up.next_after,
            len(result["trace"]),
            "SDK events caught-up cursor",
        )
        _assert_equal(
            sdk_events_caught_up.terminal_event_included,
            False,
            "SDK events caught-up terminal flag",
        )
        _assert_equal(
            sdk_events_caught_up.final_output_included,
            False,
            "SDK events caught-up final output flag",
        )
        sdk_event_types = [item.get("type") for item in sdk_events.events]
        if "model_response" not in sdk_event_types:
            raise SystemExit("SDK events should project model_response items")
        if "tool_progress" not in sdk_event_types:
            raise SystemExit("SDK events should project tool_progress items")
        if not any(
            item.get("type") == "tool_progress" and item.get("backend_owned") is True
            for item in sdk_events.events
        ):
            raise SystemExit("SDK events should mark backend-executed tools as backend_owned")
        if not any(
            item.get("subtype") == "session_state_changed" and item.get("state") == "idle"
            for item in sdk_events.events
        ):
            raise SystemExit("SDK events should project terminal session_state_changed idle")
        if "session_info" not in sdk_events.source_endpoints:
            raise SystemExit("SDK events source endpoints omitted session-info")
        if evidence.sdk_like["sdk_events_after_cursor_supported"] is not True:
            raise SystemExit("run evidence should advertise SDK events cursor support")
        if not any(item.get("type") == "tool_progress" for item in evidence.sdk_like["sdk_events"]):
            raise SystemExit("run evidence SDK-like projection should include tool_progress")
        item_types = [item["type"] for item in evidence.sdk_like["new_items"]]
        if "tool_call" not in item_types or "tool_result" not in item_types:
            raise SystemExit("run evidence should expose SDK-like tool_call and tool_result items")

        for expected in [
            "size labels such as S/M/L/XL",
            "weak search hints only",
            "garment measurement evidence",
            "body/model measurements",
        ]:
            if expected not in FITCHECK_AGENT_INSTRUCTIONS:
                raise SystemExit(f"agent instructions missing size hallucination guardrail: {expected}")


def _assert_context_compaction_summary_is_recorded_when_budget_threshold_is_crossed() -> None:
    from app.routes.agent import _agent_run_evidence_for_run

    saved_threshold = os.environ.get("FITCHECK_CONTEXT_COMPACTION_ESTIMATED_TOKEN_THRESHOLD")
    try:
        os.environ["FITCHECK_CONTEXT_COMPACTION_ESTIMATED_TOKEN_THRESHOLD"] = "1"
        with TemporaryDirectory() as temp_dir:
            fake_llm = FakeMultiToolCallLlmClient()
            runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm)
            result = runtime.start_run(
                {
                    "goal": "segment tee compare",
                    "free_text": "compare the current top with a segment tee",
                }
            )
        evidence = AgentRunEvidenceResponse.model_validate(_agent_run_evidence_for_run(result))
    finally:
        if saved_threshold is None:
            os.environ.pop("FITCHECK_CONTEXT_COMPACTION_ESTIMATED_TOKEN_THRESHOLD", None)
        else:
            os.environ["FITCHECK_CONTEXT_COMPACTION_ESTIMATED_TOKEN_THRESHOLD"] = saved_threshold

    if evidence.counts["context_compactions"] < 1:
        raise SystemExit("context compaction summary was not recorded")
    compaction = evidence.context_compactions[0]
    _assert_equal(
        compaction.mode,
        "deterministic_evidence_summary",
        "context compaction summary mode",
    )
    _assert_equal(compaction.status, "applied", "context compaction applied status")
    _assert_equal(compaction.threshold_estimated_tokens, 1, "context compaction threshold")
    if not getattr(compaction, "compacted_estimated_input_tokens", None):
        raise SystemExit("context compaction did not record compacted token estimate")
    preserved = compaction.preserved
    if not preserved.get("latest_user_input", {}).get("goal"):
        raise SystemExit("context compaction did not preserve latest user input")
    if not isinstance(preserved.get("latest_observations"), list):
        raise SystemExit("context compaction did not preserve observation summaries")
    grounding = preserved.get("grounding_constraints") or {}
    _assert_equal(
        grounding.get("facts_must_be_trace_grounded"),
        True,
        "context compaction grounding constraint",
    )
    if not fake_llm.requests:
        raise SystemExit("context compaction test did not capture LLM requests")
    first_input = fake_llm.requests[0]["input_items"]
    if "context_compaction" not in str(first_input):
        raise SystemExit("context compaction summary was not applied to the LLM input")


def _assert_response_context_reset_compacts_pending_tool_outputs() -> None:
    saved_provider_threshold = os.environ.get("FITCHECK_RESPONSE_CONTEXT_RESET_PROVIDER_TOKEN_THRESHOLD")
    saved_estimated_threshold = os.environ.get("FITCHECK_RESPONSE_CONTEXT_RESET_ESTIMATED_TOKEN_THRESHOLD")
    os.environ["FITCHECK_RESPONSE_CONTEXT_RESET_PROVIDER_TOKEN_THRESHOLD"] = "50"
    os.environ["FITCHECK_RESPONSE_CONTEXT_RESET_ESTIMATED_TOKEN_THRESHOLD"] = "0"
    try:
        with TemporaryDirectory() as temp_dir:
            fake_llm = FakeMultiToolCallLlmClient()
            runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm)
            result = runtime.start_run(
                {
                    "goal": "segment tee compare",
                    "free_text": "compare the current top with a segment tee",
                }
            )
    finally:
        _restore_env("FITCHECK_RESPONSE_CONTEXT_RESET_PROVIDER_TOKEN_THRESHOLD", saved_provider_threshold)
        _restore_env("FITCHECK_RESPONSE_CONTEXT_RESET_ESTIMATED_TOKEN_THRESHOLD", saved_estimated_threshold)

    if result["status"] not in {"completed", "failed"}:
        raise SystemExit(f"context reset run reached unexpected status: {result['status']}")
    if len(fake_llm.requests) < 2:
        raise SystemExit("context reset test did not perform second LLM request")
    second_request = fake_llm.requests[1]
    _assert_equal(second_request["previous_response_id"], None, "context reset previous response id")
    input_items = second_request["input_items"]
    if not isinstance(input_items, list) or not input_items:
        raise SystemExit(f"context reset input should be compact user item list: {input_items}")
    content = str(input_items[0].get("content") or "")
    if "response_context_reset" not in content or "pending_tool_output_summary_without_previous_response_id" not in content:
        raise SystemExit(f"context reset compact payload missing marker: {content[:500]}")
    if "function_call_output" not in content or "load_closet_memory" not in content or "search_web_candidates" not in content:
        raise SystemExit(f"context reset compact payload missing tool summaries: {content[:800]}")
    budgets = result["state"].get("context_budget") or []
    if not any(item.get("response_context_reset_applied") for item in budgets):
        raise SystemExit("context budget did not record response_context_reset_applied")
    if not any(step.get("name") == "reset_response_context" for step in result.get("trace") or []):
        raise SystemExit("context reset trace step was not recorded")


def _assert_agent_sse_event_ids_are_monotonic_and_resumable() -> None:
    from app.routes.agent import _sse_event_id, _trace_count_from_event_id

    event_payload = {
        "status": "running",
        "run_id": "run_sse_test",
        "trace_count": 7,
        "event_sequence": 7,
    }
    event_id = _sse_event_id("run_sse_test", event_payload)
    _assert_equal(event_id, "run_sse_test:trace:7", "SSE monotonic event id")
    _assert_equal(
        _trace_count_from_event_id(event_id, "run_sse_test"),
        7,
        "SSE current event cursor parse",
    )
    _assert_equal(
        _trace_count_from_event_id("run_sse_test:completed:5", "run_sse_test"),
        5,
        "SSE legacy event cursor parse",
    )
    _assert_equal(
        _trace_count_from_event_id("other_run:trace:9", "run_sse_test"),
        0,
        "SSE foreign event cursor ignored",
    )
    event = AgentStateEventResponse.model_validate(
        {
            "status": "running",
            "run_id": "run_sse_test",
            "event_id": event_id,
            "event_sequence": 7,
            "trace_count": 7,
            "trace_delta": [
                {
                    "step_index": 7,
                    "step_type": "tool_output",
                    "name": "x",
                    "status": "completed",
                    "input": {},
                    "output": {},
                    "created_at": "2026-01-01T00:00:00+00:00",
                }
            ],
        }
    )
    _assert_equal(event.event_sequence, 7, "SSE event sequence schema")


def _assert_raw_input_can_drive_direct_search_without_parser() -> None:
    raw_text = "I bought musinsa wide denim pants size M and want similar pants."
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeRawInputDirectSearchLlmClient()
        runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm)
        result = runtime.start_run(
            {
                "goal": raw_text,
                "free_text": raw_text,
            }
        )

        _assert_equal(result["status"], "completed", "raw input direct search status")
        AgentRunResponse.model_validate(result)
        first_request = fake_llm.requests[0]
        if raw_text not in str(first_request["input_items"]):
            raise SystemExit("raw input was not passed to the LLM request")
        if "Do not treat parse_user_goal as a mandatory first step" not in first_request["instructions"]:
            raise SystemExit("agent instructions did not mark parse_user_goal optional")
        if "call search_web_candidates directly" not in first_request["instructions"]:
            raise SystemExit("agent instructions did not allow direct raw-input search")
        if "Do not require parse_user_goal before search" not in str(first_request["input_items"]):
            raise SystemExit("initial model input did not carry direct search behavior guidance")
        if "parse_user_goal" not in first_request["tool_names"]:
            raise SystemExit("parse_user_goal should remain available as an optional tool")

        observations = result["state"]["observations"]
        observed_tools = [item["tool"] for item in observations]
        if "parse_user_goal" in observed_tools:
            raise SystemExit("raw input direct search should not require parse_user_goal")
        _assert_equal(observed_tools[0], "search_web_candidates", "raw input first selected tool")
        _assert_equal(
            observed_tools,
            [
                "search_web_candidates",
                "load_closet_memory",
                "enrich_closet_item_from_web",
                "fetch_product_page",
                "classify_product_assets",
                "extract_size_material_info",
                "normalize_product_spec",
                "score_candidate_match",
                "write_purchase_report",
            ],
            "raw input direct tool path",
        )

        search_observation = observations[0]
        _assert_equal(
            search_observation["arguments"]["query"],
            "musinsa wide denim pants M design",
            "raw input direct search query",
        )
        _assert_equal(
            search_observation["result"]["provider"],
            "cached_harness_search",
            "raw input direct search provider",
        )
        closet_observation = [
            item for item in observations if item["tool"] == "load_closet_memory"
        ][0]
        if not closet_observation["result"].get("closet_item"):
            raise SystemExit("raw input closet item missing")
        _assert_equal(
            closet_observation["result"]["closet_item"]["memory_source"],
            "user_text_partial_memory",
            "raw input closet memory source",
        )
        tool_call_names = [
            step["name"]
            for step in result["trace"]
            if step["step_type"] == "tool_call"
        ]
        _assert_equal(tool_call_names[0], "search_web_candidates", "raw input first tool trace")
        report_observation = [
            item for item in observations if item["tool"] == "write_purchase_report"
        ][0]
        _assert_equal(
            report_observation["result"]["status"],
            "completed",
            "raw input description-only report status",
        )
        if result["state"].get("missing_info"):
            raise SystemExit("raw input direct search should not ask for missing baseline evidence before producing a grounded description-based report")


def _assert_invalid_candidate_key_is_structured_tool_failure() -> None:
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeInvalidCandidateThenCorrectedLlmClient()
        runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm)
        result = runtime.start_run(
            {
                "goal": "?닿? 醫뗭븘?섎뒗 諛섑뙏?곗? 鍮꾩듂???꾨낫瑜?李얠븘 鍮꾧탳?댁쨾",
                "free_text": "?쇨렇癒쇳듃 諛섑뙏???꾨낫瑜?湲곗? ?룰낵 鍮꾧탳?댁쨾",
            }
        )

        _assert_equal(result["status"], "completed", "invalid candidate recovery final status")
        observations = result["state"]["observations"]
        bad_fetch = [
            item
            for item in observations
            if item["tool"] == "fetch_product_page"
            and item["arguments"].get("candidate_key") == "missing_candidate"
        ][0]
        _assert_equal(
            bad_fetch["result"]["error"]["code"],
            "CANDIDATE_NOT_FOUND",
            "invalid candidate structured error code",
        )
        if "web_test_candidate" not in bad_fetch["result"].get("available_candidate_keys", []):
            raise SystemExit("invalid candidate failure did not expose available candidate keys")
        suggestions = bad_fetch["result"].get("next_tool_suggestions", [])
        if not any(
            item.get("arguments", {}).get("candidate_key") == "web_test_candidate"
            for item in suggestions
        ):
            raise SystemExit("invalid candidate failure did not suggest available candidate key")
        if result.get("error"):
            raise SystemExit("invalid candidate recovery should not leave top-level run error")
        if "CANDIDATE_NOT_FOUND" not in str(fake_llm.requests[2]["input_items"]):
            raise SystemExit("invalid candidate tool output was not returned to the next LLM request")
        generic_execution_failures = [
            item
            for item in observations
            if (item.get("result", {}).get("error") or {}).get("code") == "TOOL_EXECUTION_FAILED"
        ]
        if generic_execution_failures:
            raise SystemExit("invalid candidate key should not become TOOL_EXECUTION_FAILED")
        corrected_fetches = [
            item
            for item in observations
            if item["tool"] == "fetch_product_page"
            and item["arguments"].get("candidate_key") == "web_test_candidate"
            and item["result"].get("status") == "completed"
        ]
        if not corrected_fetches:
            raise SystemExit("invalid candidate recovery did not fetch corrected candidate")


def _assert_user_reply_resumes_agent_with_reply_context() -> None:
    reply_text = "?좊땲?쇰툕由우??먯꽌 ???꾨뱶?곌퀬, ?ъ씠利덈뒗 XL, 硫?80% ?ъ쭏怨??됰꼮???륁씠 醫뗭븘."
    attachment = {
        "id": "img_reply_label",
        "filename": "hoodie-label.png",
        "media_type": "image/png",
        "size_bytes": 128,
        "url": "/outputs/uploads/img_reply_label.png",
        "role": "current_clothing_photo",
        "source": "user_upload",
    }
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeWaitingThenReplySearchLlmClient()
        runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm)
        waiting = runtime.start_run(
            {
                "goal": "鍮꾩듂???룹쓣 李얘퀬 ?띠???湲곗? ???ㅻ챸??議곌툑 ??以꾧쾶.",
                "free_text": "",
            }
        )

        _assert_equal(waiting["status"], "waiting_for_user", "reply setup waiting status")
        if not waiting["state"].get("missing_info"):
            raise SystemExit("waiting run did not expose missing_info")
        first_closet = waiting["state"]["observations"][0]["result"]["closet_item"]
        _assert_equal(first_closet["category"], "unknown", "initial vague closet category")
        waiting.setdefault("state", {})["search_constraints"] = {
            "query_hint": "stale vague query",
            "category": "unknown",
        }
        runtime.store.update_run(waiting)

        resumed = runtime.reply(
            waiting["run_id"],
            {
                "message": reply_text,
                "answers": [{"field": "liked_item_notes", "value": reply_text}],
                "attachments": [attachment],
                "session_id": "reply_test_session",
            },
        )

        _assert_equal(resumed["status"], "completed", "reply resumed final status")
        AgentRunResponse.model_validate(resumed)
        stale_questions = [
            item
            for item in resumed["state"].get("missing_info", [])
            if item.get("candidate_key") == "all" and item.get("field") == "unresolved_evidence"
        ]
        if stale_questions:
            raise SystemExit("reply should clear stale all-candidate missing_info")
        if resumed["state"].get("search_constraints", {}).get("query_hint") == "stale vague query":
            raise SystemExit("reply should clear stale search_constraints before resumed tool selection")

        reply_request = fake_llm.requests[2]
        _assert_equal(
            reply_request["previous_response_id"],
            "resp_wait",
            "reply previous response id",
        )
        request_text = str(reply_request["input_items"])
        if reply_text not in request_text:
            raise SystemExit("reply message was not passed to resumed LLM request")
        if "img_reply_label" not in request_text:
            raise SystemExit("reply attachment was not passed to resumed LLM request")
        if "user_replies" not in request_text:
            raise SystemExit("resumed LLM request did not include user_replies")

        user_reply_traces = [
            step for step in resumed["trace"] if step["step_type"] == "user_reply"
        ]
        if not user_reply_traces:
            raise SystemExit("reply trace was not recorded")
        if reply_text not in str(user_reply_traces[-1]["input"]):
            raise SystemExit("reply trace did not include reply message")

        observations = resumed["state"]["observations"]
        observed_tools = [item["tool"] for item in observations]
        for required_tool in [
            "load_closet_memory",
            "ask_missing_info",
            "plan_context_and_sources",
            "inspect_uploaded_images",
            "search_web_candidates",
            "enrich_closet_item_from_web",
            "fetch_product_page",
            "classify_product_assets",
            "extract_size_material_info",
            "normalize_product_spec",
            "score_candidate_match",
            "write_purchase_report",
        ]:
            if required_tool not in observed_tools:
                raise SystemExit(f"reply resumed tool path missing {required_tool}: {observed_tools}")
        if observed_tools[-1] != "write_purchase_report":
            raise SystemExit(f"reply resumed tool path should end with grounded report: {observed_tools}")

        upload_observation = [
            item for item in observations if item["tool"] == "inspect_uploaded_images"
        ][0]
        _assert_equal(upload_observation["result"]["attachment_count"], 1, "reply upload attachment count")
        _assert_equal(
            upload_observation["result"]["attachments"][0]["id"],
            "img_reply_label",
            "reply upload attachment id",
        )

        search_observation = [
            item
            for item in observations
            if item["tool"] == "search_web_candidates"
            and item.get("arguments", {}).get("query") == "union bridge hoodie XL cotton"
        ][0]
        _assert_equal(
            search_observation["arguments"]["query"],
            "union bridge hoodie XL cotton",
            "reply direct search query",
        )

        second_closet = [
            item for item in observations if item["tool"] == "load_closet_memory"
        ][-1]["result"]["closet_item"]
        if not second_closet:
            raise SystemExit("reply closet item missing")
        report_observation = [
            item for item in observations if item["tool"] == "write_purchase_report"
        ][0]
        _assert_equal(
            report_observation["result"]["status"],
            "completed",
            "reply report completes from tool evidence",
        )
        if resumed["state"].get("missing_info"):
            raise SystemExit("reply should not ask for user input after tool-grounded report")


def _assert_sparse_web_search_reply_uses_accumulated_context() -> None:
    initial_text = (
        "\ub0b4\uac00 \uc790\uc8fc \uc785\ub294 \ucc28\ucf5c \ubc18\ud314\ud2f0\ub791 "
        "\ube44\uc2b7\ud55c \uc637\uc744 \ucc3e\uc544\uc918. "
        "\ud54f\uc740 \uc5ec\uc720\ub86d\uace0 \uc624\ub798 \uc785\uc5b4\ub3c4 \uad1c\ucc2e\uc558\uc5b4. "
        "\uc0c9\uc740 \uaf2d \uac19\uc9c0 \uc54a\uc544\ub3c4 \ub3fc."
    )
    brand_size_reply = "\uac00\uae4c\uc774 \uc720\ub2c8\uc5b8\uc988\uc758 \uc0ac\uc774\uc988 L \uc778\ub370, \ucc3e\uc544\ubd10"
    web_search_reply = "\uc6f9\uc11c\uce58\ub85c \ucc3e\uc544\ubd10"

    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeWaitingThenSparseWebSearchReplyLlmClient()
        runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm)
        waiting = runtime.start_run(
            {
                "goal": initial_text,
                "free_text": initial_text,
                "session_id": "sparse_web_search_session",
            }
        )

        _assert_equal(waiting["status"], "waiting_for_user", "sparse web search setup status")
        first_resume = runtime.reply(
            waiting["run_id"],
            {
                "message": brand_size_reply,
                "answers": [{"field": "liked_item_notes", "value": brand_size_reply}],
                "attachments": [],
                "session_id": "sparse_web_search_session",
            },
        )
        _assert_equal(first_resume["status"], "waiting_for_user", "brand-size reply waiting status")

        final_resume = runtime.reply(
            waiting["run_id"],
            {
                "message": web_search_reply,
                "answers": [{"field": "search_instruction", "value": web_search_reply}],
                "attachments": [],
                "session_id": "sparse_web_search_session",
            },
        )

        _assert_equal(final_resume["status"], "waiting_for_user", "sparse web search final status")
        AgentRunResponse.model_validate(final_resume)

        second_reply_request = fake_llm.requests[3]
        _assert_equal(
            second_reply_request["previous_response_id"],
            "resp_wait_2",
            "sparse web search previous response id",
        )
        request_items = second_reply_request["input_items"]
        request_text = json.dumps(request_items, ensure_ascii=False)
        if "function_call_output" not in request_text or "call_wait_2" not in request_text:
            raise SystemExit("second reply did not include pending waiting tool output")
        if web_search_reply not in request_text:
            raise SystemExit("second reply did not include new web search instruction")

        observations = final_resume["state"]["observations"]
        search_observation = [
            item
            for item in observations
            if item["tool"] == "search_web_candidates"
            and item.get("arguments", {}).get("query") == web_search_reply
        ][0]
        _assert_equal(
            search_observation["arguments"]["query"],
            web_search_reply,
            "sparse model query preserved in observation arguments",
        )
        actual_query = str(search_observation["result"].get("query") or "")
        for expected in ["\ucc28\ucf5c", "\ubc18\ud314", "\uac00\uae4c\uc774 \uc720\ub2c8\uc5b8\uc988", "L"]:
            if expected not in actual_query:
                raise SystemExit(f"accumulated context missing from backend search query: {expected!r} in {actual_query!r}")

        constraints = final_resume["state"].get("search_constraints") or {}
        _assert_equal(constraints.get("brand"), "\uac00\uae4c\uc774 \uc720\ub2c8\uc5b8\uc988", "context brand constraint")
        if constraints.get("category") not in {"t_shirt", "unknown"}:
            raise SystemExit(f"context category constraint should remain searchable, not blocking: {constraints}")
        _assert_equal(constraints.get("category_query_label"), "\ubc18\ud314 \ud2f0\uc154\uce20", "context category label")
        _assert_equal(constraints.get("color_query_label"), "\ucc28\ucf5c", "context color label")
        _assert_equal(constraints.get("size_label"), "L", "context size constraint")

        snapshot = final_resume["state"].get("context_snapshot") or {}
        if not snapshot.get("needs_search_refresh"):
            raise SystemExit("context snapshot did not preserve latest web-search intent")
        liked_item = snapshot.get("liked_item") or {}
        _assert_equal(liked_item.get("brand"), "\uac00\uae4c\uc774 \uc720\ub2c8\uc5b8\uc988", "snapshot brand")
        if liked_item.get("category") not in {"t_shirt", "unknown"}:
            raise SystemExit(f"snapshot category should remain searchable, not blocking: {liked_item}")
        _assert_equal(liked_item.get("color_query_label"), "\ucc28\ucf5c", "snapshot color")


def _assert_latest_reply_context_is_backend_preplanned_before_model_tool_choice() -> None:
    initial_text = (
        "\ub0b4\uac00 \uc790\uc8fc \uc785\ub294 \ucc28\ucf5c \ubc18\ud314\ud2f0\ub791 "
        "\ube44\uc2b7\ud55c \uc637\uc744 \ucc3e\uc544\uc918. "
        "\ud54f\uc740 \uc5ec\uc720\ub86d\uace0 \uc624\ub798 \uc785\uc5b4\ub3c4 \uad1c\ucc2e\uc558\uc5b4."
    )
    reply = "\uac00\uae4c\uc774 \uc720\ub2c8\uc5b8\uc988\uc758 \uc0ac\uc774\uc988 L \uc778\ub370, \uc6f9\uc11c\uce58\ub85c \ucc3e\uc544\ubd10"

    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeWaitingThenDirectSearchWithoutPlanLlmClient()
        runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm)
        waiting = runtime.start_run(
            {
                "goal": initial_text,
                "free_text": initial_text,
                "session_id": "backend_preplan_reply_session",
            }
        )
        _assert_equal(waiting["status"], "waiting_for_user", "backend preplan setup status")

        resumed = runtime.reply(
            waiting["run_id"],
            {
                "message": reply,
                "answers": [{"field": "liked_item_notes", "value": reply}],
                "attachments": [],
                "session_id": "backend_preplan_reply_session",
            },
        )
        AgentRunResponse.model_validate(resumed)

        latest_reply_step = max(
            int(step.get("step_index") or 0)
            for step in resumed["trace"]
            if step.get("step_type") == "user_reply"
        )
        first_llm_after_reply = min(
            int(step.get("step_index") or 0)
            for step in resumed["trace"]
            if step.get("step_type") == "llm_response" and int(step.get("step_index") or 0) > latest_reply_step
        )
        backend_tools_before_model = [
            step
            for step in resumed["trace"]
            if step.get("step_type") == "tool_call"
            and latest_reply_step < int(step.get("step_index") or 0) < first_llm_after_reply
        ]
        tool_names = [str(step.get("name") or "") for step in backend_tools_before_model]
        if not tool_names or tool_names[0] != "plan_context_and_sources":
            raise SystemExit(f"latest reply was not backend-preplanned before model tool choice: {tool_names}")
        if "enrich_closet_item_from_web" not in tool_names:
            raise SystemExit(f"latest reply backend preplan did not run baseline lookup before model: {tool_names}")
        preplan_input = backend_tools_before_model[0].get("input") or {}
        _assert_equal(
            preplan_input.get("reason"),
            "latest_user_reply_requires_context_resolution",
            "backend preplan reason",
        )
        if not preplan_input.get("backend_recovery"):
            raise SystemExit("backend preplan tool call was not marked as backend-owned recovery")

        plan_observations = [
            item
            for item in resumed["state"]["observations"]
            if item.get("tool") == "plan_context_and_sources"
        ]
        if not plan_observations:
            raise SystemExit("backend preplan observation missing")
        constraints = (plan_observations[-1].get("result") or {}).get("search_constraints") or {}
        _assert_equal(constraints.get("brand"), "\uac00\uae4c\uc774 \uc720\ub2c8\uc5b8\uc988", "backend preplan brand")
        _assert_equal(constraints.get("size_label"), "L", "backend preplan size")
        guidance_steps = [
            step
            for step in resumed["trace"]
            if step.get("step_type") == "tool_guidance"
            and step.get("name") == "continue_latest_reply_context_followup"
        ]
        if not guidance_steps:
            raise SystemExit("backend preplan did not publish context follow-up guidance")


def _assert_sparse_web_search_reply_planner_recipe_suggests_search() -> None:
    initial_text = (
        "\ub0b4\uac00 \uc790\uc8fc \uc785\ub294 \ucc28\ucf5c \ubc18\ud314\ud2f0\ub791 "
        "\ube44\uc2b7\ud55c \uc637\uc744 \ucc3e\uc544\uc918. "
        "\ud54f\uc740 \uc5ec\uc720\ub86d\uace0 \uc624\ub798 \uc785\uc5b4\ub3c4 \uad1c\ucc2e\uc558\uc5b4."
    )
    web_search_reply = "\uc6f9\uc11c\uce58\ub85c \ub2e4\uc2dc \ucc3e\uc544\ubd10"

    with TemporaryDirectory() as temp_dir:
        fake_llm = FakePlanThenSparseWebSearchReplyLlmClient()
        runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm)
        waiting = runtime.start_run(
            {
                "goal": initial_text,
                "free_text": initial_text,
                "session_id": "planner_web_search_session",
            }
        )
        _assert_equal(waiting["status"], "waiting_for_user", "planner web search setup status")

        resumed = runtime.reply(
            waiting["run_id"],
            {
                "message": web_search_reply,
                "answers": [{"field": "search_instruction", "value": web_search_reply}],
                "attachments": [],
                "session_id": "planner_web_search_session",
            },
        )

        _assert_equal(resumed["status"], "waiting_for_user", "planner web search final status")
        AgentRunResponse.model_validate(resumed)

        observations = resumed["state"]["observations"]
        plan_observation = [
            item for item in observations if item["tool"] == "plan_context_and_sources"
        ][0]
        plan_result = plan_observation["result"]
        recipe_ids = [item.get("id") for item in plan_result.get("applied_recipes", [])]
        if "explicit_web_search_retry" not in recipe_ids:
            raise SystemExit("planner web search reply did not apply explicit_web_search_retry recipe")
        suggestions = plan_result.get("next_tool_suggestions") or []
        if not any(
            item.get("tool") == "search_web_candidates"
            and (item.get("arguments") or {}).get("refresh_web") is True
            and item.get("reason") == "recipe_explicit_web_search_retry"
            for item in suggestions
        ):
            raise SystemExit("planner recipe did not suggest refreshed search_web_candidates")
        constraints = plan_result.get("search_constraints") or {}
        _assert_equal(constraints.get("goal_mode"), "similar_clothing_discovery", "planner web search goal mode")
        if not constraints.get("needs_search_refresh"):
            raise SystemExit("planner recipe did not preserve needs_search_refresh")

        search_observation = [
            item for item in observations if item["tool"] == "search_web_candidates"
        ][-1]
        _assert_equal(
            search_observation["arguments"]["refresh_web"],
            True,
            "planner web search model refresh flag",
        )
        actual_query = str(search_observation["result"].get("query") or "")
        for expected in ["\ucc28\ucf5c", "\ubc18\ud314", "\uc624\ubc84\ud54f", "\ud0c4\ud0c4\ud55c", "\ub514\uc790\uc778"]:
            if expected not in actual_query:
                raise SystemExit(f"planner web search accumulated query missing {expected!r}: {actual_query!r}")
        if not any(
            step.get("step_type") == "tool_call" and step.get("name") == "plan_context_and_sources"
            for step in resumed["trace"]
        ):
            raise SystemExit("planner web search trace missing plan_context_and_sources tool call")
        if not any(
            step.get("step_type") == "tool_call" and step.get("name") == "search_web_candidates"
            for step in resumed["trace"]
        ):
            raise SystemExit("planner web search trace missing search_web_candidates tool call")


def _assert_runtime_reply_rejects_session_mismatch() -> None:
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeWaitingThenReplySearchLlmClient()
        runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm)
        waiting = runtime.start_run(
            {
                "goal": "鍮꾩듂???룹쓣 李얘퀬 ?띠???湲곗? ???ㅻ챸??議곌툑 ??以꾧쾶.",
                "free_text": "",
                "session_id": "owner_session",
            }
        )

        _assert_equal(waiting["status"], "waiting_for_user", "session mismatch setup status")
        request_count_before = len(fake_llm.requests)
        missing_session_result = runtime.reply(
            waiting["run_id"],
            {
                "message": "session ?녿뒗 吏곸젒 runtime reply",
                "answers": [],
                "attachments": [],
            },
        )
        _assert_equal(
            missing_session_result["status"],
            "not_found",
            "runtime reply missing session status",
        )
        _assert_equal(
            len(fake_llm.requests),
            request_count_before,
            "runtime reply missing session LLM calls",
        )
        result = runtime.reply(
            waiting["run_id"],
            {
                "message": "?ㅻⅨ session?먯꽌 蹂대궦 reply",
                "answers": [],
                "attachments": [],
                "session_id": "other_session",
            },
        )
        _assert_equal(result["status"], "not_found", "runtime reply session mismatch status")
        _assert_equal(len(fake_llm.requests), request_count_before, "runtime reply session mismatch LLM calls")
        stored = runtime.get_run(waiting["run_id"])
        _assert_equal(stored["status"], "waiting_for_user", "session mismatch stored status")
        if stored["input"].get("user_replies"):
            raise SystemExit("session mismatch reply should not mutate input.user_replies")
        if any(step["step_type"] == "user_reply" for step in stored["trace"]):
            raise SystemExit("session mismatch reply should not append user_reply trace")


def _assert_empty_runtime_reply_rejected_before_llm() -> None:
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeWaitingThenReplySearchLlmClient()
        runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm)
        waiting = runtime.start_run(
            {
                "goal": "鍮꾩듂???룹쓣 李얘퀬 ?띠???湲곗? ???ㅻ챸??議곌툑 ??以꾧쾶.",
                "free_text": "",
                "session_id": "owner_session",
            }
        )

        _assert_equal(waiting["status"], "waiting_for_user", "empty reply setup status")
        request_count_before = len(fake_llm.requests)
        result = runtime.reply(
            waiting["run_id"],
            {
                "message": "  ",
                "answers": [],
                "attachments": [],
                "session_id": "owner_session",
            },
        )
        _assert_equal(result["status"], "invalid_request", "empty runtime reply status")
        _assert_equal(
            result["error"]["code"],
            "REPLY_USER_INPUT_REQUIRED",
            "empty runtime reply error code",
        )
        _assert_equal(len(fake_llm.requests), request_count_before, "empty runtime reply LLM calls")
        stored = runtime.get_run(waiting["run_id"])
        _assert_equal(stored["status"], "waiting_for_user", "empty runtime reply stored status")
        if stored["input"].get("user_replies"):
            raise SystemExit("empty runtime reply should not mutate input.user_replies")
        if any(step["step_type"] == "user_reply" for step in stored["trace"]):
            raise SystemExit("empty runtime reply should not append user_reply trace")


def _assert_reply_requires_new_report_after_latest_user_input() -> None:
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeCompletedThenFinalWithoutReplyToolsLlmClient()
        runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm)
        completed = runtime.start_run(
            {
                "goal": "?닿? 醫뗭븘?섎뒗 諛섑뙏?곗? 鍮꾩듂???꾨낫瑜?李얠븘 鍮꾧탳?댁쨾",
                "free_text": "?쇨렇癒쇳듃 諛섑뙏???꾨낫瑜?湲곗? ?룰낵 鍮꾧탳?댁쨾",
            }
        )

        _assert_equal(completed["status"], "completed", "completed setup status")
        if not completed.get("final_report"):
            raise SystemExit("completed setup did not create final_report")
        completed_at = completed.get("completed_at")
        if not completed_at:
            raise SystemExit("completed setup did not set completed_at")

        replied = runtime.reply(
            completed["run_id"],
            {
                "message": "?대쾲?먮뒗 ??吏㏃? 湲곗옣 履쎌쑝濡??ㅼ떆 李얠븘以?",
                "answers": [],
                "attachments": [],
                "session_id": "reply_requires_new_report",
            },
        )

        _assert_equal(replied["status"], "failed", "reply stale report final status")
        _assert_equal(
            replied["error"]["code"],
            "FINAL_REPORT_TOOL_REQUIRED",
            "reply stale report guardrail code",
        )
        _assert_equal(replied.get("final_report"), None, "reply should clear old final_report")
        _assert_equal(replied.get("completed_at"), None, "reply should clear old completed_at")
        reply_steps = [
            step for step in replied["trace"] if step["step_type"] == "user_reply"
        ]
        if not reply_steps:
            raise SystemExit("reply stale report test did not record user_reply")
        latest_reply_step = reply_steps[-1]["step_index"]
        report_after_reply = [
            step
            for step in replied["trace"]
            if step["step_type"] == "tool_output"
            and step["name"] == "write_purchase_report"
            and step["step_index"] > latest_reply_step
        ]
        if report_after_reply:
            raise SystemExit("reply stale report test unexpectedly produced a new report tool output")
        guardrail_steps = [
            step
            for step in replied["trace"]
            if step["name"] == "require_grounded_report_tool_before_final"
        ]
        if not guardrail_steps:
            raise SystemExit("reply stale report guardrail trace was not recorded")


def _assert_malformed_json_tool_arguments_are_parse_failure() -> None:
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeMalformedJsonToolArgsLlmClient()
        runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm)
        result = runtime.start_run(
            {
                "goal": "湲곗? ?룰낵 鍮꾧탳?댁꽌 ?꾨낫瑜?異붿쿇?댁쨾",
                "free_text": "?쇨렇癒쇳듃 諛섑뙏???꾨낫瑜?湲곗? ?룰낵 鍮꾧탳?댁쨾",
            }
        )

        _assert_equal(result["status"], "failed", "malformed JSON tool arg final status")
        _assert_equal(
            result["error"]["code"],
            "FINAL_REPORT_TOOL_REQUIRED",
            "malformed JSON tool arg final guardrail",
        )
        observation = result["state"]["observations"][0]
        _assert_equal(observation["tool"], "fetch_product_page", "malformed JSON observation tool")
        _assert_equal(observation["arguments"], {}, "malformed JSON parsed arguments fallback")
        _assert_equal(observation["result"]["status"], "failed", "malformed JSON result status")
        _assert_equal(
            observation["result"]["error"]["code"],
            "TOOL_ARGUMENT_JSON_PARSE_FAILED",
            "malformed JSON error code",
        )
        _assert_equal(
            observation["result"]["validation_errors"][0]["code"],
            "invalid_json",
            "malformed JSON validation code",
        )
        if not observation["result"].get("raw_arguments_excerpt"):
            raise SystemExit("malformed JSON result did not include raw argument excerpt")
        tool_call_trace = [
            step
            for step in result["trace"]
            if step["step_type"] == "tool_call" and step["name"] == "fetch_product_page"
        ][0]
        _assert_equal(
            tool_call_trace["input"]["arguments_parse_error"]["code"],
            "invalid_json",
            "malformed JSON trace parse error",
        )
        tool_output_trace = [
            step
            for step in result["trace"]
            if step["step_type"] == "tool_output" and step["name"] == "fetch_product_page"
        ][0]
        _assert_equal(tool_output_trace["status"], "failed", "malformed JSON trace status")
        _assert_equal(
            tool_output_trace["output"]["error"]["code"],
            "TOOL_ARGUMENT_JSON_PARSE_FAILED",
            "malformed JSON trace error code",
        )


def _assert_malformed_tool_arguments_are_observed() -> None:
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeMalformedToolArgsLlmClient()
        runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm)
        result = runtime.start_run(
            {
                "goal": "湲곗? ?룰낵 鍮꾧탳?댁꽌 ?꾨낫瑜?異붿쿇?댁쨾",
                "free_text": "?쇨렇癒쇳듃 諛섑뙏???꾨낫瑜?湲곗? ?룰낵 鍮꾧탳?댁쨾",
            }
        )

        _assert_equal(result["status"], "failed", "malformed tool arg final status")
        _assert_equal(
            result["error"]["code"],
            "FINAL_REPORT_TOOL_REQUIRED",
            "malformed tool arg final guardrail",
        )
        observations = result["state"]["observations"]
        _assert_equal(len(observations), 1, "malformed tool arg observation count")
        observation = observations[0]
        _assert_equal(observation["tool"], "fetch_product_page", "malformed tool arg observation tool")
        _assert_equal(observation["result"]["status"], "failed", "malformed tool arg result status")
        _assert_equal(
            observation["result"]["error"]["code"],
            "TOOL_ARGUMENT_VALIDATION_FAILED",
            "malformed tool arg error code",
        )
        _assert_equal(
            observation["result"]["validation_errors"][0]["code"],
            "missing_required",
            "malformed tool arg validation detail",
        )
        _assert_equal(
            observation["result"]["schema_strictness"]["backend_validation"],
            "strict_json_schema_subset",
            "malformed tool arg schema strictness",
        )
        failed_tool_trace = [
            step
            for step in result["trace"]
            if step["step_type"] == "tool_output" and step["name"] == "fetch_product_page"
        ][0]
        _assert_equal(failed_tool_trace["status"], "failed", "malformed tool arg trace status")
        _assert_equal(
            failed_tool_trace["output"]["validation_errors"][0]["code"],
            "missing_required",
            "malformed tool arg trace validation detail",
        )


def _assert_unknown_tool_call_is_structured_failure() -> None:
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeUnknownToolLlmClient()
        runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm)
        result = runtime.start_run(
            {
                "goal": "湲곗? ?룰낵 鍮꾧탳?댁꽌 ?꾨낫瑜?異붿쿇?댁쨾",
                "free_text": "?쇨렇癒쇳듃 諛섑뙏???꾨낫瑜?湲곗? ?룰낵 鍮꾧탳?댁쨾",
            }
        )

        _assert_equal(result["status"], "failed", "unknown tool final status")
        _assert_equal(
            result["error"]["code"],
            "FINAL_REPORT_TOOL_REQUIRED",
            "unknown tool final guardrail",
        )
        observation = result["state"]["observations"][0]
        _assert_equal(observation["tool"], "invent_product_specs", "unknown tool observation tool")
        _assert_equal(observation["result"]["status"], "failed", "unknown tool result status")
        _assert_equal(
            observation["result"]["error"]["code"],
            "UNKNOWN_TOOL",
            "unknown tool error code",
        )
        if "search_web_candidates" not in observation["result"]["error"].get("available_tools", []):
            raise SystemExit("unknown tool did not include available tools")
        unknown_tool_trace = [
            step
            for step in result["trace"]
            if step["step_type"] == "tool_output" and step["name"] == "invent_product_specs"
        ][0]
        _assert_equal(unknown_tool_trace["status"], "failed", "unknown tool trace status")
        _assert_equal(
            unknown_tool_trace["output"]["error"]["code"],
            "UNKNOWN_TOOL",
            "unknown tool trace error code",
        )


def _assert_blocked_user_url_tool_call_is_structured_failure() -> None:
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeBlockedUserUrlLlmClient()
        runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm)
        result = runtime.start_run(
            {
                "goal": "candidate URL boundary",
                "free_text": "try unsafe local candidate URL",
            }
        )

        _assert_equal(result["status"], "failed", "blocked user URL final status")
        _assert_equal(
            result["error"]["code"],
            "REPEATED_FAILED_TOOL_CALL",
            "blocked user URL repeated failure code",
        )
        _assert_equal(result["error"]["tool"], "register_user_candidate_url", "blocked user URL repeated tool")
        observations = result["state"]["observations"]
        first_result = observations[0]["result"]
        _assert_equal(first_result["status"], "failed", "blocked user URL result status")
        _assert_equal(
            first_result["error"]["code"],
            "BLOCKED_USER_CANDIDATE_URL",
            "blocked user URL error code",
        )
        _assert_equal(
            first_result["provider_error_code"],
            "BLOCKED_USER_CANDIDATE_URL",
            "blocked user URL provider error code",
        )
        if result["state"].get("live_products") or result["state"].get("user_candidate_keys"):
            raise SystemExit("blocked user URL should not register live product state")


def _assert_repeated_failed_tool_call_stops_run() -> None:
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeRepeatedMalformedToolArgsLlmClient()
        runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm)
        result = runtime.start_run(
            {
                "goal": "湲곗? ?룰낵 鍮꾧탳?댁꽌 ?꾨낫瑜?異붿쿇?댁쨾",
                "free_text": "?쇨렇癒쇳듃 諛섑뙏???꾨낫瑜?湲곗? ?룰낵 鍮꾧탳?댁쨾",
            }
        )

        _assert_equal(result["status"], "failed", "repeated failed tool status")
        _assert_equal(
            result["error"]["code"],
            "REPEATED_FAILED_TOOL_CALL",
            "repeated failed tool guardrail code",
        )
        _assert_equal(result["error"]["tool"], "fetch_product_page", "repeated failed tool name")
        _assert_equal(
            result["error"]["error_code"],
            "TOOL_ARGUMENT_VALIDATION_FAILED",
            "repeated failed tool error code",
        )
        _assert_equal(result["error"]["repeat_count"], 2, "repeated failed tool count")
        observations = result["state"]["observations"]
        _assert_equal(len(observations), 2, "repeated failed tool observation count")
        guardrail_steps = [
            step
            for step in result["trace"]
            if step["name"] == "stop_repeated_failed_tool_call"
        ]
        if not guardrail_steps:
            raise SystemExit("repeated failed tool guardrail trace was not recorded")


def _assert_tool_timeout_is_observed_and_stops_on_repeat() -> None:
    def slow_handler(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        time.sleep(0.2)
        return {"status": "completed", "unexpected": True}

    slow_registry = ToolRegistry(
        [
            ToolDefinition(
                name="slow_backend_tool",
                description="Test-only backend tool that sleeps beyond its runtime timeout.",
                parameters={
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
                handler=slow_handler,
                timeout_seconds=0.05,
            )
        ]
    )
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeRepeatedSlowToolLlmClient()
        output_dir = Path(temp_dir)
        web_result_path = output_dir / "web_candidates_match_result.json"
        keyword_result_path = output_dir / "web_keyword_probe" / "keyword_web_probe_result.json"
        _write_test_cached_web_result(web_result_path)
        keyword_result_path.parent.mkdir(parents=True, exist_ok=True)
        keyword_result_path.write_text("{}", encoding="utf-8")
        runtime = FitCheckAgentRuntime(
            run_store_path=output_dir / "runs.sqlite3",
            web_result_path=web_result_path,
            keyword_result_path=keyword_result_path,
            refresh_web_candidates=lambda: _write_test_cached_web_result(web_result_path),
            llm_client=fake_llm,
            tool_registry=slow_registry,
            max_turns=4,
        )
        result = runtime.start_run(
            {
                "goal": "timeout smoke",
                "free_text": "force the model to call a slow backend tool twice",
            }
        )

        _assert_equal(result["status"], "failed", "tool timeout final status")
        _assert_equal(
            result["error"]["code"],
            "REPEATED_FAILED_TOOL_CALL",
            "tool timeout repeated guardrail code",
        )
        _assert_equal(result["error"]["error_code"], "TOOL_TIMEOUT_EXCEEDED", "tool timeout error code")
        observations = result["state"]["observations"]
        _assert_equal(len(observations), 2, "tool timeout observation count")
        first_result = observations[0]["result"]
        _assert_equal(first_result["status"], "failed", "tool timeout result status")
        _assert_equal(first_result["error"]["code"], "TOOL_TIMEOUT_EXCEEDED", "tool timeout observed code")
        _assert_equal(
            first_result["provider_error_category"],
            "timeout",
            "tool timeout provider category",
        )
        _assert_equal(observations[0]["tool_timeout_seconds"], 0.05, "tool timeout metadata")
        timeout_trace = [
            step
            for step in result["trace"]
            if step["step_type"] == "tool_output" and step["name"] == "slow_backend_tool"
        ][0]
        _assert_equal(timeout_trace["status"], "failed", "tool timeout trace status")
        _assert_equal(
            timeout_trace["output"]["error"]["code"],
            "TOOL_TIMEOUT_EXCEEDED",
            "tool timeout trace error code",
        )


def _assert_premature_missing_info_is_deferred() -> None:
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakePrematureAskLlmClient()
        runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm)
        result = runtime.start_run(
            {
                "goal": "?닿? 醫뗭븘?섎뒗 諛섑뙏?곗? 鍮꾩듂???꾨낫瑜?李얠븘 鍮꾧탳?댁쨾",
                "free_text": "?쇨렇癒쇳듃 諛섑뙏???꾨낫瑜?湲곗? ?룰낵 鍮꾧탳?댁쨾",
            }
        )

        _assert_equal(result["status"], "completed", "premature ask loop status")
        observations = result["state"]["observations"]
        observed_tools = [item["tool"] for item in observations]
        _assert_equal(
            observed_tools,
            [
                "load_closet_memory",
                "search_web_candidates",
                "ask_missing_info",
                "fetch_product_page",
                "classify_product_assets",
                "extract_size_material_info",
                "normalize_product_spec",
                "score_candidate_match",
                "write_purchase_report",
            ],
            "premature ask should not stop tool chain",
        )
        ask_result = [item for item in observations if item["tool"] == "ask_missing_info"][0]["result"]
        _assert_equal(ask_result["status"], "completed", "premature ask deferred status")
        _assert_equal(ask_result["deferred_user_question"], True, "premature ask deferred flag")
        _assert_equal(
            ask_result["reason"],
            "candidate_fields_must_be_enriched_with_tools_before_asking_user",
            "premature ask reason",
        )
        if result["state"].get("missing_info"):
            raise SystemExit("premature ask should not set missing_info before enrichment")
        guidance = result["state"].get("enrichment_guidance") or []
        if not guidance:
            raise SystemExit("premature ask did not persist enrichment guidance")
        _assert_equal(
            guidance[0]["guidance_type"],
            "backend_enrichment_required",
            "premature ask guidance type",
        )
        _assert_equal(guidance[0]["ask_user_allowed"], False, "premature ask guidance ask gate")
        if not any(item.get("tool") == "fetch_product_page" for item in guidance[0]["next_tool_suggestions"]):
            raise SystemExit("premature ask guidance did not include fetch suggestion")
        guidance_traces = [
            step
            for step in result["trace"]
            if step["step_type"] == "tool_guidance" and step["name"] == "continue_candidate_enrichment"
        ]
        if not guidance_traces:
            raise SystemExit("premature ask did not record enrichment guidance trace")
        request_after_ask = fake_llm.requests[3]["input_items"]
        if not isinstance(request_after_ask, list):
            raise SystemExit("premature ask follow-up request did not use structured input items")
        if not any(
            isinstance(item, dict)
            and "backend_runtime_guidance" in str(item.get("content", ""))
            and "fetch_product_page" in str(item.get("content", ""))
            for item in request_after_ask
        ):
            raise SystemExit("premature ask follow-up request did not include backend runtime guidance")


def _assert_missing_info_before_search_is_tool_first_guidance() -> None:
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeAskBeforeSearchLlmClient()
        runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm)
        result = runtime.start_run(
            {
                "goal": "?닿? 醫뗭븘?섎뒗 諛섑뙏?곗? 鍮꾩듂???꾨낫瑜?李얠븘 鍮꾧탳?댁쨾",
                "free_text": "?쇨렇癒쇳듃 諛섑뙏???꾨낫瑜?湲곗? ?룰낵 鍮꾧탳?댁쨾",
            }
        )

        _assert_equal(result["status"], "completed", "ask-before-search loop status")
        observations = result["state"]["observations"]
        observed_tools = [item["tool"] for item in observations]
        _assert_equal(
            observed_tools,
            [
                "ask_missing_info",
                "load_closet_memory",
                "search_web_candidates",
                "fetch_product_page",
                "classify_product_assets",
                "extract_size_material_info",
                "normalize_product_spec",
                "score_candidate_match",
                "write_purchase_report",
            ],
            "ask-before-search should recover into tool-first discovery",
        )
        ask_result = observations[0]["result"]
        _assert_equal(ask_result["status"], "completed", "ask-before-search deferred status")
        _assert_equal(ask_result["deferred_user_question"], True, "ask-before-search deferred flag")
        _assert_equal(ask_result["question_policy"], "tool_enrichment_first", "ask-before-search policy")
        suggested_tools = [item["tool"] for item in ask_result.get("next_tool_suggestions", [])]
        if "parse_user_goal" in suggested_tools:
            raise SystemExit("ask-before-search should not require parse_user_goal")
        for expected_tool in ["load_closet_memory", "search_web_candidates"]:
            if expected_tool not in suggested_tools:
                raise SystemExit(f"ask-before-search did not suggest {expected_tool}")
        if ask_result.get("questions"):
            raise SystemExit("ask-before-search should not generate user questions")
        if result["state"].get("missing_info"):
            raise SystemExit("ask-before-search should not set missing_info")
        request_after_ask = fake_llm.requests[1]["input_items"]
        if not isinstance(request_after_ask, list):
            raise SystemExit("ask-before-search follow-up request did not use structured input items")
        if not any(
            isinstance(item, dict)
            and "backend_runtime_guidance" in str(item.get("content", ""))
            and "search_web_candidates" in str(item.get("content", ""))
            for item in request_after_ask
        ):
            raise SystemExit("ask-before-search guidance did not include search suggestion")


def _assert_repeated_premature_missing_info_is_blocked() -> None:
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeRepeatedPrematureAskLlmClient()
        runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm)
        result = runtime.start_run(
            {
                "goal": "?닿? 醫뗭븘?섎뒗 諛섑뙏?곗? 鍮꾩듂???꾨낫瑜?李얠븘 鍮꾧탳?댁쨾",
                "free_text": "?쇨렇癒쇳듃 諛섑뙏???꾨낫瑜?湲곗? ?룰낵 鍮꾧탳?댁쨾",
            }
        )

        _assert_equal(result["status"], "failed", "repeated premature ask status")
        _assert_equal(
            result["error"]["code"],
            "REPEATED_PREMATURE_MISSING_INFO",
            "repeated premature ask guardrail code",
        )
        if result["state"].get("missing_info"):
            raise SystemExit("repeated premature ask should not set missing_info")
        guardrail_steps = [
            step
            for step in result["trace"]
            if step["name"] == "stop_repeated_premature_missing_info"
        ]
        if not guardrail_steps:
            raise SystemExit("repeated premature ask guardrail trace was not recorded")


def _assert_premature_score_report_recovers_with_enrichment_plan() -> None:
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakePrematureScoreThenReportLlmClient()
        runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm)
        result = runtime.start_run(
            {
                "goal": "?닿? 醫뗭븘?섎뒗 諛섑뙏?곗? 鍮꾩듂???꾨낫瑜??щ윭 媛?李얠븘 鍮꾧탳?댁쨾",
                "free_text": "?쇨렇癒쇳듃 諛섑뙏???꾨낫瑜?湲곗? ?룰낵 鍮꾧탳?댁쨾",
            }
        )

        _assert_equal(result["status"], "completed", "premature score report recovery status")
        _assert_equal(result.get("error"), None, "premature score report recovery error")
        observations = result["state"]["observations"]
        score_result = [item for item in observations if item["tool"] == "score_candidate_match"][0]["result"]
        _assert_equal(score_result["score_deferred"], True, "premature score deferred flag")
        _assert_equal(
            score_result["reason"],
            "candidate_enrichment_required_before_scoring",
            "premature score deferred reason",
        )
        if not score_result.get("next_tool_suggestions"):
            raise SystemExit("premature score did not return enrichment suggestions")
        report_result = [item for item in observations if item["tool"] == "write_purchase_report"][0]["result"]
        _assert_equal(report_result["status"], "failed", "premature report status")
        _assert_equal(report_result["score_deferred"], True, "premature report deferred flag")
        _assert_equal(
            report_result["reason"],
            "candidate_enrichment_required_before_report",
            "premature report deferred reason",
        )
        _assert_equal(
            report_result["error"]["code"],
            "CANDIDATE_ENRICHMENT_REQUIRED_BEFORE_REPORT",
            "premature report error code",
        )
        if not report_result.get("next_tool_suggestions"):
            raise SystemExit("premature report did not carry enrichment suggestions")
        guidance_traces = [
            step
            for step in result["trace"]
            if step["step_type"] == "tool_guidance" and step["name"] in {"continue_candidate_enrichment", "continue_report_enrichment"}
        ]
        if not any(
            (step.get("output") or {}).get("reason") == "candidate_enrichment_required_before_report"
            for step in guidance_traces
        ):
            raise SystemExit("premature report recovery did not record report enrichment guidance")
        observed_tools = [item["tool"] for item in observations]
        for expected_tool in [
            "fetch_product_page",
            "classify_product_assets",
            "extract_size_material_info",
            "normalize_product_spec",
            "score_candidate_match",
            "write_purchase_report",
        ]:
            if expected_tool not in observed_tools:
                raise SystemExit(f"premature report recovery did not execute {expected_tool}")
        if not result.get("final_report"):
            raise SystemExit("premature report recovery did not create final_report")


def _assert_report_after_baseline_enrichment_requires_rescore() -> None:
    def score_handler(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        baseline_attempted = any(
            item.get("tool") == "enrich_closet_item_from_web"
            for item in context.run.get("state", {}).get("observations", [])
        )
        if not baseline_attempted:
            return {
                "status": "completed",
                "candidate_key": arguments.get("candidate_key") or "all",
                "score_deferred": True,
                "reason": "baseline_enrichment_required_before_scoring",
                "evidence_status": "needs_baseline_tool_evidence",
                "next_tool_suggestions": [
                    {
                        "tool": "enrich_closet_item_from_web",
                        "arguments": {"query": "gakkai unions t-shirt"},
                        "reason": "lookup_baseline_item_before_scoring",
                    }
                ],
            }
        match = {
            "status": "completed",
            "candidate_key": "candidate_after_baseline",
            "recommendation": "need_more_info",
            "score": {"overall": 35, "size": 28, "material": 60, "price": 40, "confidence": 20},
            "missing_fields": [],
            "risks": [],
            "baseline_context": {"mode": "visual_only_after_baseline_lookup_failed"},
        }
        context.run.setdefault("state", {}).setdefault("match_results", []).append(match)
        return {
            "status": "completed",
            "candidate_key": arguments.get("candidate_key") or "all",
            "ranked_matches": [match],
        }

    def enrich_handler(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        return {
            "status": "completed",
            "baseline_enrichment_status": "no_baseline_candidate",
            "candidate_count": 0,
        }

    def report_handler(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        if not context.run.get("state", {}).get("match_results"):
            return {
                "status": "failed",
                "candidate_key": arguments.get("candidate_key") or "all",
                "deferred_user_question": True,
                "reason": "score_required_after_baseline_enrichment",
                "next_tool_suggestions": [
                    {
                        "tool": "score_candidate_match",
                        "arguments": {"candidate_key": "all"},
                        "reason": "rescore_after_baseline_enrichment",
                    }
                ],
                "error": "score_candidate_match must run after baseline enrichment before report generation",
            }
        return {
            "status": "completed",
            "candidate_key": arguments.get("candidate_key") or "all",
            "grounded": True,
            "final_report": "정확한 기준 상품은 웹검색으로 확인하지 못했습니다. 후보 비교 리포트입니다.",
        }

    registry = ToolRegistry(
        [
            ToolDefinition(
                name="score_candidate_match",
                description="Test score recovery after baseline enrichment",
                parameters={
                    "type": "object",
                    "properties": {"candidate_key": {"type": "string"}},
                    "required": ["candidate_key"],
                    "additionalProperties": False,
                },
                handler=score_handler,
            ),
            ToolDefinition(
                name="enrich_closet_item_from_web",
                description="Test baseline enrichment",
                parameters={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": True,
                },
                handler=enrich_handler,
            ),
            ToolDefinition(
                name="write_purchase_report",
                description="Test report recovery after baseline enrichment",
                parameters={
                    "type": "object",
                    "properties": {"candidate_key": {"type": "string"}},
                    "required": ["candidate_key"],
                    "additionalProperties": False,
                },
                handler=report_handler,
            ),
        ]
    )
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeBaselineEnrichThenPrematureReportLlmClient()
        runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm, tool_registry=registry)
        result = runtime.start_run(
            {
                "goal": "Uploaded shirt from Gakkai Unions; find similar clothes.",
                "free_text": "I bought this from Gakkai Unions and like the fit/material.",
            }
        )

    _assert_equal(result["status"], "completed", "baseline rescore recovery status")
    observations = result["state"]["observations"]
    score_observations = [item for item in observations if item["tool"] == "score_candidate_match"]
    if len(score_observations) < 2:
        raise SystemExit("baseline rescore recovery did not execute score_candidate_match again")
    report_results = [item["result"] for item in observations if item["tool"] == "write_purchase_report"]
    if not any(item.get("reason") == "score_required_after_baseline_enrichment" for item in report_results):
        raise SystemExit("premature baseline report did not request rescore")
    recovery_steps = [
        step
        for step in result["trace"]
        if step["step_type"] == "backend_recovery"
        and step["name"] == "execute_guidance_suggestions_after_model_no_tool_call"
    ]
    if not recovery_steps:
        raise SystemExit("baseline rescore backend recovery trace missing")
    recovered_score_outputs = [
        step
        for step in result["trace"]
        if step["step_type"] == "tool_output"
        and step["name"] == "score_candidate_match"
        and (step.get("output") or {}).get("backend_recovery") is True
    ]
    if not recovered_score_outputs:
        raise SystemExit("baseline rescore recovery did not mark recovered score tool output")
    if not result.get("final_report"):
        raise SystemExit("baseline rescore recovery did not create final_report")


def _assert_baseline_evidence_report_recovery_runs_backend_enrichment() -> None:
    def score_handler(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        baseline_recovered = bool(context.run.setdefault("state", {}).get("baseline_recovered"))
        match = {
            "status": "completed",
            "candidate_key": "candidate_after_baseline_report_recovery" if baseline_recovered else "candidate_before_baseline_recovery",
            "recommendation": "buy" if baseline_recovered else "caution",
            "score": {
                "overall": 91 if baseline_recovered else 64,
                "size": 96 if baseline_recovered else 62,
                "material": 86 if baseline_recovered else 52,
                "price": 70,
                "confidence": 88 if baseline_recovered else 35,
            },
            "measurement_deltas": {"length": 0.5, "shoulder": -0.5, "chest": 1.0, "sleeve": 0.0} if baseline_recovered else {},
            "missing_fields": [],
            "risks": [],
            "baseline_context": {
                "mode": "baseline_tool_enriched" if baseline_recovered else "text_clue_only_after_baseline_lookup_failed",
            },
        }
        context.run.setdefault("state", {}).setdefault("match_results", []).append(match)
        return {
            "status": "completed",
            "candidate_key": arguments.get("candidate_key") or "all",
            "ranked_matches": [match],
        }

    def enrich_handler(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        context.run.setdefault("state", {})["baseline_recovered"] = True
        context.run.setdefault("state", {})["closet_item"] = {
            "id": arguments.get("closet_item_id") or "current_liked_item",
            "brand": "유니언즈",
            "name": "유니언즈 L 반팔 티셔츠",
            "category": "t_shirt",
            "size_label": "L",
            "material": "cotton 100%",
            "measurements": {"length": 68, "shoulder": 52, "chest": 55, "sleeve": 20},
        }
        return {
            "status": "completed",
            "baseline_enrichment_status": "web_enriched_baseline",
            "closet_item_id": context.run["state"]["closet_item"]["id"],
            "observed_fields": ["material", "measurements.length", "measurements.shoulder", "measurements.chest", "measurements.sleeve"],
        }

    def report_handler(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        if not context.run.setdefault("state", {}).get("baseline_recovered"):
            return {
                "status": "failed",
                "candidate_key": arguments.get("candidate_key") or "all",
                "score_deferred": True,
                "evidence_status": "baseline_evidence_incomplete",
                "reason": "baseline_evidence_recovery_required_before_report",
                "missing_fields": [
                    "material",
                    "measurements.length",
                    "measurements.shoulder",
                    "measurements.chest",
                    "measurements.sleeve",
                ],
                "next_tool_suggestions": [
                    {
                        "tool": "enrich_closet_item_from_web",
                        "arguments": {
                            "query": "유니언즈 L 반팔 티셔츠 실측 소재",
                            "closet_item_id": "current_liked_item",
                            "refresh_web": True,
                        },
                        "reason": "retry_baseline_evidence_extraction_before_precise_similarity_report",
                    }
                ],
                "error": {"code": "BASELINE_EVIDENCE_RECOVERY_REQUIRED_BEFORE_REPORT"},
            }
        return {
            "status": "completed",
            "candidate_key": arguments.get("candidate_key") or "all",
            "grounded": True,
            "grounding_validation": {"valid": True},
            "report_type": "shortlist",
            "final_report": "기준 실측과 소재를 보강한 뒤 가장 가까운 후보를 비교했습니다.",
        }

    registry = ToolRegistry(
        [
            ToolDefinition(
                name="score_candidate_match",
                description="Test scoring around baseline report recovery",
                parameters={
                    "type": "object",
                    "properties": {"candidate_key": {"type": "string"}},
                    "required": ["candidate_key"],
                    "additionalProperties": False,
                },
                handler=score_handler,
            ),
            ToolDefinition(
                name="enrich_closet_item_from_web",
                description="Test baseline evidence recovery",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "closet_item_id": {"type": "string"},
                        "refresh_web": {"type": "boolean"},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
                handler=enrich_handler,
            ),
            ToolDefinition(
                name="write_purchase_report",
                description="Test report blocked by baseline evidence",
                parameters={
                    "type": "object",
                    "properties": {"candidate_key": {"type": "string"}},
                    "required": ["candidate_key"],
                    "additionalProperties": False,
                },
                handler=report_handler,
            ),
        ]
    )
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeBaselineEvidenceReportRecoveryLlmClient()
        runtime = _runtime(
            Path(temp_dir) / "runs.json",
            llm_client=fake_llm,
            tool_registry=registry,
        )
        result = runtime.start_run(
            {
                "goal": "유니언즈 L 반팔 티셔츠처럼 실측과 소재가 최대한 가까운 후보를 찾아줘.",
                "free_text": "대충 찾고 멈추지 말고 기준 옷 실측과 소재 근거를 확인해서 계속 비교해줘.",
            }
        )

    _assert_equal(result["status"], "completed", "baseline evidence report recovery status")
    observations = result["state"]["observations"]
    observed_tools = [item.get("tool") for item in observations]
    if "enrich_closet_item_from_web" not in observed_tools:
        raise SystemExit("baseline evidence report recovery did not execute baseline enrichment")
    enrich_index = observed_tools.index("enrich_closet_item_from_web")
    recovered_report_indexes = [
        index
        for index, item in enumerate(observations)
        if item.get("tool") == "write_purchase_report"
        and (item.get("result") or {}).get("status") == "completed"
    ]
    if not recovered_report_indexes or enrich_index > recovered_report_indexes[0]:
        raise SystemExit("baseline evidence enrichment did not run before the recovered report")
    backend_recovery_tools = [
        item.get("tool")
        for item in observations
        if str(item.get("call_id") or "").startswith("backend_recovery_")
    ]
    if "enrich_closet_item_from_web" not in backend_recovery_tools:
        raise SystemExit("baseline evidence recovery enrichment was not backend-owned")
    if "score_candidate_match" not in backend_recovery_tools:
        raise SystemExit("baseline evidence recovery did not rescore after enrichment")
    guidance_steps = [
        step
        for step in result["trace"]
        if step.get("step_type") == "tool_guidance"
        and (step.get("output") or {}).get("reason") == "baseline_evidence_recovery_required_before_report"
    ]
    if not guidance_steps:
        raise SystemExit("baseline evidence report recovery guidance trace missing")
    recovery_steps = [
        step
        for step in result["trace"]
        if step.get("step_type") == "backend_recovery"
        and step.get("name") == "execute_guidance_suggestions_after_model_no_tool_call"
    ]
    if not recovery_steps:
        raise SystemExit("baseline evidence report recovery backend trace missing")
    if result.get("error"):
        raise SystemExit("baseline evidence report recovery should not leave an error")
    if result.get("final_report", {}).get("report_type") != "shortlist":
        raise SystemExit("baseline evidence report recovery should complete with a grounded shortlist")


def _assert_backend_required_report_recovery_runs_baseline_enrichment() -> None:
    def score_handler(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        baseline_recovered = bool(context.run.setdefault("state", {}).get("baseline_recovered"))
        match = {
            "status": "completed",
            "candidate_key": "candidate_after_backend_required_recovery" if baseline_recovered else "candidate_before_backend_required_recovery",
            "recommendation": "buy" if baseline_recovered else "caution",
            "score": {
                "overall": 90 if baseline_recovered else 63,
                "size": 95 if baseline_recovered else 60,
                "material": 84 if baseline_recovered else 50,
                "price": 70,
                "confidence": 87 if baseline_recovered else 34,
            },
            "measurement_deltas": {"length": 0.5, "shoulder": -0.5, "chest": 1.0, "sleeve": 0.0} if baseline_recovered else {},
            "missing_fields": [],
            "risks": [],
            "baseline_context": {
                "mode": "baseline_tool_enriched" if baseline_recovered else "text_clue_only_after_baseline_lookup_failed",
            },
        }
        context.run.setdefault("state", {}).setdefault("match_results", []).append(match)
        return {
            "status": "completed",
            "candidate_key": arguments.get("candidate_key") or "all",
            "ranked_matches": [match],
        }

    def enrich_handler(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        context.run.setdefault("state", {})["baseline_recovered"] = True
        context.run.setdefault("state", {})["closet_item"] = {
            "id": arguments.get("closet_item_id") or "current_liked_item",
            "brand": "유니언즈",
            "name": "유니언즈 L 반팔 티셔츠",
            "category": "t_shirt",
            "size_label": "L",
            "material": "cotton 100%",
            "measurements": {"length": 68, "shoulder": 52, "chest": 55, "sleeve": 20},
        }
        return {
            "status": "completed",
            "baseline_enrichment_status": "web_enriched_baseline",
            "closet_item_id": context.run["state"]["closet_item"]["id"],
        }

    def report_handler(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        if not context.run.setdefault("state", {}).get("baseline_recovered"):
            return {
                "status": "failed",
                "candidate_key": arguments.get("candidate_key") or "all",
                "score_deferred": True,
                "evidence_status": "baseline_evidence_incomplete",
                "reason": "baseline_evidence_recovery_required_before_report",
                "missing_fields": ["material", "measurements.length", "measurements.shoulder", "measurements.chest", "measurements.sleeve"],
                "next_tool_suggestions": [
                    {
                        "tool": "enrich_closet_item_from_web",
                        "arguments": {
                            "query": "유니언즈 L 반팔 티셔츠 실측 소재",
                            "closet_item_id": "current_liked_item",
                            "refresh_web": True,
                        },
                        "reason": "retry_baseline_evidence_extraction_before_precise_similarity_report",
                    }
                ],
                "error": {"code": "BASELINE_EVIDENCE_RECOVERY_REQUIRED_BEFORE_REPORT"},
            }
        return {
            "status": "completed",
            "candidate_key": arguments.get("candidate_key") or "all",
            "grounded": True,
            "grounding_validation": {"valid": True},
            "report_type": "shortlist",
            "final_report": "백엔드 강제 리포트 실패 이후 기준 실측과 소재를 보강해 비교했습니다.",
        }

    registry = ToolRegistry(
        [
            ToolDefinition(
                name="score_candidate_match",
                description="Test backend-required report recovery scoring",
                parameters={
                    "type": "object",
                    "properties": {"candidate_key": {"type": "string"}},
                    "required": ["candidate_key"],
                    "additionalProperties": False,
                },
                handler=score_handler,
            ),
            ToolDefinition(
                name="enrich_closet_item_from_web",
                description="Test backend-required report baseline recovery",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "closet_item_id": {"type": "string"},
                        "refresh_web": {"type": "boolean"},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
                handler=enrich_handler,
            ),
            ToolDefinition(
                name="write_purchase_report",
                description="Test backend-required report baseline blocker",
                parameters={
                    "type": "object",
                    "properties": {"candidate_key": {"type": "string"}},
                    "required": ["candidate_key"],
                    "additionalProperties": False,
                },
                handler=report_handler,
            ),
        ]
    )
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeFinalBeforeBackendRequiredBaselineRecoveryLlmClient()
        runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm, tool_registry=registry)
        result = runtime.start_run(
            {
                "goal": "유니언즈 L 반팔 티셔츠처럼 실측과 소재가 최대한 가까운 후보를 찾아줘.",
                "free_text": "대충 찾고 멈추지 말고 기준 옷 실측과 소재 근거를 확인해서 계속 비교해줘.",
            }
        )

    _assert_equal(result["status"], "completed", "backend-required baseline recovery status")
    if result.get("error"):
        raise SystemExit("backend-required report recovery should not leave an error")
    if result.get("final_report", {}).get("report_type") != "shortlist":
        raise SystemExit("backend-required report recovery should finish with a grounded shortlist")
    guidance_steps = [
        step
        for step in result.get("trace", [])
        if step.get("step_type") == "tool_guidance"
        and step.get("name") == "continue_backend_required_report_enrichment"
        and (step.get("output") or {}).get("reason") == "baseline_evidence_recovery_required_before_report"
    ]
    if not guidance_steps:
        raise SystemExit("backend-required report recovery guidance trace missing")
    recovered_tools = [
        item.get("tool")
        for item in result["state"].get("observations", [])
        if str(item.get("call_id") or "").startswith("backend_recovery_")
    ]
    if "enrich_closet_item_from_web" not in recovered_tools:
        raise SystemExit("backend-required report recovery did not execute baseline enrichment")
    if "score_candidate_match" not in recovered_tools:
        raise SystemExit("backend-required report recovery did not rescore after enrichment")
    backend_required_report_results = [
        item.get("result") or {}
        for item in result["state"].get("observations", [])
        if str(item.get("call_id") or "").startswith("backend_required_report_")
    ]
    if not any(
        item.get("reason") == "baseline_evidence_recovery_required_before_report"
        for item in backend_required_report_results
    ):
        raise SystemExit("backend-required report failure reason was not preserved")


def _assert_repeated_baseline_evidence_recovery_waits_for_user() -> None:
    guidance = {
        "guidance_type": "backend_enrichment_required",
        "candidate_key": "all",
        "reason": "baseline_evidence_recovery_required_before_report",
        "missing_fields": [
            "material",
            "measurements.length",
            "measurements.shoulder",
            "measurements.chest",
            "measurements.sleeve",
        ],
        "next_tool_suggestions": [
            {
                "tool": "enrich_closet_item_from_web",
                "arguments": {
                    "query": "유니언즈 L 반팔 티셔츠 실측 소재",
                    "closet_item_id": "current_liked_item",
                    "refresh_web": True,
                },
                "reason": "retry_baseline_evidence_extraction_before_precise_similarity_report",
            }
        ],
        "required_action": "Call the suggested backend tools before asking the user, scoring, or writing a report.",
        "ask_user_allowed": False,
    }
    signature = json.dumps(
        {
            "reason": guidance["reason"],
            "suggestions": guidance["next_tool_suggestions"],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    with TemporaryDirectory() as temp_dir:
        runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=FakeFinalWithoutToolsLlmClient())
        run = {
            "run_id": "run_repeated_baseline_recovery",
            "status": "running",
            "input": {
                "goal": "유니언즈 L 반팔 티셔츠처럼 실측과 소재가 가까운 후보를 찾아줘.",
                "free_text": "대충 멈추지 말고 기준 실측/소재 근거를 확인해줘.",
            },
            "state": {
                "backend_guidance_recovery_counts": {signature: 2},
                "pending_tool_outputs": [],
                "observations": [],
                "enrichment_guidance": [guidance],
            },
            "trace": [],
        }
        pending_tool_outputs = [
            {
                "role": "user",
                "content": json.dumps({"backend_runtime_guidance": guidance}, ensure_ascii=False),
            }
        ]
        recovery = runtime._execute_backend_recovery_from_guidance(
            run,
            pending_tool_outputs,
            rejected_final_text="모델이 근거 없이 멈추려는 답변",
        )

    if recovery.get("executed") is not True:
        raise SystemExit("repeated baseline recovery should be treated as handled waiting state")
    _assert_equal(run["status"], "waiting_for_user", "repeated baseline recovery exhausted status")
    if run.get("error"):
        raise SystemExit("repeated baseline recovery exhausted path should not leave an error")
    questions = run.get("state", {}).get("missing_info") or []
    if not questions or questions[0].get("field") != "baseline_evidence":
        raise SystemExit("repeated baseline recovery exhausted path did not request baseline evidence")
    if not any(
        step.get("name") == "await_baseline_evidence_after_recovery_exhausted"
        and step.get("status") == "waiting_for_user"
        for step in run.get("trace", [])
    ):
        raise SystemExit("repeated baseline recovery exhausted guardrail trace missing")
    from app.routes import agent as agent_routes

    transcript = AgentClientTranscriptResponse.model_validate(agent_routes._agent_client_transcript_for_run(run))
    transcript_messages = [item.model_dump() for item in transcript.messages]
    question_messages = [
        item
        for item in transcript_messages
        if item.get("type") == "agent_question" and item.get("subtype") == "missing_info_request"
    ]
    if not question_messages:
        raise SystemExit("client transcript should include baseline evidence question after recovery exhaustion")
    if (question_messages[-1].get("questions") or [{}])[0].get("field") != "baseline_evidence":
        raise SystemExit("client transcript baseline evidence question field missing")
    guidance_messages = [
        item
        for item in transcript_messages
        if item.get("type") == "agent_guidance"
        and item.get("reason") == "baseline_evidence_recovery_required_before_report"
    ]
    if not guidance_messages:
        raise SystemExit("client transcript should preserve baseline recovery guidance after recovery exhaustion")


def _assert_backend_recovery_executes_guidance_when_model_ignores_tools() -> None:
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeIgnoresGuidanceAfterPrematureAskLlmClient()
        runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm)
        result = runtime.start_run(
            {
                "goal": "Find similar clothing and compare candidates.",
                "free_text": "Segment tee baseline, find similar options.",
            }
        )

        _assert_equal(result["status"], "completed", "backend guidance recovery status")
        recovery_steps = [
            step
            for step in result["trace"]
            if step["step_type"] == "backend_recovery"
            and step["name"] == "execute_guidance_suggestions_after_model_no_tool_call"
        ]
        if not recovery_steps:
            raise SystemExit("backend guidance recovery trace was not recorded")
        recovered_tool_outputs = [
            step
            for step in result["trace"]
            if step["step_type"] == "tool_output"
            and (step.get("output") or {}).get("backend_recovery") is True
        ]
        recovered_tools = [step["name"] for step in recovered_tool_outputs]
        for expected_tool in [
            "fetch_product_page",
            "classify_product_assets",
            "extract_size_material_info",
            "normalize_product_spec",
        ]:
            if expected_tool not in recovered_tools:
                raise SystemExit(f"backend guidance recovery did not execute {expected_tool}")
        observations = result["state"]["observations"]
        observed_tools = [item["tool"] for item in observations]
        for expected_tool in ["score_candidate_match", "write_purchase_report"]:
            if expected_tool not in observed_tools:
                raise SystemExit(f"backend guidance recovery did not continue to {expected_tool}")
        if not result.get("final_report"):
            raise SystemExit("backend guidance recovery did not create final_report")


def _assert_close_fit_guidance_runs_backend_search_recovery_when_model_stops() -> None:
    def search_handler(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        return {
            "status": "completed",
            "provider": "fake_close_fit_search",
            "query": arguments.get("query"),
            "candidate_count": 1,
            "candidates": [
                {
                    "candidate_key": "closer_candidate",
                    "name": "Closer measured tee",
                    "source_url": "https://shop.example/closer-candidate",
                }
            ],
            "external_api_called": False,
        }

    def score_handler(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        recovered_search_count = len(
            [
                item
                for item in context.run.get("state", {}).get("observations", [])
                if item.get("tool") == "search_web_candidates"
                and str(item.get("call_id") or "").startswith("backend_recovery_")
            ]
        )
        if recovered_search_count == 0:
            return {
                "status": "completed",
                "candidate_key": "all",
                "score_deferred": True,
                "reason": "continue_search_for_closer_measurements",
                "evidence_status": "no_close_measurement_match_yet",
                "ranked_matches": [
                    {
                        "candidate_key": "far_candidate",
                        "recommendation": "avoid",
                        "score": {"overall": 42, "size": 35, "material": 55, "price": 50, "confidence": 45},
                        "measurement_deltas": {"length": 4.0, "shoulder": -3.0, "chest": 5.5, "sleeve": 2.5},
                        "missing_fields": [],
                        "top_risks": [{"level": "high", "type": "chest"}],
                    }
                ],
                "next_tool_suggestions": [
                    {
                        "tool": "search_web_candidates",
                        "arguments": {
                            "query": "site:shop.example/products tee length 68 shoulder 52 chest 55 sleeve 20",
                            "refresh_web": False,
                        },
                        "reason": "continue_search_for_closer_measurement_match",
                    }
                ],
            }
        return {
            "status": "completed",
            "candidate_key": "all",
            "ranked_matches": [
                {
                    "candidate_key": "closer_candidate",
                    "recommendation": "buy",
                    "score": {"overall": 91, "size": 96, "material": 82, "price": 80, "confidence": 88},
                    "measurement_deltas": {"length": 0.5, "shoulder": -0.5, "chest": 1.0, "sleeve": 0.0},
                    "missing_fields": [],
                    "top_risks": [],
                }
            ],
        }

    def report_handler(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        return {
            "status": "completed",
            "candidate_key": arguments.get("candidate_key"),
            "grounded": True,
            "grounding_validation": {"valid": True},
            "report_type": "shortlist",
            "final_report": "기준 실측에 가까운 후보를 backend recovery 검색 이후 확인했습니다.",
        }

    registry = ToolRegistry(
        [
            ToolDefinition(
                name="search_web_candidates",
                description="Test close-fit recovery search tool",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "refresh_web": {"type": "boolean"},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
                handler=search_handler,
            ),
            ToolDefinition(
                name="score_candidate_match",
                description="Test close-fit deferred score tool",
                parameters={
                    "type": "object",
                    "properties": {"candidate_key": {"type": "string"}},
                    "required": ["candidate_key"],
                    "additionalProperties": False,
                },
                handler=score_handler,
            ),
            ToolDefinition(
                name="write_purchase_report",
                description="Test close-fit report tool",
                parameters={
                    "type": "object",
                    "properties": {"candidate_key": {"type": "string"}},
                    "required": ["candidate_key"],
                    "additionalProperties": False,
                },
                handler=report_handler,
            ),
        ]
    )
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeIgnoresCloseFitGuidanceLlmClient()
        runtime = _runtime(
            Path(temp_dir) / "runs.json",
            llm_client=fake_llm,
            tool_registry=registry,
        )
        result = runtime.start_run(
            {
                "goal": "Find the closest measured replacement tee.",
                "free_text": "기준 티셔츠 실측과 최대한 비슷한 옷을 찾을 때까지 비교해줘.",
            }
        )

    _assert_equal(result["status"], "completed", "close-fit backend recovery status")
    recovered_search_outputs = [
        step
        for step in result["trace"]
        if step.get("step_type") == "tool_output"
        and step.get("name") == "search_web_candidates"
        and (step.get("output") or {}).get("backend_recovery") is True
    ]
    if not recovered_search_outputs:
        raise SystemExit("close-fit guidance did not execute backend-owned search recovery")
    recovery_steps = [
        step
        for step in result["trace"]
        if step.get("step_type") == "backend_recovery"
        and step.get("name") == "execute_guidance_suggestions_after_model_no_tool_call"
    ]
    if not recovery_steps:
        raise SystemExit("close-fit backend recovery trace was not recorded")
    deferred_scores = [
        item
        for item in result["state"].get("observations", [])
        if item.get("tool") == "score_candidate_match"
        and (item.get("result") or {}).get("reason") == "continue_search_for_closer_measurements"
    ]
    if not deferred_scores:
        raise SystemExit("close-fit deferred score observation was not preserved")
    if not result.get("final_report"):
        raise SystemExit("close-fit backend recovery did not create final_report")


def _assert_close_fit_max_turn_recovery_rescores_before_report_fallback() -> None:
    def search_handler(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        return {
            "status": "completed",
            "provider": "fake_close_fit_search",
            "query": arguments.get("query"),
            "candidate_count": 1,
            "candidates": [
                {
                    "candidate_key": "max_turn_closer_candidate",
                    "name": "Max turn closer measured tee",
                    "source_url": "https://shop.example/max-turn-closer",
                }
            ],
            "external_api_called": False,
        }

    def score_handler(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        recovered_search_count = len(
            [
                item
                for item in context.run.get("state", {}).get("observations", [])
                if item.get("tool") == "search_web_candidates"
                and str(item.get("call_id") or "").startswith("backend_recovery_")
            ]
        )
        if recovered_search_count == 0:
            return {
                "status": "completed",
                "candidate_key": "all",
                "score_deferred": True,
                "reason": "continue_search_for_closer_measurements",
                "evidence_status": "no_close_measurement_match_yet",
                "ranked_matches": [
                    {
                        "candidate_key": "far_candidate",
                        "recommendation": "avoid",
                        "score": {"overall": 42, "size": 35, "material": 55, "price": 50, "confidence": 45},
                        "measurement_deltas": {"length": 4.0, "shoulder": -3.0, "chest": 5.5, "sleeve": 2.5},
                        "missing_fields": [],
                        "top_risks": [{"level": "high", "type": "chest"}],
                    }
                ],
                "next_tool_suggestions": [
                    {
                        "tool": "search_web_candidates",
                        "arguments": {
                            "query": "site:shop.example/products tee length 68 shoulder 52 chest 55 sleeve 20",
                            "refresh_web": False,
                        },
                        "reason": "continue_search_for_closer_measurement_match",
                    }
                ],
            }
        return {
            "status": "completed",
            "candidate_key": "all",
            "ranked_matches": [
                {
                    "candidate_key": "max_turn_closer_candidate",
                    "recommendation": "buy",
                    "score": {"overall": 92, "size": 97, "material": 82, "price": 80, "confidence": 88},
                    "measurement_deltas": {"length": 0.5, "shoulder": -0.5, "chest": 1.0, "sleeve": 0.0},
                    "missing_fields": [],
                    "top_risks": [],
                }
            ],
        }

    def report_handler(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        return {
            "status": "completed",
            "candidate_key": arguments.get("candidate_key"),
            "grounded": True,
            "grounding_validation": {"valid": True},
            "report_type": "shortlist",
            "final_report": "max_turn 이후에도 close-fit recovery 검색과 재스코어링을 먼저 수행했습니다.",
        }

    registry = ToolRegistry(
        [
            ToolDefinition(
                name="search_web_candidates",
                description="Test close-fit max-turn recovery search tool",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "refresh_web": {"type": "boolean"},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
                handler=search_handler,
            ),
            ToolDefinition(
                name="score_candidate_match",
                description="Test close-fit max-turn score tool",
                parameters={
                    "type": "object",
                    "properties": {"candidate_key": {"type": "string"}},
                    "required": ["candidate_key"],
                    "additionalProperties": False,
                },
                handler=score_handler,
            ),
            ToolDefinition(
                name="write_purchase_report",
                description="Test close-fit max-turn report tool",
                parameters={
                    "type": "object",
                    "properties": {"candidate_key": {"type": "string"}},
                    "required": ["candidate_key"],
                    "additionalProperties": False,
                },
                handler=report_handler,
            ),
        ]
    )
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeIgnoresCloseFitGuidanceLlmClient()
        runtime = _runtime(
            Path(temp_dir) / "runs.json",
            llm_client=fake_llm,
            tool_registry=registry,
            max_turns=1,
        )
        result = runtime.start_run(
            {
                "goal": "Find the closest measured replacement tee.",
                "free_text": "기준 티셔츠 실측과 최대한 비슷한 옷을 찾을 때까지 비교해줘.",
            }
        )

    _assert_equal(result["status"], "completed", "close-fit max-turn recovery status")
    score_observations = [
        item for item in result["state"].get("observations", []) if item.get("tool") == "score_candidate_match"
    ]
    if len(score_observations) < 2:
        raise SystemExit("close-fit max-turn recovery did not rescore after backend search")
    recovered_search_outputs = [
        step
        for step in result["trace"]
        if step.get("step_type") == "tool_output"
        and step.get("name") == "search_web_candidates"
        and (step.get("output") or {}).get("backend_recovery") is True
    ]
    if not recovered_search_outputs:
        raise SystemExit("close-fit max-turn recovery did not execute backend-owned search")
    if fake_llm.index != 1:
        raise SystemExit("close-fit max-turn recovery should not require another LLM turn before rescore/report")
    if result.get("final_report", {}).get("report_type") != "shortlist":
        raise SystemExit("close-fit max-turn recovery should report the recovered close shortlist")


def _assert_close_fit_recovery_continues_across_refreshed_score_guidance() -> None:
    def search_handler(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        return {
            "status": "completed",
            "provider": "fake_close_fit_search",
            "query": arguments.get("query"),
            "candidate_count": 1,
            "candidates": [
                {
                    "candidate_key": f"candidate_for_{arguments.get('query')}",
                    "name": f"Candidate for {arguments.get('query')}",
                    "source_url": f"https://shop.example/{arguments.get('query')}",
                }
            ],
            "external_api_called": False,
        }

    def score_handler(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        recovered_queries = [
            (item.get("arguments") or {}).get("query")
            for item in context.run.get("state", {}).get("observations", [])
            if item.get("tool") == "search_web_candidates"
            and str(item.get("call_id") or "").startswith("backend_recovery_")
        ]
        recovered_count = len(recovered_queries)
        if recovered_count < 3:
            next_query = f"close-fit-query-{recovered_count + 1}"
            return {
                "status": "completed",
                "candidate_key": "all",
                "score_deferred": True,
                "reason": "continue_search_for_closer_measurements",
                "evidence_status": "no_close_measurement_match_yet",
                "ranked_matches": [
                    {
                        "candidate_key": "far_candidate",
                        "recommendation": "avoid",
                        "score": {"overall": 44, "size": 40, "material": 55, "price": 50, "confidence": 45},
                        "measurement_deltas": {"length": 4.0, "shoulder": -3.0, "chest": 5.5, "sleeve": 2.5},
                        "missing_fields": [],
                        "top_risks": [{"level": "high", "type": "chest"}],
                    }
                ],
                "next_tool_suggestions": [
                    {
                        "tool": "search_web_candidates",
                        "arguments": {"query": next_query, "refresh_web": False},
                        "reason": "continue_search_for_closer_measurement_match",
                    }
                ],
                "close_fit_search_budget": {
                    "attempt_count": recovered_count,
                    "attempt_limit": 5,
                    "remaining_attempts": 5 - recovered_count,
                    "attempted_queries": recovered_queries,
                },
            }
        return {
            "status": "completed",
            "candidate_key": "all",
            "ranked_matches": [
                {
                    "candidate_key": "close_candidate",
                    "recommendation": "buy",
                    "score": {"overall": 93, "size": 96, "material": 84, "price": 80, "confidence": 88},
                    "measurement_deltas": {"length": 0.5, "shoulder": -0.5, "chest": 1.0, "sleeve": 0.0},
                    "missing_fields": [],
                    "top_risks": [],
                }
            ],
        }

    def report_handler(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        return {
            "status": "completed",
            "candidate_key": arguments.get("candidate_key"),
            "grounded": True,
            "grounding_validation": {"valid": True},
            "report_type": "shortlist",
            "final_report": "새 close-fit score guidance를 이어 받아 가장 가까운 후보를 찾았습니다.",
        }

    registry = ToolRegistry(
        [
            ToolDefinition(
                name="search_web_candidates",
                description="Test refreshed close-fit search guidance",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "refresh_web": {"type": "boolean"},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
                handler=search_handler,
            ),
            ToolDefinition(
                name="score_candidate_match",
                description="Test refreshed close-fit score guidance",
                parameters={
                    "type": "object",
                    "properties": {"candidate_key": {"type": "string"}},
                    "required": ["candidate_key"],
                    "additionalProperties": False,
                },
                handler=score_handler,
            ),
            ToolDefinition(
                name="write_purchase_report",
                description="Test refreshed close-fit report",
                parameters={
                    "type": "object",
                    "properties": {"candidate_key": {"type": "string"}},
                    "required": ["candidate_key"],
                    "additionalProperties": False,
                },
                handler=report_handler,
            ),
        ]
    )
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeAlwaysStopsDuringCloseFitRecoveryLlmClient()
        runtime = _runtime(
            Path(temp_dir) / "runs.json",
            llm_client=fake_llm,
            tool_registry=registry,
            max_turns=8,
        )
        result = runtime.start_run(
            {
                "goal": "Find the closest measured replacement tee.",
                "free_text": "대충 찾지 말고 기준 티셔츠 실측과 최대한 비슷한 옷을 찾을 때까지 계속 찾아줘.",
            }
        )

    _assert_equal(result["status"], "completed", "refreshed close-fit recovery status")
    recovered_queries = [
        (item.get("arguments") or {}).get("query")
        for item in result["state"].get("observations", [])
        if item.get("tool") == "search_web_candidates"
        and str(item.get("call_id") or "").startswith("backend_recovery_")
    ]
    _assert_equal(
        recovered_queries,
        ["close-fit-query-1", "close-fit-query-2", "close-fit-query-3"],
        "refreshed close-fit recovery queries",
    )
    if result.get("final_report", {}).get("report_type") != "shortlist":
        raise SystemExit("refreshed close-fit recovery did not finish with the close shortlist")
    guidance_traces = [
        step
        for step in result.get("trace", [])
        if step.get("name") == "continue_backend_recovery_rescore_guidance"
    ]
    if len(guidance_traces) < 2:
        raise SystemExit("refreshed close-fit score guidance was not persisted between recovery turns")


def _assert_close_fit_recovery_outruns_llm_turn_limit_until_close_match() -> None:
    def search_handler(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        return {
            "status": "completed",
            "provider": "fake_close_fit_search",
            "query": arguments.get("query"),
            "candidate_count": 1,
            "candidates": [
                {
                    "candidate_key": f"candidate_for_{arguments.get('query')}",
                    "name": f"Candidate for {arguments.get('query')}",
                    "source_url": f"https://shop.example/{arguments.get('query')}",
                }
            ],
            "external_api_called": False,
        }

    def score_handler(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        recovered_queries = [
            (item.get("arguments") or {}).get("query")
            for item in context.run.get("state", {}).get("observations", [])
            if item.get("tool") == "search_web_candidates"
            and str(item.get("call_id") or "").startswith("backend_recovery_")
        ]
        recovered_count = len(recovered_queries)
        if recovered_count < 4:
            next_query = f"persistent-close-fit-query-{recovered_count + 1}"
            return {
                "status": "completed",
                "candidate_key": "all",
                "score_deferred": True,
                "reason": "continue_search_for_closer_measurements",
                "evidence_status": "no_close_measurement_match_yet",
                "ranked_matches": [
                    {
                        "candidate_key": "not_close_enough_candidate",
                        "recommendation": "avoid",
                        "score": {"overall": 45, "size": 38, "material": 58, "price": 50, "confidence": 46},
                        "measurement_deltas": {"length": 4.0, "shoulder": -3.0, "chest": 5.5, "sleeve": 2.5},
                        "missing_fields": [],
                        "top_risks": [{"level": "high", "type": "chest"}],
                    }
                ],
                "next_tool_suggestions": [
                    {
                        "tool": "search_web_candidates",
                        "arguments": {"query": next_query, "refresh_web": False},
                        "reason": "continue_search_for_closer_measurement_match",
                    }
                ],
                "close_fit_search_budget": {
                    "attempt_count": recovered_count,
                    "attempt_limit": 6,
                    "remaining_attempts": 6 - recovered_count,
                    "attempted_queries": recovered_queries,
                },
            }
        return {
            "status": "completed",
            "candidate_key": "all",
            "ranked_matches": [
                {
                    "candidate_key": "persistent_close_candidate",
                    "recommendation": "buy",
                    "score": {"overall": 94, "size": 98, "material": 84, "price": 80, "confidence": 88},
                    "measurement_deltas": {"length": 0.5, "shoulder": -0.5, "chest": 1.0, "sleeve": 0.0},
                    "missing_fields": [],
                    "top_risks": [],
                }
            ],
        }

    def report_handler(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        return {
            "status": "completed",
            "candidate_key": arguments.get("candidate_key"),
            "grounded": True,
            "grounding_validation": {"valid": True},
            "report_type": "shortlist",
            "final_report": "LLM turn limit 이후에도 close-fit 예산을 우선해 가까운 후보를 찾았습니다.",
        }

    registry = ToolRegistry(
        [
            ToolDefinition(
                name="search_web_candidates",
                description="Test close-fit persistence search",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "refresh_web": {"type": "boolean"},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
                handler=search_handler,
            ),
            ToolDefinition(
                name="score_candidate_match",
                description="Test close-fit persistence score",
                parameters={
                    "type": "object",
                    "properties": {"candidate_key": {"type": "string"}},
                    "required": ["candidate_key"],
                    "additionalProperties": False,
                },
                handler=score_handler,
            ),
            ToolDefinition(
                name="write_purchase_report",
                description="Test close-fit persistence report",
                parameters={
                    "type": "object",
                    "properties": {"candidate_key": {"type": "string"}},
                    "required": ["candidate_key"],
                    "additionalProperties": False,
                },
                handler=report_handler,
            ),
        ]
    )
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeAlwaysStopsDuringCloseFitRecoveryLlmClient()
        runtime = _runtime(
            Path(temp_dir) / "runs.json",
            llm_client=fake_llm,
            tool_registry=registry,
            max_turns=1,
        )
        result = runtime.start_run(
            {
                "goal": "Find the closest measured replacement tee.",
                "free_text": "대충 찾지 말고 실측이 최대한 비슷한 후보가 나올 때까지 계속 찾아줘.",
            }
        )

    _assert_equal(result["status"], "completed", "close-fit recovery beyond turn limit status")
    recovered_queries = [
        (item.get("arguments") or {}).get("query")
        for item in result["state"].get("observations", [])
        if item.get("tool") == "search_web_candidates"
        and str(item.get("call_id") or "").startswith("backend_recovery_")
    ]
    _assert_equal(
        recovered_queries,
        [
            "persistent-close-fit-query-1",
            "persistent-close-fit-query-2",
            "persistent-close-fit-query-3",
            "persistent-close-fit-query-4",
        ],
        "close-fit recovery should continue past LLM turn limit",
    )
    if result.get("state", {}).get("force_no_close_report_after_search_exhausted"):
        raise SystemExit("close-fit loop should not force a weak report while budget remains")
    if result.get("final_report", {}).get("report_type") != "shortlist":
        raise SystemExit("close-fit loop should finish with the recovered close shortlist")
    from app.routes import agent as agent_routes

    sdk_events = AgentSdkEventsResponse.model_validate(agent_routes._agent_sdk_events_for_run(result))
    guidance_events = [item for item in sdk_events.events if item.get("type") == "agent_guidance"]
    if not guidance_events:
        raise SystemExit("close-fit loop should project backend guidance as SDK events")
    close_fit_guidance = [
        item
        for item in guidance_events
        if item.get("reason") == "continue_search_for_closer_measurements"
    ]
    if not close_fit_guidance:
        raise SystemExit("close-fit SDK guidance event should preserve recovery reason")
    first_guidance = close_fit_guidance[0]
    if first_guidance.get("suggested_tool_count") != 1:
        raise SystemExit("close-fit SDK guidance event should preserve suggested tool count")
    if (first_guidance.get("next_tool_suggestions") or [{}])[0].get("tool") != "search_web_candidates":
        raise SystemExit("close-fit SDK guidance event should preserve next search tool")
    budget = first_guidance.get("close_fit_search_budget") or {}
    if int(budget.get("remaining_attempts") or 0) <= 0:
        raise SystemExit("close-fit SDK guidance event should expose remaining search budget")

    transcript = AgentClientTranscriptResponse.model_validate(
        agent_routes._agent_client_transcript_for_run(result)
    )
    guidance_messages = [
        item.model_dump()
        for item in transcript.messages
        if item.type == "agent_guidance"
    ]
    if not guidance_messages:
        raise SystemExit("client transcript should include backend guidance messages")
    if not any(item.get("reason") == "continue_search_for_closer_measurements" for item in guidance_messages):
        raise SystemExit("client transcript guidance should preserve close-fit recovery reason")


def _assert_latest_deferred_score_recovery_preempts_weak_max_turn_report() -> None:
    def search_handler(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        return {
            "status": "completed",
            "provider": "fake_close_fit_search",
            "query": arguments.get("query"),
            "candidate_count": 1,
            "candidates": [
                {
                    "candidate_key": "recovered_close_candidate",
                    "name": "Recovered close candidate",
                    "source_url": "https://shop.example/recovered-close",
                }
            ],
            "external_api_called": False,
        }

    def score_handler(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        candidate_key = arguments.get("candidate_key")
        recovered_search_count = len(
            [
                item
                for item in context.run.get("state", {}).get("observations", [])
                if item.get("tool") == "search_web_candidates"
                and str(item.get("call_id") or "").startswith("backend_recovery_")
            ]
        )
        if candidate_key == "weak_candidate":
            weak_match = {
                "status": "completed",
                "candidate_key": "weak_candidate",
                "recommendation": "caution",
                "score": {"overall": 62, "size": 70, "material": 60, "price": 50, "confidence": 39},
                "measurement_deltas": {"length": 2.5, "shoulder": -2.0, "chest": 3.0, "sleeve": 1.5},
                "missing_fields": [],
                "risks": [{"level": "caution", "type": "weak_fit"}],
            }
            context.run.setdefault("state", {}).setdefault("match_results", []).append(weak_match)
            return weak_match
        if recovered_search_count == 0:
            return {
                "status": "completed",
                "candidate_key": "all",
                "score_deferred": True,
                "reason": "continue_search_for_closer_measurements",
                "evidence_status": "no_close_measurement_match_yet",
                "ranked_matches": [
                    {
                        "candidate_key": "weak_candidate",
                        "recommendation": "caution",
                        "score": {"overall": 62, "size": 70, "material": 60, "price": 50, "confidence": 39},
                        "measurement_deltas": {"length": 2.5, "shoulder": -2.0, "chest": 3.0, "sleeve": 1.5},
                        "missing_fields": [],
                        "top_risks": [{"level": "caution", "type": "weak_fit"}],
                    }
                ],
                "next_tool_suggestions": [
                    {
                        "tool": "search_web_candidates",
                        "arguments": {"query": "better measured tee close fit", "refresh_web": False},
                        "reason": "continue_search_for_closer_measurement_match",
                    }
                ],
                "close_fit_search_budget": {
                    "attempt_count": 0,
                    "attempt_limit": 4,
                    "remaining_attempts": 4,
                    "attempted_queries": [],
                },
            }
        close_match = {
            "status": "completed",
            "candidate_key": "all",
            "ranked_matches": [
                {
                    "candidate_key": "recovered_close_candidate",
                    "recommendation": "buy",
                    "score": {"overall": 94, "size": 98, "material": 84, "price": 80, "confidence": 88},
                    "measurement_deltas": {"length": 0.5, "shoulder": -0.5, "chest": 1.0, "sleeve": 0.0},
                    "missing_fields": [],
                    "top_risks": [],
                }
            ],
        }
        context.run.setdefault("state", {})["match_results"] = list(close_match["ranked_matches"])
        return close_match

    def report_handler(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        latest_score = [
            item
            for item in context.run.get("state", {}).get("observations", [])
            if item.get("tool") == "score_candidate_match"
        ][-1]
        latest_result = latest_score.get("result") or {}
        if latest_result.get("score_deferred") and not context.run.setdefault("state", {}).get(
            "force_no_close_report_after_search_exhausted"
        ):
            return {
                "status": "failed",
                "candidate_key": arguments.get("candidate_key"),
                "score_deferred": True,
                "reason": latest_result.get("reason"),
                "evidence_status": latest_result.get("evidence_status"),
                "next_tool_suggestions": latest_result.get("next_tool_suggestions") or [],
                "error": {
                    "code": "CLOSE_FIT_RECOVERY_REQUIRED_BEFORE_REPORT",
                    "message": "latest all-candidate score is deferred and must be recovered before reporting",
                },
            }
        if context.run.setdefault("state", {}).get("force_no_close_report_after_search_exhausted"):
            return {
                "status": "completed",
                "candidate_key": arguments.get("candidate_key"),
                "grounded": True,
                "grounding_validation": {"valid": True},
                "report_type": "weak_forced_report",
                "final_report": "weak report should not be written while close-fit budget remains",
            }
        return {
            "status": "completed",
            "candidate_key": arguments.get("candidate_key"),
            "grounded": True,
            "grounding_validation": {"valid": True},
            "report_type": "shortlist",
            "final_report": "최신 deferred score를 먼저 복구한 뒤 가까운 후보를 보고했습니다.",
        }

    registry = ToolRegistry(
        [
            ToolDefinition(
                name="search_web_candidates",
                description="Test latest deferred close-fit recovery search",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "refresh_web": {"type": "boolean"},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
                handler=search_handler,
            ),
            ToolDefinition(
                name="score_candidate_match",
                description="Test latest deferred score priority",
                parameters={
                    "type": "object",
                    "properties": {"candidate_key": {"type": "string"}},
                    "required": ["candidate_key"],
                    "additionalProperties": False,
                },
                handler=score_handler,
            ),
            ToolDefinition(
                name="write_purchase_report",
                description="Test latest deferred report blocker",
                parameters={
                    "type": "object",
                    "properties": {"candidate_key": {"type": "string"}},
                    "required": ["candidate_key"],
                    "additionalProperties": False,
                },
                handler=report_handler,
            ),
        ]
    )
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeWeakScoreThenDeferredAllAtMaxTurnsLlmClient()
        runtime = _runtime(
            Path(temp_dir) / "runs.json",
            llm_client=fake_llm,
            tool_registry=registry,
            max_turns=1,
        )
        result = runtime.start_run(
            {
                "goal": "Find the closest measured replacement tee.",
                "free_text": "약한 후보 하나로 끝내지 말고 실측이 최대한 비슷한 후보를 계속 찾아줘.",
            }
        )

    _assert_equal(result["status"], "completed", "latest deferred score max-turn recovery status")
    recovered_queries = [
        (item.get("arguments") or {}).get("query")
        for item in result["state"].get("observations", [])
        if item.get("tool") == "search_web_candidates"
        and str(item.get("call_id") or "").startswith("backend_recovery_")
    ]
    _assert_equal(
        recovered_queries,
        ["better measured tee close fit"],
        "latest deferred score recovery query",
    )
    if result.get("state", {}).get("force_no_close_report_after_search_exhausted"):
        raise SystemExit("latest deferred score recovery should not force a weak max-turn report")
    _assert_equal(
        result.get("final_report", {}).get("report_type"),
        "shortlist",
        "latest deferred score recovery report type",
    )


def _assert_report_blocker_guidance_recovers_before_weak_max_turn_report() -> None:
    def search_handler(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        return {
            "status": "completed",
            "provider": "fake_close_fit_search",
            "query": arguments.get("query"),
            "candidate_count": 1,
            "candidates": [
                {
                    "candidate_key": "report_recovered_close_candidate",
                    "name": "Report recovered close candidate",
                    "source_url": "https://shop.example/report-recovered-close",
                }
            ],
            "external_api_called": False,
        }

    def score_handler(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        candidate_key = arguments.get("candidate_key")
        recovered_search_count = len(
            [
                item
                for item in context.run.get("state", {}).get("observations", [])
                if item.get("tool") == "search_web_candidates"
                and str(item.get("call_id") or "").startswith("backend_recovery_")
            ]
        )
        if candidate_key == "weak_candidate":
            return {
                "status": "completed",
                "candidate_key": "weak_candidate",
                "recommendation": "caution",
                "score": {"overall": 63, "size": 70, "material": 62, "price": 55, "confidence": 42},
                "measurement_deltas": {"length": 2.4, "shoulder": -2.0, "chest": 3.2, "sleeve": 1.8},
                "missing_fields": [],
                "risks": [{"level": "caution", "type": "weak_fit"}],
            }
        if recovered_search_count <= 0:
            return {
                "status": "completed",
                "candidate_key": "all",
                "score_deferred": True,
                "reason": "continue_search_for_closer_measurements",
                "evidence_status": "no_close_measurement_match_yet",
                "next_tool_suggestions": [
                    {
                        "tool": "search_web_candidates",
                        "arguments": {"query": "report blocker closer measured tee", "refresh_web": False},
                        "reason": "continue_search_for_closer_measurement_match",
                    }
                ],
                "close_fit_search_budget": {
                    "attempt_count": 0,
                    "attempt_limit": 4,
                    "remaining_attempts": 4,
                    "attempted_queries": [],
                },
            }
        return {
            "status": "completed",
            "candidate_key": "all",
            "ranked_matches": [
                {
                    "candidate_key": "report_recovered_close_candidate",
                    "recommendation": "buy",
                    "score": {"overall": 95, "size": 98, "material": 86, "price": 80, "confidence": 90},
                    "measurement_deltas": {"length": 0.5, "shoulder": -0.5, "chest": 0.8, "sleeve": 0.0},
                    "missing_fields": [],
                    "top_risks": [],
                }
            ],
        }

    def report_handler(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        candidate_key = arguments.get("candidate_key")
        if candidate_key == "weak_candidate":
            return {
                "status": "failed",
                "candidate_key": "weak_candidate",
                "search_deferred": True,
                "reason": "candidate_not_close_enough_for_similar_fit_report",
                "evidence_status": "no_close_measurement_match_yet",
                "next_tool_suggestions": [
                    {
                        "tool": "search_web_candidates",
                        "arguments": {"query": "report blocker closer measured tee", "refresh_web": False},
                        "reason": "continue_search_for_closer_measurement_match",
                    }
                ],
                "close_fit_search_budget": {
                    "attempt_count": 0,
                    "attempt_limit": 4,
                    "remaining_attempts": 4,
                    "attempted_queries": [],
                },
                "error": {
                    "code": "CLOSE_FIT_RECOVERY_REQUIRED_BEFORE_REPORT",
                    "message": "weak single candidate cannot be reported while close-fit recovery remains",
                },
            }
        return {
            "status": "completed",
            "candidate_key": candidate_key,
            "grounded": True,
            "grounding_validation": {"valid": True},
            "report_type": "shortlist",
            "final_report": "약한 단일 후보 리포트 대신 report blocker guidance로 재검색 후 가까운 후보를 보고했습니다.",
        }

    registry = ToolRegistry(
        [
            ToolDefinition(
                name="search_web_candidates",
                description="Test report blocker close-fit recovery search",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "refresh_web": {"type": "boolean"},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
                handler=search_handler,
            ),
            ToolDefinition(
                name="score_candidate_match",
                description="Test report blocker close-fit score",
                parameters={
                    "type": "object",
                    "properties": {"candidate_key": {"type": "string"}},
                    "required": ["candidate_key"],
                    "additionalProperties": False,
                },
                handler=score_handler,
            ),
            ToolDefinition(
                name="write_purchase_report",
                description="Test report blocker close-fit report",
                parameters={
                    "type": "object",
                    "properties": {"candidate_key": {"type": "string"}},
                    "required": ["candidate_key"],
                    "additionalProperties": False,
                },
                handler=report_handler,
            ),
        ]
    )
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeWeakSingleScoreAtMaxTurnsLlmClient()
        runtime = _runtime(
            Path(temp_dir) / "runs.json",
            llm_client=fake_llm,
            tool_registry=registry,
            max_turns=1,
        )
        result = runtime.start_run(
            {
                "goal": "Find the closest measured replacement tee.",
                "free_text": "약한 후보 하나로 끝내지 말고 최대한 비슷한 옷을 찾을 때까지 계속 찾아줘.",
            }
        )

    _assert_equal(result["status"], "completed", "report blocker max-turn recovery status")
    recovered_queries = [
        (item.get("arguments") or {}).get("query")
        for item in result["state"].get("observations", [])
        if item.get("tool") == "search_web_candidates"
        and str(item.get("call_id") or "").startswith("backend_recovery_")
    ]
    _assert_equal(
        recovered_queries,
        ["report blocker closer measured tee"],
        "report blocker recovery query",
    )
    score_all = [
        item
        for item in result["state"].get("observations", [])
        if item.get("tool") == "score_candidate_match"
        and str((item.get("arguments") or {}).get("candidate_key") or "").lower() == "all"
    ]
    if not score_all:
        raise SystemExit("report blocker guidance recovery did not rescore all candidates")
    if result.get("state", {}).get("force_no_close_report_after_search_exhausted"):
        raise SystemExit("report blocker recovery should not force a weak no-close report while budget remains")
    _assert_equal(
        result.get("final_report", {}).get("grounding"),
        "write_purchase_report_all_after_max_turns_report_guidance_recovery",
        "report blocker recovery grounding",
    )


def _assert_precise_similarity_report_recovers_or_waits_for_baseline_evidence() -> None:
    old_retry_limit = os.environ.get("FITCHECK_BASELINE_ENRICH_RETRY_LIMIT")
    os.environ["FITCHECK_BASELINE_ENRICH_RETRY_LIMIT"] = "2"
    try:
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            web_result_path = temp_path / "web.json"
            keyword_path = temp_path / "keyword.json"
            web_result_path.write_text(json.dumps({"closet_result": {"closet_item": {}}}), encoding="utf-8")
            keyword_path.write_text("{}", encoding="utf-8")

            def make_context(enrich_attempts: int) -> ToolContext:
                match = {
                    "status": "completed",
                    "candidate_key": "loose_candidate",
                    "recommendation": "caution",
                    "score": {"overall": 58, "size": 62, "material": 50, "price": 60, "confidence": 28},
                    "measurement_deltas": {},
                    "missing_fields": [],
                    "risks": [],
                    "baseline_context": {"mode": "text_clue_only_after_baseline_lookup_failed"},
                }
                observations = [
                    {
                        "tool": "enrich_closet_item_from_web",
                        "arguments": {"query": "유니언즈 L 반팔 product detail size chart material"},
                        "result": {
                            "status": "completed",
                            "baseline_enrichment_status": "no_baseline_candidate",
                            "candidate_count": 0,
                        },
                    }
                    for _ in range(enrich_attempts)
                ]
                observations.extend(
                    [
                        {
                            "tool": "search_web_candidates",
                            "arguments": {"query": "유니언즈 L 반팔 비슷한 핏"},
                            "result": {
                                "status": "completed",
                                "query": "유니언즈 L 반팔 비슷한 핏",
                                "candidate_count": 1,
                                "candidates": [
                                    {
                                        "candidate_key": "loose_candidate",
                                        "name": "Loose candidate",
                                        "source_url": "https://shop.example/loose-candidate",
                                    }
                                ],
                            },
                        },
                        {
                            "tool": "score_candidate_match",
                            "arguments": {"candidate_key": "all"},
                            "result": {
                                "status": "completed",
                                "candidate_key": "all",
                                "ranked_matches": [match],
                            },
                        },
                    ]
                )
                run = {
                    "run_id": f"run_baseline_precision_{enrich_attempts}",
                    "goal": "유니언즈에서 산 L 반팔과 핏/사이즈가 최대한 비슷한 옷을 찾아줘",
                    "input": {
                        "goal": "유니언즈에서 산 L 반팔과 핏/사이즈가 최대한 비슷한 옷을 찾아줘",
                        "free_text": "대충 비슷한 후보 말고 유니언즈에서 산 L 반팔 실측과 최대한 가까운 옷을 찾고 싶어.",
                    },
                    "state": {
                        "closet_item": {
                            "id": "current_liked_item",
                            "brand": "유니언즈",
                            "name": "유니언즈 L 반팔",
                            "category": "t_shirt",
                            "size_label": "L",
                            "missing_after_lookup": [
                                "measurements.length",
                                "measurements.shoulder",
                                "measurements.chest",
                                "measurements.sleeve",
                            ],
                        },
                        "observations": observations,
                        "match_results": [match],
                        "live_products": {
                            "loose_candidate": {
                                "candidate_key": "loose_candidate",
                                "normalized_spec": {
                                    "candidate_key": "loose_candidate",
                                    "name": "Loose candidate",
                                    "source_url": "https://shop.example/loose-candidate",
                                    "category": "t_shirt",
                                    "material": "cotton",
                                    "measurements": {
                                        "length": 72,
                                        "shoulder": 54,
                                        "chest": 61,
                                        "sleeve": 24,
                                    },
                                },
                            }
                        },
                    },
                    "trace": [],
                }
                return ToolContext(
                    web_result_path=web_result_path,
                    keyword_result_path=keyword_path,
                    refresh_web_candidates=lambda: None,
                    run=run,
                )

            retry_result = _write_purchase_report({"candidate_key": "all"}, make_context(1))
            _assert_equal(retry_result["status"], "failed", "baseline precision retry report status")
            _assert_equal(
                retry_result["reason"],
                "baseline_evidence_recovery_required_before_report",
                "baseline precision retry reason",
            )
            if not retry_result.get("next_tool_suggestions"):
                raise SystemExit("baseline precision retry did not carry recovery tool suggestions")

            waiting_context = make_context(2)
            waiting_result = _write_purchase_report({"candidate_key": "all"}, waiting_context)
            _assert_equal(waiting_result["status"], "waiting_for_user", "baseline precision exhausted status")
            _assert_equal(
                waiting_result["reason"],
                "baseline_evidence_required_before_precise_similarity_report",
                "baseline precision exhausted reason",
            )
            questions = waiting_result.get("questions") or []
            if not questions or questions[0].get("field") != "baseline_evidence":
                raise SystemExit("baseline precision exhausted result did not request baseline evidence")
            if waiting_context.run.get("final_report"):
                raise SystemExit("baseline precision exhausted path should not create a weak final report")
    finally:
        if old_retry_limit is None:
            os.environ.pop("FITCHECK_BASELINE_ENRICH_RETRY_LIMIT", None)
        else:
            os.environ["FITCHECK_BASELINE_ENRICH_RETRY_LIMIT"] = old_retry_limit


def _assert_failed_report_tool_blocks_final() -> None:
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeFailedReportThenFinalLlmClient()
        runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm)
        result = runtime.start_run(
            {
                "goal": "湲곗? ?룰낵 鍮꾧탳?댁꽌 ?꾨낫瑜?異붿쿇?댁쨾",
                "free_text": "?쇨렇癒쇳듃 諛섑뙏???꾨낫瑜?湲곗? ?룰낵 鍮꾧탳?댁쨾",
            }
        )

        _assert_equal(result["status"], "failed", "failed report should block final status")
        _assert_equal(
            result["error"]["code"],
            "FINAL_REPORT_TOOL_NOT_GROUNDED",
            "failed report should block final code",
        )
        guardrail_steps = [
            step
            for step in result["trace"]
            if step["name"] == "require_grounded_report_tool_before_final"
        ]
        if not guardrail_steps:
            raise SystemExit("failed report guardrail trace was not recorded")


def _assert_deferred_score_question_blocks_premature_final_as_waiting() -> None:
    from app.routes import agent as agent_routes

    def score_handler(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        return {
            "status": "completed",
            "candidate_key": arguments.get("candidate_key") or "all",
            "score_deferred": True,
            "evidence_status": "needs_baseline_identity_clarification",
            "reason": "baseline_identity_clarification_required_before_scoring",
            "questions": [
                {
                    "field": "baseline_identity_clue",
                    "question_type": "baseline_identity_clarification",
                    "question": "기준 아이템의 종류나 상품명 단서가 필요합니다.",
                }
            ],
        }

    registry = ToolRegistry(
        [
            ToolDefinition(
                name="score_candidate_match",
                description="Test deferred score tool",
                parameters={
                    "type": "object",
                    "properties": {"candidate_key": {"type": "string"}},
                    "required": ["candidate_key"],
                    "additionalProperties": False,
                },
                handler=score_handler,
            )
        ]
    )
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeScoreAllThenFinalLlmClient()
        runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm, tool_registry=registry)
        result = runtime.start_run(
            {
                "goal": "브랜드만 알려진 기준 옷과 비슷한 옷 찾아줘",
                "free_text": "가까이 유니언즈에서 샀어. 비슷한 옷 찾아줘.",
            }
        )

    _assert_equal(result["status"], "waiting_for_user", "deferred score final recovery status")
    _assert_equal(fake_llm.index, 1, "deferred score should stop before next LLM response")
    missing_info = result["state"].get("missing_info") or []
    if not missing_info or missing_info[0].get("field") != "baseline_identity_clue":
        raise SystemExit("deferred score final recovery did not expose missing baseline identity clue")
    sdk_events = AgentSdkEventsResponse.model_validate(agent_routes._agent_sdk_events_for_run(result))
    question_events = [
        item
        for item in sdk_events.events
        if item.get("type") == "agent_question" and item.get("subtype") == "missing_info_request"
    ]
    if not question_events:
        raise SystemExit("SDK events did not project waiting_for_user missing_info request")
    _assert_equal(
        question_events[0].get("question_count"),
        1,
        "SDK missing-info question count",
    )
    _assert_equal(
        (question_events[0].get("questions") or [{}])[0].get("field"),
        "baseline_identity_clue",
        "SDK missing-info question field",
    )
    if not str((question_events[0].get("source_endpoints") or {}).get("reply") or "").endswith("/reply"):
        raise SystemExit("SDK missing-info event did not expose reply endpoint")
    guardrail_steps = [
        step
        for step in result["trace"]
        if step.get("name") == "adopt_deferred_score_missing_info_question"
    ]
    if not guardrail_steps:
        raise SystemExit("deferred score final recovery guardrail trace missing")


def _assert_final_without_tools_is_rejected() -> None:
    with TemporaryDirectory() as temp_dir:
        fake_llm = FakeFinalWithoutToolsLlmClient()
        runtime = _runtime(Path(temp_dir) / "runs.json", llm_client=fake_llm)
        result = runtime.start_run(
            {
                "goal": "湲곗? ?룰낵 鍮꾧탳?댁꽌 ?꾨낫瑜??щ룄 ?섎뒗吏 ?먮떒",
                "free_text": "?꾨낫瑜?湲곗? ?룰낵 鍮꾧탳?댁쨾",
            }
        )

        _assert_equal(result["status"], "failed", "no-tool final status")
        _assert_equal(
            result["error"]["code"],
            "NO_TOOL_CALLS_BEFORE_FINAL",
            "no-tool final guardrail",
        )
        guardrail_steps = [
            step
            for step in result["trace"]
            if step["name"] == "require_tool_observation_before_final"
        ]
        if not guardrail_steps:
            raise SystemExit("no-tool final guardrail trace was not recorded")


def _runtime(
    run_store_path: Path,
    llm_client: Any | None = None,
    *,
    max_turns: int = 12,
    tool_registry: ToolRegistry | None = None,
) -> FitCheckAgentRuntime:
    output_dir = run_store_path.parent
    web_result_path = output_dir / "web_candidates_match_result.json"
    keyword_result_path = output_dir / "web_keyword_probe" / "keyword_web_probe_result.json"
    if not web_result_path.exists():
        _write_test_cached_web_result(web_result_path)
    if not keyword_result_path.exists():
        keyword_result_path.parent.mkdir(parents=True, exist_ok=True)
        keyword_result_path.write_text(
            json.dumps(
                {
                    "keywords": [],
                    "source_configured": False,
                    "image_probe_configured": False,
                    "page_results": [],
                    "image_results": [],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return FitCheckAgentRuntime(
        run_store_path=run_store_path,
        web_result_path=web_result_path,
        keyword_result_path=keyword_result_path,
        refresh_web_candidates=lambda: _write_test_cached_web_result(web_result_path),
        llm_client=llm_client,
        tool_registry=tool_registry,
        max_turns=max_turns,
    )


def _tool_response(response_id: str, call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": response_id,
        "output": [
            {
                "type": "function_call",
                "call_id": call_id,
                "name": name,
                "arguments": json.dumps(arguments, ensure_ascii=False),
            }
        ],
    }


def _message_response(response_id: str, text: str) -> dict[str, Any]:
    return {
        "id": response_id,
        "output_text": text,
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": text,
                    }
                ],
            }
        ],
    }


def _assert_equal(observed: object, expected: object, label: str) -> None:
    if observed != expected:
        raise SystemExit(f"{label} failed: expected {expected!r}, observed {observed!r}")


def _restore_env(name: str, value: str | None) -> None:
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


def _assert_no_user_visible_english_fragments(text: str, label: str) -> None:
    forbidden_fragments = [
        "a candidate",
        "is missing evidence",
        "No additional risk",
        "page fetch",
        "Material evidence",
        "Candidate price",
        "measurement evidence is missing",
        "differs from the liked baseline",
    ]
    found = [fragment for fragment in forbidden_fragments if fragment in text]
    if found:
        raise SystemExit(f"{label} contains English fallback fragments: {found}")


if __name__ == "__main__":
    main()
