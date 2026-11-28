"""Golden-scenario manifest: machine-readable ground truth for later phases.

Expected policy and refund results are never hand-written: ``build_manifest`` evaluates the
real Phase 1 ``evaluate_policy`` and ``calculate_refund`` on the dataset's facts at the
scenario anchor, then checks the results against each scenario's declared intent (decision,
delivery day, monetary boundary) and fails if they disagree. The manifest records facts and
deterministic outcomes only: no model IDs, prompts, LLM wording, embeddings, or Phase 3
authorization decisions.

Serialization is deterministic: keys sorted, scenarios sorted by ID, two-space indentation,
money as strings, and a trailing newline.
"""

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, ConfigDict

from support_ops.domain.enums import ActionType, OrderStatus, PolicyDecision, RequestReason
from support_ops.domain.models import (
    PolicyEvaluation,
    PolicyRequest,
    RefundCalculationInput,
    RefundCalculationResult,
    RefundSelection,
)
from support_ops.domain.money import ROUNDING_POLICY
from support_ops.domain.policy_engine import POLICY_RULES, RULESET_VERSION, evaluate_policy
from support_ops.domain.refund_calculator import calculate_refund
from support_ops.synthetic.dataset_config import (
    MANIFEST_PATH,
    MANIFEST_SCHEMA_VERSION,
    REQUIRED_SCHEMA_REVISION,
)
from support_ops.synthetic.facts import FactIndex
from support_ops.synthetic.golden import (
    GOLDEN_ORDER_BY_ID,
    GOLDEN_SCENARIOS,
    FixtureKind,
    GoldenScenario,
)
from support_ops.synthetic.records import (
    SyntheticDataset,
    SyntheticDatasetError,
    dataset_fingerprint,
)


class _ManifestModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Phase3Fixture(_ManifestModel):
    """Identifies a scenario reserved for a later phase. Facts only; never an outcome."""

    kind: FixtureKind
    owner_customer_id: str | None = None
    requesting_customer_id: str | None = None
    reserved_case_id: str | None = None


class RequestItem(_ManifestModel):
    order_item_id: str
    product_id: str
    quantity: int
    returnable: bool
    available_inventory: int


class ScenarioRequest(_ManifestModel):
    action_type: ActionType
    reason: RequestReason
    items: tuple[RequestItem, ...]


class ManifestScenario(_ManifestModel):
    scenario_id: str
    dataset_version: str
    anchor_time: datetime
    coverage: tuple[str, ...]
    customer_id: str
    case_id: None = None
    order_id: str
    order_status: OrderStatus
    delivered_at: datetime | None
    days_since_delivery: int | None
    customer_request: str
    request: ScenarioRequest
    expected_policy: dict[str, Any]
    expected_refund: dict[str, Any] | None
    monetary_boundary: str | None
    phase3_fixture: Phase3Fixture | None
    notes: str


class ScenarioManifest(_ManifestModel):
    schema_version: int
    dataset_version: str
    anchor_time: datetime
    random_seed: int
    ruleset_version: str
    rounding_policy: str
    required_schema_revision: str
    row_counts: dict[str, int]
    dataset_fingerprint: str
    scenarios: tuple[ManifestScenario, ...]


FORBIDDEN_MANIFEST_KEYS: Final = frozenset(
    {
        "model",
        "model_id",
        "prompt",
        "embedding",
        "agent",
        "agent_chain",
        "authorization",
        "authorization_decision",
        "approval",
        "expected_access",
        "llm_response",
    }
)


def evaluate_scenario(
    index: FactIndex, scenario: GoldenScenario, anchor: datetime
) -> tuple[PolicyEvaluation, RefundCalculationResult | None]:
    """Run the real Phase 1 policy engine (and calculator for eligible refunds)."""
    spec = GOLDEN_ORDER_BY_ID[scenario.order_id]
    selections = tuple((spec.item_id(line), quantity) for line, quantity in scenario.lines)
    facts = index.facts(scenario.order_id)
    evaluation = evaluate_policy(
        PolicyRequest(
            action_type=scenario.action_type,
            reason=scenario.reason,
            item_ids=tuple(item_id for item_id, _ in selections),
            evaluated_at=anchor,
            order=facts,
            in_stock_item_ids=(
                index.in_stock_item_ids(selections)
                if scenario.action_type is ActionType.REPLACEMENT
                else None
            ),
        )
    )
    refund = None
    if scenario.action_type is ActionType.REFUND and evaluation.decision is PolicyDecision.ELIGIBLE:
        refund = calculate_refund(
            RefundCalculationInput(
                order=facts,
                selections=tuple(
                    RefundSelection(order_item_id=item_id, quantity=quantity)
                    for item_id, quantity in selections
                ),
                include_shipping=evaluation.shipping_refundable,
            )
        )
    return evaluation, refund


