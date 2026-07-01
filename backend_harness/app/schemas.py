from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# Boundary limits for user-supplied text. Mature agents bound input size at the
# API edge to prevent token-cost blowups, memory amplification, and oversized
# state/trace persistence. See docs/24 gap #14.
MAX_FREE_TEXT_CHARS = 8000
MAX_GOAL_CHARS = 4000
MAX_CANDIDATE_URL_CHARS = 2048
MAX_REPLY_MESSAGE_CHARS = 8000
# Per-run control bounds. See docs/24 gaps #9 (budget) and #10 (permissions).
MAX_TURNS_CEILING = 40
MIN_TURNS_FLOOR = 1
TOOL_BUDGET_CEILING = 200


def _validate_candidate_url(value: str | None) -> str | None:
    """Reject a non-fetchable candidate_url instead of silently searching it.

    docs/24 gap #12: a malformed URL was accepted and quietly used as a generic
    search query with no acknowledgement. We now require a real http(s) URL when
    a candidate_url is provided (empty/None is fine — it's optional).
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or " " in text:
        raise ValueError(
            "candidate_url must be a valid http(s) URL "
            "(e.g. https://example.com/product/123)."
        )
    return text


@dataclass
class ToolCall:
    name: str
    status: str
    output: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Attachment(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    filename: str | None = None
    stored_name: str | None = None
    media_type: str | None = None
    size_bytes: int | None = None
    url: str | None = None
    role: str = "current_clothing_photo"
    source: str = "user_upload"


class RunControls(BaseModel):
    """Caller-supplied per-run controls: budget and tool permissions.

    docs/24 gaps #9 (no per-run budget/max-turns) and #10 (no allow/deny gate).
    The runtime reads these instead of silently ignoring them.
    """

    model_config = ConfigDict(extra="forbid")

    max_turns: int | None = Field(default=None, ge=MIN_TURNS_FLOOR, le=MAX_TURNS_CEILING)
    tool_budget: int | None = Field(default=None, ge=1, le=TOOL_BUDGET_CEILING)
    allowed_tools: list[str] | None = None
    denied_tools: list[str] | None = None
    search_provider: str | None = None
    ocr_provider: str | None = None


class AgentRunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    goal: str | None = Field(default=None, max_length=MAX_GOAL_CHARS)
    free_text: str | None = Field(default=None, max_length=MAX_FREE_TEXT_CHARS)
    candidate_url: str | None = Field(default=None, max_length=MAX_CANDIDATE_URL_CHARS)
    attachments: list[Attachment] = Field(default_factory=list)
    refresh_web: bool = False
    controls: RunControls | None = None

    _check_candidate_url = field_validator("candidate_url")(_validate_candidate_url)

    @model_validator(mode="after")
    def require_initial_user_input(self) -> "AgentRunCreateRequest":
        if (
            _has_non_empty_text(self.goal)
            or _has_non_empty_text(self.free_text)
            or _has_non_empty_text(self.candidate_url)
            or bool(self.attachments)
        ):
            return self
        raise ValueError("Agent run requires goal, free_text, candidate_url, or attachments.")

    def to_runtime_payload(self, session_id: str) -> dict[str, Any]:
        payload = self.model_dump(mode="json", exclude_none=True)
        payload["session_id"] = session_id
        return payload


class AgentReplyAnswer(BaseModel):
    field: str
    value: Any
    candidate_key: str | None = None
    closet_item_id: str | None = None
    missing_fields: str | list[str] | None = None


class AgentRunReplyRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    message: str | None = Field(default=None, max_length=MAX_REPLY_MESSAGE_CHARS)
    answers: list[AgentReplyAnswer] = Field(default_factory=list)
    attachments: list[Attachment] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_reply_input(self) -> "AgentRunReplyRequest":
        if _has_non_empty_text(self.message) or bool(self.answers) or bool(self.attachments):
            return self
        raise ValueError("Agent reply requires message, answers, or attachments.")

    def to_runtime_payload(self, session_id: str) -> dict[str, Any]:
        payload = self.model_dump(mode="json", exclude_none=True)
        payload["session_id"] = session_id
        return payload


def _has_non_empty_text(value: str | None) -> bool:
    return isinstance(value, str) and bool(value.strip())


class ClosetItemUpsertRequest(BaseModel):
    """Create or update a closet (owned/liked) garment. docs/25 P0-3."""

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    name: str | None = Field(default=None, max_length=200)
    category: str | None = Field(default=None, max_length=80)
    brand: str | None = Field(default=None, max_length=120)
    size: str | None = Field(default=None, max_length=60)
    colors: list[str] = Field(default_factory=list)
    material: str | None = Field(default=None, max_length=200)
    season: str | None = Field(default=None, max_length=60)
    source_url: str | None = Field(default=None, max_length=MAX_CANDIDATE_URL_CHARS)
    notes: str | None = Field(default=None, max_length=2000)
    tags: list[str] = Field(default_factory=list)
    is_baseline: bool = False

    _check_source_url = field_validator("source_url")(_validate_candidate_url)

    @model_validator(mode="after")
    def require_some_content(self) -> "ClosetItemUpsertRequest":
        if (
            _has_non_empty_text(self.name)
            or _has_non_empty_text(self.category)
            or _has_non_empty_text(self.brand)
            or _has_non_empty_text(self.notes)
            or bool(self.colors)
            or bool(self.tags)
        ):
            return self
        raise ValueError("Closet item requires at least a name, category, brand, notes, colors, or tags.")


class ClosetItemResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    session_id: str | None = None
    name: str | None = None
    category: str | None = None
    brand: str | None = None
    size: str | None = None
    colors: list[str] = Field(default_factory=list)
    material: str | None = None
    season: str | None = None
    source_url: str | None = None
    notes: str | None = None
    tags: list[str] = Field(default_factory=list)
    is_baseline: bool = False
    status: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ClosetListResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str
    session_id: str
    items: list[ClosetItemResponse] = Field(default_factory=list)


class FavoriteUpsertRequest(BaseModel):
    """Save a candidate for later comparison across sessions. docs/25 P1-2."""

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    candidate_key: str | None = Field(default=None, max_length=300)
    name: str | None = Field(default=None, max_length=300)
    source_url: str | None = Field(default=None, max_length=MAX_CANDIDATE_URL_CHARS)
    recommendation: str | None = Field(default=None, max_length=60)
    score: dict[str, Any] = Field(default_factory=dict)
    from_run_id: str | None = Field(default=None, max_length=80)
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def require_some_content(self) -> "FavoriteUpsertRequest":
        if _has_non_empty_text(self.name) or _has_non_empty_text(self.candidate_key) or _has_non_empty_text(self.source_url):
            return self
        raise ValueError("Favorite requires a name, candidate_key, or source_url.")


class FavoriteResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    session_id: str | None = None
    candidate_key: str | None = None
    name: str | None = None
    source_url: str | None = None
    recommendation: str | None = None
    score: dict[str, Any] = Field(default_factory=dict)
    from_run_id: str | None = None
    notes: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class FavoriteListResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str
    session_id: str
    items: list[FavoriteResponse] = Field(default_factory=list)


class ImageUploadRequest(BaseModel):
    filename: str | None = None
    media_type: str
    data_url: str | None = None
    base64: str | None = None
    role: str = "current_clothing_photo"


class WebCandidateRunRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    refresh_web: bool = False


class StandardStatus(BaseModel):
    status: str


class ErrorPayload(BaseModel):
    code: str
    message: str


class ImageUploadFailure(BaseModel):
    status: Literal["failed"]
    error: ErrorPayload


class FlexibleResponse(BaseModel):
    model_config = ConfigDict(extra="allow")


class LlmStatusResponse(FlexibleResponse):
    provider: str | None = None
    base_url: str | None = None
    model: str | None = None
    api_style: str | None = None
    status: str
    configured_source: str | None = None
    external_api_called: bool | None = None


class ProviderDetailResponse(FlexibleResponse):
    selected: str | None = None
    provider: str
    mode: str
    external_provider_configured: bool


class ProvidersResponse(BaseModel):
    search: ProviderDetailResponse
    ocr: ProviderDetailResponse
    vision: ProviderDetailResponse | None = None
    page_fetch: ProviderDetailResponse | None = None


class ProductionAgentStatusResponse(FlexibleResponse):
    route: str
    events_route: str
    requires_llm_status: str
    run_store: str
    store_type: str


class OrchestrationStatusResponse(FlexibleResponse):
    current_runtime: str
    decision: str
    recommended_path: str
    backend_owned_boundaries: list[str] = Field(default_factory=list)
    responses_api: dict[str, Any] = Field(default_factory=dict)
    agents_sdk: dict[str, Any] = Field(default_factory=dict)
    codex_sdk: dict[str, Any] = Field(default_factory=dict)


class ApiStatusResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str
    session_id: str
    has_web_result: bool
    has_keyword_probe: bool
    llm: LlmStatusResponse
    providers: ProvidersResponse
    orchestration: OrchestrationStatusResponse
    production_agent: ProductionAgentStatusResponse


class ImageUploadResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: Literal["completed", "failed"]
    attachment: Attachment | None = None
    error: ErrorPayload | None = None


class GroundingEvidenceSourceResponse(FlexibleResponse):
    path: str
    value: Any = None


class GroundingEvidenceItemResponse(FlexibleResponse):
    claim_type: str
    claim: str
    evidence: list[GroundingEvidenceSourceResponse] = Field(default_factory=list)


class CitationBadgeResponse(FlexibleResponse):
    id: str
    label: str
    claim_type: str | None = None
    claim: str | None = None
    source_path: str
    source_value_preview: str | None = None
    observation_index: int | None = None
    source_kind: str | None = None
    source_count: int = 0


class RetryBadgeResponse(FlexibleResponse):
    id: str
    label: str
    status: str
    provider: str | None = None
    provider_mode: str | None = None
    retry_count: int = 0
    retry_error_count: int = 0
    latest_error_preview: str | None = None
    provider_error_code: str | None = None
    provider_error_category: str | None = None
    provider_retry_error_codes: list[str] = Field(default_factory=list)
    provider_latest_retry_error_code: str | None = None
    provider_latest_retry_error_category: str | None = None
    provider_evidence_error_codes: list[str] = Field(default_factory=list)
    provider_latest_evidence_error_code: str | None = None
    provider_latest_evidence_error_category: str | None = None
    timeout_seconds: float | None = None
    external_api_called: bool | None = None
    source_kind: str = "provider_runtime"


class ToolSuggestionResponse(FlexibleResponse):
    tool: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None


class SearchQueryQualityResponse(FlexibleResponse):
    query: str | None = None
    quality: str | None = None
    score: int | None = None
    token_count: int | None = None
    has_clothing_keyword: bool | None = None
    has_product_intent_signal: bool | None = None
    has_specificity_signal: bool | None = None
    style_content_risk: bool | None = None


class SearchDomainPolicyResponse(FlexibleResponse):
    allowed_domains_configured: bool | None = None
    allowed_domains: list[str] = Field(default_factory=list)
    preferred_domains: list[str] = Field(default_factory=list)
    blocked_domains: list[str] = Field(default_factory=list)
    policy: str | None = None
    host: str | None = None
    action: str | None = None
    matched_domain: str | None = None


class SearchAttemptResponse(FlexibleResponse):
    attempt_index: int | None = None
    current_query: str | None = None
    repeated_query: bool | None = None
    previous_query_count: int | None = None
    previous_queries: list[str] = Field(default_factory=list)
    previous_empty_attempt_count: int | None = None
    previous_candidate_found_count: int | None = None
    current_candidate_count: int | None = None
    current_query_quality: SearchQueryQualityResponse | None = None
    previous_query_quality: SearchQueryQualityResponse | None = None
    query_quality_score_delta: int | float | None = None
    retry_policy: dict[str, Any] = Field(default_factory=dict)


class SearchCandidateResponse(FlexibleResponse):
    candidate_key: str | None = None
    name: str | None = None
    brand: str | None = None
    category: str | None = None
    source_url: str | None = None
    snippet: str | None = None
    provider: str | None = None
    mode: str | None = None
    candidate_type: str | None = None
    commerce_score: int | None = None
    search_match_score: int | None = None
    commerce_signals: list[str] = Field(default_factory=list)
    content_risk_flags: list[str] = Field(default_factory=list)
    domain_policy: SearchDomainPolicyResponse | None = None
    search_result_rank: int | None = None
    search_priority: int | None = None
    recommendation_from_cache: str | None = None
    overall_from_cache: float | int | None = None
    missing_fields: list[str] = Field(default_factory=list)


class RejectedSearchCandidateResponse(FlexibleResponse):
    name: str | None = None
    source_url: str | None = None
    candidate_type: str | None = None
    commerce_score: int | None = None
    search_match_score: int | None = None
    commerce_signals: list[str] = Field(default_factory=list)
    content_risk_flags: list[str] = Field(default_factory=list)
    domain_policy: SearchDomainPolicyResponse | None = None
    search_result_rank: int | None = None
    search_priority: int | None = None


class ToolResultErrorResponse(FlexibleResponse):
    code: str | None = None
    message: str | None = None
    details: Any = None


class ToolResultResponse(FlexibleResponse):
    status: str | None = None
    error: ToolResultErrorResponse | str | None = None
    provider: str | None = None
    provider_mode: str | None = None
    mode: str | None = None
    external_api_called: bool | None = None
    timeout_seconds: float | None = None
    retry_count: int | None = None
    retry_errors: list[Any] = Field(default_factory=list)
    retry_badges: list[RetryBadgeResponse] = Field(default_factory=list)
    provider_error_code: str | None = None
    provider_error_category: str | None = None
    provider_error_message_preview: str | None = None
    provider_retry_error_codes: list[str] = Field(default_factory=list)
    provider_latest_retry_error_code: str | None = None
    provider_latest_retry_error_category: str | None = None
    provider_evidence_error_codes: list[str] = Field(default_factory=list)
    provider_latest_evidence_error_code: str | None = None
    provider_latest_evidence_error_category: str | None = None
    evidence_status: str | None = None
    query: str | None = None
    reason: str | None = None
    ask_user_allowed: bool | None = None
    search_deferred: bool | None = None
    query_quality: SearchQueryQualityResponse | None = None
    domain_policy: SearchDomainPolicyResponse | None = None
    search_attempt: SearchAttemptResponse | None = None
    candidate_filter_policy: str | None = None
    search_result_count: int | None = None
    product_like_candidate_count: int | None = None
    rejected_candidate_count: int | None = None
    candidate_count: int | None = None
    candidates: list[SearchCandidateResponse] = Field(default_factory=list)
    rejected_candidates: list[RejectedSearchCandidateResponse] = Field(default_factory=list)
    required_configuration: list[str] = Field(default_factory=list)
    score_deferred: bool | None = None
    grounded: bool | None = None
    missing_fields: list[str] = Field(default_factory=list)
    next_tool_suggestions: list[ToolSuggestionResponse] = Field(default_factory=list)


class GroundingValidationResponse(FlexibleResponse):
    valid: bool
    reason: str
    suspicious_numbers: list[str] = Field(default_factory=list)
    suspicious_urls: list[str] = Field(default_factory=list)
    suspicious_material_claims: list[str] = Field(default_factory=list)
    suspicious_score_claims: list[str] = Field(default_factory=list)
    suspicious_identity_claims: list[str] = Field(default_factory=list)
    suspicious_style_claims: list[str] = Field(default_factory=list)
    suspicious_fit_claims: list[str] = Field(default_factory=list)
    suspicious_evidence_claims: list[str] = Field(default_factory=list)
    grounding_evidence: list[GroundingEvidenceItemResponse] = Field(default_factory=list)
    citation_badges: list[CitationBadgeResponse] = Field(default_factory=list)
    has_match_observation: bool | None = None
    has_report_observation: bool | None = None


class FinalReportResponse(FlexibleResponse):
    summary: str | None = None
    grounded: bool | None = None
    grounding: str | None = None
    report_type: str | None = None
    grounding_validation: GroundingValidationResponse | None = None


class TraceEntryResponse(FlexibleResponse):
    step_index: int
    step_type: str
    name: str
    status: str
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class ToolObservationResponse(FlexibleResponse):
    tool: str
    call_id: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: ToolResultResponse = Field(default_factory=ToolResultResponse)
    result_status: str | None = None
    tool_timeout_seconds: float | None = None
    tool_risk: str | None = None
    duration_ms: float | None = None
    provider: str | None = None
    provider_mode: str | None = None
    external_api_called: bool | None = None
    provider_timeout_seconds: float | None = None
    provider_retry_count: int | None = None
    provider_retry_errors: list[Any] = Field(default_factory=list)
    provider_error_code: str | None = None
    provider_error_category: str | None = None
    provider_error_message_preview: str | None = None
    provider_retry_error_codes: list[str] = Field(default_factory=list)
    provider_latest_retry_error_code: str | None = None
    provider_latest_retry_error_category: str | None = None
    evidence_status: str | None = None
    score_deferred: bool | None = None
    grounded: bool | None = None
    retry_badges: list[RetryBadgeResponse] = Field(default_factory=list)


class HookLifecycleRecordResponse(FlexibleResponse):
    sequence: int
    hook_event: str
    tool: str
    call_id: str | None = None
    status: str
    mode: str = "record_only"
    source: str | None = None
    duration_ms: int | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class ContextBudgetRecordResponse(FlexibleResponse):
    sequence: int
    source: str
    measurement: str = "estimated"
    input_kind: str
    created_at: str
    instruction_chars: int
    input_chars: int
    instruction_bytes: int
    input_bytes: int
    estimated_instruction_tokens: int
    estimated_input_tokens: int
    estimated_total_tokens: int
    pending_tool_output_count: int = 0
    trace_count_before_request: int = 0
    observation_count_before_request: int = 0
    hook_lifecycle_count_before_request: int = 0
    provider_usage: dict[str, Any] = Field(default_factory=dict)
    provider_input_tokens: int | None = None
    provider_output_tokens: int | None = None
    provider_total_tokens: int | None = None
    provider_cache_read_input_tokens: int | None = None
    provider_cache_creation_input_tokens: int | None = None
    provider_web_search_requests: int | None = None
    provider_cost_usd: float | None = None
    response_id: str | None = None
    api_style: str | None = None
    raw_response_id: str | None = None
    provider_request_id: str | None = None
    transport: str | None = None


class ContextCompactionRecordResponse(FlexibleResponse):
    sequence: int
    source: str
    mode: str
    status: str
    created_at: str
    threshold_estimated_tokens: int
    source_budget_sequence: int | None = None
    estimated_total_tokens: int | None = None
    preserved: dict[str, Any] = Field(default_factory=dict)


class AgentStateResponse(FlexibleResponse):
    previous_response_id: str | None = None
    pending_tool_outputs: list[dict[str, Any]] = Field(default_factory=list)
    observations: list[ToolObservationResponse] = Field(default_factory=list)
    hook_lifecycle: list[HookLifecycleRecordResponse] = Field(default_factory=list)
    context_budget: list[ContextBudgetRecordResponse] = Field(default_factory=list)
    context_compactions: list[ContextCompactionRecordResponse] = Field(default_factory=list)
    missing_info: list[Any] = Field(default_factory=list)
    match_results: list[dict[str, Any]] = Field(default_factory=list)
    context_plan: dict[str, Any] = Field(default_factory=dict)
    execution_plan: dict[str, Any] = Field(default_factory=dict)
    source_refs: dict[str, Any] = Field(default_factory=dict)
    search_constraints: dict[str, Any] = Field(default_factory=dict)


class AgentRunResponse(FlexibleResponse):
    status: str
    run_id: str | None = None
    session_id: str | None = None
    trace: list[TraceEntryResponse] = Field(default_factory=list)
    state: AgentStateResponse = Field(default_factory=AgentStateResponse)
    llm: dict[str, Any] = Field(default_factory=dict)
    final_report: FinalReportResponse | None = None
    error: dict[str, Any] | None = None


class AgentRunListResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str
    session_id: str
    runs: list[dict[str, Any]] = Field(default_factory=list)


class AgentReportResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str
    run_id: str
    final_report: FinalReportResponse | None = None
    trace_count: int
    grounding_validation: GroundingValidationResponse | None = None
    citation_badges: list[CitationBadgeResponse] = Field(default_factory=list)


class AgentRunEvidenceResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str
    run_id: str
    session_id: str | None = None
    run_status: str | None = None
    llm: dict[str, Any] = Field(default_factory=dict)
    counts: dict[str, int] = Field(default_factory=dict)
    sdk_like: dict[str, Any] = Field(default_factory=dict)
    tool_use_summary: dict[str, Any] = Field(default_factory=dict)
    compact_tool_use_messages: list[dict[str, Any]] = Field(default_factory=list)
    trace: list[TraceEntryResponse] = Field(default_factory=list)
    observations: list[ToolObservationResponse] = Field(default_factory=list)
    hook_lifecycle: list[HookLifecycleRecordResponse] = Field(default_factory=list)
    context_budget: list[ContextBudgetRecordResponse] = Field(default_factory=list)
    context_compactions: list[ContextCompactionRecordResponse] = Field(default_factory=list)
    latest_observation: ToolObservationResponse | None = None
    final_report: FinalReportResponse | None = None
    grounding_validation: GroundingValidationResponse | None = None
    citation_badges: list[CitationBadgeResponse] = Field(default_factory=list)


class AgentClientTranscriptMessageResponse(FlexibleResponse):
    type: str
    role: str | None = None
    session_id: str | None = None
    uuid: str | None = None
    text: str | None = None
    summary: str | None = None
    tool_summary: str | None = None
    status: str | None = None
    step_index: int | None = None
    tool_name: str | None = None
    call_id: str | None = None
    evidence_kind: str | None = None


class AgentClientTranscriptResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str
    run_id: str
    session_id: str | None = None
    run_status: str | None = None
    status_line: dict[str, Any] = Field(default_factory=dict)
    stream: dict[str, Any] = Field(default_factory=dict)
    evidence_summary: dict[str, Any] = Field(default_factory=dict)
    tool_use_summary: dict[str, Any] = Field(default_factory=dict)
    messages: list[AgentClientTranscriptMessageResponse] = Field(default_factory=list)
    source_endpoints: dict[str, str] = Field(default_factory=dict)


class AgentSdkEventsResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str
    run_id: str
    session_id: str | None = None
    source: str
    after: int = 0
    events: list[dict[str, Any]] = Field(default_factory=list)
    raw_event_count: int = 0
    projected_event_count: int = 0
    latest_source_event_seq: int | None = None
    next_after: int = 0
    cursor_policy: dict[str, Any] = Field(default_factory=dict)
    terminal_event_seq: int | None = None
    terminal_event_included: bool = False
    final_output_included: bool = False
    source_endpoints: dict[str, str] = Field(default_factory=dict)


class AgentRunSessionInfoResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str
    run_id: str
    session_id: str | None = None
    source: str = "fitcheck_agent_session_info"
    run_status: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    completed_at: str | None = None
    goal: str | None = None
    title: str | None = None
    summary: str | None = None
    llm: dict[str, Any] = Field(default_factory=dict)
    counts: dict[str, int] = Field(default_factory=dict)
    latest_event_seq: int = 0
    latest_event_id: str | None = None
    stream: dict[str, Any] = Field(default_factory=dict)
    resumability: dict[str, Any] = Field(default_factory=dict)
    error: dict[str, Any] | None = None
    source_endpoints: dict[str, str] = Field(default_factory=dict)


class AgentStateEventResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str
    run_id: str
    event_id: str | None = None
    event_sequence: int = 0
    retry_ms: int | None = None
    trace_count: int = 0
    latest_trace: list[TraceEntryResponse] = Field(default_factory=list)
    trace_delta: list[TraceEntryResponse] = Field(default_factory=list)
    latest_observation: ToolObservationResponse | None = None
    final_report: FinalReportResponse | None = None
    error: dict[str, Any] | None = None
    citation_badges: list[CitationBadgeResponse] = Field(default_factory=list)
    missing_info: list[Any] = Field(default_factory=list)
    execution_plan: dict[str, Any] = Field(default_factory=dict)
    context_plan: dict[str, Any] = Field(default_factory=dict)


class WebCandidateRunResponse(FlexibleResponse):
    status: str
    closet_item: dict[str, Any] | None = None
    summary_table: list[dict[str, Any]] = Field(default_factory=list)
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    keyword_probe: dict[str, Any] = Field(default_factory=dict)
    agent_run: dict[str, Any] = Field(default_factory=dict)
    design_meta: dict[str, Any] = Field(default_factory=dict)
    image_assets: dict[str, str] = Field(default_factory=dict)
