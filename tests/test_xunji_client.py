"""Synthetic, network-free tests for the Xunji safety client."""

from __future__ import annotations

import copy
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock
import urllib.error


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "skill"
    / "xunji-personalised-training"
    / "scripts"
    / "xunji_client.py"
)
PROFILE_PATH = (
    Path(__file__).resolve().parents[1]
    / "skill"
    / "xunji-personalised-training"
    / "assets"
    / "user-profile.example.json"
)
SPEC = importlib.util.spec_from_file_location("xunji_client", MODULE_PATH)
if SPEC is None or SPEC.loader is None:  # pragma: no cover - import bootstrap guard.
    raise RuntimeError("Could not load xunji_client.py")
xunji_client = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(xunji_client)


class FakeResponse:
    def __init__(self, value: dict[str, object]) -> None:
        self._body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.headers: dict[str, str] = {}
        self.closed = False

    def read(self, amount: int = -1) -> bytes:
        return self._body if amount < 0 else self._body[:amount]

    def close(self) -> None:
        self.closed = True


class FakeOpener:
    def __init__(self, responses: list[dict[str, object] | BaseException]) -> None:
        self.responses = list(responses)
        self.requests: list[object] = []

    def __call__(self, request: object, timeout: int = 0) -> FakeResponse:
        del timeout
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("Unexpected network request")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return FakeResponse(response)


def synthetic_payload(*, update: bool = False) -> dict[str, object]:
    payload: dict[str, object] = {
        "datestr": "2030-04-08",
        "title": "Synthetic strength session",
        "movements": [
            {
                "name": "合成推举",
                "sets": [
                    {"weight": "30", "unit": "kg", "reps": "8"},
                    {"weight": "30", "unit": "kg", "reps": "8"},
                ],
            }
        ],
    }
    if update:
        payload.update({"localid": "synthetic-id", "start": "100", "end": "100"})
    return payload


def synthetic_plan(*, update: bool = False) -> dict[str, object]:
    payload = synthetic_payload(update=update)
    wrapper: dict[str, object] = {"xunji_payload": payload}
    if update:
        wrapper["original_xunji_payload"] = copy.deepcopy(payload)
        payload["movements"][0]["sets"][0]["reps"] = "9"
    return {
        "schema_version": "xunji_weekly_plan_v1",
        "profile_id": "synthetic-example-athlete",
        "week_start": "2030-04-08",
        "status": "draft",
        "evidence": {
            "facts": ["Synthetic client test evidence"],
            "user_reported": [],
            "derived": [],
            "assumptions": [],
            "unconfirmed": [],
        },
        "change_summary": ["Synthetic client test change"],
        "sessions": [
            {
                "session_key": "synthetic-client-session",
                "goal_tags": ["hypertrophy"],
                "reason": "Exercise the guarded client with fabricated data",
                "estimated_minutes": 45,
                **wrapper,
            }
        ],
        "progressions": (
            [
                {
                    "movement_name": "合成推举",
                    "changed_variables": ["repetitions"],
                    "reason": "Synthetic update progression",
                    "regression_gate": "Hold if completion or recovery declines",
                }
            ]
            if update
            else []
        ),
        "write_control": {
            "explicit_confirmation_required": True,
            "confirmed": False,
        },
    }


def client_profile() -> dict[str, object]:
    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    profile["goals"] = [profile["goals"][0]]
    profile["goals"][0]["priority"] = 1.0
    profile["availability"]["sessions_per_week"] = 1
    profile["availability"]["available_days"] = ["monday"]
    profile["availability"]["preferred_days"] = ["monday"]
    profile["unconfirmed"] = []
    return profile


def two_date_plan() -> dict[str, object]:
    plan = synthetic_plan()
    second = copy.deepcopy(plan["sessions"][0])
    second["session_key"] = "synthetic-client-session-two"
    second["xunji_payload"]["datestr"] = "2030-04-09"
    second["xunji_payload"]["title"] = "Synthetic second strength session"
    plan["sessions"].append(second)
    return plan


def two_date_profile() -> dict[str, object]:
    profile = client_profile()
    profile["availability"]["sessions_per_week"] = 2
    profile["availability"]["available_days"] = ["monday", "tuesday"]
    profile["availability"]["preferred_days"] = ["monday", "tuesday"]
    return profile


def successful_read(sessions: list[dict[str, object]]) -> dict[str, object]:
    return {"success": True, "res": {"trains": sessions}}


def transaction_account_cache(credential: str) -> Path:
    return xunji_client._account_cache_dir(
        xunji_client._fixed_transaction_cache_dir(), credential
    )


def mark_pending(credential: str, digest: str) -> None:
    account_cache = transaction_account_cache(credential)
    xunji_client._mark_verification_pending(
        account_cache,
        "2030-04-08",
        digest,
        1,
        xunji_client._client_request_id(digest, "2030-04-08"),
        1,
    )


def synthetic_cache_envelope(
    credential: str,
    sessions: list[dict[str, object]],
    *,
    datestr: str = "2030-04-08",
    include_full_data: bool = False,
    transaction_epoch: str = "no-transaction",
) -> dict[str, object]:
    return {
        "cache_version": xunji_client.READ_CACHE_VERSION,
        "credential_fingerprint": hashlib.sha256(
            credential.encode("utf-8")
        ).hexdigest(),
        "datestr": datestr,
        "include_full_data": include_full_data,
        "transaction_epoch": transaction_epoch,
        "sessions": sessions,
    }


