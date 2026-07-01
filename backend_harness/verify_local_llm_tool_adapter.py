from __future__ import annotations

import os
from typing import Any

from app.tools import llm_client
from app.tools.llm_client import ResponsesApiClient, get_llm_status
from app.agent_runtime import _llm_response_error_payload


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self.payload


class FakeRequests:
    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []

    def get(self, url: str, headers: dict[str, str], timeout: float) -> FakeResponse:
        if not url.endswith("/models"):
            raise AssertionError(f"unexpected GET URL: {url}")
        return FakeResponse({"data": [{"id": "llama3.1"}]})

    def post(
        self,
        url: str,
        headers: dict[str, str],
        json: dict[str, Any],
        timeout: float,
    ) -> FakeResponse:
        if not url.endswith("/chat/completions"):
            raise AssertionError(f"unexpected POST URL: {url}")
        self.posts.append({"url": url, "json": json})
        if len(self.posts) == 1:
            return FakeResponse(
                {
                    "id": "chat_1",
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {
                                            "name": "load_closet_memory",
                                            "arguments": "{}",
                                        },
                                    },
                                    {
                                        "id": "call_2",
                                        "type": "function",
                                        "function": {
                                            "name": "search_web_candidates",
                                            "arguments": '{"query": "피그먼트 반팔티", "refresh_web": false}',
                                        },
                                    }
                                ],
                            }
                        }
                    ],
                }
            )
        return FakeResponse(
            {
                "id": "chat_2",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "tool observation 기준으로 최종 답변을 작성했습니다.",
                        }
                    }
                ],
            }
        )


class FakeLegacyFunctionCallRequests:
    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []

    def get(self, url: str, headers: dict[str, str], timeout: float) -> FakeResponse:
        if not url.endswith("/models"):
            raise AssertionError(f"unexpected GET URL: {url}")
        return FakeResponse({"data": [{"id": "legacy-local"}]})

    def post(
        self,
        url: str,
        headers: dict[str, str],
        json: dict[str, Any],
        timeout: float,
    ) -> FakeResponse:
        if not url.endswith("/chat/completions"):
            raise AssertionError(f"unexpected POST URL: {url}")
        self.posts.append({"url": url, "json": json})
        if len(self.posts) == 1:
            return FakeResponse(
                {
                    "id": "chat_legacy_1",
                    "choices": [
                        {
                            "finish_reason": "function_call",
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "function_call": {
                                    "name": "search_web_candidates",
                                    "arguments": '{"query": "레거시 반팔티", "refresh_web": false}',
                                },
                            },
                        }
                    ],
                }
            )
        return FakeResponse(
            {
                "id": "chat_legacy_2",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "legacy function output 기준으로 최종 답변을 작성했습니다.",
                        }
                    }
                ],
            }
        )


class FakeSdkModel:
    def __init__(self, payload: dict[str, Any], request_id: str | None = None) -> None:
        self.payload = payload
        self._request_id = request_id

    def model_dump(self, mode: str = "json", exclude_none: bool = True) -> dict[str, Any]:
        return self.payload


class FakeSdkResponse:
    def __init__(self, status_code: int, request_id: str | None = None) -> None:
        self.status_code = status_code
        self.headers = {"x-request-id": request_id} if request_id else {}


class FakeSdkStatusError(Exception):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.request_id = "req_sdk_rate_limit"
        self.response = FakeSdkResponse(status_code, self.request_id)


