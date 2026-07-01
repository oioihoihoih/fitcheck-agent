from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class VerificationSpec(BaseModel):
    commands: list[str] = Field(default_factory=list)
    timeout_seconds: int = Field(default=120, ge=1, le=3600)


class LlmProviderSpec(BaseModel):
    provider: str = "openai_compatible"
    api_style: Literal["responses", "chat_completions"] = "chat_completions"
    base_url: str | None = None
    model: str | None = None
    api_key_env: str | None = None
    supports_tool_calling: bool = True
    supports_stateful_responses: bool = False
    notes: str | None = None


class HarnessTaskCreate(BaseModel):
    prompt: str
    idempotency_key: str | None = None
    cwd: str | None = None
    workspace_mode: Literal["provided_cwd", "client_isolated"] = "provided_cwd"
    isolated_instructions: str | None = None
    agent: str | None = None
    skills: list[str] = Field(default_factory=list)
    sandbox: Literal["read-only", "workspace-write", "danger-full-access"] = "workspace-write"
    model: str | None = None
    parent_task_id: str | None = None
    join_policy: Literal["manual", "all", "any"] = "manual"
    expected_state_version: int | None = None
    llm_provider: LlmProviderSpec | None = None
    verification: VerificationSpec = Field(default_factory=VerificationSpec)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_prompt(self) -> "HarnessTaskCreate":
        if not self.prompt.strip():
            raise ValueError("prompt must not be empty")
        return self


class HarnessTaskReply(BaseModel):
    prompt: str
    expected_state_version: int | None = None

    @model_validator(mode="after")
    def validate_prompt(self) -> "HarnessTaskReply":
        if not self.prompt.strip():
            raise ValueError("prompt must not be empty")
        return self


