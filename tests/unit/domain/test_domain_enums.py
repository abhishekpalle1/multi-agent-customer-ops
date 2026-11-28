from enum import StrEnum

import pytest
from pydantic import ValidationError

from support_ops.domain import enums
from support_ops.domain.enums import ActionStatus, Currency, Role
from support_ops.domain.models import AuthContext

DOMAIN_ENUMS = [
    obj
    for obj in vars(enums).values()
    if isinstance(obj, type) and issubclass(obj, StrEnum) and obj is not StrEnum
]


@pytest.mark.parametrize("enum_cls", DOMAIN_ENUMS, ids=lambda cls: cls.__name__)
def test_enum_names_are_upper_and_values_are_stable_lowercase(enum_cls: type[StrEnum]) -> None:
    values = [member.value for member in enum_cls]

    assert len(values) == len(set(values))
    for member in enum_cls:
        assert member.name == member.name.upper()
        if enum_cls is not Currency:
            assert member.value == member.value.lower()
            assert member.value == member.name.lower()


def test_currency_is_usd_only() -> None:
    assert [c.value for c in Currency] == ["USD"]


def test_action_lifecycle_statuses_are_exactly_the_approved_set() -> None:
    assert {s.value for s in ActionStatus} == {
        "proposed",
        "authorized",
        "awaiting_approval",
        "executing",
        "succeeded",
        "failed",
        "unknown",
        "denied",
        "rejected",
    }


def test_enum_values_serialize_and_validate_from_json() -> None:
    context = AuthContext.model_validate_json('{"role": "customer", "customer_id": "CUST-10"}')

    assert context.role is Role.CUSTOMER
    assert context.model_dump(mode="json") == {"role": "customer", "customer_id": "CUST-10"}


@pytest.mark.parametrize("raw", ["admin", "CUSTOMER", "Customer", "", "supervisor "])
def test_invalid_enum_values_are_rejected(raw: str) -> None:
    with pytest.raises(ValidationError):
        AuthContext.model_validate_json(f'{{"role": "{raw}", "customer_id": "CUST-10"}}')


def test_enum_python_input_requires_members_not_raw_strings() -> None:
    with pytest.raises(ValidationError):
        AuthContext(role="customer", customer_id="CUST-10")  # type: ignore[arg-type]
