#!/usr/bin/env python3
"""Validate a personalised Xunji training profile without network access."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence


PROFILE_SCHEMA_VERSION = "xunji_training_profile_v1"
ALLOWED_UNITS = frozenset({"metric", "imperial"})
ALLOWED_GOAL_TYPES = frozenset(
    {
        "fat_loss",
        "hypertrophy",
        "functional_fitness",
        "strength",
        "endurance",
        "general_health",
        "custom",
    }
)
FUNCTIONAL_GOAL_TYPES = frozenset({"functional_fitness", "custom"})
WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)
XUNJI_WRITE_POLICY = "review_then_explicit_confirm"
ALLOWED_MEDICAL_CLEARANCE = frozenset(
    {
        "not_required",
        "cleared_with_constraints",
        "required_not_cleared",
        "unknown",
    }
)


ValidationResult = dict[str, list[str]]


def _is_mapping(value: Any) -> bool:
    return isinstance(value, Mapping)


def _is_list(value: Any) -> bool:
    return isinstance(value, list)


def _is_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _require_mapping(
    data: Mapping[str, Any], key: str, path: str, errors: list[str]
) -> Mapping[str, Any] | None:
    value = data.get(key)
    key_path = f"{path}.{key}"
    if value is None:
        errors.append(f"{key_path}: required field is missing")
        return None
    if not _is_mapping(value):
        errors.append(f"{key_path}: expected an object")
        return None
    return value


def _require_non_empty_string(
    data: Mapping[str, Any], key: str, path: str, errors: list[str]
) -> str | None:
    value = data.get(key)
    key_path = f"{path}.{key}"
    if value is None:
        errors.append(f"{key_path}: required field is missing")
        return None
    if not _is_non_empty_string(value):
        errors.append(f"{key_path}: expected a non-empty string")
        return None
    return value.strip()


def _validate_string_list(
    value: Any,
    path: str,
    errors: list[str],
    *,
    allow_empty: bool,
) -> list[str] | None:
    if not _is_list(value):
        errors.append(f"{path}: expected a list")
        return None
    if not allow_empty and not value:
        errors.append(f"{path}: expected at least one item")
        return None

    normalised: list[str] = []
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if not _is_non_empty_string(item):
            errors.append(f"{item_path}: expected a non-empty string")
            continue
        normalised.append(item.strip())
    return normalised


def _validate_functional_targets(
    value: Any, path: str, errors: list[str]
) -> bool:
    """Validate task-oriented targets and report whether one usable target exists."""

    if not _is_list(value):
        errors.append(f"{path}: expected a list")
        return False
    if not value:
        errors.append(f"{path}: expected at least one functional target")
        return False

    usable_target_found = False
    for index, target in enumerate(value):
        target_path = f"{path}[{index}]"
        if _is_non_empty_string(target):
            usable_target_found = True
            continue
        if not _is_mapping(target):
            errors.append(f"{target_path}: expected a non-empty string or object")
            continue

        capacity = target.get("capacity")
        task = target.get("task")
        assessment = target.get("assessment")
        if not _is_non_empty_string(capacity):
            errors.append(f"{target_path}.capacity: expected a non-empty string")
        if not _is_non_empty_string(task):
            errors.append(f"{target_path}.task: expected a non-empty string")
        if not _is_non_empty_string(assessment):
            errors.append(f"{target_path}.assessment: expected a non-empty string")
        if all(_is_non_empty_string(item) for item in (capacity, task, assessment)):
            usable_target_found = True
    return usable_target_found


def _validate_goals(
    data: Mapping[str, Any], errors: list[str], warnings: list[str]
) -> None:
    goals = data.get("goals")
    if goals is None:
        errors.append("$.goals: required field is missing")
        return
    if not _is_list(goals):
        errors.append("$.goals: expected a list")
        return
    if not goals:
        errors.append("$.goals: expected at least one goal")
        return

    priorities: list[float] = []
    goal_types: list[str] = []
    all_priorities_valid = True
    top_level_targets_are_usable = False
    if "functional_targets" in data:
        top_level_targets_are_usable = _validate_functional_targets(
            data.get("functional_targets"), "$.functional_targets", errors
        )

    for index, goal in enumerate(goals):
        goal_path = f"$.goals[{index}]"
        if not _is_mapping(goal):
            errors.append(f"{goal_path}: expected an object")
            all_priorities_valid = False
            continue

        goal_type = _require_non_empty_string(goal, "type", goal_path, errors)
        if goal_type is not None:
            if goal_type not in ALLOWED_GOAL_TYPES:
                allowed = ", ".join(sorted(ALLOWED_GOAL_TYPES))
                errors.append(
                    f"{goal_path}.type: expected one of {allowed}; got {goal_type!r}"
                )
            else:
                goal_types.append(goal_type)

        priority = goal.get("priority")
        if priority is None:
            errors.append(f"{goal_path}.priority: required field is missing")
            all_priorities_valid = False
        elif not _is_finite_number(priority):
            errors.append(f"{goal_path}.priority: expected a finite number")
            all_priorities_valid = False
        elif not 0 <= float(priority) <= 1:
            errors.append(f"{goal_path}.priority: expected a value from 0 to 1")
            all_priorities_valid = False
        else:
            priorities.append(float(priority))

        _require_non_empty_string(goal, "outcome", goal_path, errors)

        if "success_metrics" not in goal:
            errors.append(f"{goal_path}.success_metrics: required field is missing")
        else:
            _validate_string_list(
                goal.get("success_metrics"),
                f"{goal_path}.success_metrics",
                errors,
                allow_empty=False,
            )

        if goal_type in FUNCTIONAL_GOAL_TYPES:
            if "functional_targets" in goal:
                _validate_functional_targets(
                    goal.get("functional_targets"),
                    f"{goal_path}.functional_targets",
                    errors,
                )
            elif not top_level_targets_are_usable:
                errors.append(
                    f"{goal_path}: {goal_type!r} requires usable functional_targets "
                    "on the goal or at the profile top level"
                )

    if all_priorities_valid and len(priorities) == len(goals):
        priority_total = math.fsum(priorities)
        if not math.isclose(priority_total, 1.0, rel_tol=0.0, abs_tol=1e-6):
            errors.append(
                "$.goals: priorities must sum to 1; "
                f"got {format(priority_total, '.12g')}"
            )

    duplicate_types = sorted(
        goal_type for goal_type in set(goal_types) if goal_types.count(goal_type) > 1
    )
    if duplicate_types:
        warnings.append(
            "$.goals: duplicate goal types should be distinguishable by outcome; "
            f"found {', '.join(duplicate_types)}"
        )


def _validate_availability(
    data: Mapping[str, Any], errors: list[str], warnings: list[str]
) -> None:
    availability = _require_mapping(data, "availability", "$", errors)
    if availability is None:
        return

    sessions_per_week = availability.get("sessions_per_week")
    if sessions_per_week is None:
        errors.append("$.availability.sessions_per_week: required field is missing")
    elif not _is_integer(sessions_per_week):
        errors.append("$.availability.sessions_per_week: expected an integer")
    elif not 1 <= sessions_per_week <= 14:
        errors.append(
            "$.availability.sessions_per_week: expected a value from 1 to 14"
        )

    available_days: list[str] | None = None
    if "available_days" not in availability:
        errors.append("$.availability.available_days: required field is missing")
    else:
        available_days = _validate_string_list(
            availability.get("available_days"),
            "$.availability.available_days",
            errors,
            allow_empty=False,
        )
        if available_days is not None:
            available_days = [day.lower() for day in available_days]
            for index, day in enumerate(available_days):
                if day not in WEEKDAYS:
                    allowed = ", ".join(WEEKDAYS)
                    errors.append(
                        "$.availability.available_days"
                        f"[{index}]: expected one of {allowed}; got {day!r}"
                    )
            if len(set(available_days)) != len(available_days):
                warnings.append(
                    "$.availability.available_days: duplicate days are ignored"
                )

    preferred_days: list[str] | None = None
    if "preferred_days" not in availability:
        errors.append("$.availability.preferred_days: required field is missing")
    else:
        preferred_days = _validate_string_list(
            availability.get("preferred_days"),
            "$.availability.preferred_days",
            errors,
            allow_empty=False,
        )
        if preferred_days is not None:
            preferred_days = [day.lower() for day in preferred_days]
            for index, day in enumerate(preferred_days):
                if day not in WEEKDAYS:
                    allowed = ", ".join(WEEKDAYS)
                    errors.append(
                        "$.availability.preferred_days"
                        f"[{index}]: expected one of {allowed}; got {day!r}"
                    )
            if len(set(preferred_days)) != len(preferred_days):
                warnings.append(
                    "$.availability.preferred_days: duplicate days are ignored"
                )
            if available_days:
                unavailable_preferences = sorted(
                    set(preferred_days) - set(available_days)
                )
                if unavailable_preferences:
                    errors.append(
                        "$.availability.preferred_days: contains day(s) outside "
                        "available_days: " + ", ".join(unavailable_preferences)
                    )

    fixed_days: list[str] | None = None
    if "fixed_days" in availability:
        fixed_days = _validate_string_list(
            availability.get("fixed_days"),
            "$.availability.fixed_days",
            errors,
            allow_empty=True,
        )
        if fixed_days is not None:
            fixed_days = [day.lower() for day in fixed_days]
            invalid_fixed = sorted(set(fixed_days) - set(WEEKDAYS))
            if invalid_fixed:
                errors.append(
                    "$.availability.fixed_days: contains invalid day(s): "
                    + ", ".join(invalid_fixed)
                )
            if available_days:
                unavailable_fixed = sorted(set(fixed_days) - set(available_days))
                if unavailable_fixed:
                    errors.append(
                        "$.availability.fixed_days: contains day(s) outside "
                        "available_days: " + ", ".join(unavailable_fixed)
                    )

    duration = _require_mapping(
        availability, "session_duration_minutes", "$.availability", errors
    )
    if duration is not None:
        default_duration = duration.get("default")
        maximum_duration = duration.get("maximum")

        if default_duration is None:
            errors.append(
                "$.availability.session_duration_minutes.default: required field is missing"
            )
        elif not _is_integer(default_duration) or default_duration <= 0:
            errors.append(
                "$.availability.session_duration_minutes.default: "
                "expected a positive integer"
            )

        if maximum_duration is None:
            errors.append(
                "$.availability.session_duration_minutes.maximum: required field is missing"
            )
        elif not _is_integer(maximum_duration) or maximum_duration <= 0:
            errors.append(
                "$.availability.session_duration_minutes.maximum: "
                "expected a positive integer"
            )

        if (
            _is_integer(default_duration)
            and default_duration > 0
            and _is_integer(maximum_duration)
            and maximum_duration > 0
            and default_duration > maximum_duration
        ):
            errors.append(
                "$.availability.session_duration_minutes.default: "
                "must not exceed maximum"
            )

    if (
        _is_integer(sessions_per_week)
        and 1 <= sessions_per_week <= 14
        and available_days
    ):
        valid_unique_days = {day for day in available_days if day in WEEKDAYS}
        if valid_unique_days and sessions_per_week > len(valid_unique_days) * 4:
            errors.append(
                "$.availability.sessions_per_week: exceeds Xunji's capacity of "
                "4 sessions per available day"
            )
        elif valid_unique_days and sessions_per_week > len(valid_unique_days):
            warnings.append(
                "$.availability: sessions_per_week requires more than one session "
                "on at least one available day"
            )


def _validate_background_and_equipment(
    data: Mapping[str, Any], errors: list[str]
) -> None:
    training_background = data.get("training_background")
    if training_background is None:
        errors.append("$.training_background: required field is missing")
    elif not _is_mapping(training_background):
        errors.append("$.training_background: expected an object")

    equipment = data.get("equipment")
    if equipment is None:
        errors.append("$.equipment: required field is missing")
    elif not (_is_mapping(equipment) or _is_list(equipment)):
        errors.append("$.equipment: expected an object or list")


def _validate_preferences(
    data: Mapping[str, Any], errors: list[str], warnings: list[str]
) -> None:
    preferences = _require_mapping(data, "preferences", "$", errors)
    if preferences is None:
        return

    if "excluded_movements" not in preferences:
        errors.append("$.preferences.excluded_movements: required field is missing")
        return

    excluded_movements = _validate_string_list(
        preferences.get("excluded_movements"),
        "$.preferences.excluded_movements",
        errors,
        allow_empty=True,
    )
    if excluded_movements is not None:
        normalised = [movement.casefold().strip() for movement in excluded_movements]
        if len(set(normalised)) != len(normalised):
            warnings.append(
                "$.preferences.excluded_movements: duplicate movements are ignored"
            )


def _validate_health_and_safety(
    data: Mapping[str, Any], errors: list[str], warnings: list[str]
) -> None:
    health = _require_mapping(data, "health_and_safety", "$", errors)
    if health is None:
        return

    medical_clearance = health.get("medical_clearance")
    if medical_clearance is None:
        errors.append("$.health_and_safety.medical_clearance: required field is missing")
    elif (
        not isinstance(medical_clearance, str)
        or medical_clearance not in ALLOWED_MEDICAL_CLEARANCE
    ):
        allowed = ", ".join(sorted(ALLOWED_MEDICAL_CLEARANCE))
        errors.append(
            "$.health_and_safety.medical_clearance: "
            f"expected one of {allowed}; got {medical_clearance!r}"
        )

    red_flags: list[str] | None = None
    if "red_flags" not in health:
        errors.append("$.health_and_safety.red_flags: required field is missing")
    else:
        red_flags = _validate_string_list(
            health.get("red_flags"),
            "$.health_and_safety.red_flags",
            errors,
            allow_empty=True,
        )

    stop_pain_score = health.get("stop_pain_score")
    if stop_pain_score is None:
        errors.append("$.health_and_safety.stop_pain_score: required field is missing")
    elif not _is_finite_number(stop_pain_score):
        errors.append(
            "$.health_and_safety.stop_pain_score: expected a finite number"
        )
    elif not 0 <= float(stop_pain_score) <= 5:
        errors.append(
            "$.health_and_safety.stop_pain_score: expected a value from 0 to 5"
        )

    if (
        red_flags
        and (
            isinstance(medical_clearance, str)
            and medical_clearance in {"required_not_cleared", "unknown"}
        )
    ):
        warnings.append(
            "$.health_and_safety: red flags are present without confirmed "
            "medical clearance; do not generate a progression"
        )


def _validate_progression(data: Mapping[str, Any], errors: list[str]) -> None:
    progression = _require_mapping(data, "progression", "$", errors)
    if progression is None:
        return

    max_variables = progression.get("max_variables_per_movement_per_week")
    if max_variables is None:
        errors.append(
            "$.progression.max_variables_per_movement_per_week: "
            "required field is missing"
        )
    elif not _is_integer(max_variables):
        errors.append(
            "$.progression.max_variables_per_movement_per_week: expected an integer"
        )
    elif not 1 <= max_variables <= 3:
        errors.append(
            "$.progression.max_variables_per_movement_per_week: "
            "expected a value from 1 to 3"
        )

    maximum_sets = progression.get("maximum_sets_per_movement")
    if maximum_sets is None:
        errors.append(
            "$.progression.maximum_sets_per_movement: required field is missing"
        )
    elif not _is_integer(maximum_sets):
        errors.append("$.progression.maximum_sets_per_movement: expected an integer")
    elif not 1 <= maximum_sets <= 20:
        errors.append(
            "$.progression.maximum_sets_per_movement: expected a value from 1 to 20"
        )


def _validate_xunji(data: Mapping[str, Any], errors: list[str]) -> None:
    if "xunji" not in data:
        return

    xunji = _require_mapping(data, "xunji", "$", errors)
    if xunji is None:
        return

    write_policy = xunji.get("write_policy")
    if write_policy is None:
        errors.append("$.xunji.write_policy: required field is missing")
    elif write_policy != XUNJI_WRITE_POLICY:
        errors.append(
            "$.xunji.write_policy: expected "
            f"{XUNJI_WRITE_POLICY!r}; got {write_policy!r}"
        )


def validate_profile_data(data: Any) -> ValidationResult:
    """Return deterministic profile validation errors and warnings.

    The function never reads files or calls the Xunji API.  Callers may therefore
    use it before displaying or persisting any proposed profile.
    """

    errors: list[str] = []
    warnings: list[str] = []

    if not _is_mapping(data):
        return {
            "errors": ["$: expected an object"],
            "warnings": [],
        }

    schema_version = data.get("schema_version")
    if schema_version is None:
        errors.append("$.schema_version: required field is missing")
    elif schema_version != PROFILE_SCHEMA_VERSION:
        errors.append(
            f"$.schema_version: expected {PROFILE_SCHEMA_VERSION!r}; "
            f"got {schema_version!r}"
        )

    _require_non_empty_string(data, "profile_id", "$", errors)
    _require_non_empty_string(data, "locale", "$", errors)
    _require_non_empty_string(data, "timezone", "$", errors)

    units = data.get("units")
    if units is None:
        errors.append("$.units: required field is missing")
    elif not isinstance(units, str) or units not in ALLOWED_UNITS:
        allowed = ", ".join(sorted(ALLOWED_UNITS))
        errors.append(f"$.units: expected one of {allowed}; got {units!r}")

    _validate_goals(data, errors, warnings)
    _validate_availability(data, errors, warnings)
    _validate_background_and_equipment(data, errors)
    _validate_preferences(data, errors, warnings)
    _validate_health_and_safety(data, errors, warnings)
    _validate_progression(data, errors)
    if "unconfirmed" in data:
        _validate_string_list(
            data.get("unconfirmed"),
            "$.unconfirmed",
            errors,
            allow_empty=True,
        )
    _validate_xunji(data, errors)

    return {"errors": errors, "warnings": warnings}


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate a xunji_training_profile_v1 JSON document locally."
    )
    parser.add_argument("profile", type=Path, help="Path to the profile JSON file")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_argument_parser()
    arguments = parser.parse_args(argv)

    try:
        data = _load_json(arguments.profile)
    except (OSError, json.JSONDecodeError) as error:
        result: ValidationResult = {
            "errors": [f"{arguments.profile}: could not read valid JSON: {error}"],
            "warnings": [],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 2

    result = validate_profile_data(data)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
