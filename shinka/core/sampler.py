from typing import Any, List, Optional, Tuple, Literal
import numpy as np
from shinka.database import Program
from shinka.database.inspirations import InspirationContextBuilder
from shinka.prompts import (
    construct_eval_history_msg,
    perf_str,
    format_text_feedback_section,
    BASE_SYSTEM_MSG,
    DIFF_SYS_FORMAT,
    DIFF_ITER_MSG,
    FULL_ITER_MSG,
    FULL_SYS_FORMATS,
    CROSS_SYS_FORMAT,
    CROSS_ITER_MSG,
    get_cross_component,
    FIX_SYS_FORMAT,
    FIX_ITER_MSG,
    format_error_output_section,
    # Phase 4 error-fix retry loop:
    ERROR_FIX_SYS_FORMAT,
    ERROR_FIX_ITER_MSG,
    AttemptRecord,
    format_prior_attempt_log,
    format_traceback_section,
    # Phase 6 literature_grounded mutation arm:
    LIT_GROUNDED_SYS_FORMAT,
    LIT_GROUNDED_ITER_MSG,
)
from shinka.prompts.prompts_init import INIT_SYSTEM_MSG, INIT_USER_MSG
from shinka.defaults import default_patch_type_probs, default_patch_types
import logging

logger = logging.getLogger(__name__)


def _brief_has_grounded_item(island_brief: Optional[Any]) -> bool:
    """True when the brief carries at least one item with a non-empty
    ``reference_snippet`` -- the suppression rule from the design doc.

    A brief without grounded items would force the literature_grounded
    arm to fabricate a reference; we prefer to skip the arm entirely.
    """
    if island_brief is None:
        return False
    items = getattr(island_brief, "items", None) or []
    for item in items:
        snippet = getattr(item, "reference_snippet", "") or ""
        if snippet.strip():
            return True
    return False


def _pick_brief_item(island_brief: Optional[Any]) -> Optional[Any]:
    """Pick one grounded brief item for a single literature_grounded call.

    Random uniform across items that have a non-empty reference_snippet.
    Returns ``None`` if no grounded item exists (caller should treat
    that as a suppression signal).
    """
    if island_brief is None:
        return None
    candidates = [
        item
        for item in getattr(island_brief, "items", None) or []
        if (getattr(item, "reference_snippet", "") or "").strip()
    ]
    if not candidates:
        return None
    idx = int(np.random.randint(0, len(candidates)))
    return candidates[idx]


def _render_island_brief(island_brief: Optional[Any]) -> Optional[str]:
    """Render a deep-research IslandBrief into the recommendation slot text.

    Returns ``None`` if the brief is missing or empty. The output is
    plain Markdown so it splices into existing system-message rendering
    without escaping. Each item gets a numbered block with idea +
    rationale + reference + gotchas; empty fields are omitted entirely.
    """
    if island_brief is None:
        return None
    items = getattr(island_brief, "items", None) or []
    summary = getattr(island_brief, "summary", "") or ""
    if not items and not summary.strip():
        return None
    rows: List[str] = []
    if summary.strip():
        rows.append(summary.strip())
    for idx, item in enumerate(items, start=1):
        idea = (getattr(item, "idea", "") or "").strip()
        rationale = (getattr(item, "rationale", "") or "").strip()
        reference = (getattr(item, "reference_snippet", "") or "").strip()
        source = (getattr(item, "source", "") or "").strip()
        gotchas = (getattr(item, "gotchas", "") or "").strip()
        if not idea:
            continue
        block = [f"{idx}. {idea}"]
        if rationale:
            block.append(f"   Rationale: {rationale}")
        if reference:
            block.append(f"   Reference: {reference}")
        if source:
            block.append(f"   Source: {source}")
        if gotchas:
            block.append(f"   Gotchas: {gotchas}")
        rows.append("\n".join(block))
    rendered = "\n\n".join(rows).strip()
    return rendered or None


