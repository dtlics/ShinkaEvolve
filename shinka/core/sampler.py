from typing import List, Optional, Tuple, Literal
import numpy as np
from shinka.database import Program
from shinka.database.inspirations import InspirationContextBuilder
from shinka.prompts import (
    construct_eval_history_msg,
    perf_str,
    objective_section,
    format_text_feedback_section,
    BASE_SYSTEM_MSG,
    EXPERT_CREATIVE_PREAMBLE,
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
)
from shinka.prompts.prompts_init import INIT_SYSTEM_MSG, INIT_USER_MSG
from shinka.defaults import default_patch_type_probs, default_patch_types
import logging

logger = logging.getLogger(__name__)


class PromptSampler:
    def __init__(
        self,
        task_sys_msg: Optional[str] = None,
        language: str = "python",
        patch_types: Optional[List[str]] = None,
        patch_type_probs: Optional[List[float]] = None,
        use_text_feedback: bool = True,
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
        island_brief: Optional[str] = None,
        failure_note: Optional[str] = None,
        objective_brief: Optional[str] = None,
        forced_patch_type: Optional[str] = None,
    ) -> Tuple[str, str, str]:
        if self.task_sys_msg is None:
            sys_msg = BASE_SYSTEM_MSG
        else:
            sys_msg = self.task_sys_msg

        # When the automatic per-window meta round has recorded a
        # per-island direction (island_brief) for this parent's island,
        # prefer it over the global freeform meta_recommendations. The
        # freeform string still acts as a fallback for islands the meta
        # round did not assign a direction this cycle.
        if island_brief:
            meta_recommendations = island_brief

        # ``cross`` is suppressed when there are no inspirations to cross-pollinate with.
        skip_cross = (
            len(archive_inspirations) == 0 and len(top_k_inspirations) == 0
        )
        if forced_patch_type is not None:
            # D4: run_window samples the patch MODE first (diff/full/cross/fix; fix is routed
            # via sample_fix and never reaches here) and forces the chosen mode so the parent
            # can be conditioned on it. Honor cross-suppression.
            patch_type = forced_patch_type
            if patch_type == "cross" and skip_cross:
                patch_type = "full"
        else:
            # Legacy internal sampling (used only when no mode is forced — e.g. the mock/test
            # path). 'fix' is never sampled here; 'cross' is suppressed with no inspirations.
            pairs = [
                (t, p) for t, p in zip(self.patch_types, self.patch_type_probs)
                if t != "fix" and not (t == "cross" and skip_cross)
            ]
            if not pairs:
                pairs = [("full", 1.0)]
            _types = [t for t, _ in pairs]
            _w = [p for _, p in pairs]
            _s = sum(_w)
            _probs = [p / _s for p in _w] if _s > 0 else [1.0 / len(_types)] * len(_types)
            patch_type = np.random.choice(_types, p=_probs)

        # Point 4.1/4.4: exactly ONE of these goes in the SYSTEM turn of a non-cross gen — a
        # DIRECTIVE direction header when a direction was sampled (treat it as the goal, not an
        # optional suggestion), else the expert/creative preamble so a no-direction gen still
        # invents from expert knowledge even though task_sys_msg replaced BASE_SYSTEM_MSG.
        # ``cross`` manages its own focus material and gets neither.
        skip_meta_rec_for = {"cross"}
        _has_direction = meta_recommendations not in [None, "none"]
        if _has_direction and patch_type not in skip_meta_rec_for:
            _verb = "edit" if patch_type == "diff" else "rewrite"
            sys_msg += "\n\n# Direction for this attempt"
            sys_msg += (
                f"\nBase your {_verb} on the direction below. It is the intended approach "
                "for this generation — treat it as the goal of your change, not an optional "
                "suggestion:\n"
            )
            sys_msg += f"\n{meta_recommendations}"
            logger.info(
                f"Added direction to system prompt: {meta_recommendations[:80]}..."
            )
        elif (not _has_direction) and patch_type in {"diff", "full"}:
            sys_msg += EXPERT_CREATIVE_PREAMBLE

        # The persistent failure caution rides into EVERY generation — rendered
        # independently of patch_type and of any per-gen direction/island_brief, so
        # it is never dropped on a cross gen, when an island_brief replaced the
        # direction, or when no direction was sampled (M1/M2/M3/M4).
        if failure_note not in (None, "", "none") and str(failure_note).strip():
            sys_msg += "\n\n# Known failure modes to avoid"
            sys_msg += (
                "\nDo NOT reintroduce these recurring failure modes seen in past "
                "attempts:\n"
            )
            sys_msg += f"\n{failure_note}"

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

        # Point 4.3: prepend the orchestrator-authored objective gloss to the raw numbers.
        # objective_brief None => "" => byte-identical to the legacy metric slot.
        metric_block = objective_section(objective_brief) + perf_str(
            parent.combined_score, parent.public_metrics
        )

        if patch_type == "diff":
            iter_msg = DIFF_ITER_MSG.format(
                language=self.language,
                code_content=parent.code,
                performance_metrics=metric_block,
                text_feedback_section=text_feedback_section,
            )
        elif patch_type == "full":
            iter_msg = FULL_ITER_MSG.format(
                language=self.language,
                code_content=parent.code,
                performance_metrics=metric_block,
                text_feedback_section=text_feedback_section,
            )
        elif patch_type == "cross":
            iter_msg = CROSS_ITER_MSG.format(
                language=self.language,
                code_content=parent.code,
                performance_metrics=metric_block,
                text_feedback_section=text_feedback_section,
            )
            iter_msg += "\n\n" + get_cross_component(
                archive_inspirations,
                top_k_inspirations,
                language=self.language,
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
        failure_note: Optional[str] = None,
        objective_brief: Optional[str] = None,
    ) -> Tuple[str, str, str]:
        """
        Generate prompts for fixing an incorrect program.

        This is used when no correct parent exists in the database,
        and we need to fix an incorrect program using its error output.

        Args:
            incorrect_program: The incorrect program to fix
            ancestor_inspirations: Programs from the ancestry of the program
                (sorted chronologically, oldest first)
            failure_note: Optional persistent caution (recurring failure modes to
                avoid), rendered into the repair prompt so a fix does not
                reintroduce a known failure class.

        Returns:
            Tuple of (system_message, user_message, patch_type="fix")
        """
        if self.task_sys_msg is None:
            sys_msg = BASE_SYSTEM_MSG
        else:
            sys_msg = self.task_sys_msg

        sys_msg += FIX_SYS_FORMAT.format(language=self.language)

        # Point 4.3: keep a repair on-task by rendering the objective gloss in the fix SYSTEM
        # message (FIX_ITER_MSG has no metric slot). None => no change.
        if objective_brief and str(objective_brief).strip():
            sys_msg += "\n\n" + objective_section(objective_brief).rstrip()

        # M4: the persistent failure caution rides into fix-mode prompts too, so a
        # repair does not reintroduce a known failure class.
        if failure_note not in (None, "", "none") and str(failure_note).strip():
            sys_msg += "\n\n# Known failure modes to avoid\n"
            sys_msg += f"{failure_note}"

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
