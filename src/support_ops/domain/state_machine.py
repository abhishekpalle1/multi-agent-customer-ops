"""Deterministic action lifecycle.

::

    proposed          -> authorized | awaiting_approval | denied
    awaiting_approval -> authorized (approved) | rejected
    authorized        -> executing
    executing         -> succeeded | failed | unknown
    unknown           -> succeeded | failed   (reconciliation only; never re-executed)
    terminal          :  succeeded, failed, denied, rejected

``authorized`` is the single gate into execution, whether reached by an ALLOW decision or by
human approval of the same stored action. This module validates transitions only; it does
not persist them.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from support_ops.domain.enums import ActionStatus
from support_ops.domain.errors import InvalidStateTransitionError

_S = ActionStatus

ACTION_TRANSITIONS: Final[Mapping[ActionStatus, frozenset[ActionStatus]]] = MappingProxyType(
    {
        _S.PROPOSED: frozenset({_S.AUTHORIZED, _S.AWAITING_APPROVAL, _S.DENIED}),
        _S.AWAITING_APPROVAL: frozenset({_S.AUTHORIZED, _S.REJECTED}),
        _S.AUTHORIZED: frozenset({_S.EXECUTING}),
        _S.EXECUTING: frozenset({_S.SUCCEEDED, _S.FAILED, _S.UNKNOWN}),
        _S.UNKNOWN: frozenset({_S.SUCCEEDED, _S.FAILED}),
        _S.SUCCEEDED: frozenset(),
        _S.FAILED: frozenset(),
        _S.DENIED: frozenset(),
        _S.REJECTED: frozenset(),
    }
)

# Fail at import time if a status is added without an explicit transition entry.
if set(ACTION_TRANSITIONS) != set(ActionStatus):  # pragma: no cover
    raise RuntimeError("ACTION_TRANSITIONS must define every ActionStatus explicitly.")

TERMINAL_ACTION_STATUSES: Final = frozenset(
    status for status, targets in ACTION_TRANSITIONS.items() if not targets
)
INITIAL_ACTION_STATUS: Final = ActionStatus.PROPOSED


def can_transition_action(from_status: ActionStatus, to_status: ActionStatus) -> bool:
    return to_status in ACTION_TRANSITIONS[from_status]


def ensure_action_transition(from_status: ActionStatus, to_status: ActionStatus) -> None:
    """Raise ``InvalidStateTransitionError`` unless the transition is explicitly allowed."""
    if not can_transition_action(from_status, to_status):
        raise InvalidStateTransitionError("action", from_status.value, to_status.value)
