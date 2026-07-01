---
name: harness-loop
description: Create bounded recurring development-agent checks through the shared harness API, with explicit cadence and stop limits.
---

# Shared Harness Loop

1. Define one idempotent task prompt.
2. Create the loop with an interval of at least 60 seconds and a finite `max_iterations`.
3. A scheduler or controlling session calls `/api/harness/v1/loops/{loop_id}/tick`.
4. Inspect each generated task independently through its trace.
5. Do not use loops for destructive or non-idempotent operations without an external approval gate.
6. Feed repeated failures back into docs, skills, hooks, verification commands, or tool schemas.
