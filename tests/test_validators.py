"""Offline tests for the deterministic profile and plan validators."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PACKAGE_ROOT / "skill" / "xunji-personalised-training"
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))
sys.path.insert(0, str(PACKAGE_ROOT / "scripts"))

import release_gate  # noqa: E402
from validate_plan import validate_plan_data  # noqa: E402
from validate_profile import validate_profile_data  # noqa: E402


def synthetic_profile() -> dict:
    return {
        "schema_version": "xunji_training_profile_v1",
        "profile_id": "synthetic-person",
        "locale": "en-GB",
        "timezone": "Europe/London",
        "units": "metric",
        "goals": [
            {
                "type": "hypertrophy",
                "priority": 0.6,
                "outcome": "Increase general muscular capacity",
                "success_metrics": ["Complete the planned resistance work"],
            },
            {
                "type": "functional_fitness",
                "priority": 0.4,
                "outcome": "Carry balanced loads with less fatigue",
                "success_metrics": ["Complete a repeatable carry assessment"],
            },
        ],
        "functional_targets": [
            {
                "capacity": "loaded_carry_endurance",
                "task": "Carry two balanced synthetic loads",
                "assessment": "Complete a repeatable timed carry with stable technique",
            }
        ],
        "availability": {
            "sessions_per_week": 2,
            "available_days": ["monday", "thursday"],
            "preferred_days": ["monday", "thursday"],
            "session_duration_minutes": {"default": 50, "maximum": 60},
        },
        "training_background": {"overall_level": "intermediate"},
        "equipment": {"available": ["adjustable_dumbbells"]},
        "preferences": {"excluded_movements": ["synthetic_excluded_movement"]},
        "health_and_safety": {
            "medical_clearance": "not_required",
            "red_flags": [],
            "stop_pain_score": 3,
        },
        "progression": {
            "max_variables_per_movement_per_week": 1,
            "maximum_sets_per_movement": 4,
        },
        "xunji": {"write_policy": "review_then_explicit_confirm"},
    }


def synthetic_plan() -> dict:
    return {
        "schema_version": "xunji_weekly_plan_v1",
        "profile_id": "synthetic-person",
        "week_start": "2030-01-07",
        "status": "draft",
        "evidence": {
            "facts": ["Synthetic availability permits two sessions"],
            "assumptions": ["Technique-led starting targets are appropriate"],
        },
        "sessions": [
            {
                "session_key": "synthetic-session-a",
                "goal_tags": ["hypertrophy"],
                "estimated_minutes": 50,
                "xunji_payload": {
                    "datestr": "2030-01-07",
                    "title": "Synthetic resistance session",
                    "movements": [
                        {
                            "name": "Synthetic dumbbell press",
                            "sets": [
                                {"weight_kg": 20, "reps": 8},
                                {"weight_kg": 20, "reps": 8},
                                {"weight_kg": 20, "reps": 8},
                            ],
                        }
                    ],
                },
            },
            {
                "session_key": "synthetic-session-b",
                "goal_tags": ["functional_fitness"],
                "estimated_minutes": 45,
                "xunji_payload": {
                    "datestr": "2030-01-10",
                    "title": "Synthetic capacity session",
                    "movements": [
                        {
                            "name": "Synthetic balanced carry",
                            "sets": [{"time": 30}, {"time": 30}],
                        }
                    ],
                },
            },
        ],
        "progressions": [
            {
                "movement_name": "Synthetic dumbbell press",
                "changed_variables": ["repetitions"],
            }
        ],
        "write_control": {
            "explicit_confirmation_required": True,
            "confirmed": False,
        },
    }


def synthetic_offline_plan() -> dict:
    return {
        "schema_version": "personalised_training_plan_v1",
        "profile_id": "synthetic-person",
        "week_start": "2030-01-07",
        "status": "draft",
        "evidence": {
            "facts": ["Synthetic availability permits two sessions"],
            "assumptions": ["Technique-led starting targets are appropriate"],
            "unconfirmed": [],
        },
        "sessions": [
            {
                "session_key": "synthetic-offline-session-a",
                "date": "2030-01-07",
                "title": "Synthetic offline resistance session",
                "goal_tags": ["hypertrophy"],
                "estimated_minutes": 50,
                "programme": {
                    "movements": [
                        {
                            "name": "Synthetic dumbbell press",
                            "sets": [
                                {"weight_kg": 20, "reps": 8},
                                {"weight_kg": 20, "reps": 8},
                                {"weight_kg": 20, "reps": 8},
                            ],
                        }
                    ]
                },
            },
            {
                "session_key": "synthetic-offline-session-b",
                "date": "2030-01-10",
                "title": "Synthetic offline capacity session",
                "goal_tags": ["functional_fitness"],
                "estimated_minutes": 45,
                "programme": {
                    "movements": [
                        {
                            "name": "Synthetic balanced carry",
                            "sets": [{"time": 30}, {"time": 30}],
                        }
                    ]
                },
            },
        ],
        "progressions": [
            {
                "movement_name": "Synthetic dumbbell press",
                "changed_variables": ["repetitions"],
            }
        ],
        "write_control": {"remote_write_requested": False},
    }


def has_message(result: dict, fragment: str, field: str = "errors") -> bool:
    return any(fragment in message for message in result[field])


class ProfileValidatorTests(unittest.TestCase):
    def test_valid_synthetic_profile(self) -> None:
        self.assertEqual(
            validate_profile_data(synthetic_profile()),
            {"errors": [], "warnings": []},
        )

    def test_valid_repository_example(self) -> None:
        example = json.loads(
            (SKILL_ROOT / "assets" / "user-profile.example.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(validate_profile_data(example)["errors"], [])

    def test_profile_rejects_conflicting_core_constraints(self) -> None:
        profile = synthetic_profile()
        profile["goals"][0]["priority"] = 0.2
        profile.pop("functional_targets")
        profile["availability"]["sessions_per_week"] = 15
        profile["health_and_safety"]["stop_pain_score"] = 6
        profile["progression"]["maximum_sets_per_movement"] = 21
        profile["xunji"]["write_policy"] = "automatic"

        result = validate_profile_data(profile)

        self.assertTrue(has_message(result, "priorities must sum to 1"))
        self.assertTrue(has_message(result, "requires usable functional_targets"))
        self.assertTrue(has_message(result, "value from 1 to 14"))
        self.assertTrue(has_message(result, "value from 0 to 5"))
        self.assertTrue(has_message(result, "value from 1 to 20"))
        self.assertTrue(has_message(result, "review_then_explicit_confirm"))

    def test_goal_level_functional_targets_are_supported(self) -> None:
        profile = synthetic_profile()
        targets = profile.pop("functional_targets")
        profile["goals"][1]["functional_targets"] = targets
        self.assertEqual(validate_profile_data(profile)["errors"], [])

    def test_malformed_clearance_reports_an_error_without_raising(self) -> None:
        profile = synthetic_profile()
        profile["health_and_safety"]["medical_clearance"] = []
        profile["health_and_safety"]["red_flags"] = ["Synthetic stop signal"]

        result = validate_profile_data(profile)

        self.assertTrue(has_message(result, "medical_clearance"))

    def test_boolean_clearance_is_rejected_as_ambiguous(self) -> None:
        profile = synthetic_profile()
        profile["health_and_safety"]["medical_clearance"] = True
        self.assertTrue(
            has_message(validate_profile_data(profile), "medical_clearance")
        )


class PlanValidatorTests(unittest.TestCase):
    def test_valid_synthetic_plan(self) -> None:
        self.assertEqual(
            validate_plan_data(synthetic_plan(), synthetic_profile()),
            {"errors": [], "warnings": []},
        )

    def test_valid_repository_example(self) -> None:
        profile = json.loads(
            (SKILL_ROOT / "assets" / "user-profile.example.json").read_text(
                encoding="utf-8"
            )
        )
        plan = json.loads(
            (SKILL_ROOT / "assets" / "weekly-plan.example.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(validate_plan_data(plan, profile)["errors"], [])

    def test_valid_synthetic_offline_plan(self) -> None:
        self.assertEqual(
            validate_plan_data(synthetic_offline_plan(), synthetic_profile()),
            {"errors": [], "warnings": []},
        )

    def test_valid_repository_offline_example(self) -> None:
        profile = json.loads(
            (SKILL_ROOT / "assets" / "user-profile.example.json").read_text(
                encoding="utf-8"
            )
        )
        profile["unconfirmed"] = []
        profile.pop("xunji", None)
        plan = json.loads(
            (SKILL_ROOT / "assets" / "offline-weekly-plan.example.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(validate_plan_data(plan, profile), {"errors": [], "warnings": []})

    def test_offline_plan_requires_a_profile(self) -> None:
        result = validate_plan_data(synthetic_offline_plan())

        self.assertTrue(
            has_message(result, "$profile: required for personalised_training_plan_v1")
        )

    def test_offline_plan_enforces_profile_constraints_and_goal_coverage(self) -> None:
        plan = synthetic_offline_plan()
        plan["sessions"][0]["date"] = "2030-01-08"
        plan["sessions"][0]["title"] = ""
        plan["sessions"][0]["programme"]["movements"][0][
            "name"
        ] = "synthetic_excluded_movement"
        plan["sessions"] = [plan["sessions"][0]]
        plan["progressions"][0]["changed_variables"] = ["reps", "load"]

        result = validate_plan_data(plan, synthetic_profile())

        self.assertTrue(has_message(result, ".title: expected a non-empty string"))
        self.assertTrue(has_message(result, "is not an available training day"))
        self.assertTrue(has_message(result, "excluded movement"))
        self.assertTrue(has_message(result, "offline session count must match"))
        self.assertTrue(has_message(result, "has no session coverage"))
        self.assertTrue(has_message(result, "changes 2 variables"))

    def test_offline_plan_forbids_xunji_payloads_and_remote_write(self) -> None:
        plan = synthetic_offline_plan()
        plan["sessions"][0]["xunji_payload"] = {"movements": []}
        plan["sessions"][1]["programme"]["original_xunji_payload"] = {}
        plan["write_control"]["remote_write_requested"] = True

        result = validate_plan_data(plan, synthetic_profile())

        self.assertTrue(has_message(result, ".xunji_payload: is forbidden"))
        self.assertTrue(has_message(result, ".original_xunji_payload: is forbidden"))
        self.assertTrue(has_message(result, "expected exactly false"))

    def test_offline_plan_blocks_unresolved_plan_and_profile_items(self) -> None:
        plan = synthetic_offline_plan()
        plan["evidence"]["unconfirmed"] = ["Confirm synthetic schedule"]
        profile = synthetic_profile()
        profile["unconfirmed"] = ["Confirm synthetic equipment"]

        result = validate_plan_data(plan, profile)

        self.assertTrue(has_message(result, "$.evidence.unconfirmed"))
        self.assertTrue(has_message(result, "$profile.unconfirmed"))

    def test_plan_evidence_unconfirmed_must_be_a_string_list(self) -> None:
        for plan, profile in (
            (synthetic_plan(), synthetic_profile()),
            (synthetic_offline_plan(), synthetic_profile()),
        ):
            with self.subTest(schema_version=plan["schema_version"], case="mapping"):
                malformed = copy.deepcopy(plan)
                malformed["evidence"]["unconfirmed"] = {"item": "Synthetic"}
                result = validate_plan_data(malformed, profile)
                self.assertTrue(
                    has_message(result, "$.evidence.unconfirmed: expected a list")
                )

            with self.subTest(schema_version=plan["schema_version"], case="blank"):
                malformed = copy.deepcopy(plan)
                malformed["evidence"]["unconfirmed"] = [""]
                result = validate_plan_data(malformed, profile)
                self.assertTrue(
                    has_message(
                        result,
                        "$.evidence.unconfirmed[0]: expected a non-empty string",
                    )
                )

        xunji_plan = synthetic_plan()
        xunji_plan["evidence"]["unconfirmed"] = ["Synthetic item may remain visible"]
        self.assertEqual(
            validate_plan_data(xunji_plan, synthetic_profile())["errors"], []
        )

    def test_offline_plan_accepts_general_dose_targets_without_api_limits(self) -> None:
        plan = synthetic_offline_plan()
        profile = synthetic_profile()
        profile["availability"]["sessions_per_week"] = 1
        profile["goals"] = [profile["goals"][0]]
        profile["goals"][0]["priority"] = 1.0
        profile.pop("functional_targets")
        profile["progression"]["maximum_sets_per_movement"] = 5
        plan["sessions"] = [plan["sessions"][0]]
        plan["progressions"] = []

        source = plan["sessions"][0]["programme"]["movements"][0]
        source["sets"] = [
            {"distance": 100},
            {"distance_m": 100},
            {"calories": 10},
            {"kcal": 10},
            {"metrics": {"stride_rate": 70}},
        ]
        plan["sessions"][0]["programme"]["movements"] = [
            {**copy.deepcopy(source), "name": f"Synthetic movement {index}"}
            for index in range(16)
        ]
        result = validate_plan_data(plan, profile)

        self.assertEqual(result["errors"], [])
        self.assertFalse(has_message(result, "Xunji allows"))

        plan_without_profile_limit = copy.deepcopy(plan)
        plan_without_profile_limit["sessions"][0]["programme"]["movements"][0][
            "sets"
        ] = [{"reps": 8} for _ in range(21)]
        result_without_profile = validate_plan_data(plan_without_profile_limit)
        self.assertTrue(has_message(result_without_profile, "$profile: required"))
        self.assertFalse(has_message(result_without_profile, "Xunji allows"))

    def test_offline_plan_rejects_non_dose_fields_as_only_targets(self) -> None:
        plan = synthetic_offline_plan()
        plan["sessions"][0]["programme"]["movements"][0]["sets"] = [
            {"rest_s": 60},
            {"tempo": "3-1-1"},
            {"range": "full"},
            {"metrics": {}},
        ]

        result = validate_plan_data(plan, synthetic_profile())

        self.assertTrue(has_message(result, "expected at least one dose target"))

    def test_offline_baseline_comparison_uses_programme_movements(self) -> None:
        plan = synthetic_offline_plan()
        baseline = copy.deepcopy(plan)
        for set_data in baseline["sessions"][0]["programme"]["movements"][0][
            "sets"
        ]:
            set_data["reps"] = 7

        result = validate_plan_data(plan, synthetic_profile(), baseline)

        self.assertEqual(result["errors"], [])

        undeclared = copy.deepcopy(plan)
        undeclared["progressions"] = []
        undeclared_result = validate_plan_data(
            undeclared, synthetic_profile(), baseline
        )
        self.assertTrue(
            has_message(undeclared_result, "declared progression")
        )

    def test_direct_calorie_aliases_are_typed_progression_variables(self) -> None:
        for calorie_field in ("calories", "kcal"):
            with self.subTest(calorie_field=calorie_field):
                plan = synthetic_offline_plan()
                movement = plan["sessions"][0]["programme"]["movements"][0]
                movement["sets"] = [{calorie_field: 20}]
                plan["progressions"] = [
                    {
                        "movement_name": movement["name"],
                        "changed_variables": [calorie_field],
                    }
                ]
                baseline = copy.deepcopy(plan)
                baseline["sessions"][0]["programme"]["movements"][0]["sets"][
                    0
                ][calorie_field] = 15

                result = validate_plan_data(plan, synthetic_profile(), baseline)
                self.assertEqual(result["errors"], [])

                undeclared = copy.deepcopy(plan)
                undeclared["progressions"] = []
                undeclared_result = validate_plan_data(
                    undeclared, synthetic_profile(), baseline
                )
                self.assertTrue(
                    has_message(undeclared_result, "declared progression")
                )
                self.assertTrue(has_message(undeclared_result, "calories"))

    def test_direct_calories_count_towards_progression_limit(self) -> None:
        plan = synthetic_offline_plan()
        movement = plan["sessions"][0]["programme"]["movements"][0]
        movement["sets"] = [{"reps": 8, "calories": 20}]
        plan["progressions"] = [
            {
                "movement_name": movement["name"],
                "changed_variables": ["reps", "calories"],
            }
        ]
        baseline = copy.deepcopy(plan)
        baseline_set = baseline["sessions"][0]["programme"]["movements"][0][
            "sets"
        ][0]
        baseline_set["reps"] = 7
        baseline_set["calories"] = 15

        result = validate_plan_data(plan, synthetic_profile(), baseline)

        self.assertTrue(has_message(result, "changes 2 variables"))

    def test_xunji_schema_rejects_offline_shadow_fields(self) -> None:
        plan = synthetic_plan()
        plan["sessions"][0].update(
            {
                "programme": {"movements": []},
                "date": "2030-01-07",
                "title": "Synthetic shadow title",
                "movements": [],
            }
        )
        plan["write_control"]["remote_write_requested"] = False

        result = validate_plan_data(plan, synthetic_profile())

        for field in (
            ".programme: offline shadow field",
            ".date: offline shadow field",
            ".title: offline shadow field",
            ".movements: offline shadow field",
            ".remote_write_requested: offline shadow field",
        ):
            self.assertTrue(has_message(result, field), field)

    def test_xunji_schema_still_requires_xunji_payload(self) -> None:
        plan = synthetic_plan()
        plan["sessions"][0]["programme"] = plan["sessions"][0].pop(
            "xunji_payload"
        )

        result = validate_plan_data(plan, synthetic_profile())

        self.assertTrue(has_message(result, ".xunji_payload: required field is missing"))

    def test_plan_enforces_duration_day_exclusion_and_set_targets(self) -> None:
        plan = synthetic_plan()
        session = plan["sessions"][0]
        session["estimated_minutes"] = 75
        session["xunji_payload"]["datestr"] = "2030-01-08"
        movement = session["xunji_payload"]["movements"][0]
        movement["name"] = "synthetic_excluded_movement"
        movement["sets"] = [{} for _ in range(21)]

        result = validate_plan_data(plan, synthetic_profile())

        self.assertTrue(has_message(result, "exceeds profile maximum"))
        self.assertTrue(has_message(result, "is not an available training day"))
        self.assertTrue(has_message(result, "excluded movement"))
        self.assertTrue(has_message(result, "at most 20 sets"))
        self.assertTrue(has_message(result, "at least one target field"))

    def test_plan_enforces_daily_session_limit(self) -> None:
        profile = synthetic_profile()
        profile["availability"]["sessions_per_week"] = 5
        profile["availability"]["available_days"] = ["monday", "tuesday"]
        profile["availability"]["preferred_days"] = ["monday", "tuesday"]

        source_session = synthetic_plan()["sessions"][0]
        sessions = []
        for index in range(5):
            session = copy.deepcopy(source_session)
            session["session_key"] = f"synthetic-{index}"
            session["goal_tags"] = [
                "hypertrophy",
                "functional_fitness" if index == 0 else "hypertrophy",
            ]
            sessions.append(session)
        plan = synthetic_plan()
        plan["sessions"] = sessions
        plan["progressions"] = []

        result = validate_plan_data(plan, profile)

        self.assertTrue(has_message(result, "daily maximum is 4"))

    def test_plan_enforces_movement_limit(self) -> None:
        profile = synthetic_profile()
        profile["availability"]["sessions_per_week"] = 1
        profile["goals"] = [profile["goals"][1]]
        profile["goals"][0]["priority"] = 1.0
        plan = synthetic_plan()
        plan["sessions"] = [plan["sessions"][1]]
        plan["progressions"] = []
        source = plan["sessions"][0]["xunji_payload"]["movements"][0]
        plan["sessions"][0]["xunji_payload"]["movements"] = [
            {**copy.deepcopy(source), "name": f"Synthetic movement {index}"}
            for index in range(16)
        ]

        result = validate_plan_data(plan, profile)

        self.assertTrue(has_message(result, "at most 15 movements"))

    def test_goal_tags_and_functional_coverage_use_profile_goal_types(self) -> None:
        plan = synthetic_plan()
        plan["sessions"][1]["goal_tags"] = ["strength"]

        result = validate_plan_data(plan, synthetic_profile())

        self.assertTrue(has_message(result, "is not declared in the profile"))
        self.assertTrue(has_message(result, "has no session coverage"))

    def test_declared_progression_respects_profile_limit(self) -> None:
        plan = synthetic_plan()
        plan["progressions"][0]["changed_variables"] = ["reps", "load"]

        result = validate_plan_data(plan, synthetic_profile())

        self.assertTrue(has_message(result, "changes 2 variables"))

    def test_baseline_detects_multiple_target_changes(self) -> None:
        plan = synthetic_plan()
        baseline = copy.deepcopy(plan)
        previous_sets = baseline["sessions"][0]["xunji_payload"]["movements"][0][
            "sets"
        ]
        for set_data in previous_sets:
            set_data["weight_kg"] = 18
            set_data["reps"] = 7

        result = validate_plan_data(plan, synthetic_profile(), baseline)

        self.assertTrue(has_message(result, "profile maximum is 1"))
        self.assertTrue(has_message(result, "load"))
        self.assertTrue(has_message(result, "reps"))

    def test_declared_progression_requires_comparable_baseline_data(self) -> None:
        result = validate_plan_data(synthetic_plan(), synthetic_profile(), {})

        self.assertTrue(has_message(result, "no comparable Xunji movements"))

    def test_baseline_change_must_match_declared_progression(self) -> None:
        plan = synthetic_plan()
        baseline = copy.deepcopy(plan)
        plan["progressions"] = []
        for set_data in baseline["sessions"][0]["xunji_payload"]["movements"][0][
            "sets"
        ]:
            set_data["reps"] = 7

        result = validate_plan_data(plan, synthetic_profile(), baseline)

        self.assertTrue(has_message(result, "declared progression"))

    def test_nested_target_change_matches_declared_progression(self) -> None:
        plan = synthetic_plan()
        nested_sets = [
            {
                "items": [
                    {"set": {"weight_kg": 20, "reps": 8}},
                    {"set": {"weight_kg": 10, "reps": 10}},
                ]
            }
        ]
        plan["sessions"][0]["xunji_payload"]["movements"][0]["sets"] = copy.deepcopy(
            nested_sets
        )
        baseline = copy.deepcopy(plan)
        baseline["sessions"][0]["xunji_payload"]["movements"][0]["sets"][0][
            "items"
        ][0]["set"]["reps"] = 7

        result = validate_plan_data(plan, synthetic_profile(), baseline)

        self.assertEqual(result["errors"], [])

    def test_nested_or_reordered_target_change_cannot_be_undeclared(self) -> None:
        plan = synthetic_plan()
        plan["progressions"] = []
        baseline = copy.deepcopy(plan)
        baseline["sessions"][0]["xunji_payload"]["movements"][0]["sets"] = [
            {
                "items": [
                    {"set": {"weight_kg": 20, "reps": 7}},
                    {"set": {"weight_kg": 10, "reps": 10}},
                ]
            }
        ]
        plan["sessions"][0]["xunji_payload"]["movements"][0]["sets"] = [
            {
                "items": [
                    {"set": {"weight_kg": 20, "reps": 8}},
                    {"set": {"weight_kg": 10, "reps": 10}},
                ]
            }
        ]

        nested_result = validate_plan_data(plan, synthetic_profile(), baseline)
        self.assertTrue(has_message(nested_result, "declared progression"))

        reordered_plan = synthetic_plan()
        reordered_plan["progressions"] = []
        reordered_plan["sessions"][0]["xunji_payload"]["movements"][0]["sets"] = [
            {"weight_kg": 20, "reps": 8},
            {"weight_kg": 25, "reps": 6},
        ]
        reordered_baseline = copy.deepcopy(reordered_plan)
        reordered_plan["sessions"][0]["xunji_payload"]["movements"][0][
            "sets"
        ].reverse()

        reordered_result = validate_plan_data(
            reordered_plan, synthetic_profile(), reordered_baseline
        )
        self.assertTrue(has_message(reordered_result, "declared progression"))

    def test_secondary_target_alias_change_cannot_be_hidden(self) -> None:
        plan = synthetic_plan()
        plan["progressions"] = []
        plan_set = plan["sessions"][0]["xunji_payload"]["movements"][0]["sets"][0]
        plan_set["weight"] = 21
        baseline = copy.deepcopy(plan)
        baseline_set = baseline["sessions"][0]["xunji_payload"]["movements"][0][
            "sets"
        ][0]
        baseline_set["weight"] = 20

        changed_secondary = validate_plan_data(plan, synthetic_profile(), baseline)
        self.assertTrue(has_message(changed_secondary, "declared progression"))

        switched_plan = synthetic_plan()
        switched_plan["progressions"] = []
        switched_baseline = copy.deepcopy(switched_plan)
        switched_plan["sessions"][0]["xunji_payload"]["movements"][0]["sets"][
            0
        ]["weight"] = switched_plan["sessions"][0]["xunji_payload"]["movements"][
            0
        ]["sets"][0].pop("weight_kg")

        switched_alias = validate_plan_data(
            switched_plan, synthetic_profile(), switched_baseline
        )
        self.assertTrue(has_message(switched_alias, "declared progression"))

        unit_plan = synthetic_plan()
        unit_plan["progressions"] = []
        unit_plan["sessions"][0]["xunji_payload"]["movements"][0]["sets"][0][
            "unit"
        ] = "lb"
        unit_baseline = copy.deepcopy(unit_plan)
        unit_baseline["sessions"][0]["xunji_payload"]["movements"][0]["sets"][0][
            "unit"
        ] = "kg"

        changed_unit = validate_plan_data(
            unit_plan, synthetic_profile(), unit_baseline
        )
        self.assertTrue(has_message(changed_unit, "declared progression"))

    def test_metrics_and_difficulty_are_typed_progression_variables(self) -> None:
        plan = synthetic_plan()
        plan["progressions"][0]["changed_variables"] = ["distance"]
        movement = plan["sessions"][0]["xunji_payload"]["movements"][0]
        movement["difficulty"] = "normal"
        movement["sets"][0]["metrics"] = {
            "distance": 100,
            "heart_rate": 130,
        }
        baseline = copy.deepcopy(plan)
        baseline["sessions"][0]["xunji_payload"]["movements"][0]["sets"][0][
            "metrics"
        ]["distance"] = 90

        distance_only = validate_plan_data(plan, synthetic_profile(), baseline)
        self.assertEqual(distance_only["errors"], [])

        plan["sessions"][0]["xunji_payload"]["movements"][0][
            "difficulty"
        ] = "hard"
        difficulty_change = validate_plan_data(plan, synthetic_profile(), baseline)
        self.assertTrue(has_message(difficulty_change, "difficulty"))

        plan["sessions"][0]["xunji_payload"]["movements"][0]["sets"][0][
            "metrics"
        ]["stride_rate"] = 80
        baseline["sessions"][0]["xunji_payload"]["movements"][0]["sets"][0][
            "metrics"
        ]["stride_rate"] = 75
        unknown_metric = validate_plan_data(plan, synthetic_profile(), baseline)
        self.assertTrue(has_message(unknown_metric, "metric:/stride_rate"))

    def test_nested_auxiliary_and_metric_changes_are_typed(self) -> None:
        plan = synthetic_plan()
        plan["progressions"][0]["changed_variables"] = ["distance"]
        child_set = {
            "reps": 8,
            "rest_s": 60,
            "metrics": {"distance": 120, "stride_rate": 80},
        }
        plan["sessions"][0]["xunji_payload"]["movements"][0]["sets"] = [
            {"items": [{"set": copy.deepcopy(child_set)}]}
        ]
        baseline = copy.deepcopy(plan)
        baseline_child = baseline["sessions"][0]["xunji_payload"]["movements"][
            0
        ]["sets"][0]["items"][0]["set"]
        baseline_child["metrics"]["distance"] = 100

        distance_only = validate_plan_data(plan, synthetic_profile(), baseline)
        self.assertEqual(distance_only["errors"], [])

        baseline_child["rest_s"] = 75
        hidden_density = validate_plan_data(plan, synthetic_profile(), baseline)
        self.assertTrue(has_message(hidden_density, "density"))

        baseline_child["rest_s"] = 60
        baseline_child["metrics"]["stride_rate"] = 70
        unknown_metric = validate_plan_data(plan, synthetic_profile(), baseline)
        self.assertTrue(has_message(unknown_metric, "metric:/stride_rate"))

    def test_literal_and_nested_metric_paths_cannot_collide(self) -> None:
        plan = synthetic_plan()
        plan["progressions"] = []
        plan["sessions"][0]["xunji_payload"]["movements"][0]["sets"][0][
            "metrics"
        ] = {"foo.bar": 1}
        baseline = copy.deepcopy(plan)
        baseline["sessions"][0]["xunji_payload"]["movements"][0]["sets"][0][
            "metrics"
        ] = {"foo": {"bar": 1}}

        result = validate_plan_data(plan, synthetic_profile(), baseline)

        self.assertTrue(has_message(result, "metric:/foo.bar"))
        self.assertTrue(has_message(result, "metric:/foo/bar"))

    def test_metric_root_and_empty_key_cannot_collide(self) -> None:
        plan = synthetic_plan()
        plan["progressions"] = []
        plan["sessions"][0]["xunji_payload"]["movements"][0]["sets"][0][
            "metrics"
        ] = 1
        baseline = copy.deepcopy(plan)
        baseline["sessions"][0]["xunji_payload"]["movements"][0]["sets"][0][
            "metrics"
        ] = {"": 1}

        result = validate_plan_data(plan, synthetic_profile(), baseline)

        self.assertTrue(result["errors"])
        self.assertTrue(has_message(result, "metric:/"))

    def test_added_set_with_novel_targets_is_not_only_a_set_change(self) -> None:
        plan = synthetic_plan()
        plan["progressions"][0]["changed_variables"] = ["sets"]
        baseline = copy.deepcopy(plan)
        plan_sets = plan["sessions"][0]["xunji_payload"]["movements"][0][
            "sets"
        ]
        plan_sets.append({"weight_kg": 20, "reps": 8})

        repeated_target = validate_plan_data(plan, synthetic_profile(), baseline)
        self.assertEqual(repeated_target["errors"], [])

        plan_sets[-1]["reps"] = 10
        novel_target = validate_plan_data(plan, synthetic_profile(), baseline)
        self.assertTrue(has_message(novel_target, "reps"))

        mixed_plan = synthetic_plan()
        mixed_plan["progressions"][0]["changed_variables"] = ["sets"]
        mixed_sets = mixed_plan["sessions"][0]["xunji_payload"]["movements"][0][
            "sets"
        ]
        mixed_sets[0] = {"weight_kg": 30, "reps": 6}
        mixed_sets[1] = {"weight_kg": 20, "reps": 10}
        mixed_baseline = copy.deepcopy(mixed_plan)
        mixed_sets.append({"weight_kg": 30, "reps": 10})
        mixed_target = validate_plan_data(
            mixed_plan, synthetic_profile(), mixed_baseline
        )
        self.assertTrue(has_message(mixed_target, "load"))
        self.assertTrue(has_message(mixed_target, "reps"))

    def test_baseline_normalises_numeric_strings(self) -> None:
        plan = synthetic_plan()
        baseline = copy.deepcopy(plan)
        for set_data in baseline["sessions"][0]["xunji_payload"]["movements"][0][
            "sets"
        ]:
            set_data["weight_kg"] = "20.0"
            set_data["reps"] = "7.0"

        result = validate_plan_data(plan, synthetic_profile(), baseline)

        self.assertFalse(any(message.startswith("$baseline:") for message in result["errors"]))

    def test_draft_write_control_cannot_be_preconfirmed(self) -> None:
        plan = synthetic_plan()
        plan["write_control"]["explicit_confirmation_required"] = False
        plan["write_control"]["confirmed"] = True

        result = validate_plan_data(plan, synthetic_profile())

        self.assertTrue(has_message(result, "explicit_confirmation_required"))
        self.assertTrue(has_message(result, "expected false for a draft plan"))

    def test_red_flags_block_declared_progression(self) -> None:
        profile = synthetic_profile()
        profile["health_and_safety"]["red_flags"] = ["Synthetic stop signal"]

        result = validate_plan_data(synthetic_plan(), profile)

        self.assertTrue(has_message(result, "red flags block automated progression"))


class ReleaseGateTests(unittest.TestCase):
    def test_private_home_path_scan_detects_supported_platforms(self) -> None:
        private_paths = [
            "/Use" + "rs/real-account/private/profile.json",
            "/ho" + "me/real-account/private/profile.json",
            "/ho" + "me//real-account/private/profile.json",
            "/ho" + "me/real-account",
            "C:" + "\\" + "Users\\real-account\\private\\profile.json",
            "C:" + "\\" + "Users\\real-account",
            "D:" + "/Use" + "rs/real-account/private/profile.json",
            "\\\\" + "server\\Use" + "rs\\real-account\\private\\profile.json",
        ]

        for private_path in private_paths:
            with self.subTest(private_path=private_path):
                self.assertTrue(release_gate.contains_personal_home_path(private_path))

    def test_private_home_path_scan_allows_explicit_placeholders(self) -> None:
        public_paths = [
            "/local/private/path/profile.json",
            "/Use" + "rs/<username>/project/profile.json",
            "/ho" + "me/<username>",
            "/ho" + "me/${USER}/project/profile.json",
            "C:" + "\\" + "Users\\%USERNAME%\\project\\profile.json",
            "C:" + "\\" + "Users\\%USERNAME%",
            "D:" + "/Use" + "rs/example-user/project/profile.json",
        ]

        for public_path in public_paths:
            with self.subTest(public_path=public_path):
                self.assertFalse(release_gate.contains_personal_home_path(public_path))

        concrete_accounts = [
            "/ho" + "me/user/private/profile.json",
            "/ho" + "me/username/private/profile.json",
            "/ho" + "me/example-user-real/private/profile.json",
            "/ho" + "me/<username>-real/private/profile.json",
            "/ho" + "me/${USER}-backup/private/profile.json",
        ]
        for concrete_path in concrete_accounts:
            with self.subTest(concrete_path=concrete_path):
                self.assertTrue(
                    release_gate.contains_personal_home_path(concrete_path)
                )

    def test_private_home_path_scan_is_wired_to_text_and_asset_gates(self) -> None:
        concrete_path = "/ho" + "me/real-account/private/profile.json"
        text_errors: list[str] = []
        with mock.patch.object(
            release_gate, "read_text", return_value=concrete_path
        ):
            release_gate.check_private_content(
                [release_gate.ROOT / "README.md"], text_errors
            )
        self.assertTrue(any("absolute personal path" in item for item in text_errors))

        asset_errors: list[str] = []
        release_gate.check_synthetic_asset_content(
            release_gate.SKILL_DIR / "assets" / "user-profile.example.json",
            {"example_path": concrete_path},
            asset_errors,
        )
        self.assertTrue(
            any("possible personal home path" in item for item in asset_errors)
        )


class ValidatorCliTests(unittest.TestCase):
    def test_profile_cli_returns_non_zero_for_validation_errors(self) -> None:
        invalid_profile = synthetic_profile()
        invalid_profile["schema_version"] = "wrong_version"
        script = SCRIPTS_ROOT / "validate_profile.py"

        with tempfile.TemporaryDirectory() as temporary_directory:
            profile_path = Path(temporary_directory) / "profile.json"
            profile_path.write_text(json.dumps(invalid_profile), encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(script), str(profile_path)],
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(completed.returncode, 1)
        output = json.loads(completed.stdout)
        self.assertTrue(output["errors"])

    def test_plan_cli_accepts_profile_and_baseline(self) -> None:
        script = SCRIPTS_ROOT / "validate_plan.py"
        plan = synthetic_plan()
        baseline = copy.deepcopy(plan)
        for set_data in baseline["sessions"][0]["xunji_payload"]["movements"][0][
            "sets"
        ]:
            set_data["reps"] = 7

        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            plan_path = temporary_path / "plan.json"
            profile_path = temporary_path / "profile.json"
            baseline_path = temporary_path / "baseline.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            profile_path.write_text(json.dumps(synthetic_profile()), encoding="utf-8")
            baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    str(plan_path),
                    "--profile",
                    str(profile_path),
                    "--baseline",
                    str(baseline_path),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["errors"], [])

    def test_offline_plan_cli_reports_missing_profile(self) -> None:
        script = SCRIPTS_ROOT / "validate_plan.py"

        with tempfile.TemporaryDirectory() as temporary_directory:
            plan_path = Path(temporary_directory) / "offline-plan.json"
            plan_path.write_text(
                json.dumps(synthetic_offline_plan()), encoding="utf-8"
            )
            completed = subprocess.run(
                [sys.executable, str(script), str(plan_path)],
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(completed.returncode, 1)
        output = json.loads(completed.stdout)
        self.assertTrue(has_message(output, "$profile: required"))


if __name__ == "__main__":
    unittest.main()
