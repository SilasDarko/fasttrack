"""250 parametrized fault-injection cases (10 fault types x 5 targets x 5
input variations -- see tests/faults/matrix.py). Kept separate from the
functional/integration test suite: this file alone accounts for the "250
parametrized fault-injection cases" measurement, never merged into a single
test-count headline with the rest of the suite.
"""

import pytest

from tests.faults.matrix import ALL_CASES, FAULT_TYPES


@pytest.mark.parametrize(
    "fault_type,target_idx,variation_idx",
    ALL_CASES,
    ids=[f"{f}-t{t}-v{v}" for f, t, v in ALL_CASES],
)
async def test_fault_scenario(session, monkeypatch, fault_type, target_idx, variation_idx):
    scenario = FAULT_TYPES[fault_type]
    await scenario(session, monkeypatch, target_idx, variation_idx)
