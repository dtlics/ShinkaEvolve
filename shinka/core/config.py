from dataclasses import dataclass, field
from typing import List, Optional, Union

from shinka.llm import BanditBase
from shinka.defaults import (
    DEFAULT_TASK_SYS_MSG,
    default_llm_dynamic_selection_kwargs,
    default_llm_kwargs,
    default_llm_models,
    default_patch_type_probs,
    default_patch_types,
    default_prompt_patch_type_probs,
    default_prompt_patch_types,
)

FOLDER_PREFIX = "gen"


@dataclass
class EvolutionConfig:
    task_sys_msg: Optional[str] = DEFAULT_TASK_SYS_MSG
    patch_types: List[str] = field(default_factory=default_patch_types)
    patch_type_probs: List[float] = field(default_factory=default_patch_type_probs)
    num_generations: int = 50
    max_patch_resamples: int = 3
    max_patch_attempts: int = 1
    job_type: str = "local"
    language: str = "python"
    llm_models: List[str] = field(default_factory=default_llm_models)
    llm_dynamic_selection: Optional[Union[str, BanditBase]] = "ucb"
    llm_dynamic_selection_kwargs: dict = field(
        default_factory=default_llm_dynamic_selection_kwargs
    )
    llm_kwargs: dict = field(default_factory=default_llm_kwargs)
    meta_rec_interval: Optional[int] = 10
    meta_llm_models: Optional[List[str]] = None
    meta_llm_kwargs: dict = field(default_factory=lambda: {})
    meta_max_recommendations: int = 5
    sample_single_meta_rec: bool = True
    embedding_model: Optional[str] = "text-embedding-3-small"
    init_program_path: Optional[str] = "initial.py"
    results_dir: Optional[str] = None
    max_novelty_attempts: int = 3
    code_embed_sim_threshold: float = 0.99
    novelty_llm_models: Optional[List[str]] = None
    novelty_llm_kwargs: dict = field(default_factory=lambda: {})
    use_text_feedback: bool = False
    max_api_costs: Optional[float] = None
    inspiration_sort_order: str = "ascending"
    enable_controlled_oversubscription: bool = False
    proposal_target_mode: str = "adaptive"
    proposal_target_min_samples: int = 5
    proposal_target_ratio_cap: float = 2.0
    proposal_buffer_max: int = 2
    proposal_target_hard_cap: Optional[int] = None
    proposal_target_ewma_alpha: float = 0.3

    # Agentic proposal — Phase D. When True, the proposal coordinator
    # dispatches to ``_run_agent_proposal`` (AgentLLMClient.run_agent
    # with apply_patch tool, agent-driven retry loop). When False
    # (default), uses the legacy ``_run_patch_async`` with an explicit
    # orchestrator-driven retry loop. Off by default so existing
    # experiments don't change behavior without opt-in.
    use_agentic_proposer: bool = False

    # Tools exposed to the agent when use_agentic_proposer=True.
    # Default is ``apply_patch`` + ``evaluate`` so the LLM can iterate
    # apply→evaluate→reflect inside one generation (Phase E). The eval
    # closure is wired up by ``_run_agent_proposal`` and its result is
    # cached on the tool context so the orchestrator skips re-evaluating
    # after the agent loop returns.
    #
    # Add ``read_host_file`` to let the agent peek at other files in the
    # task directory, ``query_evolution_db`` to read past generation
    # history, or ``web_search`` to enable OpenAI/Azure server-side web
    # search (incurs per-call $0.01-0.03 plus content-token cost).
    agentic_tools: List[str] = field(
        default_factory=lambda: ["apply_patch", "evaluate"]
    )

    # Azure-aware LLM call kwargs (phase 1 of research-grounding).
    #
    # ``cache_static_system_prompt``: derive a sha256-based
    # ``prompt_cache_key`` from each call's system prompt so the
    # Responses API hits its prompt cache on repeat calls (5-10×
    # input-cost reduction on identical system msgs across a run).
    #
    # ``tag_calls_with_metadata``: surface ``{run_id, generation,
    # island_idx, purpose}`` on every Azure call so cost dashboards
    # can break spend down by feature (proposer / meta / dr_stage_*
    # / lit_grounded).
    #
    # ``store_llm_responses``: forwarded as ``store`` on the
    # responses.create call. Azure defaults to retaining responses
    # for 31 days; flip to ``False`` (the default here) for privacy.
    cache_static_system_prompt: bool = True
    tag_calls_with_metadata: bool = True
    store_llm_responses: bool = False

    # Deep-research meta cycle (phase 2 of research-grounding).
    #
    # When enabled, every ``dr_meta_interval`` evaluated programs we
    # run a per-island Stage A→D pipeline that produces *structured*,
    # *grounded* briefs (drift → novelty → DR call → code grounding).
    # The freeform meta cycle continues to run at ``meta_rec_interval``;
    # when a fresh DR brief lands for an island, the sampler prefers
    # it over the freeform recommendations for that island.
    #
    # DR uses a SEPARATE Azure resource from the main gpt-* endpoint.
    # Set ``AZURE_DR_ENDPOINT`` + ``AZURE_DR_API_KEY`` (and optionally
    # ``AZURE_DR_API_VERSION``) in .env or the shell environment. See
    # ``shinka.llm.agent.dr_client``.
    #
    # Default is OFF — existing experiments don't change behavior
    # without opt-in.
    enable_deep_research: bool = False
    dr_meta_interval: int = 20
    dr_model: str = "o3-deep-research"
    dr_endpoint_env: str = "AZURE_DR_ENDPOINT"
    dr_api_key_env: str = "AZURE_DR_API_KEY"
    dr_api_version_env: str = "AZURE_DR_API_VERSION"
    dr_reasoning_effort: str = "medium"
    dr_max_tool_calls: int = 20
    dr_background: bool = True
    dr_max_calls_per_run: int = 30
    dr_brief_cache_threshold: float = 0.95
    dr_drift_threshold: float = 0.5
    # Cheap drift judge used in Stage A. Routed through the regular
    # AgentLLMClient so it shares prompt-cache / metadata wiring.
    dr_stage_a_llm_model: str = "azure-gpt-5.4-mini"
    # Stage D code-grounding domain allowlist passed through to the
    # web_search tool. Defaults to authoritative software/research
    # sources so the brief's reference_snippet lands in domains the
    # agent can trust to confirm.
    dr_code_grounding_domains: List[str] = field(
        default_factory=lambda: [
            "github.com",
            "arxiv.org",
            "huggingface.co",
            "paperswithcode.com",
            "docs.python.org",
        ]
    )

    # literature_grounded mutation arm (phase 3 of research-grounding).
    #
    # When enabled, a new mutation type joins ``diff`` / ``full`` /
    # ``cross`` with probability ``literature_grounded_prob``. The
    # orchestrator picks one ``BriefItem`` from the parent's island
    # brief and asks the agent to attempt the technique. ``web_search``
    # is enabled for THIS one call only (regardless of the global
    # ``agentic_tools`` config) so the agent can confirm the
    # reference snippet. The agent has explicit abort permission —
    # when it returns the parent unchanged citing insufficient
    # reference, the bandit update is skipped (we preserve the
    # "aborts ≠ low-score" invariant).
    #
    # Requires ``enable_deep_research=True`` (this arm consumes DR
    # brief items as inputs); silently no-ops otherwise.
    enable_literature_grounded: bool = False
    literature_grounded_prob: float = 0.1
    literature_grounded_max_searches: int = 3
    literature_grounded_max_fetches: int = 2
    literature_grounded_max_turns: int = 6
    literature_grounded_web_search_context_size: str = "high"

    # Meta-prompt evolution settings.
    evolve_prompts: bool = False
    prompt_patch_types: List[str] = field(default_factory=default_prompt_patch_types)
    prompt_patch_type_probs: List[float] = field(
        default_factory=default_prompt_patch_type_probs
    )
    prompt_evolution_interval: Optional[int] = None
    prompt_archive_size: int = 10
    prompt_llm_models: Optional[List[str]] = None
    prompt_llm_kwargs: dict = field(default_factory=lambda: {})
    prompt_ucb_exploration_constant: float = 1.0
    prompt_epsilon: float = 0.1
    prompt_evo_top_k_programs: int = 3
    prompt_percentile_recompute_interval: int = 20