class XunjiClientTests(unittest.TestCase):
    def test_offline_plan_is_refused_before_writeback_preparation(self) -> None:
        plan = {
            "schema_version": "personalised_training_plan_v1",
            "profile_id": "synthetic-example-athlete",
            "week_start": "2030-04-08",
            "status": "draft",
            "evidence": {"unconfirmed": []},
            "sessions": [],
            "progressions": [],
            "write_control": {"remote_write_requested": False},
        }

        with self.assertRaisesRegex(
            xunji_client.PlanValidationError,
            "offline .* cannot be prepared for Xunji writeback",
        ):
            xunji_client.prepare_write(plan, client_profile())

    def test_writeback_requires_explicit_xunji_profile_policy(self) -> None:
        profile = client_profile()
        del profile["xunji"]

        with self.assertRaisesRegex(
            xunji_client.PlanValidationError,
            "profile.xunji.write_policy",
        ):
            xunji_client.prepare_write(synthetic_plan(), profile)

    def test_xunji_shadow_programme_and_offline_write_flag_are_rejected(self) -> None:
        shadowed = synthetic_plan()
        shadowed["sessions"][0]["programme"] = {
            "movements": [{"name": "Contradictory display", "sets": [{"reps": 999}]}]
        }
        with self.assertRaisesRegex(
            xunji_client.PlanValidationError, "offline shadow field"
        ):
            xunji_client.prepare_write(shadowed, client_profile())

        contradictory_control = synthetic_plan()
        contradictory_control["write_control"]["remote_write_requested"] = False
        with self.assertRaisesRegex(
            xunji_client.PlanValidationError, "offline-only"
        ):
            xunji_client.prepare_write(contradictory_control, client_profile())

    def test_malformed_unconfirmed_values_fail_closed(self) -> None:
        malformed_plan = synthetic_plan()
        malformed_plan["evidence"]["unconfirmed"] = "synthetic unresolved item"
        with self.assertRaisesRegex(
            xunji_client.PlanValidationError, "plan.evidence.unconfirmed"
        ):
            xunji_client.prepare_write(malformed_plan, client_profile())

        malformed_profile = client_profile()
        malformed_profile["unconfirmed"] = {"item": "synthetic unresolved item"}
        with self.assertRaisesRegex(
            xunji_client.PlanValidationError, "profile.unconfirmed"
        ):
            xunji_client.prepare_write(synthetic_plan(), malformed_profile)

    def setUp(self) -> None:
        transaction_directory = tempfile.TemporaryDirectory()
        self.addCleanup(transaction_directory.cleanup)
        transaction_root = Path(transaction_directory.name) / "transactions"
        patcher = mock.patch.object(
            xunji_client,
            "_fixed_transaction_cache_dir",
            return_value=transaction_root,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_prepare_write_is_deterministic_and_never_networks(self) -> None:
        plan = synthetic_plan()
        with mock.patch.object(
            xunji_client,
            "_post_json",
            side_effect=AssertionError("prepare-write must stay offline"),
        ) as network:
            first = xunji_client.prepare_write(plan, client_profile())
            second = xunji_client.prepare_write(
                copy.deepcopy(plan), client_profile()
            )

        self.assertEqual(first, second)
        network.assert_not_called()
        self.assertEqual(
            set(first["summary"][0]),
            {"date", "title", "operation", "movement_count", "set_count"},
        )
        self.assertNotIn("合成推举", json.dumps(first, ensure_ascii=False))
        self.assertNotIn('"weight"', json.dumps(first, ensure_ascii=False))

    def test_prepare_write_binds_the_complete_profile_context(self) -> None:
        plan = synthetic_plan()
        profile = client_profile()
        manifest = xunji_client.prepare_write(plan, profile)
        changed_profile = copy.deepcopy(profile)
        changed_profile["preferences"]["liked_movements"].append(
            "synthetic_carry"
        )
        opener = FakeOpener([])

        with self.assertRaisesRegex(
            xunji_client.WriteSafetyError, "reviewed manifest"
        ):
            xunji_client.write_plan(
                plan,
                manifest,
                changed_profile,
                expected_digest=manifest["payload_sha256"],
                write_confirmed=True,
                opener=opener,
                environ={},
            )
        self.assertEqual(opener.requests, [])

    def test_unconfirmed_context_and_medical_stop_state_block_preparation(self) -> None:
        plan = synthetic_plan()
        unconfirmed_profile = client_profile()
        unconfirmed_profile["unconfirmed"] = ["Synthetic unresolved choice"]
        with self.assertRaisesRegex(
            xunji_client.PlanValidationError, "Unconfirmed"
        ):
            xunji_client.prepare_write(plan, unconfirmed_profile)

        blocked_profile = client_profile()
        blocked_profile["health_and_safety"]["medical_clearance"] = (
            "required_not_cleared"
        )
        with self.assertRaisesRegex(
            xunji_client.PlanValidationError, "validation failed"
        ):
            xunji_client.prepare_write(plan, blocked_profile)

    def test_declared_progression_requires_a_bound_baseline(self) -> None:
        plan = synthetic_plan()
        plan["progressions"] = [
            {
                "movement_name": "合成推举",
                "changed_variables": ["repetitions"],
                "reason": "Synthetic progression declaration",
                "regression_gate": "Hold if completion or recovery declines",
            }
        ]
        with self.assertRaisesRegex(
            xunji_client.PlanValidationError, "baseline plan is required"
        ):
            xunji_client.prepare_write(plan, client_profile())

        with self.assertRaisesRegex(
            xunji_client.PlanValidationError, "new movement"
        ):
            xunji_client.prepare_write(plan, client_profile(), {})

        baseline = copy.deepcopy(plan)
        for set_data in baseline["sessions"][0]["xunji_payload"]["movements"][0][
            "sets"
        ]:
            set_data["reps"] = "7"
        manifest = xunji_client.prepare_write(plan, client_profile(), baseline)
        self.assertEqual(
            manifest["personalisation_validation"]["status"], "passed"
        )

        unchanged_baseline = copy.deepcopy(plan)
        with self.assertRaisesRegex(
            xunji_client.PlanValidationError, "do not match"
        ):
            xunji_client.prepare_write(
                plan, client_profile(), unchanged_baseline
            )

    def test_update_original_is_a_mandatory_progression_baseline(self) -> None:
        hidden = synthetic_plan(update=True)
        hidden["progressions"] = []
        with self.assertRaisesRegex(
            xunji_client.PlanValidationError, "dose change"
        ):
            xunji_client.prepare_write(hidden, client_profile())

        multi_variable = synthetic_plan(update=True)
        multi_variable["sessions"][0]["xunji_payload"]["movements"][0]["sets"][
            0
        ]["weight"] = "32"
        with self.assertRaisesRegex(
            xunji_client.PlanValidationError, "dose change"
        ):
            xunji_client.prepare_write(multi_variable, client_profile())

        red_flag_profile = client_profile()
        red_flag_profile["health_and_safety"]["red_flags"] = [
            "Synthetic progression stop"
        ]
        with self.assertRaisesRegex(
            xunji_client.PlanValidationError, "validation failed"
        ):
            xunji_client.prepare_write(
                synthetic_plan(update=True), red_flag_profile
            )

    def test_update_unclassified_metadata_change_fails_closed(self) -> None:
        plan = synthetic_plan(update=True)
        original_movement = plan["sessions"][0]["original_xunji_payload"][
            "movements"
        ][0]
        proposed_movement = plan["sessions"][0]["xunji_payload"]["movements"][0]
        original_movement["synthetic_unknown_dose"] = 1
        proposed_movement["synthetic_unknown_dose"] = 2

        with self.assertRaisesRegex(
            xunji_client.PlanValidationError, "unrelated or unclassified"
        ):
            xunji_client.prepare_write(plan, client_profile())

    def test_new_objects_reject_unclassified_or_completion_fields(self) -> None:
        new_set = synthetic_plan(update=True)
        session = new_set["sessions"][0]
        proposed_sets = session["xunji_payload"]["movements"][0]["sets"]
        proposed_sets[0]["reps"] = "8"
        proposed_sets.append(
            {"weight": "30", "unit": "kg", "reps": "8", "mystery": 1}
        )
        new_set["progressions"][0]["changed_variables"] = ["sets"]
        with self.assertRaisesRegex(
            xunji_client.PlanValidationError, "unclassified"
        ):
            xunji_client.prepare_write(new_set, client_profile())

        completed_set = copy.deepcopy(new_set)
        del completed_set["sessions"][0]["xunji_payload"]["movements"][0][
            "sets"
        ][-1]["mystery"]
        completed_set["sessions"][0]["xunji_payload"]["movements"][0]["sets"][
            -1
        ]["done"] = True
        with self.assertRaisesRegex(
            xunji_client.PlanValidationError, "done must be false"
        ):
            xunji_client.prepare_write(completed_set, client_profile())

        new_item = synthetic_plan(update=True)
        item_session = new_item["sessions"][0]
        item_session["xunji_payload"]["movements"][0]["sets"][0]["reps"] = "8"
        item_session["xunji_payload"]["movements"][0]["sets"][0]["items"] = [
            {"set": {"reps": "8"}, "mystery": 1}
        ]
        new_item["progressions"][0]["changed_variables"] = ["sets"]
        with self.assertRaisesRegex(
            xunji_client.PlanValidationError, "unclassified"
        ):
            xunji_client.prepare_write(new_item, client_profile())

        new_movement = synthetic_plan(update=True)
        movement_session = new_movement["sessions"][0]
        movement_session["xunji_payload"]["movements"][0]["sets"][0][
            "reps"
        ] = "8"
        movement_session["xunji_payload"]["movements"].append(
            {"name": "合成划船", "sets": [{"reps": "8"}], "mystery": 1}
        )
        new_movement["progressions"][0] = {
            "movement_name": "合成划船",
            "changed_variables": ["exercise_selection"],
            "reason": "Synthetic movement addition",
            "regression_gate": "Hold if completion or recovery declines",
        }
        with self.assertRaisesRegex(
            xunji_client.PlanValidationError, "unclassified"
        ):
            xunji_client.prepare_write(new_movement, client_profile())

    def test_added_set_with_novel_target_requires_exact_declaration(self) -> None:
        repeated_target = synthetic_plan(update=True)
        session = repeated_target["sessions"][0]
        proposed_sets = session["xunji_payload"]["movements"][0]["sets"]
        proposed_sets[0]["reps"] = "8"
        proposed_sets.append({"weight": "30", "unit": "kg", "reps": "8"})
        repeated_target["progressions"][0]["changed_variables"] = ["sets"]
        manifest = xunji_client.prepare_write(repeated_target, client_profile())
        self.assertEqual(
            manifest["personalisation_validation"]["status"], "passed"
        )

        novel_target = copy.deepcopy(repeated_target)
        novel_target["sessions"][0]["xunji_payload"]["movements"][0]["sets"][
            -1
        ]["reps"] = "10"
        with self.assertRaisesRegex(
            xunji_client.PlanValidationError, "dose change"
        ):
            xunji_client.prepare_write(novel_target, client_profile())

        mixed_target = synthetic_plan(update=True)
        mixed_session = mixed_target["sessions"][0]
        original_sets = mixed_session["original_xunji_payload"]["movements"][0][
            "sets"
        ]
        original_sets[0] = {"weight": "30", "unit": "kg", "reps": "6"}
        original_sets[1] = {"weight": "20", "unit": "kg", "reps": "10"}
        mixed_session["xunji_payload"]["movements"][0]["sets"] = copy.deepcopy(
            original_sets
        )
        mixed_session["xunji_payload"]["movements"][0]["sets"].append(
            {"weight": "30", "unit": "kg", "reps": "10"}
        )
        mixed_target["progressions"][0]["changed_variables"] = ["sets"]
        with self.assertRaisesRegex(
            xunji_client.PlanValidationError, "dose change"
        ):
            xunji_client.prepare_write(mixed_target, client_profile())

    def test_deep_nested_completion_evidence_blocks_update(self) -> None:
        plan = synthetic_plan(update=True)
        session = plan["sessions"][0]
        deep_set = {
            "reps": "8",
            "items": [
                {
                    "set": {
                        "reps": "8",
                        "items": [{"set": {"reps": "8", "done": True}}],
                    }
                }
            ],
        }
        session["original_xunji_payload"]["movements"][0]["sets"][1] = (
            copy.deepcopy(deep_set)
        )
        session["xunji_payload"]["movements"][0]["sets"][1] = copy.deepcopy(
            deep_set
        )

        with self.assertRaisesRegex(
            xunji_client.PlanValidationError, "completion evidence"
        ):
            xunji_client.prepare_write(plan, client_profile())

    def test_update_metrics_and_difficulty_use_the_bound_original(self) -> None:
        metric_plan = synthetic_plan(update=True)
        session = metric_plan["sessions"][0]
        proposed_movement = session["xunji_payload"]["movements"][0]
        original_movement = session["original_xunji_payload"]["movements"][0]
        proposed_movement["sets"][0]["reps"] = "8"
        original_movement["sets"][0]["metrics"] = {"distance": 100}
        proposed_movement["sets"][0]["metrics"] = {"distance": 120}
        metric_plan["progressions"][0]["changed_variables"] = ["distance"]

        manifest = xunji_client.prepare_write(metric_plan, client_profile())
        self.assertEqual(
            manifest["personalisation_validation"]["status"], "passed"
        )

        hidden_difficulty = copy.deepcopy(metric_plan)
        hidden_session = hidden_difficulty["sessions"][0]
        hidden_session["original_xunji_payload"]["movements"][0][
            "difficulty"
        ] = "normal"
        hidden_session["xunji_payload"]["movements"][0]["difficulty"] = "hard"
        with self.assertRaisesRegex(
            xunji_client.PlanValidationError, "dose change"
        ):
            xunji_client.prepare_write(hidden_difficulty, client_profile())

        missing_evidence = copy.deepcopy(metric_plan)
        del missing_evidence["sessions"][0]["original_xunji_payload"][
            "movements"
        ][0]["sets"][0]["metrics"]
        with self.assertRaisesRegex(
            xunji_client.PlanValidationError, "comparable evidence"
        ):
            xunji_client.prepare_write(missing_evidence, client_profile())

    def test_existing_difficulty_changes_require_the_documented_enum(self) -> None:
        plan = synthetic_plan(update=True)
        session = plan["sessions"][0]
        original_movement = session["original_xunji_payload"]["movements"][0]
        proposed_movement = session["xunji_payload"]["movements"][0]
        original_movement["difficulty"] = "legacy-imported-value"
        proposed_movement["difficulty"] = "legacy-imported-value"

        preserved_manifest = xunji_client.prepare_write(plan, client_profile())
        self.assertEqual(
            preserved_manifest["personalisation_validation"]["status"], "passed"
        )

        invalid = copy.deepcopy(plan)
        invalid["sessions"][0]["xunji_payload"]["movements"][0][
            "difficulty"
        ] = "invalid-value"
        invalid["progressions"][0]["changed_variables"] = [
            "repetitions",
            "difficulty",
        ]
        permissive_profile = client_profile()
        permissive_profile["progression"][
            "max_variables_per_movement_per_week"
        ] = 2
        with self.assertRaisesRegex(
            xunji_client.PlanValidationError, "difficulty must be easy, normal, or hard"
        ):
            xunji_client.prepare_write(invalid, permissive_profile)

        for valid_difficulty in ("easy", "normal", "hard"):
            with self.subTest(valid_difficulty=valid_difficulty):
                valid = copy.deepcopy(invalid)
                valid["sessions"][0]["xunji_payload"]["movements"][0][
                    "difficulty"
                ] = valid_difficulty
                valid_manifest = xunji_client.prepare_write(
                    valid, permissive_profile
                )
                self.assertEqual(
                    valid_manifest["personalisation_validation"]["status"],
                    "passed",
                )

        type_changed = copy.deepcopy(plan)
        type_changed["sessions"][0]["original_xunji_payload"]["movements"][0][
            "difficulty"
        ] = False
        type_changed["sessions"][0]["xunji_payload"]["movements"][0][
            "difficulty"
        ] = 0
        type_changed["progressions"][0]["changed_variables"] = [
            "repetitions",
            "difficulty",
        ]
        with self.assertRaisesRegex(
            xunji_client.PlanValidationError, "difficulty must be easy, normal, or hard"
        ):
            xunji_client.prepare_write(type_changed, permissive_profile)

        added = synthetic_plan(update=True)
        added["sessions"][0]["xunji_payload"]["movements"][0][
            "difficulty"
        ] = "invalid-value"
        added["progressions"][0]["changed_variables"] = [
            "repetitions",
            "difficulty",
        ]
        with self.assertRaisesRegex(
            xunji_client.PlanValidationError, "difficulty must be easy, normal, or hard"
        ):
            xunji_client.prepare_write(added, permissive_profile)

        added["sessions"][0]["xunji_payload"]["movements"][0][
            "difficulty"
        ] = "easy"
        with self.assertRaisesRegex(
            xunji_client.PlanValidationError, "comparable evidence"
        ):
            xunji_client.prepare_write(added, permissive_profile)

        removed = copy.deepcopy(plan)
        del removed["sessions"][0]["xunji_payload"]["movements"][0]["difficulty"]
        removed["progressions"][0]["changed_variables"] = [
            "repetitions",
            "difficulty",
        ]
        with self.assertRaisesRegex(
            xunji_client.PlanValidationError, "difficulty must be easy, normal, or hard"
        ):
            xunji_client.prepare_write(removed, permissive_profile)

    def test_nested_metric_change_cannot_hide_from_update_declarations(self) -> None:
        plan = synthetic_plan(update=True)
        session = plan["sessions"][0]
        original_set = {
            "reps": "8",
            "items": [{"set": {"reps": "8", "metrics": {"distance": 100}}}],
        }
        proposed_set = copy.deepcopy(original_set)
        proposed_set["items"][0]["set"]["metrics"]["distance"] = 120
        session["original_xunji_payload"]["movements"][0]["sets"][0] = original_set
        session["xunji_payload"]["movements"][0]["sets"][0] = proposed_set
        plan["progressions"] = []

        with self.assertRaisesRegex(
            xunji_client.PlanValidationError, "dose change"
        ):
            xunji_client.prepare_write(plan, client_profile())

    def test_literal_and_nested_metric_paths_cannot_hide_an_update(self) -> None:
        plan = synthetic_plan(update=True)
        session = plan["sessions"][0]
        session["xunji_payload"]["movements"][0]["sets"][0]["reps"] = "8"
        session["original_xunji_payload"]["movements"][0]["sets"][0][
            "metrics"
        ] = {"foo": {"bar": 1}}
        session["xunji_payload"]["movements"][0]["sets"][0]["metrics"] = {
            "foo.bar": 1
        }
        plan["progressions"] = []

        with self.assertRaisesRegex(
            xunji_client.PlanValidationError, "comparable evidence"
        ):
            xunji_client.prepare_write(plan, client_profile())

    def test_metric_root_and_empty_key_cannot_hide_an_update(self) -> None:
        plan = synthetic_plan(update=True)
        session = plan["sessions"][0]
        session["xunji_payload"]["movements"][0]["sets"][0]["reps"] = "8"
        session["original_xunji_payload"]["movements"][0]["sets"][0][
            "metrics"
        ] = 1
        session["xunji_payload"]["movements"][0]["sets"][0]["metrics"] = {
            "": 1
        }
        plan["progressions"] = []

        with self.assertRaisesRegex(
            xunji_client.PlanValidationError, "comparable evidence"
        ):
            xunji_client.prepare_write(plan, client_profile())

    def test_added_duplicate_movement_is_exercise_selection(self) -> None:
        plan = synthetic_plan(update=True)
        session = plan["sessions"][0]
        proposed_movement = session["xunji_payload"]["movements"][0]
        proposed_movement["sets"][0]["reps"] = "8"
        session["xunji_payload"]["movements"].append(
            {
                "name": "合成推举",
                "difficulty": "hard",
                "sets": [{"weight": "30", "unit": "kg", "reps": "8"}],
            }
        )
        plan["progressions"][0]["changed_variables"] = ["exercise_selection"]

        manifest = xunji_client.prepare_write(plan, client_profile())
        self.assertEqual(
            manifest["personalisation_validation"]["status"], "passed"
        )

        hidden = copy.deepcopy(plan)
        hidden["progressions"][0]["changed_variables"] = ["sets"]
        with self.assertRaisesRegex(
            xunji_client.PlanValidationError, "dose change"
        ):
            xunji_client.prepare_write(hidden, client_profile())

    def test_create_progression_name_cannot_borrow_update_baseline(self) -> None:
        plan = synthetic_plan(update=True)
        created = copy.deepcopy(plan["sessions"][0])
        created["session_key"] = "synthetic-created-session"
        created.pop("original_xunji_payload")
        created_payload = created["xunji_payload"]
        created_payload.pop("localid")
        created_payload.pop("start")
        created_payload.pop("end")
        created_payload["datestr"] = "2030-04-09"
        created_payload["title"] = "Synthetic created session"
        plan["sessions"].append(created)

        with self.assertRaisesRegex(
            xunji_client.PlanValidationError, "baseline plan is required"
        ):
            xunji_client.prepare_write(plan, two_date_profile())

        unrelated_create = copy.deepcopy(plan)
        unrelated_create["sessions"][1]["xunji_payload"]["movements"][0][
            "name"
        ] = "合成划船"
        manifest = xunji_client.prepare_write(
            unrelated_create, two_date_profile()
        )
        self.assertEqual(
            manifest["personalisation_validation"]["status"], "passed"
        )

    def test_duplicate_titles_and_non_chinese_movements_are_rejected(self) -> None:
        duplicate_plan = synthetic_plan()
        duplicate = copy.deepcopy(duplicate_plan["sessions"][0])
        duplicate["session_key"] = "synthetic-client-session-two"
        duplicate["xunji_payload"]["title"] = "synthetic strength session"
        duplicate_plan["sessions"].append(duplicate)
        with self.assertRaisesRegex(
            xunji_client.PlanValidationError, "duplicate case-insensitive titles"
        ):
            xunji_client.prepare_write(duplicate_plan, client_profile())

        english_plan = synthetic_plan()
        english_plan["sessions"][0]["xunji_payload"]["movements"][0][
            "name"
        ] = "Synthetic press"
        with self.assertRaisesRegex(
            xunji_client.PlanValidationError, "Chinese Xunji movement name"
        ):
            xunji_client.prepare_write(english_plan, client_profile())

    def test_changed_payload_invalidates_digest_before_network_or_credentials(self) -> None:
        plan = synthetic_plan()
        manifest = xunji_client.prepare_write(plan, client_profile())
        changed = copy.deepcopy(plan)
        changed["sessions"][0]["xunji_payload"]["movements"][0]["sets"][0][
            "reps"
        ] = "9"
        opener = FakeOpener([])

        with self.assertRaises(xunji_client.WriteSafetyError):
            xunji_client.write_plan(
                changed,
                manifest,
                client_profile(),
                expected_digest=manifest["payload_sha256"],
                write_confirmed=True,
                opener=opener,
                environ={},
            )
        self.assertEqual(opener.requests, [])

    def test_credential_is_header_only_and_write_stays_verify_pending(self) -> None:
        plan = synthetic_plan()
        manifest = xunji_client.prepare_write(plan, client_profile())
        opener = FakeOpener(
            [successful_read([]), {"success": True, "res": {"trains": []}}]
        )
        credential = "synthetic-secret-that-must-not-leak"

        with tempfile.TemporaryDirectory() as directory:
            cache_dir = Path(directory) / "private-cache"
            result = xunji_client.write_plan(
                plan,
                manifest,
                client_profile(),
                expected_digest=manifest["payload_sha256"],
                write_confirmed=True,
                opener=opener,
                environ={"XUNJI_API_KEY": credential},
            )

            self.assertEqual(result["status"], "verify_pending")
            account_cache = transaction_account_cache(credential)
            marker = account_cache / "write-2030-04-08-verification.json"
            marker_value = json.loads(marker.read_text(encoding="utf-8"))
            self.assertEqual(marker_value["status"], "verify_pending")
            self.assertEqual(stat.S_IMODE(account_cache.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(marker.stat().st_mode), 0o600)

        self.assertEqual(len(opener.requests), 2)
        for request in opener.requests:
            self.assertEqual(
                request.get_header("Authorization"), f"Bearer {credential}"
            )
            self.assertNotIn(credential.encode("utf-8"), request.data)
            self.assertTrue(request.full_url.startswith(xunji_client.BASE_URL + "/"))
        self.assertEqual(opener.requests[0].full_url, xunji_client.BASE_URL + xunji_client.READ_ENDPOINT)
        self.assertEqual(opener.requests[1].full_url, xunji_client.BASE_URL + xunji_client.WRITE_ENDPOINT)

    def test_write_intent_directory_is_synced_before_remote_dispatch(self) -> None:
        plan = synthetic_plan()
        profile = client_profile()
        manifest = xunji_client.prepare_write(plan, profile)
        credential = "synthetic-durability-key"
        events: list[tuple[str, object]] = []
        responses = [successful_read([]), {"success": True, "res": {"trains": []}}]
        account_cache = transaction_account_cache(credential)
        marker_path = account_cache / "write-2030-04-08-verification.json"

        def opener(request: object, timeout: int = 0) -> FakeResponse:
            del timeout
            endpoint = str(request.full_url)
            events.append(("request", endpoint))
            return FakeResponse(responses.pop(0))

        real_sync = xunji_client._fsync_directory

        def record_sync(path: Path) -> None:
            events.append(("directory_sync", Path(path)))
            real_sync(path)

        real_replace = xunji_client.os.replace

        def record_replace(source: object, destination: object) -> None:
            events.append(("replace", Path(destination)))
            real_replace(source, destination)

        with mock.patch.object(
            xunji_client, "_fsync_directory", side_effect=record_sync
        ), mock.patch.object(
            xunji_client.os, "replace", side_effect=record_replace
        ):
            result = xunji_client.write_plan(
                plan,
                manifest,
                profile,
                expected_digest=manifest["payload_sha256"],
                write_confirmed=True,
                opener=opener,
                environ={"XUNJI_API_KEY": credential},
            )

        read_index = next(
            index
            for index, event in enumerate(events)
            if event == (
                "request",
                xunji_client.BASE_URL + xunji_client.READ_ENDPOINT,
            )
        )
        write_index = next(
            index
            for index, event in enumerate(events)
            if event == (
                "request",
                xunji_client.BASE_URL + xunji_client.WRITE_ENDPOINT,
            )
        )
        replace_index = next(
            index
            for index, event in enumerate(events)
            if index > read_index and event == ("replace", marker_path)
        )
        sync_index = next(
            index
            for index, event in enumerate(events)
            if index > replace_index and event == ("directory_sync", account_cache)
        )
        self.assertLess(read_index, replace_index)
        self.assertLess(replace_index, sync_index)
        self.assertLess(sync_index, write_index)
        self.assertEqual(result["status"], "verify_pending")

    def test_write_stops_before_dispatch_when_intent_directory_sync_fails(self) -> None:
        plan = synthetic_plan()
        profile = client_profile()
        manifest = xunji_client.prepare_write(plan, profile)
        credential = "synthetic-sync-failure-key"
        opener = FakeOpener([successful_read([])])
        account_cache = transaction_account_cache(credential)
        real_sync = xunji_client._fsync_directory
        real_replace = xunji_client.os.replace
        read_finished = False
        intent_replaced = False
        failed = False

        def record_read(request: object, timeout: int = 0) -> FakeResponse:
            nonlocal read_finished
            response = opener(request, timeout=timeout)
            read_finished = True
            return response

        def fail_intent_sync(path: Path) -> None:
            nonlocal failed
            if (
                read_finished
                and intent_replaced
                and Path(path) == account_cache
                and not failed
            ):
                failed = True
                raise OSError("synthetic directory sync failure")
            real_sync(path)

        def record_replace(source: object, destination: object) -> None:
            nonlocal intent_replaced
            real_replace(source, destination)
            if Path(destination).name == "write-2030-04-08-verification.json":
                intent_replaced = True

        with mock.patch.object(
            xunji_client, "_fsync_directory", side_effect=fail_intent_sync
        ), mock.patch.object(
            xunji_client.os, "replace", side_effect=record_replace
        ):
            with self.assertRaises(xunji_client.XunjiClientError):
                xunji_client.write_plan(
                    plan,
                    manifest,
                    profile,
                    expected_digest=manifest["payload_sha256"],
                    write_confirmed=True,
                    opener=record_read,
                    environ={"XUNJI_API_KEY": credential},
                )

        self.assertTrue(failed)
        self.assertEqual(len(opener.requests), 1)
        self.assertEqual(
            opener.requests[0].full_url,
            xunji_client.BASE_URL + xunji_client.READ_ENDPOINT,
        )

    def test_first_use_transaction_directory_chain_is_synced_before_read(self) -> None:
        plan = synthetic_plan()
        profile = client_profile()
        manifest = xunji_client.prepare_write(plan, profile)
        credential = "synthetic-first-use-durability-key"
        transaction_root = xunji_client._fixed_transaction_cache_dir()
        account_cache = transaction_account_cache(credential)
        events: list[tuple[str, object]] = []
        responses = [successful_read([]), {"success": True, "res": {"trains": []}}]
        real_sync = xunji_client._fsync_directory

        def record_sync(path: Path) -> None:
            events.append(("directory_sync", Path(path)))
            real_sync(path)

        def opener(request: object, timeout: int = 0) -> FakeResponse:
            del timeout
            events.append(("request", str(request.full_url)))
            return FakeResponse(responses.pop(0))

        with mock.patch.object(
            xunji_client, "_fsync_directory", side_effect=record_sync
        ):
            xunji_client.write_plan(
                plan,
                manifest,
                profile,
                expected_digest=manifest["payload_sha256"],
                write_confirmed=True,
                opener=opener,
                environ={"XUNJI_API_KEY": credential},
            )

        read_index = events.index(
            ("request", xunji_client.BASE_URL + xunji_client.READ_ENDPOINT)
        )
        synchronised_before_read = {
            event[1]
            for event in events[:read_index]
            if event[0] == "directory_sync"
        }
        self.assertIn(transaction_root.parent, synchronised_before_read)
        self.assertIn(transaction_root, synchronised_before_read)
        self.assertIn(account_cache, synchronised_before_read)

    def test_directory_sync_helper_calls_the_operating_system_fsync(self) -> None:
        if os.name != "posix":
            self.skipTest("Directory fsync is a POSIX transaction requirement")
        real_fsync = xunji_client.os.fsync
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            xunji_client.os, "fsync", wraps=real_fsync
        ) as fsync_spy:
            xunji_client._fsync_directory(Path(directory))

        fsync_spy.assert_called_once()

    def test_ambiguous_write_is_reconciled_and_cannot_be_blindly_retried(self) -> None:
        plan = synthetic_plan()
        profile = client_profile()
        manifest = xunji_client.prepare_write(plan, profile)
        credential = "synthetic-key"
        opener = FakeOpener(
            [successful_read([]), urllib.error.URLError("synthetic timeout")]
        )

        with tempfile.TemporaryDirectory() as directory:
            cache_root = Path(directory)
            with self.assertRaisesRegex(
                xunji_client.XunjiClientError, "outcome.*ambiguous"
            ):
                xunji_client.write_plan(
                    plan,
                    manifest,
                    profile,
                    expected_digest=manifest["payload_sha256"],
                    write_confirmed=True,
                    opener=opener,
                    environ={
                        "XUNJI_API_KEY": credential,
                        "XDG_CACHE_HOME": str(cache_root / "first-root"),
                    },
                )

            account_cache = transaction_account_cache(credential)
            marker_path = account_cache / "write-2030-04-08-verification.json"
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            self.assertEqual(marker["status"], "ambiguous")

            retry_opener = FakeOpener([])
            with self.assertRaisesRegex(
                xunji_client.WriteSafetyError, "unresolved local transaction"
            ):
                xunji_client.write_plan(
                    plan,
                    manifest,
                    profile,
                    expected_digest=manifest["payload_sha256"],
                    write_confirmed=True,
                    opener=retry_opener,
                    environ={
                        "XUNJI_API_KEY": credential,
                        "XDG_CACHE_HOME": str(cache_root / "second-root"),
                    },
                )
            self.assertEqual(retry_opener.requests, [])

            actual = synthetic_payload()
            actual.update({"localid": "created-id", "start": "200", "end": "200"})
            reconciliation_opener = FakeOpener([successful_read([actual])])
            result = xunji_client.verify_plan(
                plan,
                manifest,
                profile,
                opener=reconciliation_opener,
                environ={"XUNJI_API_KEY": credential},
            )
            self.assertEqual(result["reconciled_dates"], ["2030-04-08"])
            verified_marker = json.loads(marker_path.read_text(encoding="utf-8"))
            self.assertEqual(verified_marker["status"], "fully_verified")
            self.assertEqual(verified_marker["reconciled_from"], "ambiguous")

    def test_write_intent_invalidates_read_cache_in_any_root(self) -> None:
        plan = synthetic_plan()
        profile = client_profile()
        manifest = xunji_client.prepare_write(plan, profile)
        credential = "synthetic-key"

        with tempfile.TemporaryDirectory() as directory:
            read_cache_root = Path(directory) / "movable-read-cache"
            initial_read = FakeOpener([successful_read([])])
            self.assertEqual(
                xunji_client.read_sessions(
                    "2030-04-08",
                    opener=initial_read,
                    environ={"XUNJI_API_KEY": credential},
                    cache_dir=read_cache_root,
                ),
                [],
            )

            write_opener = FakeOpener(
                [successful_read([]), {"success": True, "res": {"trains": []}}]
            )
            xunji_client.write_plan(
                plan,
                manifest,
                profile,
                expected_digest=manifest["payload_sha256"],
                write_confirmed=True,
                opener=write_opener,
                environ={"XUNJI_API_KEY": credential},
            )
            account_cache = transaction_account_cache(credential)
            marker_path = account_cache / "write-2030-04-08-verification.json"
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            self.assertEqual(marker["status"], "verify_pending")

            fresh_payload = synthetic_payload()
            refreshed_read = FakeOpener([successful_read([fresh_payload])])
            refreshed = xunji_client.read_sessions(
                "2030-04-08",
                opener=refreshed_read,
                environ={"XUNJI_API_KEY": credential},
                cache_dir=read_cache_root,
            )
            self.assertEqual(refreshed[0]["title"], fresh_payload["title"])
            self.assertEqual(len(refreshed_read.requests), 1)

            repeated_read = FakeOpener([successful_read([fresh_payload])])
            xunji_client.read_sessions(
                "2030-04-08",
                opener=repeated_read,
                environ={"XUNJI_API_KEY": credential},
                cache_dir=read_cache_root,
            )
            self.assertEqual(len(repeated_read.requests), 1)

    def test_transaction_lock_blocks_a_second_writer_before_network(self) -> None:
        plan = synthetic_plan()
        profile = client_profile()
        manifest = xunji_client.prepare_write(plan, profile)
        credential = "synthetic-key"
        opener = FakeOpener([])

        with tempfile.TemporaryDirectory() as directory:
            cache_root = Path(directory)
            account_cache = transaction_account_cache(credential)
            xunji_client._ensure_private_directory(account_cache)
            lock_path = xunji_client._transaction_lock_path(
                account_cache, "2030-04-08"
            )
            lock_path.write_text("synthetic active writer\n", encoding="utf-8")
            lock_path.chmod(0o600)
            with self.assertRaisesRegex(
                xunji_client.WriteSafetyError, "already being written or verified"
            ):
                xunji_client.write_plan(
                    plan,
                    manifest,
                    profile,
                    expected_digest=manifest["payload_sha256"],
                    write_confirmed=True,
                    opener=opener,
                    environ={"XUNJI_API_KEY": credential},
                )
            read_opener = FakeOpener([])
            with self.assertRaisesRegex(
                xunji_client.WriteSafetyError, "already being written or verified"
            ):
                xunji_client.read_sessions(
                    "2030-04-08",
                    opener=read_opener,
                    environ={"XUNJI_API_KEY": credential},
                    cache_dir=Path(directory) / "read-cache",
                )
            self.assertEqual(read_opener.requests, [])
        self.assertEqual(opener.requests, [])

    def test_marker_change_during_verification_cannot_be_marked_verified(self) -> None:
        plan = synthetic_plan()
        profile = client_profile()
        manifest = xunji_client.prepare_write(plan, profile)
        digest = manifest["payload_sha256"]
        credential = "synthetic-key"
        actual = synthetic_payload()
        actual.update({"localid": "created-id", "start": "200", "end": "200"})

        with tempfile.TemporaryDirectory() as directory:
            cache_root = Path(directory)
            account_cache = transaction_account_cache(credential)
            mark_pending(credential, digest)
            replacement_digest = "a" * 64

            def mutate_marker(request: object, timeout: int = 0) -> FakeResponse:
                del request, timeout
                xunji_client._write_transaction_marker(
                    account_cache,
                    "2030-04-08",
                    replacement_digest,
                    status="verify_pending",
                    client_request_id=xunji_client._client_request_id(
                        replacement_digest, "2030-04-08"
                    ),
                    session_count=1,
                    successful_write_requests=1,
                )
                return FakeResponse(successful_read([actual]))

            with self.assertRaisesRegex(
                xunji_client.WriteSafetyError, "state changed during read-back"
            ):
                xunji_client.verify_plan(
                    plan,
                    manifest,
                    profile,
                    opener=mutate_marker,
                    environ={"XUNJI_API_KEY": credential},
                )
            marker_path = account_cache / "write-2030-04-08-verification.json"
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            self.assertEqual(marker["payload_sha256"], replacement_digest)
            self.assertEqual(marker["status"], "verify_pending")

    def test_same_digest_marker_state_change_cannot_be_overwritten(self) -> None:
        plan = synthetic_plan()
        profile = client_profile()
        manifest = xunji_client.prepare_write(plan, profile)
        digest = manifest["payload_sha256"]
        credential = "synthetic-same-digest-cas-key"
        actual = synthetic_payload()
        actual.update({"localid": "created-id", "start": "200", "end": "200"})
        account_cache = transaction_account_cache(credential)
        mark_pending(credential, digest)

        def mutate_marker(request: object, timeout: int = 0) -> FakeResponse:
            del request, timeout
            xunji_client._write_transaction_marker(
                account_cache,
                "2030-04-08",
                digest,
                status="drifted",
                client_request_id=xunji_client._client_request_id(
                    digest, "2030-04-08"
                ),
                session_count=1,
                successful_write_requests=1,
            )
            return FakeResponse(successful_read([actual]))

        with self.assertRaisesRegex(
            xunji_client.WriteSafetyError, "state changed during read-back"
        ):
            xunji_client.verify_plan(
                plan,
                manifest,
                profile,
                opener=mutate_marker,
                environ={"XUNJI_API_KEY": credential},
            )

        marker = json.loads(
            (
                account_cache / "write-2030-04-08-verification.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(marker["payload_sha256"], digest)
        self.assertEqual(marker["status"], "drifted")

    def test_partial_multi_date_write_can_verify_and_resume_without_resend(self) -> None:
        plan = two_date_plan()
        profile = two_date_profile()
        manifest = xunji_client.prepare_write(plan, profile)
        credential = "synthetic-key"
        initial_opener = FakeOpener(
            [
                successful_read([]),
                {"success": True, "res": {"trains": []}},
                urllib.error.URLError("synthetic second-date read failure"),
            ]
        )

        with tempfile.TemporaryDirectory() as directory:
            cache_root = Path(directory)
            with self.assertRaises(xunji_client.XunjiClientError):
                xunji_client.write_plan(
                    plan,
                    manifest,
                    profile,
                    expected_digest=manifest["payload_sha256"],
                    write_confirmed=True,
                    opener=initial_opener,
                    environ={"XUNJI_API_KEY": credential},
                )
            self.assertEqual(len(initial_opener.requests), 3)

            first_actual = copy.deepcopy(
                plan["sessions"][0]["xunji_payload"]
            )
            first_actual.update(
                {"localid": "created-first", "start": "200", "end": "200"}
            )
            partial_result = xunji_client.verify_plan(
                plan,
                manifest,
                profile,
                opener=FakeOpener([successful_read([first_actual])]),
                environ={"XUNJI_API_KEY": credential},
            )
            self.assertEqual(partial_result["status"], "partially_verified")
            self.assertEqual(partial_result["dates"], ["2030-04-08"])
            self.assertEqual(
                partial_result["not_dispatched_dates"], ["2030-04-09"]
            )

            resume_opener = FakeOpener(
                [successful_read([]), {"success": True, "res": {"trains": []}}]
            )
            resume_result = xunji_client.write_plan(
                plan,
                manifest,
                profile,
                expected_digest=manifest["payload_sha256"],
                write_confirmed=True,
                opener=resume_opener,
                environ={"XUNJI_API_KEY": credential},
            )
            self.assertEqual(resume_result["status"], "verify_pending")
            self.assertEqual(
                resume_result["already_verified_dates"], ["2030-04-08"]
            )
            self.assertEqual(resume_result["dates"], ["2030-04-09"])
            self.assertEqual(len(resume_opener.requests), 2)

            second_actual = copy.deepcopy(
                plan["sessions"][1]["xunji_payload"]
            )
            second_actual.update(
                {"localid": "created-second", "start": "300", "end": "300"}
            )
            final_opener = FakeOpener(
                [successful_read([first_actual]), successful_read([second_actual])]
            )
            final_result = xunji_client.verify_plan(
                plan,
                manifest,
                profile,
                opener=final_opener,
                environ={"XUNJI_API_KEY": credential},
            )
            self.assertEqual(final_result["status"], "fully_verified")
            self.assertEqual(
                final_result["already_verified_dates"], ["2030-04-08"]
            )
            self.assertEqual(final_result["dates"], ["2030-04-08", "2030-04-09"])
            self.assertEqual(len(final_opener.requests), 2)

    def test_completed_existing_session_is_never_overwritten(self) -> None:
        plan = synthetic_plan(update=True)
        manifest = xunji_client.prepare_write(plan, client_profile())
        existing = synthetic_payload(update=True)
        existing["movements"][0]["sets"][0]["done"] = True
        opener = FakeOpener([successful_read([existing])])

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                xunji_client.WriteSafetyError, "completion evidence"
            ):
                xunji_client.write_plan(
                    plan,
                    manifest,
                    client_profile(),
                    expected_digest=manifest["payload_sha256"],
                    write_confirmed=True,
                    opener=opener,
                    environ={"XUNJI_API_KEY": "synthetic-key"},
                )

        self.assertEqual(len(opener.requests), 1)
        read_body = json.loads(opener.requests[0].data.decode("utf-8"))
        self.assertIs(read_body["include_full_data"], True)

    def test_verify_forces_full_read_and_fails_on_target_mismatch(self) -> None:
        plan = synthetic_plan()
        manifest = xunji_client.prepare_write(plan, client_profile())
        actual = synthetic_payload()
        actual.update({"localid": "created-id", "start": "200", "end": "200"})
        actual["movements"][0]["sets"][1]["reps"] = "7"
        opener = FakeOpener([successful_read([actual])])

        with tempfile.TemporaryDirectory() as directory:
            cache_root = Path(directory)
            mark_pending("synthetic-key", manifest["payload_sha256"])
            with self.assertRaises(xunji_client.VerificationError) as caught:
                xunji_client.verify_plan(
                    plan,
                    manifest,
                    client_profile(),
                    opener=opener,
                    environ={"XUNJI_API_KEY": "synthetic-key"},
                )

        self.assertTrue(any("reps differs" in item for item in caught.exception.mismatches))
        self.assertNotIn("7", str(caught.exception))
        self.assertNotIn("Synthetic press", str(caught.exception))
        request_body = json.loads(opener.requests[0].data.decode("utf-8"))
        self.assertIs(request_body["include_full_data"], True)

    def test_verify_binds_manifest_and_reports_fully_verified(self) -> None:
        plan = synthetic_plan()
        manifest = xunji_client.prepare_write(plan, client_profile())
        actual = synthetic_payload()
        actual.update({"localid": "created-id", "start": "200", "end": "200"})
        opener = FakeOpener([successful_read([actual])])

        with tempfile.TemporaryDirectory() as directory:
            cache_root = Path(directory)
            mark_pending("synthetic-key", manifest["payload_sha256"])
            result = xunji_client.verify_plan(
                plan,
                manifest,
                client_profile(),
                opener=opener,
                environ={"XUNJI_API_KEY": "synthetic-key"},
            )

        self.assertEqual(result["status"], "fully_verified")

        changed_actual = copy.deepcopy(actual)
        changed_actual["movements"][0]["sets"][0]["reps"] = "7"
        repeat_opener = FakeOpener([successful_read([changed_actual])])
        with self.assertRaises(xunji_client.VerificationError):
            xunji_client.verify_plan(
                plan,
                manifest,
                client_profile(),
                opener=repeat_opener,
                environ={"XUNJI_API_KEY": "synthetic-key"},
            )
        self.assertEqual(len(repeat_opener.requests), 1)

    def test_detected_historical_drift_blocks_the_reviewed_write(self) -> None:
        plan = synthetic_plan()
        profile = client_profile()
        manifest = xunji_client.prepare_write(plan, profile)
        credential = "synthetic-drift-key"
        actual = synthetic_payload()
        actual.update({"localid": "created-id", "start": "200", "end": "200"})
        mark_pending(credential, manifest["payload_sha256"])

        first_result = xunji_client.verify_plan(
            plan,
            manifest,
            profile,
            opener=FakeOpener([successful_read([actual])]),
            environ={"XUNJI_API_KEY": credential},
        )
        self.assertEqual(first_result["status"], "fully_verified")

        drifted = copy.deepcopy(actual)
        drifted["movements"][0]["sets"][0]["reps"] = "7"
        with self.assertRaises(xunji_client.VerificationError):
            xunji_client.verify_plan(
                plan,
                manifest,
                profile,
                opener=FakeOpener([successful_read([drifted])]),
                environ={"XUNJI_API_KEY": credential},
            )

        marker_path = (
            transaction_account_cache(credential)
            / "write-2030-04-08-verification.json"
        )
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        self.assertEqual(marker["status"], "drifted")

        no_network = FakeOpener([])
        with self.assertRaisesRegex(
            xunji_client.WriteSafetyError, "drifted|fresh review"
        ):
            xunji_client.write_plan(
                plan,
                manifest,
                profile,
                expected_digest=manifest["payload_sha256"],
                write_confirmed=True,
                opener=no_network,
                environ={"XUNJI_API_KEY": credential},
            )
        self.assertEqual(no_network.requests, [])

        recovered = xunji_client.verify_plan(
            plan,
            manifest,
            profile,
            opener=FakeOpener([successful_read([actual])]),
            environ={"XUNJI_API_KEY": credential},
        )
        self.assertEqual(recovered["status"], "fully_verified")

        historical = xunji_client.write_plan(
            plan,
            manifest,
            profile,
            expected_digest=manifest["payload_sha256"],
            write_confirmed=True,
            opener=FakeOpener([]),
            environ={"XUNJI_API_KEY": credential},
        )
        self.assertEqual(historical["status"], "fully_verified")
        self.assertEqual(historical["write_request_count"], 0)

    def test_historical_reverification_timeout_blocks_write_retry(self) -> None:
        plan = synthetic_plan()
        profile = client_profile()
        manifest = xunji_client.prepare_write(plan, profile)
        credential = "synthetic-reverify-timeout-key"
        actual = synthetic_payload()
        actual.update({"localid": "created-id", "start": "200", "end": "200"})
        mark_pending(credential, manifest["payload_sha256"])
        xunji_client.verify_plan(
            plan,
            manifest,
            profile,
            opener=FakeOpener([successful_read([actual])]),
            environ={"XUNJI_API_KEY": credential},
        )

        with self.assertRaises(xunji_client.XunjiClientError):
            xunji_client.verify_plan(
                plan,
                manifest,
                profile,
                opener=FakeOpener([urllib.error.URLError("synthetic timeout")]),
                environ={"XUNJI_API_KEY": credential},
            )

        marker_path = (
            transaction_account_cache(credential)
            / "write-2030-04-08-verification.json"
        )
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        self.assertEqual(marker["status"], "reverify_pending")
        no_network = FakeOpener([])
        with self.assertRaisesRegex(
            xunji_client.WriteSafetyError, "reverification|unresolved"
        ):
            xunji_client.write_plan(
                plan,
                manifest,
                profile,
                expected_digest=manifest["payload_sha256"],
                write_confirmed=True,
                opener=no_network,
                environ={"XUNJI_API_KEY": credential},
            )
        self.assertEqual(no_network.requests, [])

    def test_fresh_digest_can_replace_a_historical_drift_marker(self) -> None:
        plan = synthetic_plan()
        profile = client_profile()
        manifest = xunji_client.prepare_write(plan, profile)
        credential = "synthetic-fresh-review-after-drift-key"
        actual = synthetic_payload()
        actual.update({"localid": "created-id", "start": "200", "end": "200"})
        mark_pending(credential, manifest["payload_sha256"])
        xunji_client.verify_plan(
            plan,
            manifest,
            profile,
            opener=FakeOpener([successful_read([actual])]),
            environ={"XUNJI_API_KEY": credential},
        )

        drifted = copy.deepcopy(actual)
        drifted["movements"][0]["sets"][0]["reps"] = "7"
        with self.assertRaises(xunji_client.VerificationError):
            xunji_client.verify_plan(
                plan,
                manifest,
                profile,
                opener=FakeOpener([successful_read([drifted])]),
                environ={"XUNJI_API_KEY": credential},
            )

        revised_plan = copy.deepcopy(plan)
        revised_plan["sessions"][0]["xunji_payload"]["title"] = (
            "Synthetic reviewed replacement session"
        )
        revised_manifest = xunji_client.prepare_write(revised_plan, profile)
        opener = FakeOpener(
            [successful_read([drifted]), {"success": True, "res": {"trains": []}}]
        )
        result = xunji_client.write_plan(
            revised_plan,
            revised_manifest,
            profile,
            expected_digest=revised_manifest["payload_sha256"],
            write_confirmed=True,
            opener=opener,
            environ={"XUNJI_API_KEY": credential},
        )

        self.assertEqual(result["status"], "verify_pending")
        self.assertEqual(len(opener.requests), 2)
        marker = json.loads(
            (
                transaction_account_cache(credential)
                / "write-2030-04-08-verification.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(marker["payload_sha256"], revised_manifest["payload_sha256"])
        self.assertEqual(marker["status"], "verify_pending")

    def test_verify_rejects_normalised_duplicate_titles(self) -> None:
        plan = synthetic_plan()
        profile = client_profile()
        manifest = xunji_client.prepare_write(plan, profile)
        credential = "synthetic-title-key"
        actual = synthetic_payload()
        actual.update({"localid": "created-id-a", "start": "200", "end": "200"})
        duplicate = copy.deepcopy(actual)
        duplicate["title"] = "synthetic strength session"
        duplicate["localid"] = "created-id-b"
        mark_pending(credential, manifest["payload_sha256"])

        with self.assertRaisesRegex(
            xunji_client.VerificationError, "not found uniquely"
        ):
            xunji_client.verify_plan(
                plan,
                manifest,
                profile,
                opener=FakeOpener([successful_read([actual, duplicate])]),
                environ={"XUNJI_API_KEY": credential},
            )

        marker_path = (
            transaction_account_cache(credential)
            / "write-2030-04-08-verification.json"
        )
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        self.assertEqual(marker["status"], "verify_pending")

    def test_update_verification_rejects_normalised_title_sibling(self) -> None:
        plan = synthetic_plan(update=True)
        profile = client_profile()
        manifest = xunji_client.prepare_write(plan, profile)
        credential = "synthetic-update-title-key"
        actual = copy.deepcopy(plan["sessions"][0]["xunji_payload"])
        duplicate = copy.deepcopy(actual)
        duplicate["title"] = "synthetic strength session"
        duplicate["localid"] = "different-created-id"
        mark_pending(credential, manifest["payload_sha256"])

        with self.assertRaisesRegex(
            xunji_client.VerificationError, "not found uniquely"
        ):
            xunji_client.verify_plan(
                plan,
                manifest,
                profile,
                opener=FakeOpener([successful_read([actual, duplicate])]),
                environ={"XUNJI_API_KEY": credential},
            )

    def test_verification_allows_an_unrelated_title_sibling(self) -> None:
        plan = synthetic_plan()
        profile = client_profile()
        manifest = xunji_client.prepare_write(plan, profile)
        credential = "synthetic-unrelated-title-key"
        actual = synthetic_payload()
        actual.update({"localid": "created-id-a", "start": "200", "end": "200"})
        sibling = copy.deepcopy(actual)
        sibling["title"] = "Synthetic unrelated session"
        sibling["localid"] = "created-id-b"
        mark_pending(credential, manifest["payload_sha256"])

        result = xunji_client.verify_plan(
            plan,
            manifest,
            profile,
            opener=FakeOpener([successful_read([actual, sibling])]),
            environ={"XUNJI_API_KEY": credential},
        )

        self.assertEqual(result["status"], "fully_verified")

    def test_verify_rejects_a_changed_plan_before_network(self) -> None:
        plan = synthetic_plan()
        manifest = xunji_client.prepare_write(plan, client_profile())
        changed = copy.deepcopy(plan)
        changed["sessions"][0]["xunji_payload"]["title"] = "Changed draft"
        opener = FakeOpener([])

        with self.assertRaises(xunji_client.WriteSafetyError):
            xunji_client.verify_plan(
                changed,
                manifest,
                client_profile(),
                opener=opener,
                environ={},
            )
        self.assertEqual(opener.requests, [])

    def test_verify_cli_returns_nonzero_for_a_mismatch(self) -> None:
        plan = synthetic_plan()
        manifest = xunji_client.prepare_write(plan, client_profile())
        actual = synthetic_payload()
        actual.update({"localid": "created-id", "start": "200", "end": "200"})
        actual["movements"][0]["sets"][0]["weight"] = "31"
        opener = FakeOpener([successful_read([actual])])

        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            plan_path = directory_path / "plan.json"
            manifest_path = directory_path / "manifest.json"
            profile_path = directory_path / "profile.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            profile_path.write_text(json.dumps(client_profile()), encoding="utf-8")
            cache_root = directory_path / "cache"
            mark_pending("synthetic-key", manifest["payload_sha256"])
            with mock.patch.object(
                xunji_client, "_open_without_redirects", new=opener
            ), mock.patch.dict(
                os.environ, {"XUNJI_API_KEY": "synthetic-key"}
            ), contextlib.redirect_stderr(io.StringIO()):
                return_code = xunji_client.main(
                    [
                        "verify",
                        "--plan",
                        str(plan_path),
                        "--manifest",
                        str(manifest_path),
                        "--profile",
                        str(profile_path),
                    ]
                )

        self.assertEqual(return_code, xunji_client.VerificationError.exit_code)

    def test_read_cache_is_private_and_avoids_a_second_request(self) -> None:
        credential = "synthetic-key"
        opener = FakeOpener([successful_read([synthetic_payload()])])
        with tempfile.TemporaryDirectory() as directory:
            cache_dir = Path(directory) / "cache"
            first = xunji_client.read_sessions(
                "2030-04-08",
                opener=opener,
                environ={"XUNJI_API_KEY": credential},
                cache_dir=cache_dir,
            )
            second = xunji_client.read_sessions(
                "2030-04-08",
                opener=opener,
                environ={"XUNJI_API_KEY": credential},
                cache_dir=cache_dir,
            )
            account_cache = xunji_client._account_cache_dir(cache_dir, credential)
            cache_file = account_cache / "read-2030-04-08-summary.json"
            cache_value = json.loads(cache_file.read_text(encoding="utf-8"))
            self.assertEqual(stat.S_IMODE(account_cache.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(cache_file.stat().st_mode), 0o600)
            self.assertEqual(
                cache_value["credential_fingerprint"],
                hashlib.sha256(credential.encode("utf-8")).hexdigest(),
            )
            self.assertEqual(len(cache_value["credential_fingerprint"]), 64)
            self.assertEqual(cache_value["datestr"], "2030-04-08")
            self.assertIs(cache_value["include_full_data"], False)
            self.assertEqual(cache_value["transaction_epoch"], "no-transaction")

        self.assertEqual(first, second)
        self.assertEqual(len(opener.requests), 1)

    def test_read_cache_rejects_symlinked_account_directory(self) -> None:
        credential = "synthetic-cache-symlink-key"
        with tempfile.TemporaryDirectory() as directory:
            cache_root = Path(directory) / "movable-cache"
            target = Path(directory) / "untrusted-target"
            cache_root.mkdir(mode=0o700)
            target.mkdir(mode=0o700)
            account_cache = xunji_client._account_cache_dir(cache_root, credential)
            account_cache.symlink_to(target, target_is_directory=True)
            poisoned_cache = synthetic_cache_envelope(
                credential, [{"title": "Poisoned synthetic cache"}]
            )
            cache_file = target / "read-2030-04-08-summary.json"
            cache_file.write_text(json.dumps(poisoned_cache), encoding="utf-8")
            cache_file.chmod(0o600)

            with self.assertRaisesRegex(
                xunji_client.XunjiClientError, "private local directory|symbolic link"
            ):
                xunji_client.read_sessions(
                    "2030-04-08",
                    opener=FakeOpener([]),
                    environ={"XUNJI_API_KEY": credential},
                    cache_dir=cache_root,
                )

    def test_read_cache_rejects_symlinked_cache_root(self) -> None:
        if os.name != "posix":
            self.skipTest("Symbolic-link cache boundaries are exercised on POSIX")
        credential = "synthetic-cache-root-symlink-key"
        with tempfile.TemporaryDirectory() as directory:
            temporary_root = Path(directory)
            target_root = temporary_root / "private-target"
            target_root.mkdir(mode=0o700)
            cache_root = temporary_root / "movable-cache"
            cache_root.symlink_to(target_root, target_is_directory=True)
            account_cache = xunji_client._account_cache_dir(target_root, credential)
            account_cache.mkdir(mode=0o700)
            cache_file = account_cache / "read-2030-04-08-summary.json"
            cache_file.write_text(
                json.dumps(
                    synthetic_cache_envelope(
                        credential, [{"title": "Poisoned synthetic cache"}]
                    )
                ),
                encoding="utf-8",
            )
            cache_file.chmod(0o600)
            opener = FakeOpener([])

            with self.assertRaisesRegex(
                xunji_client.XunjiClientError, "private local directory"
            ):
                xunji_client.read_sessions(
                    "2030-04-08",
                    opener=opener,
                    environ={"XUNJI_API_KEY": credential},
                    cache_dir=cache_root,
                )

        self.assertEqual(opener.requests, [])

    def test_read_cache_rejects_symlinked_cache_file(self) -> None:
        credential = "synthetic-cache-file-symlink-key"
        with tempfile.TemporaryDirectory() as directory:
            temporary_root = Path(directory)
            cache_root = temporary_root / "movable-cache"
            account_cache = xunji_client._account_cache_dir(cache_root, credential)
            account_cache.mkdir(mode=0o700, parents=True)
            cache_root.chmod(0o700)
            target = temporary_root / "outside-cache.json"
            target.write_text(
                json.dumps(
                    synthetic_cache_envelope(
                        credential, [{"title": "Poisoned synthetic cache"}]
                    )
                ),
                encoding="utf-8",
            )
            target.chmod(0o600)
            cache_file = account_cache / "read-2030-04-08-summary.json"
            cache_file.symlink_to(target)
            opener = FakeOpener([])

            with self.assertRaisesRegex(
                xunji_client.XunjiClientError, "private regular file"
            ):
                xunji_client.read_sessions(
                    "2030-04-08",
                    opener=opener,
                    environ={"XUNJI_API_KEY": credential},
                    cache_dir=cache_root,
                )

        self.assertEqual(opener.requests, [])

    def test_read_cache_rejects_non_private_file_and_directory_modes(self) -> None:
        if os.name != "posix":
            self.skipTest("POSIX permission semantics are required")
        credential = "synthetic-cache-mode-key"
        for boundary in ("file", "account-directory", "root-directory"):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as directory:
                cache_root = Path(directory) / "movable-cache"
                account_cache = xunji_client._account_cache_dir(cache_root, credential)
                account_cache.mkdir(mode=0o700, parents=True)
                cache_root.chmod(0o700)
                cache_file = account_cache / "read-2030-04-08-summary.json"
                cache_file.write_text(
                    json.dumps(
                        synthetic_cache_envelope(
                            credential, [{"title": "Poisoned synthetic cache"}]
                        )
                    ),
                    encoding="utf-8",
                )
                cache_file.chmod(0o600)
                if boundary == "file":
                    cache_file.chmod(0o640)
                elif boundary == "account-directory":
                    account_cache.chmod(0o750)
                else:
                    cache_root.chmod(0o755)
                opener = FakeOpener([])

                with self.assertRaisesRegex(
                    xunji_client.XunjiClientError, "permissions are not private"
                ):
                    xunji_client.read_sessions(
                        "2030-04-08",
                        opener=opener,
                        environ={"XUNJI_API_KEY": credential},
                        cache_dir=cache_root,
                    )

                self.assertEqual(opener.requests, [])

    def test_read_cache_envelope_is_bound_to_date_mode_and_credential(self) -> None:
        credential = "synthetic-cache-binding-key"
        expected = synthetic_payload()
        expected["title"] = "Fresh synthetic network record"
        mutations = {
            "credential_fingerprint": "0" * 64,
            "datestr": "2099-01-01",
            "include_full_data": True,
            "transaction_epoch": "sha256:" + "0" * 64,
        }
        for field, wrong_value in mutations.items():
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                cache_root = Path(directory) / "movable-cache"
                account_cache = xunji_client._account_cache_dir(cache_root, credential)
                account_cache.mkdir(mode=0o700, parents=True)
                cache_root.chmod(0o700)
                poisoned_cache = synthetic_cache_envelope(
                    credential, [{"title": "Wrong synthetic cache record"}]
                )
                poisoned_cache[field] = wrong_value
                cache_file = account_cache / "read-2030-04-08-summary.json"
                cache_file.write_text(json.dumps(poisoned_cache), encoding="utf-8")
                cache_file.chmod(0o600)
                opener = FakeOpener([successful_read([expected])])

                result = xunji_client.read_sessions(
                    "2030-04-08",
                    include_full_data=False,
                    opener=opener,
                    environ={"XUNJI_API_KEY": credential},
                    cache_dir=cache_root,
                )

                self.assertEqual(
                    result[0]["title"], "Fresh synthetic network record"
                )
                self.assertEqual(len(opener.requests), 1)

    def test_plan_cannot_preconfirm_itself(self) -> None:
        plan = synthetic_plan()
        plan["write_control"]["confirmed"] = True
        with self.assertRaisesRegex(
            xunji_client.PlanValidationError, "cannot mark itself confirmed"
        ):
            xunji_client.prepare_write(plan, client_profile())

    def test_credential_hidden_in_escaped_text_is_rejected_before_network(self) -> None:
        credential = 'synthetic-secret-"quoted"'
        opener = FakeOpener([])
        with self.assertRaisesRegex(xunji_client.XunjiClientError, "body contains"):
            xunji_client._post_json(
                xunji_client.READ_ENDPOINT,
                {"note": f"Do not store {credential} here"},
                credential,
                opener=opener,
            )
        self.assertEqual(opener.requests, [])

    def test_cache_isolated_between_training_data_keys(self) -> None:
        first_payload = synthetic_payload()
        second_payload = synthetic_payload()
        second_payload["title"] = "Different synthetic account"
        opener = FakeOpener(
            [successful_read([first_payload]), successful_read([second_payload])]
        )
        with tempfile.TemporaryDirectory() as directory:
            cache_dir = Path(directory) / "cache"
            first = xunji_client.read_sessions(
                "2030-04-08",
                opener=opener,
                environ={"XUNJI_API_KEY": "synthetic-key-one"},
                cache_dir=cache_dir,
            )
            second = xunji_client.read_sessions(
                "2030-04-08",
                opener=opener,
                environ={"XUNJI_API_KEY": "synthetic-key-two"},
                cache_dir=cache_dir,
            )
        self.assertNotEqual(first[0]["title"], second[0]["title"])
        self.assertEqual(len(opener.requests), 2)

    def test_verify_detects_session_note_mismatch(self) -> None:
        plan = synthetic_plan()
        plan["sessions"][0]["xunji_payload"]["note"] = {"text": "Reviewed"}
        manifest = xunji_client.prepare_write(plan, client_profile())
        actual = copy.deepcopy(plan["sessions"][0]["xunji_payload"])
        actual["note"] = {"text": "Changed remotely"}
        actual.update({"localid": "created-id", "start": "200", "end": "200"})
        opener = FakeOpener([successful_read([actual])])
        with tempfile.TemporaryDirectory() as directory:
            cache_root = Path(directory)
            mark_pending("synthetic-key", manifest["payload_sha256"])
            with self.assertRaises(xunji_client.VerificationError) as caught:
                xunji_client.verify_plan(
                    plan,
                    manifest,
                    client_profile(),
                    opener=opener,
                    environ={"XUNJI_API_KEY": "synthetic-key"},
                )
        self.assertTrue(any("note differs" in item for item in caught.exception.mismatches))

    def test_update_cannot_omit_existing_note(self) -> None:
        plan = synthetic_plan(update=True)
        plan["sessions"][0]["original_xunji_payload"]["note"] = {
            "text": "Synthetic note to preserve"
        }
        with self.assertRaisesRegex(
            xunji_client.PlanValidationError, "omits original field note"
        ):
            xunji_client.prepare_write(plan, client_profile())

    def test_more_than_four_sessions_on_one_date_is_rejected(self) -> None:
        plan = synthetic_plan()
        wrapper = plan["sessions"][0]
        plan["sessions"] = []
        for index in range(5):
            item = copy.deepcopy(wrapper)
            item["xunji_payload"]["title"] = f"Synthetic session {index}"
            plan["sessions"].append(item)
        with self.assertRaisesRegex(xunji_client.PlanValidationError, "more than 4"):
            xunji_client.prepare_write(plan, client_profile())

    def test_verify_requires_a_matching_pending_write_before_network(self) -> None:
        plan = synthetic_plan()
        manifest = xunji_client.prepare_write(plan, client_profile())
        opener = FakeOpener([])
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                xunji_client.WriteSafetyError, "no matching transaction marker"
            ):
                xunji_client.verify_plan(
                    plan,
                    manifest,
                    client_profile(),
                    opener=opener,
                    environ={"XUNJI_API_KEY": "synthetic-key"},
                )
        self.assertEqual(opener.requests, [])

    def test_review_digest_binds_the_original_update_snapshot(self) -> None:
        plan = synthetic_plan(update=True)
        manifest = xunji_client.prepare_write(plan, client_profile())
        changed = copy.deepcopy(plan)
        changed["sessions"][0]["original_xunji_payload"]["title"] = (
            "Different imported title"
        )
        opener = FakeOpener([])
        with self.assertRaises(xunji_client.WriteSafetyError):
            xunji_client.write_plan(
                changed,
                manifest,
                client_profile(),
                expected_digest=manifest["payload_sha256"],
                write_confirmed=True,
                opener=opener,
                environ={},
            )
        self.assertEqual(opener.requests, [])

    def test_remote_update_change_requires_refresh_and_new_review(self) -> None:
        plan = synthetic_plan(update=True)
        manifest = xunji_client.prepare_write(plan, client_profile())
        changed_remote = synthetic_payload(update=True)
        changed_remote["server_metadata"] = {"revision": "new"}
        opener = FakeOpener([successful_read([changed_remote])])
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                xunji_client.WriteSafetyError, "changed in Xunji after review"
            ):
                xunji_client.write_plan(
                    plan,
                    manifest,
                    client_profile(),
                    expected_digest=manifest["payload_sha256"],
                    write_confirmed=True,
                    opener=opener,
                    environ={"XUNJI_API_KEY": "synthetic-key"},
                )
        self.assertEqual(len(opener.requests), 1)

    def test_duplicate_movement_metadata_cannot_be_silently_dropped(self) -> None:
        plan = synthetic_plan(update=True)
        original = plan["sessions"][0]["original_xunji_payload"]
        proposed = plan["sessions"][0]["xunji_payload"]
        duplicate = copy.deepcopy(original["movements"][0])
        duplicate["note"] = {"text": "Preserve the second occurrence"}
        original["movements"].append(duplicate)
        proposed["movements"].append(copy.deepcopy(duplicate))
        del proposed["movements"][1]["note"]
        with self.assertRaisesRegex(
            xunji_client.PlanValidationError, "omits original field note"
        ):
            xunji_client.prepare_write(plan, client_profile())

    def test_no_op_update_is_refused(self) -> None:
        plan = synthetic_plan(update=True)
        plan["sessions"][0]["xunji_payload"] = copy.deepcopy(
            plan["sessions"][0]["original_xunji_payload"]
        )
        with self.assertRaisesRegex(xunji_client.PlanValidationError, "no-op update"):
            xunji_client.prepare_write(plan, client_profile())

    def test_update_cannot_delete_an_original_movement_or_set(self) -> None:
        movement_plan = synthetic_plan(update=True)
        original_movement = copy.deepcopy(
            movement_plan["sessions"][0]["original_xunji_payload"]["movements"][0]
        )
        original_movement["name"] = "合成划船"
        movement_plan["sessions"][0]["original_xunji_payload"]["movements"].append(
            original_movement
        )
        with self.assertRaisesRegex(
            xunji_client.PlanValidationError, "cannot remove original movement"
        ):
            xunji_client.prepare_write(movement_plan, client_profile())

        set_plan = synthetic_plan(update=True)
        del set_plan["sessions"][0]["xunji_payload"]["movements"][0]["sets"][1]
        with self.assertRaisesRegex(
            xunji_client.PlanValidationError, "cannot remove original sets"
        ):
            xunji_client.prepare_write(set_plan, client_profile())

    def test_verify_detects_unknown_metadata_mismatch(self) -> None:
        plan = synthetic_plan(update=True)
        session = plan["sessions"][0]
        session["original_xunji_payload"]["synthetic_metadata"] = {
            "preserve": "reviewed"
        }
        session["xunji_payload"]["synthetic_metadata"] = {
            "preserve": "reviewed"
        }
        manifest = xunji_client.prepare_write(plan, client_profile())
        actual = copy.deepcopy(session["xunji_payload"])
        actual["synthetic_metadata"]["preserve"] = "changed"
        opener = FakeOpener([successful_read([actual])])
        with tempfile.TemporaryDirectory() as directory:
            cache_root = Path(directory)
            mark_pending("synthetic-key", manifest["payload_sha256"])
            with self.assertRaises(xunji_client.VerificationError) as caught:
                xunji_client.verify_plan(
                    plan,
                    manifest,
                    client_profile(),
                    opener=opener,
                    environ={"XUNJI_API_KEY": "synthetic-key"},
                )
        self.assertTrue(
            any("synthetic_metadata differs" in item for item in caught.exception.mismatches)
        )


if __name__ == "__main__":
    unittest.main()
