// Typed client for the FitCheck agent API.
// Mirrors backend_harness/app/routes/agent.py, status.py, uploads.py — including
// the run-control, cancel, steering-queue, and streaming features added in the
// docs/24 gap remediation.

export interface RunControls {
  max_turns?: number | null;
  tool_budget?: number | null;
  allowed_tools?: string[] | null;
  denied_tools?: string[] | null;
  search_provider?: string | null;
  ocr_provider?: string | null;
}

export interface Attachment {
  id: string;
  filename?: string | null;
  media_type?: string | null;
  url?: string | null;
  role?: string;
}

export interface CreateRunBody {
  free_text?: string;
  goal?: string;
  candidate_url?: string;
  attachments?: Attachment[];
  refresh_web?: boolean;
  controls?: RunControls;
}

export interface TraceEntry {
  step_index: number;
  step_type: string;
  name: string;
  status: string;
  input?: Record<string, unknown>;
  output?: Record<string, unknown>;
  created_at: string;
}

export interface MatchResult {
  candidate_key?: string;
  recommendation?: string;
  score?: {
    overall?: number;
    size?: number;
    material?: number;
    price?: number;
    confidence?: number;
  };
  missing_fields?: string[];
  [k: string]: unknown;
}

export interface CitationBadge {
  id: string;
  label: string;
  claim_type?: string;
  claim?: string;
  source_path: string;
  source_value_preview?: string;
  observation_index?: number;
  source_kind?: string;
  source_count?: number;
}

export interface FinalReport {
  summary?: string;
  grounded?: boolean;
  degraded?: boolean;
  report_type?: string;
  llm_final_text?: string;
  grounding_validation?: {
    valid?: boolean;
    reason?: string;
    citation_badges?: CitationBadge[];
  };
  [k: string]: unknown;
}

export interface AgentRun {
  status: string;
  run_id?: string;
  session_id?: string;
  trace?: TraceEntry[];
  state?: {
    match_results?: MatchResult[];
    missing_info?: unknown[];
    model_text_deltas?: string[];
    pending_user_messages?: unknown[];
    resolved_shortlist_reference?: { rank?: number; candidate_key?: string };
    prior_turn_summary?: unknown;
    tool_calls_executed?: number;
    [k: string]: unknown;
  };
  llm?: Record<string, unknown>;
  final_report?: FinalReport | null;
  error?: { code?: string; message?: string; user_message?: string } | null;
  // present on a queued steering reply
  queued?: boolean;
  queue_depth?: number;
  message?: string;
}

export interface ApiStatus {
  status: string;
  session_id: string;
  llm: { status: string; provider?: string; model?: string; api_style?: string };
  providers: Record<string, unknown>;
  [k: string]: unknown;
}

async function jsonOrThrow<T>(res: Response): Promise<T> {
  const text = await res.text();
  let data: unknown;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    throw new ApiError(res.status, `Non-JSON response (${res.status})`, text);
  }
  if (!res.ok) {
    const detail = (data as { detail?: unknown })?.detail;
    throw new ApiError(res.status, describeError(detail) ?? `HTTP ${res.status}`, data);
  }
  return data as T;
}

export class ApiError extends Error {
  constructor(public status: number, message: string, public body?: unknown) {
    super(message);
    this.name = "ApiError";
  }
}

function describeError(detail: unknown): string | undefined {
  if (!detail) return undefined;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => (d && typeof d === "object" && "msg" in d ? (d as { msg: string }).msg : String(d)))
      .join("; ");
  }
  return undefined;
}

export async function getStatus(): Promise<ApiStatus> {
  return jsonOrThrow<ApiStatus>(await fetch("/api/status", { credentials: "same-origin" }));
}

// Always create in background so we get an immediate run handle and can stream.
export async function createRun(body: CreateRunBody): Promise<AgentRun> {
  return jsonOrThrow<AgentRun>(
    await fetch("/api/agent/runs?background=true", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(body),
    }),
  );
}

export async function getRun(runId: string): Promise<AgentRun> {
  return jsonOrThrow<AgentRun>(
    await fetch(`/api/agent/runs/${encodeURIComponent(runId)}`, { credentials: "same-origin" }),
  );
}

export async function listRuns(): Promise<{ runs: AgentRun[] }> {
  return jsonOrThrow(await fetch("/api/agent/runs", { credentials: "same-origin" }));
}

