"""Builds the 10 x 5 x 5 = 250 fault-injection scenario matrix.

Axes:
  - fault_type: 10 distinct, realistic failure modes (see scenarios.py)
  - target_idx (0-4): what the fault is applied to. For most fault types this
    is "which of the 5 diagnostic tools"; for a few (missing_deployment,
    stale_runbook, conflicting_evidence, openai_timeout, duplicate_alerts,
    vector_query_failure, unavailable_dependency) it is instead "which of 5
    realistic variants of that specific failure" -- documented per function
    in scenarios.py and in ARCHITECTURE.md. Kept as an index so the matrix
    stays perfectly regular.
  - variation_idx (0-4): a different synthetic input (service/message/etc.)
    per case, so a fault's handling is proven across varied inputs rather
    than one cherry-picked example.
"""

from itertools import product

from tests.faults import scenarios

FAULT_TYPES = {
    "db_timeout": scenarios.db_timeout,
    "malformed_telemetry": scenarios.malformed_telemetry,
    "missing_deployment": scenarios.missing_deployment,
    "stale_runbook": scenarios.stale_runbook,
    "conflicting_evidence": scenarios.conflicting_evidence,
    "openai_timeout": scenarios.openai_timeout,
    "invalid_tool_output": scenarios.invalid_tool_output,
    "duplicate_alerts": scenarios.duplicate_alerts,
    "vector_query_failure": scenarios.vector_query_failure,
    "unavailable_dependency": scenarios.unavailable_dependency,
}

assert len(FAULT_TYPES) == 10

ALL_CASES = [
    (fault_type, target_idx, variation_idx)
    for fault_type, target_idx, variation_idx in product(FAULT_TYPES.keys(), range(5), range(5))
]

assert len(ALL_CASES) == 250