def build_scenarios(
    index: FactIndex, *, dataset_version: str, anchor: datetime
) -> tuple[ManifestScenario, ...]:
    scenarios = []
    for scenario in GOLDEN_SCENARIOS:
        evaluation, refund = evaluate_scenario(index, scenario, anchor)
        _check_intent(scenario, evaluation, refund, index, anchor)
        spec = GOLDEN_ORDER_BY_ID[scenario.order_id]
        order = index.orders[scenario.order_id]
        shipment = index.shipments_by_order.get(scenario.order_id)
        delivered_at = shipment.delivered_at if shipment is not None else None
        fixture = scenario.phase3_fixture
        scenarios.append(
            ManifestScenario(
                scenario_id=scenario.scenario_id,
                dataset_version=dataset_version,
                anchor_time=anchor,
                coverage=scenario.coverage,
                customer_id=order.customer_id,
                order_id=scenario.order_id,
                order_status=order.status,
                delivered_at=delivered_at,
                days_since_delivery=(
                    (anchor.date() - delivered_at.date()).days if delivered_at else None
                ),
                customer_request=scenario.customer_request,
                request=ScenarioRequest(
                    action_type=scenario.action_type,
                    reason=scenario.reason,
                    items=tuple(
                        _request_item(index, spec.item_id(line), quantity)
                        for line, quantity in scenario.lines
                    ),
                ),
                expected_policy=evaluation.model_dump(mode="json"),
                expected_refund=refund.model_dump(mode="json") if refund else None,
                monetary_boundary=scenario.monetary_boundary,
                phase3_fixture=(
                    Phase3Fixture(
                        kind=fixture.kind,
                        owner_customer_id=fixture.owner_customer_id,
                        requesting_customer_id=fixture.requesting_customer_id,
                        reserved_case_id=fixture.reserved_case_id,
                    )
                    if fixture
                    else None
                ),
                notes=scenario.notes,
            )
        )
    return tuple(sorted(scenarios, key=lambda s: s.scenario_id))


def _request_item(index: FactIndex, order_item_id: str, quantity: int) -> RequestItem:
    item = index.item(order_item_id)
    return RequestItem(
        order_item_id=order_item_id,
        product_id=item.product_id,
        quantity=quantity,
        returnable=index.products[item.product_id].is_returnable,
        available_inventory=index.inventory[item.product_id].available_quantity,
    )


def _check_intent(
    scenario: GoldenScenario,
    evaluation: PolicyEvaluation,
    refund: RefundCalculationResult | None,
    index: FactIndex,
    anchor: datetime,
) -> None:
    sid = scenario.scenario_id
    if evaluation.decision is not scenario.intended_decision:
        raise SyntheticDatasetError(f"{sid}: policy decision contradicts the declared intent.")
    unknown = set(evaluation.triggered_rule_ids) - set(POLICY_RULES)
    if unknown:
        raise SyntheticDatasetError(f"{sid}: unknown rule IDs {sorted(unknown)}.")
    if scenario.intended_days is not None:
        shipment = index.shipments_by_order.get(scenario.order_id)
        if shipment is None or shipment.delivered_at is None:
            raise SyntheticDatasetError(f"{sid}: declared a delivery day but is undelivered.")
        days = (anchor.date() - shipment.delivered_at.date()).days
        engine_days = evaluation.days_since_delivery
        if days != scenario.intended_days or engine_days not in (None, days):
            raise SyntheticDatasetError(f"{sid}: delivery day contradicts the declared intent.")
    if scenario.monetary_boundary is not None and (
        refund is None or refund.total_refund_amount != Decimal(scenario.monetary_boundary)
    ):
        raise SyntheticDatasetError(f"{sid}: refund total misses the declared boundary.")


def build_manifest(dataset: SyntheticDataset) -> ScenarioManifest:
    return ScenarioManifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        dataset_version=dataset.dataset_version,
        anchor_time=dataset.anchor,
        random_seed=dataset.seed,
        ruleset_version=RULESET_VERSION,
        rounding_policy=ROUNDING_POLICY,
        required_schema_revision=REQUIRED_SCHEMA_REVISION,
        row_counts=dataset.row_counts(),
        dataset_fingerprint=dataset_fingerprint(dataset),
        scenarios=build_scenarios(
            FactIndex.from_dataset(dataset),
            dataset_version=dataset.dataset_version,
            anchor=dataset.anchor,
        ),
    )


def render_manifest(manifest: ScenarioManifest) -> str:
    return json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"


def parse_manifest(text: str) -> ScenarioManifest:
    return ScenarioManifest.model_validate_json(text)


def write_manifest(manifest: ScenarioManifest, path: Path = MANIFEST_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_manifest(manifest), encoding="utf-8", newline="\n")


def check_committed_manifest(manifest: ScenarioManifest, path: Path = MANIFEST_PATH) -> None:
    """Fail unless the committed manifest is byte-identical to a fresh generation."""
    if not path.is_file():
        raise SyntheticDatasetError(f"Committed manifest {path.name} is missing.")
    if path.read_text(encoding="utf-8") != render_manifest(manifest):
        raise SyntheticDatasetError(
            "Committed golden manifest is out of date; regenerate it with the "
            "synthetic-manifest task and review the diff."
        )
