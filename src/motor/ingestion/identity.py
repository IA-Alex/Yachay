"""Job identifiers and the versioned job_key (spec section 7, ADR-011).

job_id / job_key / run_id / attempt are separate: job_id is one processing
request, job_key is result equivalence, run_id is one execution attempt, and
attempt is a per-job counter that increments on retry.
"""

from __future__ import annotations

import hashlib
import uuid

_SEP = "\x1f"


def new_job_id() -> str:
    """Return a fresh stable job_id (one processing request)."""
    return f"job-{uuid.uuid4().hex}"


def new_run_id() -> str:
    """Return a fresh run_id (one execution attempt)."""
    return f"run-{uuid.uuid4().hex}"


def compute_job_key(
    *,
    file_hash: str,
    pipeline_version: str,
    rules_version: str,
    config_hash: str,
    llm_enabled: bool,
    model_version: str = "",
    prompt_version: str = "",
) -> str:
    """Compute the job_key over the canonical field order of section 7.

    `model_version` and `prompt_version` participate only when the LLM is
    enabled, so toggling the LLM yields a distinct, traceable result.
    """
    parts = [
        file_hash,
        pipeline_version,
        rules_version,
        config_hash,
        "1" if llm_enabled else "0",
    ]
    if llm_enabled:
        parts.append(model_version)
        parts.append(prompt_version)
    return hashlib.sha256(_SEP.join(parts).encode("utf-8")).hexdigest()
