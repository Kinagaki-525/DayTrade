"""Selection threshold activation.

Turning Selection on is a **human** decision. This module never chooses a
threshold pair: it applies one specific, human-supplied ``pair_id`` that a
completed Calibration report already evaluated, and refuses everything else.

Preconditions (all required, all fail-closed):

* the calibration report is ``calibration_status == "COMPLETE"``,
* ``pair_id`` exists in ``evaluated_threshold_pairs``,
* ``selection.enabled`` is currently ``false``,
* both selection thresholds are currently ``null``.

Only three configuration paths may change:

* ``selection.enabled``
* ``selection.rules.minimum_turnover_yen.threshold_yen``
* ``selection.rules.maximum_relative_tick_size.threshold_ratio``

The write is load -> validate -> apply -> validate -> temp file -> atomic
replace. On any failure the previous configuration file is left untouched.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from src.config import DEFAULT_CONFIG_PATH, load_strategy_config, load_yaml
from src.file_io import atomic_write_text


HUMAN_ACTION_STATUS = "HUMAN_ACTION_REQUIRED_SELECTION_THRESHOLD_PAIR"

MUTABLE_CONFIG_PATHS = (
    "selection.enabled",
    "selection.rules.minimum_turnover_yen.threshold_yen",
    "selection.rules.maximum_relative_tick_size.threshold_ratio",
)


class SelectionActivationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def find_threshold_pair(calibration: dict[str, Any], pair_id: str) -> dict[str, Any]:
    if calibration.get("calibration_status") != "COMPLETE":
        raise SelectionActivationError(
            "CALIBRATION_NOT_COMPLETE",
            "selection thresholds may only be activated from a COMPLETE calibration report",
        )
    matches = [
        pair
        for pair in calibration.get("evaluated_threshold_pairs", [])
        if pair.get("pair_id") == pair_id
    ]
    if not matches:
        raise SelectionActivationError(
            "THRESHOLD_PAIR_NOT_FOUND",
            f"pair_id {pair_id} is not present in evaluated_threshold_pairs",
        )
    if len(matches) > 1:
        raise SelectionActivationError(
            "THRESHOLD_PAIR_AMBIGUOUS",
            f"pair_id {pair_id} appears {len(matches)} times",
        )
    return matches[0]


def check_activation_preconditions(config: dict[str, Any]) -> None:
    selection = config.get("selection")
    if not isinstance(selection, dict):
        raise SelectionActivationError(
            "SELECTION_BLOCK_MISSING", "strategy config has no selection block"
        )
    if selection.get("enabled") is not False:
        raise SelectionActivationError(
            "SELECTION_ALREADY_ENABLED",
            "selection.enabled must currently be false to activate thresholds",
        )
    rules = selection.get("rules") or {}
    turnover = (rules.get("minimum_turnover_yen") or {}).get("threshold_yen")
    tick = (rules.get("maximum_relative_tick_size") or {}).get("threshold_ratio")
    if turnover is not None or tick is not None:
        raise SelectionActivationError(
            "SELECTION_THRESHOLDS_ALREADY_SET",
            "both selection thresholds must currently be null to activate",
        )


_SELECTION_BLOCK = re.compile(r"^selection:\s*$", re.MULTILINE)


def apply_selection_edits(
    text: str,
    *,
    threshold_yen: int,
    numerator: int,
    denominator: int,
) -> str:
    """Edit exactly the three mutable lines inside the ``selection:`` block.

    Line-scoped editing (rather than a full YAML round-trip) keeps every other
    byte of the risk configuration -- including its comments -- untouched.
    """
    match = _SELECTION_BLOCK.search(text)
    if match is None:
        raise SelectionActivationError(
            "SELECTION_BLOCK_MISSING", "config text has no top-level selection: block"
        )
    lines = text.split("\n")
    start = text[: match.start()].count("\n")
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if line and not line.startswith((" ", "\t", "#")):
            end = index
            break

    edits = {"enabled": False, "threshold_yen": False, "threshold_ratio": False}
    output = list(lines)
    in_tick_rule = False
    for index in range(start + 1, end):
        stripped = output[index].strip()
        if stripped == "maximum_relative_tick_size:":
            in_tick_rule = True
        elif stripped.endswith(":") and not output[index].startswith("      "):
            in_tick_rule = stripped == "maximum_relative_tick_size:"
        if stripped == "enabled: false":
            output[index] = output[index].replace("enabled: false", "enabled: true")
            edits["enabled"] = True
        elif stripped == "threshold_yen: null":
            output[index] = output[index].replace(
                "threshold_yen: null", f"threshold_yen: {threshold_yen}"
            )
            edits["threshold_yen"] = True
        elif stripped == "threshold_ratio: null" and in_tick_rule:
            indent = output[index][: len(output[index]) - len(output[index].lstrip())]
            output[index] = (
                f"{indent}threshold_ratio:\n"
                f"{indent}  numerator: {numerator}\n"
                f"{indent}  denominator: {denominator}"
            )
            edits["threshold_ratio"] = True

    missing = [name for name, done in edits.items() if not done]
    if missing:
        raise SelectionActivationError(
            "SELECTION_CONFIG_SHAPE_UNEXPECTED",
            "could not locate mutable selection field(s): " + ", ".join(missing),
        )
    return "\n".join(output)


def activate_selection_config(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    calibration: dict[str, Any],
    pair_id: str,
) -> dict[str, Any]:
    """Activate ``pair_id``. Returns a summary; raises on any violation."""
    config_path = Path(config_path)
    pair = find_threshold_pair(calibration, pair_id)

    # load + validate the CURRENT config before touching anything
    current = load_strategy_config(config_path)
    check_activation_preconditions(current)

    original_text = config_path.read_text(encoding="utf-8")
    ratio = pair["maximum_relative_tick_size"]
    updated_text = apply_selection_edits(
        original_text,
        threshold_yen=int(pair["minimum_turnover_yen"]),
        numerator=int(ratio["numerator"]),
        denominator=int(ratio["denominator"]),
    )

    temp_path = config_path.with_name(f".{config_path.name}.activation.tmp")
    temp_path.write_text(updated_text, encoding="utf-8")
    try:
        # validate the NEW config before it can ever become the live config
        candidate = load_strategy_config(temp_path)
        _assert_only_mutable_paths_changed(current, candidate)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    temp_path.unlink(missing_ok=True)

    atomic_write_text(config_path, updated_text)

    return {
        "status": "SELECTION_CONFIG_ACTIVATED",
        "pair_id": pair_id,
        "minimum_turnover_yen": int(pair["minimum_turnover_yen"]),
        "maximum_relative_tick_size": {
            "numerator": int(ratio["numerator"]),
            "denominator": int(ratio["denominator"]),
        },
        "mutated_paths": list(MUTABLE_CONFIG_PATHS),
        "downstream_invalidated": True,
        "notes": [
            "Selection configuration changed: the trust chain must be "
            "regenerated from a new strategy_snapshot.yaml. Ranking / "
            "Selection / Recommendation artifacts produced under the previous "
            "selection configuration must not be reused.",
        ],
    }


def _assert_only_mutable_paths_changed(
    before: dict[str, Any], after: dict[str, Any]
) -> None:
    """Guard the blast radius: nothing outside the 3 paths may differ."""
    before_copy = _scrub(before)
    after_copy = _scrub(after)
    if before_copy != after_copy:
        raise SelectionActivationError(
            "SELECTION_ACTIVATION_TOUCHED_FROZEN_CONFIG",
            "activation changed configuration outside the three mutable selection paths",
        )


def _scrub(config: dict[str, Any]) -> dict[str, Any]:
    import copy

    scrubbed = copy.deepcopy(config)
    selection = scrubbed.get("selection")
    if isinstance(selection, dict):
        selection["enabled"] = None
        rules = selection.get("rules") or {}
        if isinstance(rules.get("minimum_turnover_yen"), dict):
            rules["minimum_turnover_yen"]["threshold_yen"] = None
        if isinstance(rules.get("maximum_relative_tick_size"), dict):
            rules["maximum_relative_tick_size"]["threshold_ratio"] = None
    return scrubbed


def human_action_required_report(calibration: dict[str, Any]) -> dict[str, Any]:
    """What the agent must emit instead of picking a pair itself."""
    return {
        "status": HUMAN_ACTION_STATUS,
        "calibration_status": calibration.get("calibration_status"),
        "available_pair_ids": [
            pair.get("pair_id")
            for pair in calibration.get("evaluated_threshold_pairs", [])
        ],
        "notes": [
            "A human must choose exactly one pair_id and run "
            "activate-selection-config --pair-id <id>. The agent must not "
            "select, recommend, or rank threshold pairs.",
        ],
    }


__all__ = [
    "HUMAN_ACTION_STATUS",
    "MUTABLE_CONFIG_PATHS",
    "SelectionActivationError",
    "activate_selection_config",
    "apply_selection_edits",
    "check_activation_preconditions",
    "find_threshold_pair",
    "human_action_required_report",
    "load_yaml",
]
