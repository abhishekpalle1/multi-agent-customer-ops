from collections import deque
from itertools import product

import pytest

from support_ops.domain.enums import ActionStatus as S
from support_ops.domain.errors import InvalidStateTransitionError
from support_ops.domain.state_machine import (
    ACTION_TRANSITIONS,
    INITIAL_ACTION_STATUS,
    TERMINAL_ACTION_STATUSES,
    can_transition_action,
    ensure_action_transition,
)

# Independent copy of the approved lifecycle; the implementation must match it exactly.
ALLOWED = {
    (S.PROPOSED, S.AUTHORIZED),
    (S.PROPOSED, S.AWAITING_APPROVAL),
    (S.PROPOSED, S.DENIED),
    (S.AWAITING_APPROVAL, S.AUTHORIZED),
    (S.AWAITING_APPROVAL, S.REJECTED),
    (S.AUTHORIZED, S.EXECUTING),
    (S.EXECUTING, S.SUCCEEDED),
    (S.EXECUTING, S.FAILED),
    (S.EXECUTING, S.UNKNOWN),
    (S.UNKNOWN, S.SUCCEEDED),
    (S.UNKNOWN, S.FAILED),
}
ALL_PAIRS = list(product(S, S))


@pytest.mark.parametrize(("source", "target"), ALL_PAIRS, ids=lambda s: s.value)
def test_every_pair_matches_the_approved_lifecycle(source: S, target: S) -> None:
    allowed = (source, target) in ALLOWED

    assert can_transition_action(source, target) is allowed
    if allowed:
        ensure_action_transition(source, target)
    else:
        with pytest.raises(InvalidStateTransitionError) as exc_info:
            ensure_action_transition(source, target)
        assert (exc_info.value.entity, exc_info.value.from_status, exc_info.value.to_status) == (
            "action",
            source.value,
            target.value,
        )


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (S.PROPOSED, S.SUCCEEDED),
        (S.PROPOSED, S.EXECUTING),
        (S.AWAITING_APPROVAL, S.EXECUTING),
        (S.AUTHORIZED, S.SUCCEEDED),
        (S.UNKNOWN, S.EXECUTING),
        (S.FAILED, S.EXECUTING),
        (S.SUCCEEDED, S.FAILED),
        (S.DENIED, S.AUTHORIZED),
        (S.REJECTED, S.AUTHORIZED),
        (S.EXECUTING, S.EXECUTING),
    ],
    ids=lambda s: s.value,
)
def test_representative_forbidden_transitions(source: S, target: S) -> None:
    with pytest.raises(InvalidStateTransitionError, match=f"{source.value} -> {target.value}"):
        ensure_action_transition(source, target)


def test_transition_map_is_complete() -> None:
    """Adding an ActionStatus without an explicit transition entry must fail."""
    assert set(ACTION_TRANSITIONS) == set(S)
    assert {(src, dst) for src, dsts in ACTION_TRANSITIONS.items() for dst in dsts} == ALLOWED


def test_terminal_states_have_no_exits() -> None:
    assert {S.SUCCEEDED, S.FAILED, S.DENIED, S.REJECTED} == TERMINAL_ACTION_STATUSES
    assert S.UNKNOWN not in TERMINAL_ACTION_STATUSES


def test_execution_is_only_entered_from_authorized() -> None:
    assert {src for src, dst in ALLOWED if dst is S.EXECUTING} == {S.AUTHORIZED}


def _reachable(start: S) -> set[S]:
    seen, queue = {start}, deque([start])
    while queue:
        for nxt in ACTION_TRANSITIONS[queue.popleft()]:
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def test_every_status_is_reachable_and_can_terminate() -> None:
    assert _reachable(INITIAL_ACTION_STATUS) == set(S)
    for status in S:
        assert _reachable(status) & TERMINAL_ACTION_STATUSES