class HarnessTaskAnnotation(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    tag: str | None = Field(default=None, max_length=80)
    summary: str | None = Field(default=None, max_length=2000)
    expected_state_version: int | None = None

    @model_validator(mode="after")
    def validate_annotation(self) -> "HarnessTaskAnnotation":
        for field_name in ("title", "tag", "summary"):
            value = getattr(self, field_name)
            if value is not None and not value.strip():
                raise ValueError(f"{field_name} must not be empty when provided")
        return self


class HarnessTaskFork(BaseModel):
    prompt: str | None = None
    idempotency_key: str | None = None
    expected_state_version: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_fork(self) -> "HarnessTaskFork":
        if self.prompt is not None and not self.prompt.strip():
            raise ValueError("prompt must not be empty when provided")
        if self.idempotency_key is not None and not self.idempotency_key.strip():
            raise ValueError("idempotency_key must not be empty when provided")
        return self


class HarnessQueuedMessage(BaseModel):
    prompt: str
    priority: Literal["now", "next", "later"] = "later"
    message_uuid: str | None = None
    expected_state_version: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_prompt(self) -> "HarnessQueuedMessage":
        if not self.prompt.strip():
            raise ValueError("prompt must not be empty")
        if self.message_uuid is not None and not self.message_uuid.strip():
            raise ValueError("message_uuid must not be empty when provided")
        return self


class HarnessQueuedMessageCancel(BaseModel):
    message_uuid: str
    expected_state_version: int | None = None

    @model_validator(mode="after")
    def validate_message_uuid(self) -> "HarnessQueuedMessageCancel":
        if not self.message_uuid.strip():
            raise ValueError("message_uuid must not be empty")
        return self


class LoopEvidenceContract(BaseModel):
    trace_sources: list[str] = Field(default_factory=list)
    eval_targets: list[str] = Field(default_factory=list)
    verification_commands: list[str] = Field(default_factory=list)
    termination_conditions: list[str] = Field(default_factory=list)


class HarnessLoopCreate(BaseModel):
    name: str
    task: HarnessTaskCreate
    interval_seconds: int = Field(default=600, ge=60)
    max_iterations: int = Field(default=12, ge=1, le=1000)
    evidence_contract: LoopEvidenceContract | None = None


class HarnessLoopTick(BaseModel):
    force: bool = False
    start: bool = True
    background: bool = True


class HarnessCommand(BaseModel):
    command: Literal["pause", "resume", "cancel"]
    expected_state_version: int | None = None


class WorkerHeartbeat(BaseModel):
    task_id: str
    lease_epoch: int = Field(ge=1)
    lease_seconds: int = Field(default=300, ge=1, le=3600)

    @model_validator(mode="after")
    def validate_task_id(self) -> "WorkerHeartbeat":
        if not self.task_id.strip():
            raise ValueError("task_id must not be empty")
        return self


class LlmProbeRequest(BaseModel):
    provider: LlmProviderSpec
    timeout_seconds: int = Field(default=5, ge=1, le=60)


class HarnessGuidanceRequest(BaseModel):
    goal: Literal["provider", "task", "tool-evidence", "eval", "worker", "failure", "subagent", "trace"] = "failure"
    status: str | None = None
    task_id: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class ToolCallRecord(BaseModel):
    call_id: str | None = None
    tool_name: str
    input: dict[str, Any] = Field(default_factory=dict)
    status: Literal["queued", "running", "completed", "failed", "cancelled"] = "queued"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_tool_name(self) -> "ToolCallRecord":
        if not self.tool_name.strip():
            raise ValueError("tool_name must not be empty")
        return self


class ObservationRecord(BaseModel):
    call_id: str | None = None
    tool_name: str
    result: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    status: Literal["completed", "failed"] = "completed"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_tool_name(self) -> "ObservationRecord":
        if not self.tool_name.strip():
            raise ValueError("tool_name must not be empty")
        return self


class PermissionDecisionRecord(BaseModel):
    tool_name: str
    behavior: Literal["allow", "ask", "deny", "passthrough"]
    call_id: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    updated_input: dict[str, Any] | None = None
    decision_reason: dict[str, Any] = Field(default_factory=dict)
    message: str | None = None
    blocked_path: str | None = None
    suggestions: list[dict[str, Any]] = Field(default_factory=list)
    status: Literal["recorded", "applied", "superseded"] = "recorded"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_permission_decision(self) -> "PermissionDecisionRecord":
        if not self.tool_name.strip():
            raise ValueError("tool_name must not be empty")
        if self.call_id is not None and not self.call_id.strip():
            raise ValueError("call_id must not be empty when provided")
        if self.message is not None and not self.message.strip():
            raise ValueError("message must not be empty when provided")
        return self


class PermissionRequestRecord(BaseModel):
    request_id: str | None = None
    tool_name: str
    tool_use_id: str
    input: dict[str, Any] = Field(default_factory=dict)
    permission_suggestions: list[dict[str, Any]] = Field(default_factory=list)
    blocked_path: str | None = None
    decision_reason: str | None = None
    title: str | None = None
    display_name: str | None = None
    agent_id: str | None = None
    description: str | None = None
    status: Literal["pending", "cancelled"] = "pending"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_permission_request(self) -> "PermissionRequestRecord":
        if self.request_id is not None and not self.request_id.strip():
            raise ValueError("request_id must not be empty when provided")
        if not self.tool_name.strip():
            raise ValueError("tool_name must not be empty")
        if not self.tool_use_id.strip():
            raise ValueError("tool_use_id must not be empty")
        return self


class PermissionRequestResolution(BaseModel):
    request_id: str
    behavior: Literal["allow", "deny", "cancel"]
    updated_input: dict[str, Any] | None = None
    message: str | None = None
    updated_permissions: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_permission_request_resolution(self) -> "PermissionRequestResolution":
        if not self.request_id.strip():
            raise ValueError("request_id must not be empty")
        if self.message is not None and not self.message.strip():
            raise ValueError("message must not be empty when provided")
        return self


class TaskProgressItem(BaseModel):
    content: str = Field(max_length=500)
    active_form: str = Field(max_length=500)
    status: Literal["pending", "in_progress", "completed"]
    item_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_progress_item(self) -> "TaskProgressItem":
        if not self.content.strip():
            raise ValueError("content must not be empty")
        if not self.active_form.strip():
            raise ValueError("active_form must not be empty")
        if self.item_id is not None and not self.item_id.strip():
            raise ValueError("item_id must not be empty when provided")
        return self


class TaskProgressUpdate(BaseModel):
    items: list[TaskProgressItem]
    expected_state_version: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ArtifactRecord(BaseModel):
    kind: str
    path: str | None = None
    url: str | None = None
    digest: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_reference(self) -> "ArtifactRecord":
        if not self.kind.strip():
            raise ValueError("kind must not be empty")
        if not (self.path or self.url):
            raise ValueError("path or url is required")
        return self


class EvalCaseRecord(BaseModel):
    name: str
    source_task_id: str | None = None
    failure_label: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    expected: dict[str, Any] = Field(default_factory=dict)
    verifier_command: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    status: Literal["active", "archived"] = "active"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_eval_case(self) -> "EvalCaseRecord":
        if not self.name.strip():
            raise ValueError("name must not be empty")
        if self.verifier_command is not None and not self.verifier_command.strip():
            raise ValueError("verifier_command must not be empty when provided")
        return self