class FakeOpenAI:
    instances: list["FakeOpenAI"] = []

    def __init__(self, api_key: str, base_url: str, timeout: float, max_retries: int) -> None:
        self.api_key = api_key
        self.base_url = str(base_url)
        self.timeout = timeout
        self.max_retries = max_retries
        self.models = self.Models(self)
        self.responses = self.Responses(self)
        self.chat = self.Chat(self)
        self.model_list_calls = 0
        self.response_create_payloads: list[dict[str, Any]] = []
        self.chat_create_payloads: list[dict[str, Any]] = []
        FakeOpenAI.instances.append(self)

    class Models:
        def __init__(self, parent: "FakeOpenAI") -> None:
            self.parent = parent

        def list(self) -> FakeSdkModel:
            self.parent.model_list_calls += 1
            return FakeSdkModel({"data": [{"id": "gpt-sdk-test"}]})

    class Responses:
        def __init__(self, parent: "FakeOpenAI") -> None:
            self.parent = parent

        def create(self, **payload: Any) -> FakeSdkModel:
            self.parent.response_create_payloads.append(payload)
            if payload.get("model") == "sdk-rate-limit":
                raise FakeSdkStatusError("rate limit from fake sdk", 429)
            return FakeSdkModel(
                {
                    "id": "resp_sdk_1",
                    "output_text": "",
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_sdk_1",
                            "name": "search_web_candidates",
                            "arguments": '{"query": "sdk denim"}',
                        }
                    ],
                },
                request_id="req_resp_sdk_1",
            )

    class Chat:
        def __init__(self, parent: "FakeOpenAI") -> None:
            self.completions = FakeOpenAI.ChatCompletions(parent)

    class ChatCompletions:
        def __init__(self, parent: "FakeOpenAI") -> None:
            self.parent = parent

        def create(self, **payload: Any) -> FakeSdkModel:
            self.parent.chat_create_payloads.append(payload)
            if payload.get("model") == "sdk-rate-limit":
                raise FakeSdkStatusError("rate limit from fake sdk", 429)
            return FakeSdkModel(
                {
                    "id": "chat_sdk_1",
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "sdk chat final",
                            }
                        }
                    ],
                },
                request_id="req_chat_sdk_1",
            )


