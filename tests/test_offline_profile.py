"""Offline-profile coverage for the deterministic profile validator."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = (
    PACKAGE_ROOT / "skill" / "xunji-personalised-training" / "scripts"
)
sys.path.insert(0, str(SCRIPTS_ROOT))

from validate_profile import validate_profile_data  # noqa: E402


def synthetic_offline_profile() -> dict:
    return {
        "schema_version": "xunji_training_profile_v1",
        "profile_id": "synthetic-offline-athlete",
        "locale": "en-GB",
        "timezone": "Europe/London",
        "units": "metric",
        "goals": [
            {
                "type": "custom",
                "priority": 1.0,
                "outcome": "Carry uneven loads upstairs for ten minutes",
                "success_metrics": [
                    "Measure continuous work time and technique breakdown"
                ],
            }
        ],
        "functional_targets": [
            {
                "capacity": "asymmetrical_loaded_carry_endurance",
                "task": "Carry uneven synthetic loads upstairs",
                "assessment": "Use a repeatable timed stair-carry protocol",
            }
        ],
        "availability": {
            "sessions_per_week": 4,
            "available_days": [
                "monday",
                "tuesday",
                "thursday",
                "saturday",
            ],
            "preferred_days": [
                "monday",
                "tuesday",
                "thursday",
                "saturday",
            ],
            "session_duration_minutes": {"default": 45, "maximum": 60},
        },
        "training_background": {"overall_level": "intermediate"},
        "equipment": {"available": ["kettlebells", "stairs"]},
        "preferences": {"excluded_movements": []},
        "health_and_safety": {
            "medical_clearance": "not_required",
            "red_flags": [],
            "stop_pain_score": 3,
        },
        "progression": {
            "max_variables_per_movement_per_week": 1,
            "maximum_sets_per_movement": 4,
        },
    }


class OfflineProfileTests(unittest.TestCase):
    def test_missing_xunji_block_is_valid_for_offline_planning(self) -> None:
        result = validate_profile_data(synthetic_offline_profile())

        self.assertEqual(result, {"errors": [], "warnings": []})

    def test_empty_unconfirmed_list_is_valid(self) -> None:
        profile = synthetic_offline_profile()
        profile["unconfirmed"] = []

        self.assertEqual(
            validate_profile_data(profile),
            {"errors": [], "warnings": []},
        )

    def test_malformed_unconfirmed_container_is_rejected(self) -> None:
        for malformed_value in ("pending", {"item": "pending"}, 1):
            with self.subTest(malformed_value=malformed_value):
                profile = synthetic_offline_profile()
                profile["unconfirmed"] = malformed_value

                result = validate_profile_data(profile)

                self.assertTrue(
                    any("$.unconfirmed" in error for error in result["errors"])
                )

    def test_blank_unconfirmed_entry_is_rejected(self) -> None:
        profile = synthetic_offline_profile()
        profile["unconfirmed"] = ["Confirmed item", "   "]

        result = validate_profile_data(profile)

        self.assertTrue(
            any("$.unconfirmed[1]" in error for error in result["errors"])
        )

    def test_present_malformed_xunji_block_is_rejected(self) -> None:
        profile = synthetic_offline_profile()
        profile["xunji"] = []

        result = validate_profile_data(profile)

        self.assertTrue(any("$.xunji" in error for error in result["errors"]))

    def test_present_xunji_block_rejects_policy_mismatch(self) -> None:
        profile = copy.deepcopy(synthetic_offline_profile())
        profile["xunji"] = {"write_policy": "automatic"}

        result = validate_profile_data(profile)

        self.assertTrue(
            any("review_then_explicit_confirm" in error for error in result["errors"])
        )


if __name__ == "__main__":
    unittest.main()
