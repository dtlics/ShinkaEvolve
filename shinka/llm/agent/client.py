"""``AgentLLMClient`` — drop-in replacement for ``AsyncLLMClient`` that
routes OpenAI / Azure OpenAI calls through the ``openai-agents`` SDK
with background-mode polling.

Design constraints
------------------
The existing orchestrator (`shinka.core.async_runner._run_patch_async`)
expects a `.query(msg, system_msg, ...) -> Optional[QueryResult]`
contract. To let call sites migrate one at a time without coordinated
changes, ``AgentLLMClient`` exposes exactly the same public surface
as ``AsyncLLMClient``: ``.query``, ``.batch_query``,
``.batch_kwargs_query``, ``.get_kwargs``.

Provider routing
----------------
For Azure / OpenAI text generation, calls flow through:

    AgentLLMClient.query
        -> _query_via_agents
            -> agents.Runner.run
                -> BackgroundOpenAIResponsesModel._fetch_response
                    -> client.responses.create(background=True)
                    -> client.responses.retrieve(id)  (poll loop)
        -> _runresult_to_queryresult

For non-OpenAI providers (Anthropic, Gemini, DeepSeek, Bedrock, etc.)
and for structured-output requests, calls fall back to the existing
``query_async`` path in ``shinka.llm.query``.

Why no extra outer retry layer
------------------------------
Background mode (``BackgroundOpenAIResponsesModel``) submits the
inference as a server-side job and polls for status with sub-second
HTTP requests. There is no long idle TCP connection that an Azure
load balancer / NAT can silently kill — the failure mode that
motivated the original ``_query_async_with_retry`` /
``RobustRunner`` fresh-client logic is structurally absent now.

For the remaining transport-level errors (network blips on individual
poll/create calls, transient 429s and 5xxs), we rely on the OpenAI
Python SDK's built-in retry (``max_retries=2`` by default, with
exponential backoff). The agents SDK uses that underlying client
directly, so we inherit it for free.

The constructor still accepts ``max_attempts`` — it controls the
**legacy** (Anthropic / Gemini / etc.) path's outer retry, which
matches the behavior of ``AsyncLLMClient._query_async_with_retry``.
For the agents-SDK path, this parameter is intentionally unused.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel

from ..client import get_async_client_llm
from ..kwargs import sample_model_kwargs
from ..providers import QueryResult
from ..providers.pricing import calculate_cost, model_exists
from ..providers.model_resolver import resolve_model_backend
from ..query import query_async
from .background_model import (
    BackgroundOpenAIResponsesModel,
    DEFAULT_MAX_QUEUED_WAIT_SEC,
    DEFAULT_POLL_INTERVAL_SEC,
    DEFAULT_POLL_TIMEOUT_SEC,
)

logger = logging.getLogger(__name__)


# Providers we route through the agents SDK. Everything else falls
# back to the legacy ``query_async`` path.
_AGENT_SDK_PROVIDERS = frozenset({"azure_openai", "openai"})

# Default retry count for the legacy provider path only. The
# agents-SDK path relies on the OpenAI SDK's built-in retry.
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_MAX_TURNS = 10


class AgentLLMClient:
    """Async LLM client that routes Azure/OpenAI through agents SDK.

    Constructor matches ``AsyncLLMClient`` 1:1 so existing call sites
    can swap class names without touching anything else. Extra
    parameters specific to the agentic path are keyword-only and
    optional.
    """

    def __init__(
        self,
        model_names: Union[List[str], str] = "gpt-5.1",
        temperatures: Union[float, List[float]] = 0.75,
        max_tokens: Union[int, List[int]] = 4096,
        reasoning_efforts: Union[str, List[str]] = "disabled",
        model_sample_probs: Optional[List[float]] = None,
        output_model: Optional[type[BaseModel]] = None,
        verbose: bool = True,
        *,
        # Agent-loop tunables. Hidden from the legacy interface so
        # AsyncLLMClient call sites don't need to know about them; the
        # defaults match production sizing.
        tools: Optional[List[Any]] = None,
        builtin_tools: Optional[List[str]] = None,
        max_tool_steps: int = DEFAULT_MAX_TURNS,
        poll_interval_sec: float = DEFAULT_POLL_INTERVAL_SEC,
        poll_timeout_sec: float = DEFAULT_POLL_TIMEOUT_SEC,
        max_queued_wait_sec: float = DEFAULT_MAX_QUEUED_WAIT_SEC,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        if isinstance(model_names, str):
            model_names = [model_names]
        self.model_names = model_names
        self.temperatures = temperatures
        self.max_tokens = max_tokens
        self.reasoning_efforts = reasoning_efforts
        self.model_sample_probs = model_sample_probs
        self.output_model = output_model
        self.structured_output = output_model is not None
        self.verbose = verbose

        # Agent-loop config
        self._tools = list(tools or [])
        self._builtin_tools = list(builtin_tools or [])
        self._max_tool_steps = max_tool_steps
        self._poll_interval_sec = poll_interval_sec
        self._poll_timeout_sec = poll_timeout_sec
        self._max_queued_wait_sec = max_queued_wait_sec
        self._max_attempts = max_attempts

    # ------------------------------------------------------------------
    # Public API — parity with AsyncLLMClient
    # ------------------------------------------------------------------

    def get_kwargs(
        self, model_sample_probs: Optional[List[float]] = None
    ) -> Dict[str, Any]:
        """Sample per-call kwargs from configured model/temperature/effort lists.

        Identical to ``AsyncLLMClient.get_kwargs``.
        """
        posterior = (
            model_sample_probs
            if model_sample_probs is not None
            else self.model_sample_probs
        )
        if self.verbose:
            lines = ["==> SAMPLING:"]
            default_probs = [1.0 / len(self.model_names)] * len(self.model_names)
            probs_to_display = posterior if posterior is not None else default_probs
            for name, prob in zip(self.model_names, probs_to_display):
                lines.append(f"  {name:<30} {prob:>8.4f}")
            logger.info("\n".join(lines))
        return sample_model_kwargs(
            model_names=self.model_names,
            temperatures=self.temperatures,
            max_tokens=self.max_tokens,
            reasoning_efforts=self.reasoning_efforts,
            model_sample_probs=posterior,
        )

    async def query(
        self,
        msg: str,
        system_msg: str,
        msg_history: Optional[List[Dict[str, Any]]] = None,
        llm_kwargs: Optional[Dict[str, Any]] = None,
        model_sample_probs: Optional[List[float]] = None,
        model_posterior: Optional[List[float]] = None,
    ) -> Optional[QueryResult]:
        """Single query. Routes to agents SDK for OpenAI/Azure, legacy
        path otherwise. Returns ``None`` only after exhausting all
        retry attempts (matches ``AsyncLLMClient.query``)."""
        if msg_history is None:
            msg_history = []
        posterior = (
            model_sample_probs
            if model_sample_probs is not None
            else self.model_sample_probs
        )
        if llm_kwargs is None:
            llm_kwargs = sample_model_kwargs(
                model_names=self.model_names,
                temperatures=self.temperatures,
                max_tokens=self.max_tokens,
                reasoning_efforts=self.reasoning_efforts,
                model_sample_probs=posterior,
            )
        elif "model_name" not in llm_kwargs:
            sampled = sample_model_kwargs(
                model_names=self.model_names,
                temperatures=self.temperatures,
                max_tokens=self.max_tokens,
                reasoning_efforts=self.reasoning_efforts,
                model_sample_probs=posterior,
            )
            llm_kwargs = {**sampled, **llm_kwargs}

        if self.verbose:
            logger.info(
                "==> QUERYING: %s", [str(v) for v in llm_kwargs.values()]
            )

        model_posteriors: Optional[Dict[str, float]] = None
        if model_posterior is not None:
            model_posteriors = {
                name: float(prob)
                for name, prob in zip(self.model_names, model_posterior)
            }

        # Provider routing.
        resolved = resolve_model_backend(llm_kwargs["model_name"])
        provider = resolved.provider
        use_agents_sdk = (
            provider in _AGENT_SDK_PROVIDERS and not self.structured_output
        )

        if use_agents_sdk:
            return await self._query_via_agents(
                msg=msg,
                system_msg=system_msg,
                msg_history=msg_history,
                llm_kwargs=llm_kwargs,
                model_posteriors=model_posteriors,
            )
        return await self._query_via_legacy(
            msg=msg,
            system_msg=system_msg,
            msg_history=msg_history,
            llm_kwargs=llm_kwargs,
            model_posteriors=model_posteriors,
        )

    async def batch_query(
        self,
        num_samples: int,
        msg: Union[str, List[str]],
        system_msg: Union[str, List[str]],
        msg_history: Union[List[Dict[str, Any]], List[List[Dict[str, Any]]]] = (),
        llm_kwargs: Optional[List[Dict[str, Any]]] = None,
    ) -> List[QueryResult]:
        """Concurrent batch — wraps ``self.query`` with ``asyncio.gather``."""
        if isinstance(msg, str):
            msg = [msg] * num_samples
        if isinstance(system_msg, str):
            system_msg = [system_msg] * num_samples
        if len(msg_history) == 0:
            histories: List[List[Dict[str, Any]]] = [[] for _ in range(num_samples)]
        elif isinstance(msg_history[0], dict):
            histories = [list(msg_history) for _ in range(num_samples)]  # type: ignore[list-item]
        else:
            histories = [list(h) for h in msg_history]  # type: ignore[arg-type]

        if llm_kwargs is None:
            llm_kwargs = [None] * num_samples  # type: ignore[list-item]

        tasks = [
            self.query(
                msg=msg[i],
                system_msg=system_msg[i],
                msg_history=histories[i],
                llm_kwargs=llm_kwargs[i],
            )
            for i in range(num_samples)
        ]
        gathered = await asyncio.gather(*tasks, return_exceptions=True)

        final: List[QueryResult] = []
        for i, item in enumerate(gathered):
            if isinstance(item, Exception):
                logger.info("Error in batch query task %d: %s", i, item)
            elif item is not None:
                final.append(item)
        if self.verbose:
            costs = [r.cost for r in final if r.cost is not None]
            logger.info("==> SAMPLING: Total API costs: $%.4f", sum(costs))
        return final

    async def batch_kwargs_query(
        self,
        num_samples: int,
        msg: Union[str, List[str]],
        system_msg: Union[str, List[str]],
        msg_history: Union[List[Dict[str, Any]], List[List[Dict[str, Any]]]] = (),
        model_sample_probs: Optional[List[float]] = None,
    ) -> List[QueryResult]:
        """Concurrent batch where each task samples its own kwargs.

        Matches ``AsyncLLMClient.batch_kwargs_query``: ``model_sample_probs``
        is used both as the sampling distribution AND as the posterior
        recorded on each resulting ``QueryResult.model_posteriors`` for
        downstream bandit accounting.
        """
        posterior = (
            model_sample_probs
            if model_sample_probs is not None
            else self.model_sample_probs
        )
        per_task_kwargs = [
            sample_model_kwargs(
                model_names=self.model_names,
                temperatures=self.temperatures,
                max_tokens=self.max_tokens,
                reasoning_efforts=self.reasoning_efforts,
                model_sample_probs=posterior,
            )
            for _ in range(num_samples)
        ]
        # Normalize message inputs the same way batch_query does, so we
        # can dispatch individual tasks through self.query() with the
        # posterior threaded as model_posterior (which becomes
        # QueryResult.model_posteriors). batch_query() expects msg/
        # system_msg either as a single string or as a list — we
        # delegate the spread to it via the per-task lists.
        if isinstance(msg, str):
            msg_list = [msg] * num_samples
        else:
            msg_list = list(msg)
        if isinstance(system_msg, str):
            sys_list = [system_msg] * num_samples
        else:
            sys_list = list(system_msg)
        if len(msg_history) == 0:
            histories: List[List[Dict[str, Any]]] = [[] for _ in range(num_samples)]
        elif isinstance(msg_history[0], dict):
            histories = [list(msg_history) for _ in range(num_samples)]  # type: ignore[list-item]
        else:
            histories = [list(h) for h in msg_history]  # type: ignore[arg-type]

        tasks = [
            self.query(
                msg=msg_list[i],
                system_msg=sys_list[i],
                msg_history=histories[i],
                llm_kwargs=per_task_kwargs[i],
                model_sample_probs=posterior,
                model_posterior=posterior,
            )
            for i in range(num_samples)
        ]
        gathered = await asyncio.gather(*tasks, return_exceptions=True)
        final: List[QueryResult] = []
        for i, item in enumerate(gathered):
            if isinstance(item, Exception):
                logger.info("Error in batch query task %d: %s", i, item)
            elif item is not None:
                final.append(item)
        if self.verbose:
            costs = [r.cost for r in final if r.cost is not None]
            logger.info("==> SAMPLING: Total API costs: $%.4f", sum(costs))
        return final

    # ------------------------------------------------------------------
    # Internal: agents-SDK path
    # ------------------------------------------------------------------

    async def _query_via_agents(
        self,
        msg: str,
        system_msg: str,
        msg_history: List[Dict[str, Any]],
        llm_kwargs: Dict[str, Any],
        model_posteriors: Optional[Dict[str, float]],
        *,
        tools_override: Optional[List[Any]] = None,
        tool_context: Optional[Any] = None,
        max_turns_override: Optional[int] = None,
        output_type: Optional[type] = None,
    ) -> Optional[QueryResult]:
        """Internal agents-SDK run.

        Used by both ``query`` (no tools, no context) and ``run_agent``
        (per-call tools + context). Builds a fresh Agent + Model +
        AsyncOpenAI client per query (one shinka generation), then
        calls ``agents.Runner.run`` directly. Transport-level retry on
        individual API calls is handled by the OpenAI SDK's built-in
        ``max_retries`` (default 2) — we don't add an outer retry
        layer because background mode removes the long-idle-TCP
        failure mode that motivated it.
        """
        from agents import Agent, ModelSettings, Runner

        model_name = llm_kwargs["model_name"]
        # Resolve API model name once — deterministic for a given
        # shinka model id, so we don't re-resolve later.
        resolved = resolve_model_backend(model_name)
        api_model_name = resolved.api_model_name

        # ModelSettings derived from sampled llm_kwargs.
        model_settings_kwargs: Dict[str, Any] = {}
        if "temperature" in llm_kwargs and llm_kwargs["temperature"] is not None:
            model_settings_kwargs["temperature"] = llm_kwargs["temperature"]
        if "max_output_tokens" in llm_kwargs:
            model_settings_kwargs["max_tokens"] = llm_kwargs["max_output_tokens"]
        reasoning_spec = llm_kwargs.get("reasoning")
        if reasoning_spec and reasoning_spec.get("effort"):
            # Lazy import keeps this module importable when the
            # openai SDK structure changes.
            from openai.types.shared import Reasoning

            reasoning_payload: Dict[str, Any] = {"effort": reasoning_spec["effort"]}
            if "summary" in reasoning_spec:
                reasoning_payload["summary"] = reasoning_spec["summary"]
            model_settings_kwargs["reasoning"] = Reasoning(**reasoning_payload)

        # Per-call tools take precedence over constructor-time defaults.
        effective_tools = (
            list(tools_override) if tools_override is not None else list(self._tools)
        )

        # Build the Agent (and its AsyncOpenAI client) fresh for this
        # query. Each shinka generation gets its own client; we don't
        # share clients across generations to avoid any cross-call
        # pool state. ``get_async_client_llm`` already implements the
        # Azure base_url + api_version overrides.
        client, _api_model, _provider = get_async_client_llm(
            model_name, structured_output=False
        )
        model = BackgroundOpenAIResponsesModel(
            model=api_model_name,
            openai_client=client,
            poll_interval_sec=self._poll_interval_sec,
            poll_timeout_sec=self._poll_timeout_sec,
            max_queued_wait_sec=self._max_queued_wait_sec,
        )
        # Lazy import — avoids a top-level circular reference between
        # client.py and hooks.py (hooks pulls in the tool-context type).
        from .hooks import ShinkaAgentHooks

        agent_kwargs: Dict[str, Any] = {
            "name": "shinka",
            "instructions": system_msg,
            "model": model,
            "model_settings": ModelSettings(**model_settings_kwargs),
            "tools": effective_tools,
            # SDK-native lifecycle hooks. Replace the legacy
            # ``record_tool_call`` plumbing each tool wrapper used to
            # carry; tools now just set ``ctx.last_tool_extras`` if
            # they have structured per-call data, and the hook
            # appends a trace entry on ``on_tool_end``.
            "hooks": ShinkaAgentHooks(),
        }
        if output_type is not None:
            # The SDK instructs the model to emit a final response
            # matching this schema and parses it into a typed instance
            # accessible via ``run_result.final_output``. We surface
            # that on the returned ``QueryResult.final_output_obj`` so
            # callers don't need to re-parse text with regex.
            agent_kwargs["output_type"] = output_type
        agent = Agent(**agent_kwargs)

        # Compose the agent input: prior history items if any, plus
        # the current user message. The agents SDK accepts a plain
        # string for single-turn input, but msg_history makes it
        # multi-turn.
        agent_input: Union[str, List[Dict[str, Any]]]
        if msg_history:
            agent_input = [*msg_history, {"role": "user", "content": msg}]
        else:
            agent_input = msg

        runner_kwargs: Dict[str, Any] = {
            "max_turns": max_turns_override
            if max_turns_override is not None
            else self._max_tool_steps,
        }
        if tool_context is not None:
            runner_kwargs["context"] = tool_context

        try:
            run_result = await Runner.run(agent, agent_input, **runner_kwargs)
        except Exception as exc:
            logger.info("Agent run failed: %s", exc)
            return None

        return _runresult_to_queryresult(
            run_result,
            msg=msg,
            system_msg=system_msg,
            msg_history=msg_history,
            shinka_model_name=model_name,
            api_model_name=api_model_name,
            llm_kwargs=llm_kwargs,
            model_posteriors=model_posteriors,
            verbose=self.verbose,
        )

    # ------------------------------------------------------------------
    # Public: agentic (tool-using) entry point
    # ------------------------------------------------------------------

    async def run_agent(
        self,
        msg: str,
        system_msg: str,
        *,
        tool_context: Any,
        tools: List[Any],
        max_turns: Optional[int] = None,
        msg_history: Optional[List[Dict[str, Any]]] = None,
        llm_kwargs: Optional[Dict[str, Any]] = None,
        output_type: Optional[type] = None,
        model_sample_probs: Optional[List[float]] = None,
        model_posterior: Optional[List[float]] = None,
    ) -> Optional[QueryResult]:
        """Run the agent with the given tools and a per-call context.

        Unlike ``query``, this accepts:

        * ``tools`` — an explicit list of agent tools to expose for
          this call (FunctionTool, WebSearchTool, etc.).
        * ``tool_context`` — a ``ShinkaToolContext`` (or anything the
          tools expect) passed to ``Runner.run(context=...)``. Tools
          read from and mutate this; the caller can inspect it after
          the call to see what changed.
        * ``max_turns`` — cap on agent-loop iterations for this call.
          Defaults to the constructor's ``max_tool_steps``.

        For Azure/OpenAI text models, the run goes through the
        agents SDK (bg+poll transport + the OpenAI SDK's built-in
        retry). For other providers or structured-output requests,
        falls back to ``query`` semantics — tools/context are
        ignored since the legacy path doesn't support them. The
        caller is responsible for handling that gracefully
        (typically by not setting ``tool_context`` when running on
        non-OpenAI providers).
        """
        if msg_history is None:
            msg_history = []
        posterior = (
            model_sample_probs
            if model_sample_probs is not None
            else self.model_sample_probs
        )
        if llm_kwargs is None:
            llm_kwargs = sample_model_kwargs(
                model_names=self.model_names,
                temperatures=self.temperatures,
                max_tokens=self.max_tokens,
                reasoning_efforts=self.reasoning_efforts,
                model_sample_probs=posterior,
            )
        elif "model_name" not in llm_kwargs:
            sampled = sample_model_kwargs(
                model_names=self.model_names,
                temperatures=self.temperatures,
                max_tokens=self.max_tokens,
                reasoning_efforts=self.reasoning_efforts,
                model_sample_probs=posterior,
            )
            llm_kwargs = {**sampled, **llm_kwargs}

        if self.verbose:
            logger.info(
                "==> AGENT RUN: %s (tools=%d, max_turns=%s)",
                [str(v) for v in llm_kwargs.values()],
                len(tools),
                max_turns,
            )

        model_posteriors: Optional[Dict[str, float]] = None
        if model_posterior is not None:
            model_posteriors = {
                name: float(prob)
                for name, prob in zip(self.model_names, model_posterior)
            }

        resolved = resolve_model_backend(llm_kwargs["model_name"])
        provider = resolved.provider
        use_agents_sdk = (
            provider in _AGENT_SDK_PROVIDERS and not self.structured_output
        )

        if use_agents_sdk:
            return await self._query_via_agents(
                msg=msg,
                system_msg=system_msg,
                msg_history=msg_history,
                llm_kwargs=llm_kwargs,
                model_posteriors=model_posteriors,
                tools_override=tools,
                tool_context=tool_context,
                max_turns_override=max_turns,
                output_type=output_type,
            )

        # Non-OpenAI: tools/context can't be honored. Fall back to
        # the legacy single-turn query and let the caller deal with
        # the lack of tool support.
        if tools:
            logger.warning(
                "run_agent: tools requested on non-OpenAI provider %r; "
                "falling back to single-turn query (tools ignored).",
                provider,
            )
        if output_type is not None:
            logger.warning(
                "run_agent: output_type requested on non-OpenAI provider %r; "
                "structured output is silently ignored on the legacy path.",
                provider,
            )
        return await self._query_via_legacy(
            msg=msg,
            system_msg=system_msg,
            msg_history=msg_history,
            llm_kwargs=llm_kwargs,
            model_posteriors=model_posteriors,
        )

    # ------------------------------------------------------------------
    # Internal: legacy (non-agents-SDK) path
    # ------------------------------------------------------------------

    async def _query_via_legacy(
        self,
        msg: str,
        system_msg: str,
        msg_history: List[Dict[str, Any]],
        llm_kwargs: Dict[str, Any],
        model_posteriors: Optional[Dict[str, float]],
    ) -> Optional[QueryResult]:
        """Fall through to existing ``query_async`` for non-OpenAI
        providers and structured-output requests. Mirrors the inner
        retry loop of ``AsyncLLMClient.query``."""
        try_count = 0
        while try_count < self._max_attempts:
            try:
                result = await query_async(
                    msg=msg,
                    system_msg=system_msg,
                    msg_history=msg_history,
                    output_model=self.output_model,
                    model_posteriors=model_posteriors,
                    **llm_kwargs,
                )
                if (
                    self.verbose
                    and hasattr(result, "cost")
                    and result.cost is not None
                ):
                    logger.info("==> QUERY: API cost: $%.4f", result.cost)
                return result
            except Exception as exc:
                try_count += 1
                logger.info(
                    "%d/%d Error in legacy query: %s",
                    try_count,
                    self._max_attempts,
                    exc,
                )
                if try_count < self._max_attempts:
                    await asyncio.sleep(1.0)
        return None


# ----------------------------------------------------------------------
# RunResult -> QueryResult adapter
# ----------------------------------------------------------------------


def _runresult_to_queryresult(
    run_result: Any,
    *,
    msg: str,
    system_msg: str,
    msg_history: List[Dict[str, Any]],
    shinka_model_name: str,
    api_model_name: str,
    llm_kwargs: Dict[str, Any],
    model_posteriors: Optional[Dict[str, float]],
    verbose: bool,
) -> QueryResult:
    """Adapt an agents-SDK ``RunResult`` into shinka's ``QueryResult``.

    Sums token usage across all underlying ``raw_responses`` (an agent
    run may make multiple LLM calls if it invokes tools). Costs are
    computed via the existing pricing module so we stay consistent
    with the legacy path and the ``programs.cost`` field in the DB.
    """
    raw_responses = list(getattr(run_result, "raw_responses", []) or [])

    total_input_tokens = 0
    total_output_tokens = 0
    total_thinking_tokens = 0
    for resp in raw_responses:
        usage = getattr(resp, "usage", None)
        if usage is None:
            continue
        total_input_tokens += getattr(usage, "input_tokens", 0) or 0
        total_output_tokens += getattr(usage, "output_tokens", 0) or 0
        out_details = getattr(usage, "output_tokens_details", None)
        if out_details is not None:
            total_thinking_tokens += getattr(out_details, "reasoning_tokens", 0) or 0

    # output_tokens from the API includes reasoning_tokens; shinka's
    # convention (see openai.py:get_openai_costs) is to surface
    # non-thinking output separately.
    visible_output_tokens = max(0, total_output_tokens - total_thinking_tokens)

    if model_exists(api_model_name):
        input_cost, output_cost = calculate_cost(
            api_model_name,
            total_input_tokens,
            total_output_tokens,  # total = visible + thinking, matches legacy path
        )
    else:
        if verbose:
            logger.warning(
                "Model %r has no pricing entry; defaulting cost to 0",
                api_model_name,
            )
        input_cost, output_cost = 0.0, 0.0

    # Tool call count. ``run_result.new_items`` is a list of RunItem
    # objects; tool calls are typed differently per SDK version. We
    # count via ``type`` attribute which is the stable string
    # discriminator (`tool_call_item`, `function_call_item`, etc.).
    num_tool_calls = 0
    for item in getattr(run_result, "new_items", []) or []:
        item_type = getattr(item, "type", "")
        if isinstance(item_type, str) and (
            "tool_call" in item_type or "function_call" in item_type
        ):
            num_tool_calls += 1

    final_output = getattr(run_result, "final_output", "")
    if isinstance(final_output, str):
        content = final_output
        final_output_obj: Optional[Any] = None
    else:
        # Structured output (e.g. a Pydantic instance) — caller will
        # consume the typed object; keep ``content`` as a string repr
        # so the rest of the QueryResult shape is unchanged.
        final_output_obj = final_output
        if hasattr(final_output, "model_dump_json"):
            try:
                content = final_output.model_dump_json()
            except Exception:
                content = str(final_output)
        else:
            content = str(final_output)

    new_msg_history = list(msg_history) + [
        {"role": "user", "content": msg},
        {"role": "assistant", "content": content},
    ]

    # Parity with legacy QueryResult shape:
    #
    # - ``model_name`` stores the API name (e.g. ``"gpt-5.4-mini"``).
    #   Matches ``query_openai_async`` which receives the resolved
    #   api_model_name from ``query_async`` and passes it through.
    #   Downstream analysis (DB metadata via to_dict, shinka_visualize)
    #   then sees the same string regardless of proposal path.
    #
    # - ``kwargs`` stores temperature / max_output_tokens / reasoning /
    #   etc. but NOT ``model_name``. Legacy ``query_async`` extracts
    #   ``model_name`` as a named parameter, so by the time it
    #   reaches ``query_*_async(**kwargs)`` the dict has been
    #   stripped. Reproducing that strip keeps the DB row's
    #   ``llm_result.kwargs`` consistent across paths.
    #
    # The shinka-prefixed model id is preserved separately by the
    # orchestrator in ``meta_patch_data`` via ``**llm_kwargs``.
    kwargs_without_model = {k: v for k, v in llm_kwargs.items() if k != "model_name"}

    return QueryResult(
        content=content,
        msg=msg,
        system_msg=system_msg,
        new_msg_history=new_msg_history,
        model_name=api_model_name,
        kwargs=kwargs_without_model,
        input_tokens=total_input_tokens,
        output_tokens=visible_output_tokens,
        thinking_tokens=total_thinking_tokens,
        cost=input_cost + output_cost,
        input_cost=input_cost,
        output_cost=output_cost,
        thought="",  # TODO Phase B follow-up: extract reasoning summary
        model_posteriors=model_posteriors,
        num_tool_calls=num_tool_calls,
        num_total_queries=len(raw_responses),
        final_output_obj=final_output_obj,
    )
