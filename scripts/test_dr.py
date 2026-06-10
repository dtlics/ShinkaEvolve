"""Standalone smoke test for the deep-research (o3-deep-research) Azure resource.

scripts/test_azure.py covers ONLY the MAIN gpt-* resource; this hits the SEPARATE
deep-research resource (AZURE_DR_ENDPOINT / AZURE_DR_API_KEY) in isolation and prints the
verbatim failure reason — so you can fix an Azure-side precondition (web_search_preview
provisioning, quota / rate limit, model version 2025-06-26) WITHOUT burning a whole
orchestrator window. A persistent terminal status='failed' across DR runs is almost always
one of those preconditions, not a framework bug.

Run from repo root:
    conda run -n shinka python scripts/test_dr.py
"""

import asyncio
import time


def main() -> int:
    from shinka.llm.agent.dr_client import get_dr_async_client, run_dr_call

    print("Probing the deep-research resource (o3-deep-research) ...")
    try:
        client, base_url = get_dr_async_client()
    except Exception as exc:
        print(f"FAIL  could not build DR client: {type(exc).__name__}: {exc}")
        return 1
    print(f"  base_url = {base_url}")

    t0 = time.time()
    try:
        text, cost = asyncio.run(
            run_dr_call(
                client,
                model="o3-deep-research",
                system_msg="You are a deep-research assistant. Be brief.",
                user_msg="In one sentence, what is a CNOT gate? Cite one source.",
                reasoning_effort="medium",
                max_tool_calls=3,
                max_output_tokens=4000,
                call_metadata={"purpose": "dr_smoke", "source": "scripts/test_dr.py"},
            )
        )
        dt = time.time() - t0
        snippet = (text or "").strip().replace("\n", " ")[:160]
        print(f"  OK   o3-deep-research  {dt:5.1f}s  ${cost:.4f}  | {snippet!r}")
        return 0
    except Exception as exc:
        dt = time.time() - t0
        print(f"FAIL  o3-deep-research  {dt:5.1f}s  {type(exc).__name__}: {exc}")
        # run_dr_call now surfaces the structured failure reason off response.error.
        for attr in ("error_code", "error_message", "submitted", "cost"):
            val = getattr(exc, attr, None)
            if val is not None:
                print(f"       {attr} = {val!r}")
        print(
            "\nIf status='failed' with no useful error: the DR resource most likely lacks the "
            "web_search_preview\ntool (provision/enable it), is over quota, or the deployment "
            "name / model-version (2025-06-26)\nis wrong. Fix on the Azure side, then re-run."
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
