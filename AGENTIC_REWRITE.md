# Agentic Rewrite — Plan, Architecture, and Migration Path

This document is the single source of truth for the `agentic-rewrite` branch. Read it before making any structural change.

## Goal

Rewrite ShinkaEvolve's LLM call layer and proposal loop on top of the official `openai-agents` SDK, while preserving full functional parity with the current framework (running on `shinka-azure-v1-fix`). The motivating outcome: the orchestrator becomes a thinner shell around an agent that has access to tools (apply_patch, evaluate, web_search, query_evolution_db, read_host_file, run_probe), enabling future capability additions without further per-feature plumbing.

Non-goal: changing the database schema, the prompt-sampling logic, the embedding pipeline, or the user-facing task contract (`evaluate.py`, `initial.<ext>`, Hydra YAML configs). Those layers stay unchanged unless they intersect with the LLM call site.

## Why this rewrite (the information-source argument)

The current loop's information sources reduce to:

1. Initial template code
2. LLM weights
3. Evaluator output (a score, sometimes a traceback)

Everything else the loop appears to use — parent programs, novelty signals, meta recommendations — is downstream of (1) and (2), rearranged under pressure of (3). The loop cannot consult library docs, the rest of the host codebase, the literature, or probe the runtime. Long-running evolution plateaus at the LLM's pretrained knowledge.

The agentic rewrite adds **external information channels** by letting the LLM call tools mid-generation. The agents SDK provides the loop and tool-calling protocol; we provide the tools and a small set of Azure-compat overrides.

## Locked decisions (and why)

### Transport mode: Responses API with `background=True` + polling

**Rationale**: shinka calls take 20-60 minutes for reasoning-heavy models. Non-streaming responses leave a TCP connection idle for the full duration, which Azure LB/NAT can kill silently, producing the 5-hour silent hang documented in commit `fd018d8`. Streaming would solve liveness via SSE event cadence, but background mode is OpenAI's official recommendation for long reasoning calls and is fundamentally more resilient (decouples client connection lifetime from inference duration).

**Cost**: identical to non-streaming or streaming. Background polls (`responses.retrieve(id)`) are status checks, not inference calls, and don't consume tokens. Only the actual inference is billed, at the model's standard rates.

**Maturity caveat**: as of 2026-05, the agents SDK has no native background-mode support. We override `OpenAIResponsesModel._fetch_response` to submit `background=True` and poll internally. The base `openai-python` SDK supports the primitives (`background=True` param, `responses.retrieve(id)` method); the gap is at the SDK abstraction layer, not the API.

### SDK: `openai-agents` (≥ 0.17.2)

**Rationale**: writing our own tool-call loop is ~100 LOC, but writing the surrounding ergonomics (tool decoration, JSON schema generation from type hints, tracing, model settings abstraction) duplicates several hundred more LOC the SDK already provides. The SDK also gives us a clean migration path if Sakana eventually adopts it upstream.

**Costs accepted**:
- Adapter layer to preserve our `QueryResult` interface so call sites can migrate incrementally (~80 LOC).
- Subclass for background-mode support (~80 LOC).
- Wrapper around `Runner.run()` to reconstruct the agent (and its `AsyncOpenAI` client) per outer retry attempt, preserving our Azure pool-poisoning fix (~50 LOC).