// ---- closet (docs/25 P0-3 API / P1-1 UI) ----
export interface ClosetItem {
  id: string;
  name?: string;
  category?: string;
  brand?: string;
  size?: string;
  colors?: string[];
  material?: string;
  season?: string;
  source_url?: string;
  notes?: string;
  tags?: string[];
  is_baseline?: boolean;
  updated_at?: string;
  [k: string]: unknown;
}

export interface ClosetItemInput {
  name?: string;
  category?: string;
  brand?: string;
  size?: string;
  colors?: string[];
  material?: string;
  season?: string;
  source_url?: string;
  notes?: string;
  tags?: string[];
  is_baseline?: boolean;
}

export async function listCloset(): Promise<ClosetItem[]> {
  const res = await jsonOrThrow<{ items: ClosetItem[] }>(
    await fetch("/api/closet", { credentials: "same-origin" }),
  );
  return res.items ?? [];
}

export async function createClosetItem(item: ClosetItemInput): Promise<ClosetItem> {
  const res = await jsonOrThrow<{ items: ClosetItem[] }>(
    await fetch("/api/closet", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(item),
    }),
  );
  return res.items[0];
}

export async function updateClosetItem(id: string, item: ClosetItemInput): Promise<ClosetItem> {
  const res = await jsonOrThrow<{ items: ClosetItem[] }>(
    await fetch(`/api/closet/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(item),
    }),
  );
  return res.items[0];
}

export async function deleteClosetItem(id: string): Promise<void> {
  await jsonOrThrow(
    await fetch(`/api/closet/${encodeURIComponent(id)}`, {
      method: "DELETE",
      credentials: "same-origin",
    }),
  );
}

// ---- favorites / saved candidates (docs/25 P1-2) ----
export interface Favorite {
  id: string;
  candidate_key?: string;
  name?: string;
  source_url?: string;
  recommendation?: string;
  score?: Record<string, number>;
  from_run_id?: string;
  notes?: string;
  updated_at?: string;
  [k: string]: unknown;
}

export interface FavoriteInput {
  candidate_key?: string;
  name?: string;
  source_url?: string;
  recommendation?: string;
  score?: Record<string, number>;
  from_run_id?: string;
  notes?: string;
}

export async function listFavorites(): Promise<Favorite[]> {
  const res = await jsonOrThrow<{ items: Favorite[] }>(
    await fetch("/api/favorites", { credentials: "same-origin" }),
  );
  return res.items ?? [];
}

export async function saveFavorite(item: FavoriteInput): Promise<Favorite> {
  const res = await jsonOrThrow<{ items: Favorite[] }>(
    await fetch("/api/favorites", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(item),
    }),
  );
  return res.items[0];
}

export async function deleteFavorite(id: string): Promise<void> {
  await jsonOrThrow(
    await fetch(`/api/favorites/${encodeURIComponent(id)}`, {
      method: "DELETE",
      credentials: "same-origin",
    }),
  );
}

export async function replyToRun(
  runId: string,
  message: string,
): Promise<AgentRun> {
  // Background reply: if the run is still running, this enqueues a steering
  // message (status "queued"); otherwise it starts a new turn.
  return jsonOrThrow<AgentRun>(
    await fetch(`/api/agent/runs/${encodeURIComponent(runId)}/reply?background=true`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ message }),
    }),
  );
}

export async function cancelRun(runId: string): Promise<AgentRun> {
  return jsonOrThrow<AgentRun>(
    await fetch(`/api/agent/runs/${encodeURIComponent(runId)}/cancel`, {
      method: "POST",
      credentials: "same-origin",
    }),
  );
}

export async function uploadImage(file: File, role = "current_clothing_photo"): Promise<Attachment> {
  const dataUrl = await fileToDataUrl(file);
  const res = await jsonOrThrow<{ status: string; attachment?: Attachment; error?: { message?: string } }>(
    await fetch("/api/uploads/images", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({
        filename: file.name,
        media_type: file.type || "image/jpeg",
        data_url: dataUrl,
        role,
      }),
    }),
  );
  if (res.status !== "completed" || !res.attachment) {
    throw new ApiError(400, res.error?.message ?? "이미지 업로드에 실패했습니다.");
  }
  return res.attachment;
}

function fileToDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

export const TERMINAL_STATUSES = new Set([
  "completed",
  "failed",
  "cancelled",
  "configuration_required",
  "waiting_for_user",
  "not_found",
]);

export function isTerminal(status: string | undefined): boolean {
  return !!status && TERMINAL_STATUSES.has(status);
}