class PromptSampler:
    def __init__(
        self,
        task_sys_msg: Optional[str] = None,
        language: str = "python",
        patch_types: Optional[List[str]] = None,
        patch_type_probs: Optional[List[float]] = None,
        use_text_feedback: bool = False,
        inspiration_sort_order: Literal[
            "ascending", "chronological", "none"
        ] = "ascending",
    ):
        if patch_types is None:
            patch_types = default_patch_types()
        if patch_type_probs is None:
            patch_type_probs = default_patch_type_probs()

        self.task_sys_msg = task_sys_msg
        self.language = language
        self.patch_types = patch_types
        self.patch_type_probs = patch_type_probs
        # Check if probabilities sum to 1.0 w. tolerance for errors
        prob_sum = np.sum(patch_type_probs)
        if not np.isclose(prob_sum, 1.0, atol=1e-6):
            raise ValueError(
                f"Coding type probabilities must sum to 1.0, got {prob_sum:.6f}"
            )
        # Whether to use text feedback in the prompt
        self.use_text_feedback = use_text_feedback
        # Context builder for sorting inspirations (least-to-most by default)
        self.context_builder = InspirationContextBuilder(
            sort_order=inspiration_sort_order
        )

    def initial_program_prompt(self) -> Tuple[str, str]:
        """Generate the prompt for the initial program."""
        if self.task_sys_msg is None:
            sys_msg = INIT_SYSTEM_MSG
            task_description = "The user has not provided a task description."
        else:
            sys_msg = self.task_sys_msg
            task_description = self.task_sys_msg

        user_msg = INIT_USER_MSG.format(
            language=self.language,
            task_description=task_description,
        )
        return sys_msg, user_msg

    def sample(
        self,
        parent: Program,
        archive_inspirations: List[Program],
        top_k_inspirations: List[Program],
        meta_recommendations: Optional[str] = None,
        island_brief: Optional[Any] = None,
    ) -> Tuple[str, str, str]:
        if self.task_sys_msg is None:
            sys_msg = BASE_SYSTEM_MSG
        else:
            sys_msg = self.task_sys_msg

        # Sample coding type. Apply suppression rules first:
        # - "cross" is suppressed when there are no inspirations.
        # - "literature_grounded" (Phase 6) is suppressed when the linked
        #   island brief has no items carrying a non-empty
        #   reference_snippet -- the arm has nothing to ground on.
        suppressed_types: List[str] = []
        if len(archive_inspirations) == 0 and len(top_k_inspirations) == 0:
            suppressed_types.append("cross")
        if not _brief_has_grounded_item(island_brief):
            suppressed_types.append("literature_grounded")

        if suppressed_types:
            valid_types = [
                t for t in self.patch_types if t not in suppressed_types
            ]
            valid_probs = [
                p
                for t, p in zip(self.patch_types, self.patch_type_probs)
                if t not in suppressed_types
            ]
            # Renormalize probabilities
            prob_sum = sum(valid_probs)
            if prob_sum > 0:
                valid_probs = [p / prob_sum for p in valid_probs]
            else:
                # Fallback: uniform distribution if all probs are zero
                if len(valid_types) > 0:
                    valid_probs = [1.0 / len(valid_types)] * len(valid_types)
                else:
                    # No valid types, fall back to original patch types
                    valid_types = self.patch_types
                    valid_probs = self.patch_type_probs
            patch_type = np.random.choice(valid_types, p=valid_probs)
        else:
            patch_type = np.random.choice(
                self.patch_types,
                p=self.patch_type_probs,
            )

        # Add meta-recommendations BEFORE format instructions (if provided).
        # Phase 5d of research-grounding: when an ``island_brief`` is
        # supplied, render its structured items into the SAME slot the
        # freeform meta_recommendations use (so downstream prompt formatting
        # stays bit-identical). Brief items take precedence over the
        # freeform string when both are available -- they're the more
        # researched, structured input.
        rendered_recommendations = _render_island_brief(island_brief)
        if rendered_recommendations is None and meta_recommendations not in (
            None,
            "none",
        ):
            rendered_recommendations = meta_recommendations
        if rendered_recommendations is not None and patch_type != "cross":
            sys_msg += "\n\n# Potential Recommendations"
            sys_msg += (
                "\nThe following are potential recommendations for the "
                "next program generation:\n"
            )
            sys_msg += f"\n{rendered_recommendations}"
            logger.info(
                f"Added meta recommendation to system prompt: "
                f"{rendered_recommendations[:80]}..."
            )
        else:
            logger.debug(
                f"No meta recommendation added: "
                f"meta_recommendations={bool(meta_recommendations)}, "
                f"patch_type={patch_type}"
            )

        # Add format instructions AFTER meta-recommendations
        if patch_type == "diff":
            sys_msg += DIFF_SYS_FORMAT
        elif patch_type == "full":
            # Randomly sample from different full rewrite variants
            full_variant_idx = np.random.randint(0, len(FULL_SYS_FORMATS))
            selected_format = FULL_SYS_FORMATS[full_variant_idx]
            sys_msg += selected_format
        elif patch_type == "cross":
            sys_msg += CROSS_SYS_FORMAT
        elif patch_type == "literature_grounded":
            sys_msg += LIT_GROUNDED_SYS_FORMAT.format(language=self.language)

        # Build sorted inspiration context (combines archive + top-k)
        sorted_inspirations = self.context_builder.build_context(
            archive_inspirations, top_k_inspirations
        )

        if len(sorted_inspirations) > 0:
            eval_history_msg = construct_eval_history_msg(
                sorted_inspirations,
                language=self.language,
                include_text_feedback=self.use_text_feedback,
            )
        else:
            eval_history_msg = ""

        # Format text feedback section for current program
        text_feedback_section = ""
        if self.use_text_feedback:
            text_feedback_section = "\n" + format_text_feedback_section(
                parent.text_feedback
            )

        if patch_type == "diff":
            iter_msg = DIFF_ITER_MSG.format(
                language=self.language,
                code_content=parent.code,
                performance_metrics=perf_str(
                    parent.combined_score, parent.public_metrics
                ),
                text_feedback_section=text_feedback_section,
            )
        elif patch_type == "full":
            iter_msg = FULL_ITER_MSG.format(
                language=self.language,
                code_content=parent.code,
                performance_metrics=perf_str(
                    parent.combined_score, parent.public_metrics
                ),
                text_feedback_section=text_feedback_section,
            )
        elif patch_type == "cross":
            iter_msg = CROSS_ITER_MSG.format(
                language=self.language,
                code_content=parent.code,
                performance_metrics=perf_str(
                    parent.combined_score, parent.public_metrics
                ),
                text_feedback_section=text_feedback_section,
            )
            iter_msg += "\n\n" + get_cross_component(
                archive_inspirations,
                top_k_inspirations,
                language=self.language,
            )
        elif patch_type == "literature_grounded":
            chosen_item = _pick_brief_item(island_brief)
            if chosen_item is None:
                # This should never fire because the suppression rule
                # above filters literature_grounded out when no grounded
                # item exists, but defend the dispatch path anyway.
                raise ValueError(
                    "literature_grounded sampled with no grounded brief item"
                )
            iter_msg = LIT_GROUNDED_ITER_MSG.format(
                language=self.language,
                code_content=parent.code,
                performance_metrics=perf_str(
                    parent.combined_score, parent.public_metrics
                ),
                text_feedback_section=text_feedback_section,
                idea=getattr(chosen_item, "idea", ""),
                rationale=getattr(chosen_item, "rationale", ""),
                reference_snippet=getattr(chosen_item, "reference_snippet", ""),
                source=getattr(chosen_item, "source", "")
                or "(no source recorded)",
                gotchas=getattr(chosen_item, "gotchas", "") or "(none recorded)",
            )
        elif patch_type == "paper":
            raise NotImplementedError("Paper edit not implemented.")
        else:
            raise ValueError(f"Invalid patch type: {patch_type}")

        return (
            sys_msg,
            eval_history_msg + "\n" + iter_msg,
            patch_type,
        )

    def sample_fix(
        self,
        incorrect_program: Program,
        ancestor_inspirations: Optional[List[Program]] = None,
    ) -> Tuple[str, str, str]:
        """
        Generate prompts for fixing an incorrect program.

        This is used when no correct parent exists in the database,
        and we need to fix an incorrect program using its error output.

        Args:
            incorrect_program: The incorrect program to fix
            ancestor_inspirations: Programs from the ancestry of the program
                (sorted chronologically, oldest first)
            meta_recommendations: Optional recommendations from meta summarizer

        Returns:
            Tuple of (system_message, user_message, patch_type="fix")
        """
        if self.task_sys_msg is None:
            sys_msg = BASE_SYSTEM_MSG
        else:
            sys_msg = self.task_sys_msg

        sys_msg += FIX_SYS_FORMAT.format(language=self.language)

        # Build eval history from ancestor inspirations (already chronological)
        if ancestor_inspirations and len(ancestor_inspirations) > 0:
            eval_history_msg = construct_eval_history_msg(
                ancestor_inspirations,
                language=self.language,
                include_text_feedback=self.use_text_feedback,
                correct=False,
            )
        else:
            eval_history_msg = ""

        # Format text feedback section
        text_feedback_section = ""
        if self.use_text_feedback and incorrect_program.text_feedback:
            text_feedback_section = "\n" + format_text_feedback_section(
                incorrect_program.text_feedback
            )

        # Extract stdout/stderr from metadata if available
        metadata = incorrect_program.metadata or {}
        stdout_log = metadata.get("stdout_log", "")
        stderr_log = metadata.get("stderr_log", "")

        error_output_section = format_error_output_section(
            stdout_log=stdout_log,
            stderr_log=stderr_log,
        )

        iter_msg = FIX_ITER_MSG.format(
            language=self.language,
            code_content=incorrect_program.code,
            text_feedback_section=text_feedback_section,
            error_output_section=error_output_section,
        )

        patch_type = "fix"
        logger.info(
            f"Generated FIX prompt for incorrect program "
            f"(Gen: {incorrect_program.generation}, "
            f"Score: {incorrect_program.combined_score or 0.0:.4f}, "
            f"Ancestors: {len(ancestor_inspirations or [])})"
        )

        return (
            sys_msg,
            eval_history_msg + "\n" + iter_msg if eval_history_msg else iter_msg,
            patch_type,
        )

    def sample_error_fix(
        self,
        parent: Program,
        failed: Program,
        mutation_intent: Optional[str],
        prior_attempts: List[AttemptRecord],
        round_number: int,
        rounds_remaining: int,
    ) -> Tuple[str, str, str]:
        """Generate prompts for the per-candidate error-fix retry loop.

        Distinct from :meth:`sample_fix`, which is the single-shot bootstrap
        fix used when an island has *no* correct parents at all. This method
        runs on a per-candidate basis (the same parent + failed mutation is
        re-prompted up to ``error_fix_rounds_by_type`` times). The contract:

        - ``mutation_intent`` is the validated MutationIntent render or the
          ``NO_INTENT_RECORDED`` sentinel -- never freeform prompt text.
        - ``prior_attempts`` summarizes earlier rounds as structured rows
          (one short line per attempt). The model never sees the full
          previous prompt/response pairs.
        - The system prompt explicitly tells the model how many fix
          attempts remain so it can abort if the cause isn't clear.

        Returns ``(sys_msg, user_msg, patch_type="error_fix")``.
        """
        if self.task_sys_msg is None:
            sys_msg = BASE_SYSTEM_MSG
        else:
            sys_msg = self.task_sys_msg

        sys_msg += ERROR_FIX_SYS_FORMAT.format(
            language=self.language,
            n_remaining=max(0, int(rounds_remaining)),
        )

        metadata = failed.metadata or {}
        error_message = (
            metadata.get("error_message")
            or metadata.get("stderr_log", "").strip()
            or "No error message recorded."
        )
        traceback_section = format_traceback_section(failed.error_traceback)
        prior_attempts_section = format_prior_attempt_log(prior_attempts)

        text_feedback_section = ""
        if self.use_text_feedback and failed.text_feedback:
            text_feedback_section = "\n" + format_text_feedback_section(
                failed.text_feedback
            )

        iter_msg = ERROR_FIX_ITER_MSG.format(
            round_number=int(round_number),
            mutation_intent=mutation_intent or "no intent recorded",
            language=self.language,
            parent_code=parent.code,
            failed_code=failed.code,
            error_message=error_message,
            traceback_section=traceback_section,
            prior_attempts_section=prior_attempts_section,
            text_feedback_section=text_feedback_section,
        )

        patch_type = "error_fix"
        logger.info(
            "Generated ERROR_FIX prompt round=%d remaining=%d parent=%s failed=%s",
            round_number,
            rounds_remaining,
            getattr(parent, "id", "?"),
            getattr(failed, "id", "?"),
        )
        return sys_msg, iter_msg, patch_type
