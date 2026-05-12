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
    # Phase 2 of research-grounding: in bg+poll mode the Responses API
    # requires server-side storage during the response lifecycle. When True
    # (default) we issue ``responses.delete(id)`` after retrieval so Azure's
    # 31-day retention is bypassed; when False the response lingers on Azure
    # for 31 days.
    delete_llm_responses_after_retrieval: bool = True
    # Phase 3b: hash the static system prompt into ``prompt_cache_key`` so
    # repeated calls with identical system messages get OpenAI's prompt-cache
    # discount on input tokens.
    cache_static_system_prompt: bool = True
    # Phase 3b: tag every Responses API call with metadata
    # ``{run_id, generation, island_idx, purpose}`` so Azure dashboards can
    # break costs down per-run / per-generation / per-stage.
    tag_calls_with_metadata: bool = True
    # --- Phase 4: error-fix retry loop ---
    # When True, evaluator failures route into an iterative retry loop with
    # the error in context (different from the existing bootstrap fix mode
    # in sample_fix(); the retry loop is per-candidate, not per-island).
    enable_error_fix_loop: bool = False
    # Per-mutation-type retry budgets (hardcoded for v1 per the doc; tune
    # once we have real per-round success-rate data).
    error_fix_rounds_by_type: dict = field(
        default_factory=lambda: {
            "diff": 2,
            "full": 3,
            "cross": 3,
            "literature_grounded": 4,
        }
    )
    # Decay factor applied to the proposer-bandit reward on successful fixes:
    # reward = final_score * decay ** attempt_round. 0.7 means a fix at round
    # 1 gets 70% credit, round 2 gets 49%, etc. Captures first-pass quality
    # without zeroing rescued candidates.
    error_fix_score_decay: float = 0.7
    # Separate bandit instance dedicated to selecting the model that RUNS the
    # fix rounds. Disabling falls back to reusing the proposer-model bandit's
    # current pick.
    enable_fixer_bandit: bool = True
    fixer_bandit_algorithm: str = "ucb"
    # Optional Codex server-side shell tool, gated to the error-fix loop ONLY
    # (never proposer / meta / DR / lit_grounded). Disabled by default.
    error_fix_enable_shell: bool = False
    error_fix_shell_budget: int = 4
    error_fix_shell_models: List[str] = field(
        default_factory=lambda: ["gpt-5-codex"]
    )
    # --- Phase 5: deep-research meta pipeline ---
    # When True, the meta callsite splits into a 4-stage pipeline
    # (drift -> novelty -> deep_research -> code_grounding) at the
    # ``dr_meta_interval`` cadence. The existing freeform meta still
    # runs at ``meta_rec_interval`` cadence on the off-rounds so the
    # off-the-shelf path stays warm.
    enable_deep_research: bool = False
    # Cadence (in evaluated programs) at which the DR pipeline fires.
    # Must be >= meta_rec_interval; default 20 (every other freeform
    # meta cycle).
    dr_meta_interval: int = 20
    # Model + endpoint config. The actual key/endpoint live in env vars
    # so secrets never enter the config or git.
    dr_model: str = "o3-deep-research"
    dr_endpoint_env: str = "AZURE_DR_ENDPOINT"
    dr_api_key_env: str = "AZURE_DR_API_KEY"
    # Standard preset: medium reasoning + ~20 tool calls per DR run
    # gives ~5-10 min wall clock at ~$5-10 / call. Background polling
    # handles the long wait without dropping the HTTP socket.
    dr_reasoning_effort: str = "medium"
    dr_max_tool_calls: int = 20
    dr_background: bool = True
    # Hard cap on DR calls across a whole evolution run.
    dr_max_calls_per_run: int = 30
    # Above this cosine similarity, Stage B links to an existing brief
    # instead of triggering another DR call (cross-island dedup).
    dr_brief_cache_threshold: float = 0.95
    # Below this drift score, Stage A short-circuits the pipeline and
    # the island re-uses its previous brief unchanged.
    dr_drift_threshold: float = 0.5
    # Allowed-domains list for Stage D code grounding. Stage D's web
    # search and fetch tools are constrained to these so they stay on
    # task instead of wandering the internet.
    dr_code_grounding_domains: List[str] = field(
        default_factory=lambda: [
            "github.com",
            "arxiv.org",
            "huggingface.co",
            "paperswithcode.com",
            "docs.python.org",
        ]
    )
    proposal_target_mode: str = "adaptive"
    proposal_target_min_samples: int = 5
    proposal_target_ratio_cap: float = 2.0
    proposal_buffer_max: int = 2
    proposal_target_hard_cap: Optional[int] = None
    proposal_target_ewma_alpha: float = 0.3

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
