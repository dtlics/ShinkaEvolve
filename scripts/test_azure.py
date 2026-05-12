"""Smoke test: hits each Azure deployment with a tiny prompt, prints latency + cost.

Run from repo root:
    /opt/anaconda3/envs/shinka/bin/python scripts/test_azure.py
"""

import time

from shinka.llm.kwargs import sample_model_kwargs
from shinka.llm.query import query

MODELS = [
    "azure-gpt-5.4-mini",
    "azure-gpt-5.3-codex",
    "azure-gpt-5.4-pro",
    "azure-gpt-5.5",
]

SYSTEM = "You are a smoke-test target. Reply with a single token only."
USER = "Respond with exactly: OK"


def main() -> int:
    failures = 0
    for model in MODELS:
        try:
            kwargs = sample_model_kwargs(
                model_names=[model],
                temperatures=[1.0],
                max_tokens=[256],
                reasoning_efforts=["medium"],  # 5.4-pro requires >= medium
            )
            kwargs.pop("model_name")  # query() takes model_name explicitly
            t0 = time.time()
            result = query(
                model_name=model,
                msg=USER,
                system_msg=SYSTEM,
                **kwargs,
            )
            dt = time.time() - t0
            content = (result.content or "").strip().replace("\n", " ")[:80]
            print(
                f"  OK  {model:24s}  {dt:5.1f}s  "
                f"in={result.input_tokens:>5}  out={result.output_tokens:>4}  "
                f"think={result.thinking_tokens:>4}  "
                f"${result.cost:.5f}  | {content!r}"
            )
        except Exception as exc:
            failures += 1
            print(f"FAIL  {model:24s}  {type(exc).__name__}: {exc}")
    return failures


if __name__ == "__main__":
    raise SystemExit(main())
