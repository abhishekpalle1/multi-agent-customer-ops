"""Domain-specific errors.

Structural problems in a single value or collection (a float amount, a malformed ID, a
duplicate item ID) surface as Pydantic ``ValidationError``. The errors below describe
well-formed input that is impossible for the domain. ``DomainError`` deliberately does not
subclass ``ValueError``, so Pydantic never wraps these errors into a ``ValidationError``.
"""


class DomainError(Exception):
    """Base class for deterministic domain errors."""


class DomainInvariantError(DomainError):
    """Trusted facts contradict each other, or a computed result broke an invariant."""


class InvalidStateTransitionError(DomainError):
    """A lifecycle transition is not in the explicit transition map."""

    def __init__(self, entity: str, from_status: str, to_status: str) -> None:
        self.entity = entity
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(f"Invalid {entity} transition: {from_status} -> {to_status}.")


class RefundCalculationError(DomainError):
    """A well-formed refund request cannot be calculated for the given order facts."""


class PolicyEvaluationError(DomainError):
    """A well-formed policy request cannot be evaluated against the given order facts."""