def main() -> None:
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
            "FITCHECK_LLM_USE_OPENAI_SDK",
            "OPENAI_API_KEY",
            "OPENAI_MODEL",
            "OPENAI_BASE_URL",
        ]
    }
    saved_requests = llm_client.requests
    saved_openai = llm_client.OpenAI
    fake_requests = FakeRequests()
    try:
        for key in saved_env:
            os.environ.pop(key, None)
        os.environ["FITCHECK_LLM_BASE_URL"] = "http://local-llm.test/v1"
        os.environ["FITCHECK_LLM_API_KEY"] = "local-test-key"
        os.environ["FITCHECK_LLM_MODEL"] = "llama3.1"
        os.environ["FITCHECK_LLM_API"] = "chat_completions"
        os.environ["FITCHECK_LLM_TIMEOUT"] = "5"
        os.environ["FITCHECK_LLM_MAX_RETRIES"] = "0"
        os.environ["FITCHECK_LLM_USE_OPENAI_SDK"] = "0"
        llm_client.requests = fake_requests

        status = get_llm_status()
        _assert_equal(status["status"], "connected", "local status")
        _assert_equal(status["api_style"], "chat_completions", "local api style")
        _assert_equal(status["hosted_tools_supported"], False, "hosted tools disabled")
        _assert_equal(status["local_function_tools_supported"], True, "local tools enabled")

        client = ResponsesApiClient()
        tools = [
            {
                "type": "function",
                "name": "load_closet_memory",
                "description": "Load closet memory.",
                "parameters": {"type": "object", "properties": {}, "required": []},
                "strict": False,
            },
            {
                "type": "function",
                "name": "search_web_candidates",
                "description": "Search online clothing candidates.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "refresh_web": {"type": "boolean"},
                    },
                    "required": ["query"],
                },
                "strict": False,
            },
        ]
        first = client.create_response(
            instructions="Use tools.",
            input_items='{"goal": "test"}',
            tools=tools,
        )
        _assert_equal(first["api_style"], "chat_completions", "first response style")
        _assert_equal(first["output"][0]["type"], "function_call", "tool call type")
        _assert_equal(first["output"][0]["name"], "load_closet_memory", "tool call name")
        _assert_equal(len(first["output"]), 2, "multi tool call output count")
        _assert_equal(first["output"][1]["call_id"], "call_2", "second tool call id")
        _assert_equal(first["output"][1]["name"], "search_web_candidates", "second tool call name")
        _assert_equal(
            json_loads(first["output"][1]["arguments"])["query"],
            "피그먼트 반팔티",
            "second tool call arguments preserved",
        )
        first_payload_tools = fake_requests.posts[0]["json"]["tools"]
        _assert_equal(
            first_payload_tools[0]["function"]["name"],
            "load_closet_memory",
            "chat tool schema conversion",
        )
        _assert_equal(
            first_payload_tools[0]["function"]["strict"],
            False,
            "chat tool strict conversion",
        )
        _assert_equal(
            first_payload_tools[1]["function"]["name"],
            "search_web_candidates",
            "second chat tool schema conversion",
        )
        _assert_equal(
            first_payload_tools[1]["function"]["strict"],
            False,
            "second chat tool strict conversion",
        )
        _assert_equal(
            first_payload_tools[1]["function"]["parameters"]["required"],
            ["query"],
            "second chat tool required args preserved",
        )

        second = client.create_response(
            instructions="Use tools.",
            input_items=[
                {
                    "id": "rs_local_ignored",
                    "type": "reasoning",
                    "summary": [
                        {
                            "type": "summary_text",
                            "text": "Responses-only reasoning context.",
                        }
                    ],
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": '{"status": "completed"}',
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_2",
                    "output": '{"status": "completed", "candidate_count": 1}',
                },
                {
                    "role": "user",
                    "content": '{"backend_runtime_guidance": {"next_tool_suggestions": [{"tool": "fetch_product_page"}]}}',
                },
            ],
            tools=tools,
            previous_response_id=first["id"],
        )
        _assert_equal(second["output_text"], "tool observation 기준으로 최종 답변을 작성했습니다.", "final text")
        second_messages = fake_requests.posts[1]["json"]["messages"]
        if any("rs_local_ignored" in str(message) for message in second_messages):
            raise SystemExit("Responses reasoning context leaked into local chat messages")
        roles = [message["role"] for message in second_messages]
        if "tool" not in roles:
            raise SystemExit("tool output was not submitted as a chat tool message")
        assistant_messages = [
            message
            for message in second_messages
            if message["role"] == "assistant" and message.get("tool_calls")
        ]
        if not assistant_messages:
            raise SystemExit("assistant tool_calls history was not preserved")
        _assert_equal(
            len(assistant_messages[-1]["tool_calls"]),
            2,
            "assistant multi tool_calls history count",
        )
        tool_messages = [
            message for message in second_messages if message["role"] == "tool"
        ]
        _assert_equal(len(tool_messages), 2, "chat tool output message count")
        _assert_equal(
            [message["tool_call_id"] for message in tool_messages],
            ["call_1", "call_2"],
            "chat tool output call ids",
        )
        guidance_messages = [
            message
            for message in second_messages
            if message["role"] == "user" and "backend_runtime_guidance" in message.get("content", "")
        ]
        if not guidance_messages:
            raise SystemExit("backend runtime guidance was not submitted as a chat user message")

        durable_client = ResponsesApiClient()
        durable_run = {
            "run_id": "run_local_resume",
            "input": {"goal": "durable local resume", "free_text": "segment tee"},
            "state": {
                "observations": [
                    {
                        "tool": "load_closet_memory",
                        "call_id": "call_1",
                        "result": {"status": "completed", "closet_item": {"name": "owned tee"}},
                    },
                    {
                        "tool": "search_web_candidates",
                        "call_id": "call_2",
                        "result": {"status": "completed", "candidate_count": 1},
                    },
                ]
            },
            "trace": [
                {
                    "step_type": "llm_response",
                    "name": "chat_completions",
                    "status": "completed",
                    "input": {},
                    "output": {"response_id": "chat_1"},
                },
                {
                    "step_type": "tool_call",
                    "name": "load_closet_memory",
                    "status": "started",
                    "input": {"call_id": "call_1", "arguments": {}},
                    "output": {},
                },
                {
                    "step_type": "tool_call",
                    "name": "search_web_candidates",
                    "status": "started",
                    "input": {"call_id": "call_2", "arguments": {"query": "segment tee"}},
                    "output": {},
                },
                {
                    "step_type": "tool_output",
                    "name": "load_closet_memory",
                    "status": "completed",
                    "input": {},
                    "output": {"call_id": "call_1"},
                },
                {
                    "step_type": "tool_output",
                    "name": "search_web_candidates",
                    "status": "completed",
                    "input": {},
                    "output": {"call_id": "call_2"},
                },
            ],
        }
        pending_outputs = [
            {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": '{"status": "completed"}',
            },
            {
                "type": "function_call_output",
                "call_id": "call_2",
                "output": '{"status": "completed", "candidate_count": 1}',
            },
        ]
        prepared_items = durable_client.prepare_input_items(
            run=durable_run,
            instructions="Use tools.",
            input_items=pending_outputs,
        )
        durable_response = durable_client.create_response(
            instructions="Use tools.",
            input_items=prepared_items,
            tools=tools,
            previous_response_id="chat_1",
        )
        _assert_equal(durable_response["output_text"], "tool observation 기준으로 최종 답변을 작성했습니다.", "durable final text")
        durable_messages = fake_requests.posts[2]["json"]["messages"]
        durable_assistant_messages = [
            message
            for message in durable_messages
            if message["role"] == "assistant" and message.get("tool_calls")
        ]
        if not durable_assistant_messages:
            raise SystemExit("durable local chat history did not reconstruct assistant tool_calls")
        _assert_equal(
            [call["id"] for call in durable_assistant_messages[-1]["tool_calls"]],
            ["call_1", "call_2"],
            "durable reconstructed tool call ids",
        )
        durable_tool_messages = [
            message for message in durable_messages if message["role"] == "tool"
        ]
        _assert_equal(
            [message["tool_call_id"] for message in durable_tool_messages],
            ["call_1", "call_2"],
            "durable reconstructed tool result ids",
        )
        if not any("durable local resume" in message.get("content", "") for message in durable_messages if message["role"] == "user"):
            raise SystemExit("durable local chat history did not include run input")

        legacy_requests = FakeLegacyFunctionCallRequests()
        llm_client.requests = legacy_requests
        legacy_client = ResponsesApiClient()
        legacy_first = legacy_client.create_response(
            instructions="Use tools.",
            input_items='{"goal": "legacy test"}',
            tools=tools,
        )
        _assert_equal(
            legacy_first["output"][0]["compatibility"],
            "deprecated_chat_function_call",
            "legacy compatibility marker",
        )
        _assert_equal(
            legacy_first["output"][0]["name"],
            "search_web_candidates",
            "legacy function call name",
        )
        _assert_equal(
            json_loads(legacy_first["output"][0]["arguments"])["query"],
            "레거시 반팔티",
            "legacy function call arguments",
        )
        legacy_call_id = legacy_first["output"][0]["call_id"]
        _assert_equal(
            legacy_call_id,
            "chat_legacy_1_function_call",
            "legacy synthetic call id",
        )
        legacy_second = legacy_client.create_response(
            instructions="Use tools.",
            input_items=[
                {
                    "type": "function_call_output",
                    "call_id": legacy_call_id,
                    "output": '{"status": "completed", "candidate_count": 1}',
                }
            ],
            tools=tools,
            previous_response_id=legacy_first["id"],
        )
        _assert_equal(
            legacy_second["output_text"],
            "legacy function output 기준으로 최종 답변을 작성했습니다.",
            "legacy final text",
        )
        legacy_second_messages = legacy_requests.posts[1]["json"]["messages"]
        legacy_assistant_messages = [
            message
            for message in legacy_second_messages
            if message["role"] == "assistant" and message.get("function_call")
        ]
        if not legacy_assistant_messages:
            raise SystemExit("legacy assistant function_call history was not preserved")
        legacy_function_messages = [
            message for message in legacy_second_messages if message["role"] == "function"
        ]
        _assert_equal(len(legacy_function_messages), 1, "legacy function output message count")
        _assert_equal(
            legacy_function_messages[0]["name"],
            "search_web_candidates",
            "legacy function output name",
        )
        if "candidate_count" not in legacy_function_messages[0]["content"]:
            raise SystemExit("legacy function output content was not submitted")

        for key in saved_env:
            os.environ.pop(key, None)
        FakeOpenAI.instances = []
        llm_client.OpenAI = FakeOpenAI
        os.environ["OPENAI_API_KEY"] = "sk-test"
        os.environ["FITCHECK_LLM_MODEL"] = "gpt-sdk-test"
        os.environ["FITCHECK_LLM_API"] = "responses"
        os.environ["FITCHECK_LLM_TIMEOUT"] = "7"
        os.environ["FITCHECK_LLM_MAX_RETRIES"] = "0"
        sdk_status = get_llm_status()
        _assert_equal(sdk_status["status"], "connected", "OpenAI SDK status")
        _assert_equal(sdk_status["transport"], "openai_python_sdk", "OpenAI SDK status transport")
        if not FakeOpenAI.instances or FakeOpenAI.instances[-1].model_list_calls != 1:
            raise SystemExit("OpenAI SDK models.list was not used for status probing")
        sdk_client = ResponsesApiClient()
        sdk_response = sdk_client.create_response(
            instructions="Use tools.",
            input_items='{"goal": "sdk"}',
            tools=tools,
            previous_response_id="resp_previous",
        )
        _assert_equal(sdk_response["id"], "resp_sdk_1", "OpenAI SDK response id")
        _assert_equal(
            sdk_response["provider_request_id"],
            "req_resp_sdk_1",
            "OpenAI SDK responses request id",
        )
        sdk_instance = FakeOpenAI.instances[-1]
        if not sdk_instance.response_create_payloads:
            raise SystemExit("OpenAI SDK responses.create was not called")
        sdk_payload = sdk_instance.response_create_payloads[-1]
        _assert_equal(sdk_payload["previous_response_id"], "resp_previous", "OpenAI SDK previous response id")
        _assert_equal(sdk_payload["tools"][0]["name"], "load_closet_memory", "OpenAI SDK Responses tools")

        os.environ["FITCHECK_LLM_API"] = "chat_completions"
        os.environ["FITCHECK_LLM_USE_OPENAI_SDK"] = "1"
        sdk_chat_client = ResponsesApiClient()
        sdk_chat_response = sdk_chat_client.create_response(
            instructions="Use tools.",
            input_items='{"goal": "sdk chat"}',
            tools=tools,
        )
        _assert_equal(sdk_chat_response["output_text"], "sdk chat final", "OpenAI SDK chat output")
        _assert_equal(
            sdk_chat_response["provider_request_id"],
            "req_chat_sdk_1",
            "OpenAI SDK chat request id",
        )
        sdk_chat_instance = FakeOpenAI.instances[-1]
        if not sdk_chat_instance.chat_create_payloads:
            raise SystemExit("OpenAI SDK chat.completions.create was not called")
        _assert_equal(
            sdk_chat_instance.chat_create_payloads[-1]["tools"][0]["function"]["name"],
            "load_closet_memory",
            "OpenAI SDK chat tool conversion",
        )

        os.environ["FITCHECK_LLM_MODEL"] = "sdk-rate-limit"
        os.environ["FITCHECK_LLM_API"] = "responses"
        os.environ.pop("FITCHECK_LLM_USE_OPENAI_SDK", None)
        try:
            ResponsesApiClient().create_response(
                instructions="Use tools.",
                input_items='{"goal": "sdk rate limit"}',
                tools=tools,
            )
        except Exception as exc:
            error_payload = _llm_response_error_payload(
                exc,
                pending_tool_output_count=1,
                pending_tool_outputs_present=True,
            )
            _assert_equal(
                error_payload["provider_error_code"],
                "HTTP_429_RATE_LIMITED",
                "OpenAI SDK rate limit provider code",
            )
            _assert_equal(
                error_payload["provider_error_category"],
                "rate_limit",
                "OpenAI SDK rate limit category",
            )
            _assert_equal(
                error_payload["provider_request_id"],
                "req_sdk_rate_limit",
                "OpenAI SDK rate limit request id",
            )
            _assert_equal(error_payload["retryable"], True, "OpenAI SDK rate limit retryable")
            _assert_equal(error_payload["resume_available"], True, "OpenAI SDK rate limit resumable")
        else:
            raise SystemExit("OpenAI SDK rate limit exception was not raised")
    finally:
        llm_client.requests = saved_requests
        llm_client.OpenAI = saved_openai
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    print("Local LLM tool adapter verification passed.")


def _assert_equal(observed: object, expected: object, label: str) -> None:
    if observed != expected:
        raise SystemExit(f"{label} failed: expected {expected!r}, observed {observed!r}")


def json_loads(value: str) -> dict[str, Any]:
    import json

    return json.loads(value)


if __name__ == "__main__":
    main()
