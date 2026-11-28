"""Golden manifest: committed ground truth equals real Phase 1 engine/calculator results."""

import dataclasses
import json
from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest

from support_ops.domain.enums import ActionType, PolicyDecision, PolicyReasonCode
from support_ops.domain.models import PolicyRequest, RefundCalculationInput, RefundSelection
from support_ops.domain.policy_engine import POLICY_RULES, RULESET_VERSION, evaluate_policy
from support_ops.domain.refund_calculator import calculate_refund
from support_ops.synthetic import manifest as manifest_module
from support_ops.synthetic.dataset import PreparedDataset
from support_ops.synthetic.dataset_config import (
    DATASET_VERSION,
    MANIFEST_PATH,
    REQUIRED_SCHEMA_REVISION,
    SCENARIO_ANCHOR,
)
from support_ops.synthetic.facts import FactIndex
from support_ops.synthetic.golden import GOLDEN_SCENARIOS
from support_ops.synthetic.manifest import (
    FORBIDDEN_MANIFEST_KEYS,
    ManifestScenario,
    build_manifest,
    check_committed_manifest,
    parse_manifest,
    render_manifest,
)
from support_ops.synthetic.records import SyntheticDatasetError


def _scenarios(prepared: PreparedDataset) -> dict[str, ManifestScenario]:
    return {s.scenario_id: s for s in prepared.manifest.scenarios}


def test_committed_manifest_is_byte_identical_to_a_fresh_generation(
    prepared: PreparedDataset,
) -> None:
    check_committed_manifest(prepared.manifest)


def test_serialization_is_deterministic_sorted_and_round_trips(
    prepared: PreparedDataset,
) -> None:
    text = render_manifest(prepared.manifest)
    ids = [s.scenario_id for s in prepared.manifest.scenarios]

    assert text.endswith("}\n")
    assert render_manifest(parse_manifest(text)) == text
    assert json.dumps(json.loads(text), indent=2, sort_keys=True) + "\n" == text
    assert ids == sorted(ids)
    assert len(ids) == len(set(ids)) == 30


def test_top_level_identity(prepared: PreparedDataset) -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    assert manifest["dataset_version"] == DATASET_VERSION
    assert manifest["anchor_time"] == "2026-11-01T12:00:00Z"
    assert manifest["random_seed"] == 20261101
    assert manifest["ruleset_version"] == RULESET_VERSION
    assert manifest["required_schema_revision"] == REQUIRED_SCHEMA_REVISION
    assert all(s["dataset_version"] == DATASET_VERSION for s in manifest["scenarios"])
    assert all(s["anchor_time"] == "2026-11-01T12:00:00Z" for s in manifest["scenarios"])
    assert all(s["case_id"] is None for s in manifest["scenarios"])


def test_expectations_equal_independent_real_phase1_calls(prepared: PreparedDataset) -> None:
    index = FactIndex.from_dataset(prepared.dataset)
    for scenario in prepared.manifest.scenarios:
        selections = tuple((item.order_item_id, item.quantity) for item in scenario.request.items)
        facts = index.facts(scenario.order_id)
        in_stock = None
        if scenario.request.action_type is ActionType.REPLACEMENT:
            in_stock = frozenset(
                item.order_item_id
                for item in scenario.request.items
                if item.available_inventory >= item.quantity
            )
        evaluation = evaluate_policy(
            PolicyRequest(
                action_type=scenario.request.action_type,
                reason=scenario.request.reason,
                item_ids=tuple(item_id for item_id, _ in selections),
                evaluated_at=SCENARIO_ANCHOR,
                order=facts,
                in_stock_item_ids=in_stock,
            )
        )
        assert scenario.expected_policy == evaluation.model_dump(mode="json"), scenario.scenario_id

        refundable = (
            scenario.request.action_type is ActionType.REFUND
            and evaluation.decision is PolicyDecision.ELIGIBLE
        )
        if not refundable:
            assert scenario.expected_refund is None, scenario.scenario_id
            continue
        refund = calculate_refund(
            RefundCalculationInput(
                order=facts,
                selections=tuple(
                    RefundSelection(order_item_id=i, quantity=q) for i, q in selections
                ),
                include_shipping=evaluation.shipping_refundable,
            )
        )
        assert scenario.expected_refund == refund.model_dump(mode="json"), scenario.scenario_id


@pytest.mark.parametrize(
    ("scenario_id", "day", "decision"),
    [
        ("damaged-day-008-refund", 8, "eligible"),
        ("damaged-day-030-refund", 30, "eligible"),
        ("damaged-day-031-refund", 31, "ineligible"),
        ("change-of-mind-day-030-refund", 30, "eligible"),
        ("change-of-mind-day-031-refund", 31, "ineligible"),
        ("change-of-mind-day-090-return", 90, "ineligible"),
    ],
)
def test_window_scenarios_land_on_the_intended_calendar_day(
    prepared: PreparedDataset, scenario_id: str, day: int, decision: str
) -> None:
    scenario = _scenarios(prepared)[scenario_id]
    assert scenario.delivered_at is not None
    elapsed = SCENARIO_ANCHOR - scenario.delivered_at

    assert scenario.days_since_delivery == day
    assert scenario.expected_policy["days_since_delivery"] == day
    assert scenario.expected_policy["decision"] == decision
    if day == 30:
        assert elapsed < timedelta(days=30)
    if day == 31:
        assert elapsed > timedelta(days=31)