Net new code under our maintenance: ~210 LOC of glue, versus ~600+ LOC of hand-rolled equivalent. The glue is upstream-friendly (could be PR'd as a `BackgroundOpenAIResponsesModel` and `RobustRunner` to the agents SDK if they want it).

### Retry strategy: outer-loop client reconstruction (preserved)

**Rationale**: commit `72f8dc2` disabled the inner `@backoff` retry because it reused the same poisoned `AsyncOpenAI` client. The outer retry layer (`AsyncLLMClient._query_async_with_retry`) is the only mechanism that recovers from pool poisoning by getting a fresh client each attempt. The agents SDK's `OpenAIResponsesModel._get_client()` reuses `self._client`, so we cannot rely on the SDK's retry. Our wrapper destroys the Agent (and therefore its model and client) per outer retry attempt.

### LLM-controllable tools include `apply_patch` and `evaluate`

**Rationale (user-directed)**: previously these were framework-controlled — the orchestrator generated a patch via LLM, applied it, then ran the evaluator on its own. Promoting them to LLM-callable tools lets the agent iterate within a single generation: propose → apply → check compile → fix → run evaluator → see score → refine. This is the genuinely agentic move.

**Costs accepted**:
- Cost per generation goes up (more LLM round-trips per generation).
- Latency per generation goes up.
- Semantics shift: scoring is no longer end-of-generation; it can happen mid-tool-loop.

**Mitigation**: `max_tool_steps` caps the loop, `tool_budget_usd` caps spend per generation, and we keep the existing `max_api_costs` global ceiling.

## Architecture: current vs target

### Current (`shinka-azure-v1-fix`)

```
shinka_run
  └─ ShinkaEvolveRunner.run_async
       ├─ _proposal_coordinator_task
       │    └─ _generate_proposal_async
       │         └─ _run_patch_async              [async_runner.py:3507]
       │              ├─ PromptSampler.sample
       │              ├─ self.llm.query           [LLM call - one round]
       │              │    └─ AsyncLLMClient._query_async_with_retry  [reconstructs client per attempt]
       │              │         └─ query_async    [llm/query.py]
       │              │              └─ query_openai_async
       │              │                   └─ client.responses.create  [non-streaming, hangs on dead pool]
       │              └─ apply_patch_async        [framework-controlled patch application]
       └─ _job_monitor_task
            └─ run_shinka_eval                    [framework-controlled evaluator, post-patch]
```

### Target (`agentic-rewrite`)

```
shinka_run
  └─ ShinkaEvolveRunner.run_async
       ├─ _proposal_coordinator_task
       │    └─ _generate_proposal_async
       │         └─ _run_agent_proposal           [new — replaces _run_patch_async]
       │              ├─ PromptSampler.sample
       │              └─ AgentLLMClient.run        [agentic loop: many tool calls inside]
       │                   └─ RobustRunner.run    [reconstructs Agent per outer retry]
       │                        └─ Agent + BackgroundOpenAIResponsesModel + Tools
       │                             ├─ apply_patch_tool         [calls existing apply_patch_async]
       │                             ├─ evaluate_tool            [calls existing run_shinka_eval]
       │                             ├─ web_search built-in
       │                             ├─ query_evolution_db_tool  [reads existing sqlite via dbase.py]
       │                             ├─ read_host_file_tool
       │                             └─ run_probe_tool           [later phase]
       └─ _job_monitor_task                       [now mostly just persists agent-emitted final results]
```

Key shifts:
- The LLM loop becomes the agent loop. Patch application and evaluation are tools the agent chooses to call.
- `PromptSampler` output is the agent's *initial* input message — it still curates parent programs, archive, top-K, etc.
- DB writes still happen at end-of-agent-turn, just from the agent's reported final state instead of from the orchestrator's bespoke handoff.
- Embedding client (`shinka/embed/client.py`) stays as-is (separate concern, not an LLM call).

## Phased plan

Each phase is a separate commit (or small commit series). Tests/validation gate phase boundaries.

### Phase A — Foundation (~2 commits, ~200 LOC)

**A.1** `BackgroundOpenAIResponsesModel`: subclass `OpenAIResponsesModel`, override `_fetch_response` to inject `background=True` for non-streaming calls, poll via `client.responses.retrieve(id)` until terminal, return the completed `Response` object. Unit-tested with mocked client.

**A.2** `RobustRunner`: thin wrapper around `agents.Runner.run()` that catches transport-level exceptions and creates a fresh `Agent` (with fresh `BackgroundOpenAIResponsesModel` and fresh `AsyncAzureOpenAI` client) per outer attempt. Unit-tested with a mocked Runner.

Gate: both files import cleanly, unit tests green.

### Phase B — Adapter layer (~3 commits, ~250 LOC)

**B.1** `AgentLLMClient`: a class that accepts the same constructor surface as the existing `LLMClient` (model_names, temperatures, max_tokens, reasoning_efforts, etc.) but internally builds an `Agent` and runs it via `RobustRunner`. Exposes a `.query(...) -> QueryResult` method matching the existing signature so call sites don't have to change. For "plain" queries with no tools, it behaves identically to the existing client. Unit-tested.

**B.2** Multi-provider dispatch: route Azure/OpenAI calls through `AgentLLMClient`; route Anthropic/Gemini/DeepSeek through their existing `query_*_async` paths (unchanged). Gives us a clean way to migrate per-provider, starting with our actual use case (Azure).

**B.3** `pyproject.toml` updates: add `openai-agents>=0.17.2`, relax `httpx` pin from `==0.27` to `>=0.28`, verify no other deps break.

Gate: shinka still runs end-to-end on `circle_packing` via Azure with no tool use, with `AgentLLMClient` as the LLM client. Score should be statistically similar to baseline.

### Phase C — Tool surface (~4-5 commits, ~400 LOC)

Each tool is its own commit with its own test.

**C.1** `apply_patch_tool`: wraps existing `apply_patch_async`. Input: `patch_text`, `patch_type ∈ {diff, full, cross}`. Output: success/failure + error message. Stateful (mutates the per-generation working directory).

**C.2** `evaluate_tool`: wraps existing `run_shinka_eval`. Input: none (uses the current applied state). Output: combined_score, public_metrics, correct, error. Heavy operation — agent should call once near end of generation, not repeatedly.

**C.3** `web_search_tool`: enabled by adding `{"type": "web_search"}` to the agent's tools list. Server-side, no executor needed. **Strategic enablement**: not on every generation — see the per-task config additions in `core/config.py`.

**C.4** `query_evolution_db_tool`: read-only query against existing `evolution_db.sqlite` via `dbase.py` helpers. Args: `query_type ∈ {top_n_by_score, recent_failures, similar_to_embedding, lineage_of_id}` + params.

**C.5** `read_host_file_tool`: sandboxed read under `tool_root_dir` (defaults to task dir). Args: `path`, `max_bytes`.

(Deferred to a later phase: `run_probe_tool` — needs subprocess sandboxing design.)

Gate: each tool is callable in isolation via a unit test. Integration test: agent generates a circle_packing solution by calling `apply_patch_tool` and `evaluate_tool`, scoring at parity with baseline.

### Phase D — Orchestrator rewire (~2-3 commits, ~300 LOC delta)

Replace `_run_patch_async` (`async_runner.py:3507-3748`) with `_run_agent_proposal`. The new function:

1. Calls `PromptSampler.sample()` (unchanged) for initial context.
2. Invokes `AgentLLMClient.run(initial_msg, tools=...)`.
3. Receives the agent's final state — applied code, metrics, tool trace.
4. Returns the structured result the proposal coordinator expects.

The patch-retry loop disappears: the agent does retry internally via its own tool calls (apply → check error → fix).

Gate: end-to-end shinka_run on circle_packing produces results comparable to baseline. Score distribution match (within statistical noise) over ~30 generations.

### Phase E — Parity validation (~1 commit, smoke tests)

End-to-end runs on at least:
- `circle_packing` (vanilla example)
- One of the user's actual tasks (e.g., `cnot_grid_synth`)

Each run for ~30 generations. Compare against baseline runs from `shinka-azure-v1-fix` on the same task spec.

Pass criteria:
- No silent hangs (bg+poll resilience confirmed)
- Combined score distribution within 1 standard deviation of baseline
- No crashes from the agent loop
- Cost per generation within ~2× baseline (the cost increase from tool calls is acceptable but bounded)

## File migration map

| Current file | Status | Action |
|---|---|---|
| `shinka/llm/client.py` | Modified | Keep `get_async_client_llm` (creates fresh AsyncAzureOpenAI). New helper `get_agent_async_client_llm` for the agents SDK to consume. |
| `shinka/llm/llm.py` | Modified | Existing `LLMClient` kept for backward-compat. New `AgentLLMClient` added alongside, becomes default for OpenAI/Azure. |
| `shinka/llm/query.py` | Modified | Add `agent_query_async` that goes through `AgentLLMClient`; preserve `query_async` for non-OpenAI providers. |
| `shinka/llm/providers/openai.py` | Modified | Existing `query_openai_async` deprecated for Azure/OpenAI but kept for OpenRouter (which doesn't expose background mode). |
| `shinka/llm/providers/anthropic.py` | Unchanged | Anthropic stays on its existing path. |
| `shinka/llm/providers/gemini.py` | Unchanged | Same. |
| `shinka/llm/providers/deepseek.py` | Unchanged | Same. |
| `shinka/llm/providers/local_openai.py` | Unchanged | Same. |
| `shinka/llm/providers/result.py` | Modified | `QueryResult` gains optional `tool_trace: list[dict]` field. |
| `shinka/llm/constants.py` | Unchanged | Backoff tunables still relevant for non-Azure providers. |
| **NEW** `shinka/llm/agent/__init__.py` | New | Public API for the new layer. |
| **NEW** `shinka/llm/agent/background_model.py` | New | `BackgroundOpenAIResponsesModel`. |
| **NEW** `shinka/llm/agent/robust_runner.py` | New | `RobustRunner`. |
| **NEW** `shinka/llm/agent/client.py` | New | `AgentLLMClient`. |
| **NEW** `shinka/llm/agent/tools/__init__.py` | New | Tool registry. |
| **NEW** `shinka/llm/agent/tools/apply_patch.py` | New | `apply_patch_tool`. |
| **NEW** `shinka/llm/agent/tools/evaluate.py` | New | `evaluate_tool`. |
| **NEW** `shinka/llm/agent/tools/query_db.py` | New | `query_evolution_db_tool`. |
| **NEW** `shinka/llm/agent/tools/read_file.py` | New | `read_host_file_tool`. |
| `shinka/embed/client.py` | Unchanged | Separate concern; not migrating. |
| `shinka/edit/apply_diff.py` | Unchanged | Wrapped by tool, not replaced. |
| `shinka/edit/async_apply.py` | Unchanged | Same. |
| `shinka/core/wrap_eval.py` | Unchanged | Wrapped by tool, not replaced. |
| `shinka/core/sampler.py` | Unchanged | Output feeds agent's initial message. |
| `shinka/core/async_runner.py` | Modified | `_run_patch_async` → `_run_agent_proposal`. |
| `shinka/core/config.py` | Modified | Add `tools`, `max_tool_steps`, `tool_budget_usd`, `tool_root_dir`, `enable_background_mode`. |
| `shinka/database/dbase.py` | Unchanged | DB schema preserved. Tool reads from it. |
| `pyproject.toml` | Modified | Add `openai-agents>=0.17.2`, relax `httpx` pin. |

## Tradeoffs accepted

1. **Cost may go up 1.5-2× per generation** as the agent makes multiple tool calls internally. We get the agentic capability in exchange. The `max_api_costs` ceiling continues to gate total spend.

2. **Latency may go up similarly** per generation. Run throughput drops proportionally. For evolution this is a worthwhile trade.

3. **Background mode has reported flakiness** (jobs stuck in "queued"). We mitigate with a max poll wall-clock and fall back to a fresh submit on timeout.

4. **Fork divergence increases** versus upstream Sakana. This rewrite is upstream-able in principle (the SDK is OpenAI-official, our overrides are isolated) but practically we'd be on a long-running branch for months.

5. **Non-OpenAI providers (Anthropic, Gemini, DeepSeek) stay on the legacy path** for now. The agents SDK supports them via `OpenAIChatCompletionsModel` over their respective base URLs but the tool-call protocols differ enough that unifying is a separate effort. For this user's stack (Azure-primary) this is fine.

## Open questions deferred to implementation time

1. **Per-tool cost accounting**: how granularly do we record per-call latency/cost in `QueryResult.tool_trace`? Affects telemetry/budget enforcement.
2. **`evaluate_tool` cost**: each `evaluate` invocation triggers a real evaluator run (~seconds to minutes depending on task). Should the agent be discouraged from calling it more than 1-2× per generation?
3. **`background=True` + `stream=True` combo**: the SDK supports streaming over a background response. Worth considering for finer liveness if poll-only proves insufficient.
4. **Reasoning summary persistence**: agents SDK's tracing system can capture reasoning summaries. Decide whether to wire into our `programs.metadata`.
5. **Run-resume semantics**: shinka's existing checkpointing assumes the LLM call is the only async unit. With agent-internal tool calls, mid-generation checkpoints become a question.

## Progress log

| Date | Phase | Status | Commit |
|------|-------|--------|--------|
| 2026-05-12 | Architecture sketch | done | `690269e` |
| 2026-05-12 | Phase A.1 — `BackgroundOpenAIResponsesModel` + 8 tests | done | `a9eb325` |
| 2026-05-12 | Phase A.2 — `RobustRunner` + 9 tests | done | `6cf6a79` |
