#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


PROVIDERS: dict[str, dict[str, Any]] = {
    "ollama": {
        "provider": "ollama",
        "api_style": "chat_completions",
        "base_url": "http://localhost:11434/v1",
        "api_key_env": None,
        "supports_tool_calling": True,
        "supports_stateful_responses": False,
    },
    "lm_studio": {
        "provider": "lm_studio",
        "api_style": "chat_completions",
        "base_url": "http://localhost:1234/v1",
        "api_key_env": None,
        "supports_tool_calling": True,
        "supports_stateful_responses": False,
    },
    "vllm": {
        "provider": "vllm_or_other_openai_compatible",
        "api_style": "chat_completions",
        "base_url": "http://localhost:8000/v1",
        "api_key_env": None,
        "supports_tool_calling": True,
        "supports_stateful_responses": False,
    },
    "openai": {
        "provider": "openai",
        "api_style": "responses",
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "supports_tool_calling": True,
        "supports_stateful_responses": True,
    },
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a reusable agentic harness scaffold.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--name", default="agentic-harness-run")
    parser.add_argument("--provider", choices=sorted(PROVIDERS), default="ollama")
    parser.add_argument("--model", default="qwen3-coder")
    parser.add_argument("--harness-base-url", default="http://127.0.0.1:8000/api/harness/v1")
    parser.add_argument("--client-id", default="replace-with-client-id")
    parser.add_argument("--cwd", default="C:\\com2us\\developer_harness")
    parser.add_argument("--verifier", default="python -B verify.py")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    provider = dict(PROVIDERS[args.provider])
    provider["model"] = args.model

    files = {
        "provider_profile.json": provider_profile(provider),
        "harness_task_payload.json": task_payload(args, provider),
        "evidence_holder_payload.json": evidence_holder_payload(args, provider),
        "evidence_templates.json": evidence_templates(),
        "eval_case_seed.json": eval_case_seed(args),
        "agent_loop_contract.md": loop_contract(args, provider),
        "run_probe.ps1": run_probe(args),
        "run_task.ps1": run_task(args),
        "run_evidence_holder.ps1": run_evidence_holder(args),
        "ingest_trace.ps1": ingest_trace(args),
    }
    written = []
    for relative, content in files.items():
        path = output_dir / relative
        if path.exists() and not args.force:
            raise SystemExit(f"{path} already exists; pass --force to overwrite.")
        path.write_text(content, encoding="utf-8")
        written.append(str(path))
    print(json.dumps({"output_dir": str(output_dir), "written": written}, ensure_ascii=False, indent=2))
    return 0


def provider_profile(provider: dict[str, Any]) -> str:
    return json.dumps(provider, ensure_ascii=False, indent=2) + "\n"


def task_payload(args: argparse.Namespace, provider: dict[str, Any]) -> str:
    payload = {
        "prompt": (
            "Use the agentic harness loop. Probe provider diagnostics, implement the smallest "
            "agent/runtime improvement, record tool evidence, create eval seeds for repeatable gaps, "
            "and report only evidence-backed claims."
        ),
        "idempotency_key": f"{args.name}-{provider['provider']}-{args.model}",
        "cwd": args.cwd,
        "sandbox": "workspace-write",
        "skills": ["agentic-harness-loop", "local-llm-provider-diagnostics"],
        "llm_provider": provider,
        "metadata": {
            "agentic_loop_template": "agents_sdk_trace_eval_codex",
            "agentic_loop": {
                "trace_sources": ["harness_events", "tool_observations", "agent_trace_json"],
                "eval_targets": ["provider_diagnostics", "tool_loop_regression"],
                "verification_commands": [args.verifier],
            },
        },
        "verification": {"commands": [args.verifier], "timeout_seconds": 120},
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def evidence_holder_payload(args: argparse.Namespace, provider: dict[str, Any]) -> str:
    payload = {
        "prompt": (
            "Evidence holder for live provider smoke, tool-call observations, SDK projection checks, "
            "and eval seed reproduction. This task is created with start=false and must not run Codex."
        ),
        "idempotency_key": f"{args.name}-{provider['provider']}-{args.model}-evidence-holder",
        "cwd": args.cwd,
        "sandbox": "read-only",
        "skills": ["agentic-harness-loop", "local-llm-provider-diagnostics"],
        "llm_provider": provider,
        "metadata": {
            "agentic_loop_template": "agents_sdk_trace_eval_codex",
            "evidence_kind": "external_live_llm_smoke_holder",
            "agentic_loop": {
                "trace_sources": ["harness_events", "tool_observations", "external_live_llm_calls"],
                "eval_targets": ["provider_diagnostics", "tool_loop_regression"],
                "verification_commands": [args.verifier],
            },
        },
        "verification": {"commands": [], "timeout_seconds": 120},
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def evidence_templates() -> str:
    templates = {
        "tool_call": {
            "tool_name": "provider_probe",
            "input": {"provider": "ollama", "base_url": "http://localhost:11434/v1"},
            "status": "completed",
            "metadata": {"source": "scaffold"},
        },
        "observation": {
            "tool_name": "provider_probe",
            "result": {"status": "configuration_required"},
            "evidence_refs": ["diagnostic:<id>"],
            "status": "completed",
            "metadata": {"source": "scaffold"},
        },
        "artifact": {
            "kind": "agent_trace",
            "path": "agent-result.json",
            "metadata": {"source": "scaffold"},
        },
    }
    return json.dumps(templates, ensure_ascii=False, indent=2) + "\n"


def eval_case_seed(args: argparse.Namespace) -> str:
    seed = {
        "name": f"{args.name} provider/tool regression",
        "failure_label": "configuration_required",
        "input": {"provider": args.provider, "model": args.model},
        "expected": {"status": "completed_or_configuration_required_with_diagnostic"},
        "verifier_command": args.verifier,
        "evidence_refs": ["task:<task_id>", "diagnostic:<diagnostic_id>"],
        "metadata": {"source": "scaffold"},
    }
    return json.dumps(seed, ensure_ascii=False, indent=2) + "\n"


def loop_contract(args: argparse.Namespace, provider: dict[str, Any]) -> str:
    return (
        f"# {args.name}\n\n"
        "This scaffold is for generic development harness work, not FitCheck production behavior.\n\n"
        "## Required Flow\n\n"
        "1. Probe the provider with `run_probe.ps1` before claiming connectivity.\n"
        "2. Create or run a harness task with `run_task.ps1`.\n"
        "3. For external live LLM smoke evidence, create a non-running holder with `run_evidence_holder.ps1`.\n"
        "4. If an Agent SDK or Responses run emits JSON, save it as `agent-result.json` and run `ingest_trace.ps1`.\n"
        "5. Inspect task evidence before final claims.\n"
        "6. Promote repeatable failures with `eval_case_seed.json`.\n\n"
        "## Guardrails\n\n"
        "- Treat mock, cached, fixture, and deterministic outputs as harness evidence only.\n"
        "- Keep provider diagnostics separate from production agent claims.\n"
        "- Record tool calls, observations, handoffs, approvals, guardrails, and final output separately when available.\n"
        "- Refresh state_version or lease_epoch after 409 conflicts.\n\n"
        "## Provider\n\n"
        "```json\n"
        + json.dumps(provider, ensure_ascii=False, indent=2)
        + "\n```\n"
    )


def skill_script(name: str) -> str:
    return f"C:\\com2us\\.agents\\skills\\agentic-harness-loop\\scripts\\{name}"


def run_probe(args: argparse.Namespace) -> str:
    provider = PROVIDERS[args.provider]
    stateful_flag = " true" if provider["supports_stateful_responses"] else " false"
    tool_flag = " true" if provider["supports_tool_calling"] else " false"
    return (
        f"$env:HARNESS_BASE_URL = \"{args.harness_base_url}\"\n"
        f"$env:HARNESS_CLIENT_ID = \"{args.client_id}\"\n"
        f"python {skill_script('harness_api.py')} probe --provider {provider['provider']} --api-style {provider['api_style']} --base-url {provider['base_url']} --model {args.model} --supports-tool-calling{tool_flag} --supports-stateful-responses{stateful_flag}\n"
    )


def run_task(args: argparse.Namespace) -> str:
    return (
        f"$env:HARNESS_BASE_URL = \"{args.harness_base_url}\"\n"
        f"$env:HARNESS_CLIENT_ID = \"{args.client_id}\"\n"
        "$headers = @{ \"X-Harness-Client-Id\" = $env:HARNESS_CLIENT_ID; \"Content-Type\" = \"application/json\" }\n"
        "if ($env:HARNESS_API_KEY) { $headers[\"X-Harness-Api-Key\"] = $env:HARNESS_API_KEY }\n"
        "$body = Get-Content -Raw .\\harness_task_payload.json\n"
        "Invoke-RestMethod -Uri \"$env:HARNESS_BASE_URL/tasks?background=false\" -Method Post -Headers $headers -Body $body\n"
    )


def run_evidence_holder(args: argparse.Namespace) -> str:
    return (
        f"$env:HARNESS_BASE_URL = \"{args.harness_base_url}\"\n"
        f"$env:HARNESS_CLIENT_ID = \"{args.client_id}\"\n"
        "$headers = @{ \"X-Harness-Client-Id\" = $env:HARNESS_CLIENT_ID; \"Content-Type\" = \"application/json\" }\n"
        "if ($env:HARNESS_API_KEY) { $headers[\"X-Harness-Api-Key\"] = $env:HARNESS_API_KEY }\n"
        "$body = Get-Content -Raw .\\evidence_holder_payload.json\n"
        "Invoke-RestMethod -Uri \"$env:HARNESS_BASE_URL/tasks?start=false\" -Method Post -Headers $headers -Body $body\n"
    )


def ingest_trace(args: argparse.Namespace) -> str:
    return (
        "param([Parameter(Mandatory=$true)][string]$TaskId, [string]$TraceJson = \".\\agent-result.json\")\n"
        f"$env:HARNESS_BASE_URL = \"{args.harness_base_url}\"\n"
        f"$env:HARNESS_CLIENT_ID = \"{args.client_id}\"\n"
        f"python {skill_script('trace_ingest.py')} --task-id $TaskId --trace-json $TraceJson\n"
    )


if __name__ == "__main__":
    raise SystemExit(main())