@pytest.mark.parametrize("amount", ["49.00", "149.00", "150.00", "151.00", "799.00"])
def test_amount_boundaries_land_exactly_without_an_authorization_outcome(
    prepared: PreparedDataset, amount: str
) -> None:
    label = amount.split(".")[0].zfill(3)
    scenario = _scenarios(prepared)[f"amount-boundary-{label}"]
    refund = scenario.expected_refund
    assert refund is not None

    assert scenario.monetary_boundary == amount
    assert Decimal(refund["total_refund_amount"]) == Decimal(amount)
    assert Decimal(refund["items_refund_total"]) == Decimal(amount)
    assert Decimal(refund["shipping_refund_amount"]) == 0
    assert scenario.phase3_fixture is not None
    assert scenario.phase3_fixture.model_dump() == {
        "kind": "amount_boundary",
        "owner_customer_id": None,
        "requesting_customer_id": None,
        "reserved_case_id": None,
    }


def test_cross_customer_fixture_records_facts_but_no_access_decision(
    prepared: PreparedDataset,
) -> None:
    scenario = _scenarios(prepared)["cross-customer-ownership"]
    fixture = scenario.phase3_fixture
    assert fixture is not None

    assert scenario.customer_id == fixture.owner_customer_id == "CUST-20"
    assert fixture.requesting_customer_id == "CUST-10"
    assert "expected_access" not in scenario.model_dump()


def test_duplicate_and_timeout_bases_reserve_case_ids_without_case_rows(
    prepared: PreparedDataset,
) -> None:
    scenarios = _scenarios(prepared)
    reserved = {
        scenarios[sid].phase3_fixture.reserved_case_id  # type: ignore[union-attr]
        for sid in ("duplicate-refund-request-base", "timeout-after-commit-base")
    }

    assert reserved == {"CASE-DUPLICATE-001", "CASE-TIMEOUT-COMMIT-001"}
    assert "cases" not in prepared.dataset.tables()


def test_every_rule_id_and_reason_code_is_covered_and_known(prepared: PreparedDataset) -> None:
    rule_ids = {
        rule
        for s in prepared.manifest.scenarios
        for rule in s.expected_policy["triggered_rule_ids"]
    }
    codes = {
        code for s in prepared.manifest.scenarios for code in s.expected_policy["reason_codes"]
    }

    assert rule_ids == set(POLICY_RULES)
    assert codes == {code.value for code in PolicyReasonCode}


def _keys(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _keys(child)


def test_manifest_contains_no_llm_or_authorization_fields() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    assert not set(_keys(manifest)) & FORBIDDEN_MANIFEST_KEYS
    text = MANIFEST_PATH.read_text(encoding="utf-8").lower()
    for word in ("requires_approval", "gemini", "gpt-", "embedding"):
        assert word not in text


def test_a_scenario_whose_intent_contradicts_the_engine_fails_generation(
    prepared: PreparedDataset, monkeypatch: pytest.MonkeyPatch
) -> None:
    wrong = dataclasses.replace(GOLDEN_SCENARIOS[2], intended_decision=PolicyDecision.ELIGIBLE)
    monkeypatch.setattr(
        manifest_module, "GOLDEN_SCENARIOS", (*GOLDEN_SCENARIOS[:2], wrong, *GOLDEN_SCENARIOS[3:])
    )

    with pytest.raises(SyntheticDatasetError, match="declared intent"):
        build_manifest(prepared.dataset)


def test_a_boundary_that_misses_its_amount_fails_generation(
    prepared: PreparedDataset, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = next(
        i for i, s in enumerate(GOLDEN_SCENARIOS) if s.scenario_id == "amount-boundary-150"
    )
    wrong = dataclasses.replace(GOLDEN_SCENARIOS[index], monetary_boundary="150.01")
    patched = (*GOLDEN_SCENARIOS[:index], wrong, *GOLDEN_SCENARIOS[index + 1 :])
    monkeypatch.setattr(manifest_module, "GOLDEN_SCENARIOS", patched)

    with pytest.raises(SyntheticDatasetError, match="boundary"):
        build_manifest(prepared.dataset)


def test_out_of_date_committed_manifest_is_detected(
    prepared: PreparedDataset, tmp_path: Any
) -> None:
    stale = tmp_path / "scenarios.json"
    stale.write_text(render_manifest(prepared.manifest).replace("104.18", "104.19"), "utf-8")

    with pytest.raises(SyntheticDatasetError, match="out of date"):
        check_committed_manifest(prepared.manifest, stale)
