"""Synthetic policy prose stays aligned with the Phase 1 executable rule catalog."""

import json
import re
from pathlib import Path

import pytest

from support_ops.domain.policy_engine import (
    DAMAGE_REPORT_WINDOW_DAYS,
    POLICY_RULES,
    RETURN_WINDOW_DAYS,
    RULESET_VERSION,
)
from support_ops.synthetic.dataset_config import MANIFEST_PATH, POLICY_DIR

DOCUMENT_FOR_PREFIX = {
    "ORD": "order_eligibility_v1.md",
    "RFD": "refunds_v1.md",
    "RET": "returns_v1.md",
    "DMG": "damaged_items_v1.md",
    "CAN": "cancellations_v1.md",
    "RPL": "replacements_v1.md",
}
BANNER = "Synthetic policy for a fictional demo store; not the policy of any real company."
SECTION = re.compile(r"^## ([A-Z]{2,5}-[0-9]+(?:\.[0-9]+)*) (.+)$", re.MULTILINE)
RULE_REFERENCE = re.compile(r"\b[A-Z]{2,5}-[0-9]+\.[0-9]+\b")


def _front_matter(text: str) -> dict[str, str]:
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert match, "missing front matter"
    pairs = (line.split(": ", 1) for line in match.group(1).splitlines())
    return {key: value for key, value in pairs}


def _documents() -> dict[str, str]:
    return {path.name: path.read_text(encoding="utf-8") for path in sorted(POLICY_DIR.glob("*.md"))}


def _sections(text: str) -> dict[str, str]:
    """Rule ID -> section body (up to the next section)."""
    matches = list(SECTION.finditer(text))
    return {
        m.group(1): text[m.end() : matches[i + 1].start() if i + 1 < len(matches) else len(text)]
        for i, m in enumerate(matches)
    }


def test_exactly_the_six_policy_documents_exist() -> None:
    assert set(_documents()) == set(DOCUMENT_FOR_PREFIX.values())


@pytest.mark.parametrize("name", sorted(DOCUMENT_FOR_PREFIX.values()))
def test_document_is_labelled_synthetic_with_versioned_front_matter(name: str) -> None:
    text = (POLICY_DIR / name).read_text(encoding="utf-8")
    meta = _front_matter(text)

    assert meta["policy_key"] == name.removesuffix("_v1.md")
    assert re.fullmatch(r"[a-z0-9]+(_[a-z0-9]+)*", meta["policy_key"])
    assert meta["version"] == "1"
    assert meta["ruleset_version"] == RULESET_VERSION
    assert meta["synthetic"] == "true"
    assert meta["effective_from"] == "2026-01-01T00:00:00Z"
    assert BANNER in text
    assert "$" not in text


def test_every_executable_rule_has_exactly_one_section_in_its_family_document() -> None:
    seen: dict[str, str] = {}
    for name, text in _documents().items():
        for rule_id in _sections(text):
            assert rule_id not in seen, f"{rule_id} duplicated in {name} and {seen[rule_id]}"
            seen[rule_id] = name

    assert set(seen) == set(POLICY_RULES)
    for rule_id, name in seen.items():
        assert DOCUMENT_FOR_PREFIX[rule_id.split("-")[0]] == name


def test_section_titles_match_the_rule_catalog() -> None:
    for text in _documents().values():
        for match in SECTION.finditer(text):
            assert match.group(2) == POLICY_RULES[match.group(1)].title


def test_prose_references_only_known_rule_ids() -> None:
    for name, text in _documents().items():
        unknown = set(RULE_REFERENCE.findall(text)) - set(POLICY_RULES)
        assert not unknown, f"{name} references unknown rule IDs {unknown}"


def test_windows_state_the_engine_constants_and_day_semantics() -> None:
    returns = _sections((POLICY_DIR / "returns_v1.md").read_text(encoding="utf-8"))
    damage = _sections((POLICY_DIR / "damaged_items_v1.md").read_text(encoding="utf-8"))

    assert f"within {RETURN_WINDOW_DAYS} days of delivery" in returns["RET-3.1"]
    assert "day 30 is" in returns["RET-3.1"]
    assert "day 31" in returns["RET-3.1"]
    assert "UTC calendar days" in returns["RET-3.1"]
    assert f"within {DAMAGE_REPORT_WINDOW_DAYS} days of delivery" in damage["DMG-4.1"]
    assert "day 31" in damage["DMG-4.1"]
    assert "non-returnable" in damage["DMG-4.2"]


def test_policies_do_not_introduce_approval_thresholds() -> None:
    for name, text in _documents().items():
        lowered = text.lower()
        for word in ("approval", "threshold", "supervisor"):
            assert word not in lowered, f"{name} mentions {word}"


def test_manifest_uses_only_known_rule_ids() -> None:
    manifest = json.loads(Path(MANIFEST_PATH).read_text(encoding="utf-8"))
    used = {
        rule
        for scenario in manifest["scenarios"]
        for rule in scenario["expected_policy"]["triggered_rule_ids"]
    }

    assert used <= set(POLICY_RULES)
