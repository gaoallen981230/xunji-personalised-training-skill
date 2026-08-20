#!/usr/bin/env python3
"""Validate a draft personalised weekly plan without network access."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

try:
    from .validate_profile import (
        ALLOWED_GOAL_TYPES,
        FUNCTIONAL_GOAL_TYPES,
        WEEKDAYS,
        validate_profile_data,
    )
except ImportError:  # Support direct execution from the scripts directory.
    from validate_profile import (  # type: ignore[no-redef]
        ALLOWED_GOAL_TYPES,
        FUNCTIONAL_GOAL_TYPES,
        WEEKDAYS,
        validate_profile_data,
    )


PLAN_SCHEMA_VERSION = "xunji_weekly_plan_v1"
OFFLINE_PLAN_SCHEMA_VERSION = "personalised_training_plan_v1"
TARGET_FIELDS = ("weight", "weight_kg", "reps", "time", "duration_s", "selfWeight")
OFFLINE_TARGET_FIELDS = TARGET_FIELDS + (
    "distance",
    "distance_m",
    "calories",
    "kcal",
    "metrics",
)
API_MAX_SESSIONS_PER_DAY = 4
API_MAX_MOVEMENTS_PER_SESSION = 15
API_MAX_SETS_PER_MOVEMENT = 20

VARIABLE_ALIASES = {
    "set": "sets",
    "sets": "sets",
    "set_count": "sets",
    "volume_sets": "sets",
    "weight": "load",
    "weight_kg": "load",
    "load": "load",
    "repetition": "reps",
    "repetitions": "reps",
    "rep": "reps",
    "reps": "reps",
    "time": "duration",
    "duration": "duration",
    "duration_s": "duration",
    "distance": "distance",
    "distance_m": "distance",
    "calorie": "calories",
    "calories": "calories",
    "kcal": "calories",
    "heart_rate": "intensity",
    "heartrate": "intensity",
    "hr": "intensity",
    "intensity": "intensity",
    "difficulty": "difficulty",
    "exercise_selection": "exercise_selection",
    "movement_selection": "exercise_selection",
    "rest": "density",
    "rest_s": "density",
    "density": "density",
    "tempo": "tempo",
    "range": "range",
    "range_of_motion": "range",
}

METRIC_VARIABLE_ALIASES = {
    "distance": "distance",
    "distance_m": "distance",
    "workout_time": "duration",
    "workouttime": "duration",
    "duration": "duration",
    "duration_s": "duration",
    "time": "duration",
    "calorie": "calories",
    "calories": "calories",
    "kcal": "calories",
    "heart_rate": "intensity",
    "heartrate": "intensity",
    "average_heart_rate": "intensity",
    "max_heart_rate": "intensity",
    "hr": "intensity",
    "intensity": "intensity",
}

SET_AUXILIARY_VARIABLE_FIELDS = {
    "distance": "distance",
    "distance_m": "distance",
    "rest": "density",
    "rest_s": "density",
    "tempo": "tempo",
    "range": "range",
    "range_of_motion": "range",
}

ValidationResult = dict[str, list[str]]
TrainingSession = tuple[Mapping[str, Any], str]


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


def _has_present_entries(value: Any) -> bool:
    """Return whether an unconfirmed field contains a user-visible item."""

    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if _is_mapping(value):
        return any(_has_present_entries(item) for item in value.values())
    if _is_list(value):
        return any(_has_present_entries(item) for item in value)
    return True


def _find_key_paths(
    value: Any,
    forbidden_keys: set[str],
    path: str = "$",
) -> Iterable[str]:
    """Yield paths for forbidden keys at any depth in a JSON-like document."""

    if _is_mapping(value):
        for key, item in value.items():
            key_text = str(key)
            item_path = f"{path}.{key_text}"
            if key_text in forbidden_keys:
                yield item_path
            yield from _find_key_paths(item, forbidden_keys, item_path)
    elif _is_list(value):
        for index, item in enumerate(value):
            yield from _find_key_paths(item, forbidden_keys, f"{path}[{index}]")


def _normalise_name(value: str) -> str:
    return " ".join(value.casefold().split())


def _normalise_variable(value: str) -> str:
    normalised = _normalise_name(value).replace(" ", "_")
    return VARIABLE_ALIASES.get(normalised, normalised)


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

    strings: list[str] = []
    for index, item in enumerate(value):
        if not _is_non_empty_string(item):
            errors.append(f"{path}[{index}]: expected a non-empty string")
            continue
        strings.append(item.strip())
    return strings


def _parse_date(value: Any, path: str, errors: list[str]) -> date | None:
    if not _is_non_empty_string(value):
        errors.append(f"{path}: expected an ISO date string in YYYY-MM-DD format")
        return None
    try:
        parsed = date.fromisoformat(value.strip())
    except ValueError:
        errors.append(f"{path}: expected an ISO date string in YYYY-MM-DD format")
        return None
    if parsed.isoformat() != value.strip():
        errors.append(f"{path}: expected an ISO date string in YYYY-MM-DD format")
        return None
    return parsed


def _extract_list_sessions(
    value: Any, path: str, errors: list[str]
) -> list[TrainingSession]:
    if not _is_list(value):
        errors.append(f"{path}: expected a list")
        return []
    if not value:
        errors.append(f"{path}: expected at least one Xunji session")
        return []

    sessions: list[TrainingSession] = []
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if not _is_mapping(item):
            errors.append(f"{item_path}: expected an object")
            continue
        sessions.append((item, item_path))
    return sessions


def _extract_payload_sessions(
    payload: Mapping[str, Any], path: str, errors: list[str]
) -> list[TrainingSession]:
    """Return direct or wrapped Xunji sessions with stable source paths."""

    if "res" in payload:
        response = payload.get("res")
        response_path = f"{path}.res"
        if _is_list(response):
            return _extract_list_sessions(response, response_path, errors)
        if _is_mapping(response):
            if "trains" in response:
                return _extract_list_sessions(
                    response.get("trains"), f"{response_path}.trains", errors
                )
            if "movements" in response:
                return [(response, response_path)]
            errors.append(
                f"{response_path}: expected a session object or an object with trains"
            )
            return []
        errors.append(f"{response_path}: expected a list or object")
        return []

    if "trains" in payload:
        return _extract_list_sessions(payload.get("trains"), f"{path}.trains", errors)

    return [(payload, path)]


def _extract_payload_sessions_quiet(payload: Any) -> list[Mapping[str, Any]]:
    if not _is_mapping(payload):
        return []
    if "res" in payload:
        response = payload.get("res")
        if _is_list(response):
            return [item for item in response if _is_mapping(item)]
        if _is_mapping(response):
            trains = response.get("trains")
            if _is_list(trains):
                return [item for item in trains if _is_mapping(item)]
            if "movements" in response:
                return [response]
        return []
    trains = payload.get("trains")
    if _is_list(trains):
        return [item for item in trains if _is_mapping(item)]
    return [payload]


def _target_is_present(set_data: Mapping[str, Any], field: str) -> bool:
    if field not in set_data:
        return False
    value = set_data.get(field)
    if field == "selfWeight":
        return value is True
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, float) and not math.isfinite(value):
        return False
    return not isinstance(value, bool)


def _set_has_target(set_data: Mapping[str, Any]) -> bool:
    return any(_target_is_present(set_data, field) for field in TARGET_FIELDS)


def _offline_metrics_have_target(value: Any) -> bool:
    if _is_mapping(value):
        return bool(value) and any(
            _offline_metrics_have_target(item) for item in value.values()
        )
    if _is_list(value):
        return bool(value) and any(_offline_metrics_have_target(item) for item in value)
    if isinstance(value, str):
        return bool(value.strip())
    return _is_finite_number(value)


def _offline_set_has_target(set_data: Mapping[str, Any]) -> bool:
    direct_fields = OFFLINE_TARGET_FIELDS[:-1]
    if any(_target_is_present(set_data, field) for field in direct_fields):
        return True
    return "metrics" in set_data and _offline_metrics_have_target(
        set_data.get("metrics")
    )


def _validate_nested_set_targets(
    set_data: Mapping[str, Any], path: str, errors: list[str]
) -> bool:
    if _set_has_target(set_data):
        return True

    items = set_data.get("items")
    if not _is_list(items) or not items:
        return False

    all_children_valid = True
    for item_index, item in enumerate(items):
        item_path = f"{path}.items[{item_index}]"
        if not _is_mapping(item):
            errors.append(f"{item_path}: expected an object")
            all_children_valid = False
            continue
        child_set = item.get("set")
        if not _is_mapping(child_set):
            errors.append(f"{item_path}.set: expected an object")
            all_children_valid = False
            continue
        if not _set_has_target(child_set):
            allowed = ", ".join(TARGET_FIELDS)
            errors.append(
                f"{item_path}.set: expected at least one target field ({allowed})"
            )
            all_children_valid = False
    return all_children_valid


def _validate_nested_offline_set_targets(
    set_data: Mapping[str, Any], path: str, errors: list[str]
) -> bool:
    if _offline_set_has_target(set_data):
        return True

    items = set_data.get("items")
    if not _is_list(items) or not items:
        return False

    all_children_valid = True
    for item_index, item in enumerate(items):
        item_path = f"{path}.items[{item_index}]"
        if not _is_mapping(item):
            errors.append(f"{item_path}: expected an object")
            all_children_valid = False
            continue
        child_set = item.get("set")
        if not _is_mapping(child_set):
            errors.append(f"{item_path}.set: expected an object")
            all_children_valid = False
            continue
        if not _validate_nested_offline_set_targets(
            child_set, f"{item_path}.set", errors
        ):
            allowed = ", ".join(OFFLINE_TARGET_FIELDS)
            errors.append(
                f"{item_path}.set: expected at least one dose target ({allowed})"
            )
            all_children_valid = False
    return all_children_valid


def _profile_values(profile: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return profile values in a named mapping to keep validation readable."""

    if profile is None:
        return {
            "goal_types": set(),
            "functional_goal_types": set(),
            "excluded_movements": set(),
            "sessions_per_week": None,
            "default_duration": None,
            "maximum_duration": None,
            "max_variables": None,
            "profile_id": None,
            "maximum_sets": None,
            "available_days": set(),
            "preferred_days": set(),
            "red_flags": [],
            "medical_clearance": None,
        }

    goals = profile.get("goals")
    goal_types = {
        goal.get("type")
        for goal in goals
        if _is_mapping(goal)
        and isinstance(goal.get("type"), str)
        and goal.get("type") in ALLOWED_GOAL_TYPES
    } if _is_list(goals) else set()

    preferences = profile.get("preferences")
    excluded_values = (
        preferences.get("excluded_movements")
        if _is_mapping(preferences)
        else None
    )
    excluded_movements = {
        _normalise_name(value)
        for value in excluded_values
        if _is_non_empty_string(value)
    } if _is_list(excluded_values) else set()

    availability = profile.get("availability")
    duration = (
        availability.get("session_duration_minutes")
        if _is_mapping(availability)
        else None
    )
    preferred_values = (
        availability.get("preferred_days") if _is_mapping(availability) else None
    )
    available_values = (
        availability.get("available_days") if _is_mapping(availability) else None
    )
    progression = profile.get("progression")
    health_and_safety = profile.get("health_and_safety")
    red_flags = (
        health_and_safety.get("red_flags")
        if _is_mapping(health_and_safety)
        else None
    )

    return {
        "goal_types": set(goal_types),
        "functional_goal_types": set(goal_types) & set(FUNCTIONAL_GOAL_TYPES),
        "excluded_movements": excluded_movements,
        "sessions_per_week": (
            availability.get("sessions_per_week")
            if _is_mapping(availability)
            and _is_integer(availability.get("sessions_per_week"))
            else None
        ),
        "default_duration": (
            duration.get("default")
            if _is_mapping(duration) and _is_integer(duration.get("default"))
            else None
        ),
        "maximum_duration": (
            duration.get("maximum")
            if _is_mapping(duration) and _is_integer(duration.get("maximum"))
            else None
        ),
        "max_variables": (
            progression.get("max_variables_per_movement_per_week")
            if _is_mapping(progression)
            and _is_integer(
                progression.get("max_variables_per_movement_per_week")
            )
            else None
        ),
        "maximum_sets": (
            progression.get("maximum_sets_per_movement")
            if _is_mapping(progression)
            and _is_integer(progression.get("maximum_sets_per_movement"))
            else None
        ),
        "profile_id": (
            profile.get("profile_id")
            if _is_non_empty_string(profile.get("profile_id"))
            else None
        ),
        "preferred_days": {
            value.strip().casefold()
            for value in preferred_values
            if _is_non_empty_string(value)
        } if _is_list(preferred_values) else set(),
        "available_days": {
            value.strip().casefold()
            for value in available_values
            if _is_non_empty_string(value)
        } if _is_list(available_values) else set(),
        "red_flags": red_flags if _is_list(red_flags) else [],
        "medical_clearance": (
            health_and_safety.get("medical_clearance")
            if _is_mapping(health_and_safety)
            else None
        ),
    }


def _derive_date_from_day(
    day_value: Any,
    week_start: date | None,
    path: str,
    errors: list[str],
) -> date | None:
    if day_value is None:
        return None
    if not _is_non_empty_string(day_value):
        errors.append(f"{path}: expected a weekday string")
        return None
    day_name = day_value.strip().casefold()
    if day_name not in WEEKDAYS:
        errors.append(f"{path}: expected one of {', '.join(WEEKDAYS)}")
        return None
    if week_start is None:
        return None
    offset = (WEEKDAYS.index(day_name) - week_start.weekday()) % 7
    return week_start + timedelta(days=offset)


def _session_date(
    plan_session: Mapping[str, Any],
    payload: Mapping[str, Any],
    xunji_session: Mapping[str, Any],
    xunji_path: str,
    plan_session_path: str,
    week_start: date | None,
    errors: list[str],
) -> date | None:
    date_sources = (
        (xunji_session.get("datestr"), f"{xunji_path}.datestr"),
        (payload.get("datestr"), f"{plan_session_path}.xunji_payload.datestr"),
        (plan_session.get("datestr"), f"{plan_session_path}.datestr"),
        (plan_session.get("date"), f"{plan_session_path}.date"),
    )
    for value, value_path in date_sources:
        if value is not None:
            return _parse_date(value, value_path, errors)

    derived = _derive_date_from_day(
        plan_session.get("day"),
        week_start,
        f"{plan_session_path}.day",
        errors,
    )
    if derived is not None:
        return derived

    errors.append(
        f"{xunji_path}.datestr: required to validate availability and daily limits"
    )
    return None


def _validate_session_schedule(
    session_date: date,
    date_path: str,
    week_start: date | None,
    context: Mapping[str, Any],
    daily_counts: Counter[str],
    errors: list[str],
    warnings: list[str],
) -> None:
    date_key = session_date.isoformat()
    daily_counts[date_key] += 1
    weekday = WEEKDAYS[session_date.weekday()]
    if context["available_days"] and weekday not in context["available_days"]:
        errors.append(f"{date_path}: {weekday} is not an available training day")
    elif context["preferred_days"] and weekday not in context["preferred_days"]:
        warnings.append(f"{date_path}: {weekday} is available but not preferred")
    if week_start is not None and not (
        week_start <= session_date <= week_start + timedelta(days=6)
    ):
        errors.append(f"{date_path}: date lies outside the declared plan week")


def _validate_movements(
    xunji_session: Mapping[str, Any],
    path: str,
    errors: list[str],
    excluded_movements: set[str],
    maximum_sets: int | None,
    *,
    enforce_xunji_limits: bool = True,
    offline_targets: bool = False,
) -> None:
    movements = xunji_session.get("movements")
    movements_path = f"{path}.movements"
    if not _is_list(movements):
        errors.append(f"{movements_path}: expected a list")
        return
    if not movements:
        errors.append(f"{movements_path}: expected at least one movement")
        return
    if enforce_xunji_limits and len(movements) > API_MAX_MOVEMENTS_PER_SESSION:
        errors.append(
            f"{movements_path}: Xunji allows at most "
            f"{API_MAX_MOVEMENTS_PER_SESSION} movements per session; got {len(movements)}"
        )

    for movement_index, movement in enumerate(movements):
        movement_path = f"{movements_path}[{movement_index}]"
        if not _is_mapping(movement):
            errors.append(f"{movement_path}: expected an object")
            continue

        movement_name = movement.get("name")
        if not _is_non_empty_string(movement_name):
            errors.append(f"{movement_path}.name: expected a non-empty string")
        elif _normalise_name(movement_name) in excluded_movements:
            errors.append(
                f"{movement_path}.name: excluded movement {movement_name!r} is not allowed"
            )

        sets = movement.get("sets")
        sets_path = f"{movement_path}.sets"
        if not _is_list(sets):
            errors.append(f"{sets_path}: expected a list")
            continue
        if not sets:
            errors.append(f"{sets_path}: expected at least one set")
            continue
        if enforce_xunji_limits and len(sets) > API_MAX_SETS_PER_MOVEMENT:
            errors.append(
                f"{sets_path}: Xunji allows at most "
                f"{API_MAX_SETS_PER_MOVEMENT} sets per movement; got {len(sets)}"
            )
        if maximum_sets is not None and len(sets) > maximum_sets:
            errors.append(
                f"{sets_path}: exceeds profile maximum_sets_per_movement "
                f"of {maximum_sets}; got {len(sets)}"
            )

        for set_index, set_data in enumerate(sets):
            set_path = f"{sets_path}[{set_index}]"
            if not _is_mapping(set_data):
                errors.append(f"{set_path}: expected an object")
                continue
            if offline_targets:
                target_is_valid = _validate_nested_offline_set_targets(
                    set_data, set_path, errors
                )
                allowed = ", ".join(OFFLINE_TARGET_FIELDS)
                target_name = "dose target"
            else:
                target_is_valid = _validate_nested_set_targets(
                    set_data, set_path, errors
                )
                allowed = ", ".join(TARGET_FIELDS)
                target_name = "target field"
            if not target_is_valid:
                errors.append(
                    f"{set_path}: expected at least one {target_name} ({allowed})"
                )


def _extract_progression_variables(
    progression: Mapping[str, Any], path: str, errors: list[str]
) -> set[str]:
    raw_variables: Any = None
    raw_path = f"{path}.variables"
    for key in ("variables", "changed_variables", "variables_changed", "variable"):
        if key in progression:
            raw_variables = progression.get(key)
            raw_path = f"{path}.{key}"
            break

    if raw_variables is None and "changes" in progression:
        changes = progression.get("changes")
        raw_path = f"{path}.changes"
        if _is_mapping(changes):
            raw_variables = list(changes.keys())
        else:
            raw_variables = changes

    if raw_variables is None:
        errors.append(f"{raw_path}: required field is missing")
        return set()

    if _is_non_empty_string(raw_variables):
        values = [raw_variables.strip()]
    elif _is_list(raw_variables):
        values = []
        for index, value in enumerate(raw_variables):
            if not _is_non_empty_string(value):
                errors.append(f"{raw_path}[{index}]: expected a non-empty string")
                continue
            values.append(value.strip())
    else:
        errors.append(f"{raw_path}: expected a string or list")
        return set()

    if not values:
        errors.append(f"{raw_path}: expected at least one progression variable")
        return set()
    return {_normalise_variable(value) for value in values}


def _validate_progressions(
    plan: Mapping[str, Any],
    errors: list[str],
    max_variables: int | None,
) -> dict[str, set[str]]:
    progressions = plan.get("progressions")
    if progressions is None:
        errors.append("$.progressions: required field is missing")
        return {}
    if not _is_list(progressions):
        errors.append("$.progressions: expected a list")
        return {}

    by_movement: dict[str, set[str]] = {}
    display_names: dict[str, str] = {}
    for index, progression in enumerate(progressions):
        progression_path = f"$.progressions[{index}]"
        if not _is_mapping(progression):
            errors.append(f"{progression_path}: expected an object")
            continue

        movement_name: str | None = None
        movement_path = f"{progression_path}.movement_name"
        for key in ("movement_name", "movement", "name"):
            if key in progression:
                movement_path = f"{progression_path}.{key}"
                value = progression.get(key)
                if _is_non_empty_string(value):
                    movement_name = value.strip()
                else:
                    errors.append(f"{movement_path}: expected a non-empty string")
                break
        if movement_name is None and not any(
            key in progression for key in ("movement_name", "movement", "name")
        ):
            errors.append(f"{movement_path}: required field is missing")

        variables = _extract_progression_variables(progression, progression_path, errors)
        if movement_name is None:
            continue
        normalised_name = _normalise_name(movement_name)
        display_names.setdefault(normalised_name, movement_name)
        by_movement.setdefault(normalised_name, set()).update(variables)

    effective_limit = max_variables if max_variables is not None else 3
    for normalised_name in sorted(by_movement):
        variables = by_movement[normalised_name]
        if len(variables) > effective_limit:
            errors.append(
                "$.progressions: movement "
                f"{display_names[normalised_name]!r} changes {len(variables)} variables "
                f"({', '.join(sorted(variables))}); maximum is {effective_limit}"
            )
    return by_movement


def _canonical_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return f"boolean:{str(value).lower()}"
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        decimal_value = Decimal(str(value)).normalize()
        return f"number:{format(decimal_value, 'f')}"
    if isinstance(value, str):
        stripped = value.strip()
        try:
            decimal_value = Decimal(stripped).normalize()
        except InvalidOperation:
            return f"text:{stripped}"
        if decimal_value.is_finite():
            return f"number:{format(decimal_value, 'f')}"
        return f"text:{stripped}"
    return f"json:{json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)}"


def _iter_set_paths(
    set_data: Mapping[str, Any], path: tuple[int, ...]
) -> Iterable[tuple[int, ...]]:
    """Yield every parent and nested set position."""

    yield path
    items = set_data.get("items")
    if not _is_list(items):
        return
    for item_index, item in enumerate(items):
        if not _is_mapping(item) or not _is_mapping(item.get("set")):
            continue
        yield from _iter_set_paths(item["set"], path + (item_index,))


def _metric_variable(path: tuple[str, ...]) -> str:
    normalised_path = tuple(
        _normalise_name(part).replace(" ", "_") for part in path
    )
    leaf = normalised_path[-1] if normalised_path else "unknown"
    return METRIC_VARIABLE_ALIASES.get(
        leaf, "metric:" + _metric_pointer(normalised_path)
    )


def _metric_pointer(path: Sequence[str]) -> str:
    """Encode metric paths without literal-key versus nesting collisions."""

    if not path:
        return ""
    escaped = [part.replace("~", "~0").replace("/", "~1") for part in path]
    return "/" + "/".join(escaped)


def _iter_metric_signatures(
    value: Any,
    set_path: tuple[int, ...],
    metric_path: tuple[str, ...] = (),
) -> Iterable[tuple[str, tuple[int, ...], str, str]]:
    """Flatten metric leaves into typed progression signatures."""

    if _is_mapping(value):
        for key in sorted(value, key=lambda item: str(item)):
            yield from _iter_metric_signatures(
                value[key], set_path, metric_path + (str(key),)
            )
        return
    variable = _metric_variable(metric_path)
    field_name = "metrics" + _metric_pointer(metric_path)
    yield variable, set_path, field_name, _canonical_scalar(value)


def _iter_set_dose_signatures(
    set_data: Mapping[str, Any], set_path: tuple[int, ...]
) -> Iterable[tuple[str, tuple[int, ...], str, str]]:
    """Yield every typed dose field at every nested set depth."""

    target_groups = (
        ("load", ("weight_kg", "weight", "selfWeight", "unit")),
        ("reps", ("reps",)),
        ("duration", ("duration_s", "time")),
        ("calories", ("calories", "kcal")),
    )
    for variable, fields in target_groups:
        for field in fields:
            if _target_is_present(set_data, field):
                yield (
                    variable,
                    set_path,
                    field,
                    _canonical_scalar(set_data.get(field)),
                )
    for field, variable in SET_AUXILIARY_VARIABLE_FIELDS.items():
        if _target_is_present(set_data, field):
            yield (
                variable,
                set_path,
                field,
                _canonical_scalar(set_data.get(field)),
            )
    if "metrics" in set_data:
        yield from _iter_metric_signatures(set_data.get("metrics"), set_path)

    items = set_data.get("items")
    if not _is_list(items):
        return
    for item_index, item in enumerate(items):
        if not _is_mapping(item) or not _is_mapping(item.get("set")):
            continue
        yield from _iter_set_dose_signatures(
            item["set"], set_path + (item_index,)
        )


def _iter_document_sessions(document: Any) -> Iterable[Mapping[str, Any]]:
    if _is_list(document):
        for item in document:
            if _is_mapping(item):
                if "xunji_payload" in item:
                    yield from _extract_payload_sessions_quiet(item.get("xunji_payload"))
                elif _is_mapping(item.get("programme")):
                    yield item["programme"]
                elif "movements" in item:
                    yield item
        return

    if not _is_mapping(document):
        return

    plan_sessions = document.get("sessions")
    if _is_list(plan_sessions):
        is_offline_document = (
            document.get("schema_version") == OFFLINE_PLAN_SCHEMA_VERSION
        )
        for plan_session in plan_sessions:
            if not _is_mapping(plan_session):
                continue
            programme = plan_session.get("programme")
            if is_offline_document and _is_mapping(programme):
                yield programme
            else:
                yield from _extract_payload_sessions_quiet(
                    plan_session.get("xunji_payload")
                )
        return

    yield from _extract_payload_sessions_quiet(document)


def _summarise_movements(document: Any) -> dict[str, dict[str, Any]]:
    summaries: dict[str, dict[str, Any]] = {}
    for training_session in _iter_document_sessions(document):
        movements = training_session.get("movements")
        if not _is_list(movements):
            continue
        for movement in movements:
            if not _is_mapping(movement) or not _is_non_empty_string(
                movement.get("name")
            ):
                continue
            display_name = movement.get("name").strip()
            normalised_name = _normalise_name(display_name)
            summary = summaries.setdefault(
                normalised_name,
                {
                    "display_name": display_name,
                    "movement_paths": [],
                    "set_paths": [],
                    "variables": {},
                    "occurrence_count": 0,
                },
            )
            occurrence_index = summary["occurrence_count"]
            summary["occurrence_count"] += 1
            movement_path = (occurrence_index,)
            summary["movement_paths"].append(movement_path)
            if _is_non_empty_string(movement.get("difficulty")):
                summary["variables"].setdefault("difficulty", []).append(
                    (
                        movement_path,
                        "difficulty",
                        _canonical_scalar(movement.get("difficulty")),
                    )
                )
            sets = movement.get("sets")
            if not _is_list(sets):
                continue
            for set_index, set_data in enumerate(sets):
                if not _is_mapping(set_data):
                    continue
                target_path = (occurrence_index, set_index)
                summary["set_paths"].extend(
                    _iter_set_paths(set_data, target_path)
                )
                for variable, path, field, value in _iter_set_dose_signatures(
                    set_data, target_path
                ):
                    summary["variables"].setdefault(variable, []).append(
                        (path, field, value)
                    )

    for summary in summaries.values():
        summary["movement_paths"] = tuple(summary["movement_paths"])
        summary["set_paths"] = tuple(summary["set_paths"])
        summary["variables"] = {
            variable: tuple(values)
            for variable, values in sorted(summary["variables"].items())
        }
    return summaries


def _movement_changes(
    proposed: Mapping[str, Any], previous: Mapping[str, Any]
) -> set[str]:
    """Return typed dose changes, including novel targets on added sets."""

    changes: set[str] = set()
    proposed_set_paths = set(proposed.get("set_paths", ()))
    previous_set_paths = set(previous.get("set_paths", ()))
    if proposed.get("set_paths") != previous.get("set_paths"):
        changes.add("sets")
    common_set_paths = proposed_set_paths & previous_set_paths
    common_movement_paths = set(proposed.get("movement_paths", ())) & set(
        previous.get("movement_paths", ())
    )
    proposed_variables = proposed.get("variables", {})
    previous_variables = previous.get("variables", {})
    for variable in sorted(set(proposed_variables) | set(previous_variables)):
        allowed_paths = (
            common_movement_paths if variable == "difficulty" else common_set_paths
        )
        proposed_signature = tuple(
            item for item in proposed_variables.get(variable, ()) if item[0] in allowed_paths
        )
        previous_signature = tuple(
            item for item in previous_variables.get(variable, ()) if item[0] in allowed_paths
        )
        if proposed_signature != previous_signature:
            changes.add(variable)

    def dose_by_path(
        variables: Mapping[str, Sequence[tuple[tuple[int, ...], str, str]]]
    ) -> dict[tuple[int, ...], dict[str, tuple[tuple[str, str], ...]]]:
        grouped: dict[
            tuple[int, ...], dict[str, list[tuple[str, str]]]
        ] = {}
        for variable, signatures in variables.items():
            if variable == "difficulty":
                continue
            for path, field, value in signatures:
                grouped.setdefault(path, {}).setdefault(variable, []).append(
                    (field, value)
                )
        return {
            path: {
                variable: tuple(values)
                for variable, values in sorted(variable_groups.items())
            }
            for path, variable_groups in grouped.items()
        }

    previous_by_path = dose_by_path(previous_variables)
    proposed_by_path = dose_by_path(proposed_variables)
    previous_doses = list(previous_by_path.values())
    for added_path in proposed_set_paths - previous_set_paths:
        added_dose = proposed_by_path.get(added_path, {})
        if not added_dose or added_dose in previous_doses:
            continue
        if not previous_doses:
            changes.update(added_dose)
            continue
        differences = [
            {
                variable
                for variable in set(added_dose) | set(previous_dose)
                if added_dose.get(variable) != previous_dose.get(variable)
            }
            for previous_dose in previous_doses
        ]
        minimum = min(len(difference) for difference in differences)
        for difference in differences:
            if len(difference) == minimum:
                changes.update(difference)
    return changes


def _compare_baseline(
    plan: Mapping[str, Any],
    baseline: Any,
    errors: list[str],
    warnings: list[str],
    max_variables: int | None,
    declared_progressions: dict[str, set[str]],
) -> None:
    proposed_summaries = _summarise_movements(plan)
    baseline_summaries = _summarise_movements(baseline)
    if not baseline_summaries:
        message = (
            "$baseline: no comparable Xunji movements were found; "
            "automatic progression comparison could not run"
        )
        if any(declared_progressions.values()):
            errors.append(message)
        else:
            warnings.append(message)
        return

    new_movements = sorted(set(proposed_summaries) - set(baseline_summaries))
    if new_movements:
        names = ", ".join(
            proposed_summaries[name]["display_name"] for name in new_movements
        )
        message = (
            "$baseline: no comparable baseline was found for new movement(s): " + names
        )
        invalid_new = [
            name
            for name in new_movements
            if declared_progressions.get(name, set()) != {"exercise_selection"}
        ]
        if invalid_new and any(declared_progressions.get(name) for name in invalid_new):
            errors.append(message + "; declare only exercise_selection for a new movement")
        elif invalid_new:
            warnings.append(message)

    effective_limit = max_variables if max_variables is not None else 3
    computed_changes: dict[str, set[str]] = {}
    for movement_name in sorted(set(proposed_summaries) & set(baseline_summaries)):
        proposed = proposed_summaries[movement_name]
        previous = baseline_summaries[movement_name]
        changes = _movement_changes(proposed, previous)
        computed_changes[movement_name] = changes
        if len(changes) > effective_limit:
            errors.append(
                "$baseline: movement "
                f"{proposed['display_name']!r} changes {len(changes)} variables "
                f"({', '.join(sorted(changes))}); profile maximum is {effective_limit}"
            )

    for movement_name in sorted(computed_changes):
        computed = computed_changes[movement_name]
        declared = declared_progressions.get(movement_name, set())
        if computed != declared and (computed or declared):
            display_name = proposed_summaries[movement_name]["display_name"]
            errors.append(
                "$baseline: declared progression for "
                f"{display_name!r} is {sorted(declared)}, but target comparison "
                f"found {sorted(computed)}"
            )


def validate_plan_data(
    data: Any,
    profile: Any | None = None,
    baseline: Any | None = None,
) -> ValidationResult:
    """Return deterministic weekly-plan validation errors and warnings.

    Supply the corresponding profile to enforce personal availability, duration,
    movement exclusions, goal coverage and progression limits.  Supply a baseline
    plan or Xunji response to calculate target changes by movement name.
    """

    errors: list[str] = []
    warnings: list[str] = []

    if not _is_mapping(data):
        return {"errors": ["$: expected an object"], "warnings": []}

    profile_mapping: Mapping[str, Any] | None = None
    if profile is not None:
        if not _is_mapping(profile):
            errors.append("$profile: expected an object")
        else:
            profile_mapping = profile
            profile_result = validate_profile_data(profile)
            errors.extend(
                f"$profile{error[1:]}" if error.startswith("$") else f"$profile: {error}"
                for error in profile_result["errors"]
            )
            warnings.extend(
                f"$profile{warning[1:]}"
                if warning.startswith("$")
                else f"$profile: {warning}"
                for warning in profile_result["warnings"]
            )

    context = _profile_values(profile_mapping)

    schema_version = data.get("schema_version")
    is_offline_plan = schema_version == OFFLINE_PLAN_SCHEMA_VERSION
    is_xunji_plan = schema_version == PLAN_SCHEMA_VERSION
    if schema_version is None:
        errors.append("$.schema_version: required field is missing")
    elif schema_version not in (PLAN_SCHEMA_VERSION, OFFLINE_PLAN_SCHEMA_VERSION):
        errors.append(
            "$.schema_version: expected one of "
            f"{PLAN_SCHEMA_VERSION!r}, {OFFLINE_PLAN_SCHEMA_VERSION!r}; "
            f"got {schema_version!r}"
        )

    if is_offline_plan:
        if profile is None:
            errors.append(
                f"$profile: required for {OFFLINE_PLAN_SCHEMA_VERSION} validation"
            )
        for forbidden_path in _find_key_paths(
            data, {"xunji_payload", "original_xunji_payload"}
        ):
            errors.append(
                f"{forbidden_path}: is forbidden in {OFFLINE_PLAN_SCHEMA_VERSION}"
            )
        if profile_mapping is not None and _has_present_entries(
            profile_mapping.get("unconfirmed")
        ):
            errors.append(
                "$profile.unconfirmed: resolve all present items before programming "
                "an offline plan"
            )

    profile_id = data.get("profile_id")
    if not _is_non_empty_string(profile_id):
        errors.append("$.profile_id: expected a non-empty string")
    elif context["profile_id"] is not None and profile_id != context["profile_id"]:
        errors.append(
            f"$.profile_id: does not match profile id {context['profile_id']!r}"
        )

    week_start_value = data.get("week_start")
    if week_start_value is None:
        errors.append("$.week_start: required field is missing")
        week_start = None
    else:
        week_start = _parse_date(week_start_value, "$.week_start", errors)
        if week_start is not None and week_start.weekday() != 0:
            warnings.append("$.week_start: is not a Monday")

    status = data.get("status")
    if status is None:
        errors.append("$.status: required field is missing")
    elif status != "draft":
        errors.append(f"$.status: expected 'draft'; got {status!r}")

    evidence = data.get("evidence")
    if evidence is None:
        errors.append("$.evidence: required field is missing")
    elif not _is_mapping(evidence):
        errors.append("$.evidence: expected an object")
    elif not evidence:
        warnings.append("$.evidence: is empty; document the basis and limitations")
    else:
        unconfirmed_items: list[str] | None = []
        if "unconfirmed" in evidence:
            unconfirmed_items = _validate_string_list(
                evidence.get("unconfirmed"),
                "$.evidence.unconfirmed",
                errors,
                allow_empty=True,
            )
        if is_offline_plan and unconfirmed_items:
            errors.append(
                "$.evidence.unconfirmed: resolve all present items before programming "
                "an offline plan"
            )

    sessions = data.get("sessions")
    if sessions is None:
        errors.append("$.sessions: required field is missing")
        sessions = []
    elif not _is_list(sessions):
        errors.append("$.sessions: expected a list")
        sessions = []
    elif not sessions:
        errors.append("$.sessions: expected at least one session")

    daily_counts: Counter[str] = Counter()
    all_goal_tags: set[str] = set()
    total_training_sessions = 0

    for session_index, plan_session in enumerate(sessions):
        session_path = f"$.sessions[{session_index}]"
        if not _is_mapping(plan_session):
            errors.append(f"{session_path}: expected an object")
            continue

        if is_xunji_plan:
            for shadow_field in ("programme", "date", "title", "movements"):
                if shadow_field in plan_session:
                    errors.append(
                        f"{session_path}.{shadow_field}: offline shadow field is "
                        f"forbidden in {PLAN_SCHEMA_VERSION}"
                    )

        goal_tags = _validate_string_list(
            plan_session.get("goal_tags"),
            f"{session_path}.goal_tags",
            errors,
            allow_empty=False,
        )
        if goal_tags is not None:
            normalised_tags = [tag.casefold() for tag in goal_tags]
            all_goal_tags.update(normalised_tags)
            if len(set(normalised_tags)) != len(normalised_tags):
                warnings.append(f"{session_path}.goal_tags: duplicate tags are ignored")
            allowed_goal_tags = (
                context["goal_types"]
                if context["goal_types"]
                else set(ALLOWED_GOAL_TYPES)
            )
            for tag_index, tag in enumerate(normalised_tags):
                if tag not in allowed_goal_tags:
                    errors.append(
                        f"{session_path}.goal_tags[{tag_index}]: goal type {tag!r} "
                        "is not declared in the profile"
                    )

        estimated_minutes = plan_session.get("estimated_minutes")
        if estimated_minutes is None:
            errors.append(f"{session_path}.estimated_minutes: required field is missing")
        elif not _is_integer(estimated_minutes) or estimated_minutes <= 0:
            errors.append(
                f"{session_path}.estimated_minutes: expected a positive integer"
            )
        else:
            maximum_duration = context["maximum_duration"]
            default_duration = context["default_duration"]
            if maximum_duration is not None and estimated_minutes > maximum_duration:
                errors.append(
                    f"{session_path}.estimated_minutes: exceeds profile maximum "
                    f"of {maximum_duration} minutes"
                )
            elif default_duration is not None and estimated_minutes > default_duration:
                warnings.append(
                    f"{session_path}.estimated_minutes: exceeds the profile default "
                    f"of {default_duration} minutes"
                )

        if is_offline_plan:
            total_training_sessions += 1
            title = plan_session.get("title")
            if not _is_non_empty_string(title):
                errors.append(f"{session_path}.title: expected a non-empty string")

            if "date" not in plan_session:
                errors.append(f"{session_path}.date: required field is missing")
                session_date = None
            else:
                session_date = _parse_date(
                    plan_session.get("date"), f"{session_path}.date", errors
                )
            if session_date is not None:
                _validate_session_schedule(
                    session_date,
                    f"{session_path}.date",
                    week_start,
                    context,
                    daily_counts,
                    errors,
                    warnings,
                )

            programme = plan_session.get("programme")
            if programme is None:
                errors.append(f"{session_path}.programme: required field is missing")
                continue
            if not _is_mapping(programme):
                errors.append(f"{session_path}.programme: expected an object")
                continue
            _validate_movements(
                programme,
                f"{session_path}.programme",
                errors,
                context["excluded_movements"],
                context["maximum_sets"],
                enforce_xunji_limits=False,
                offline_targets=True,
            )
            continue

        payload = plan_session.get("xunji_payload")
        if payload is None:
            errors.append(f"{session_path}.xunji_payload: required field is missing")
            continue
        if not _is_mapping(payload):
            errors.append(f"{session_path}.xunji_payload: expected an object")
            continue

        xunji_sessions = _extract_payload_sessions(
            payload, f"{session_path}.xunji_payload", errors
        )
        total_training_sessions += len(xunji_sessions)
        for xunji_session, xunji_path in xunji_sessions:
            session_date = _session_date(
                plan_session,
                payload,
                xunji_session,
                xunji_path,
                session_path,
                week_start,
                errors,
            )
            if session_date is not None:
                _validate_session_schedule(
                    session_date,
                    f"{xunji_path}.datestr",
                    week_start,
                    context,
                    daily_counts,
                    errors,
                    warnings,
                )

            _validate_movements(
                xunji_session,
                xunji_path,
                errors,
                context["excluded_movements"],
                context["maximum_sets"],
            )

    for date_key in sorted(daily_counts):
        count = daily_counts[date_key]
        if not is_offline_plan and count > API_MAX_SESSIONS_PER_DAY:
            errors.append(
                f"$.sessions: {date_key} contains {count} Xunji sessions; "
                f"daily maximum is {API_MAX_SESSIONS_PER_DAY}"
            )

    sessions_per_week = context["sessions_per_week"]
    if sessions_per_week is not None and total_training_sessions != sessions_per_week:
        session_label = "offline session" if is_offline_plan else "Xunji session"
        errors.append(
            f"$.sessions: {session_label} count must match profile sessions_per_week "
            f"({sessions_per_week}); got {total_training_sessions}"
        )

    missing_functional_goals = sorted(
        context["functional_goal_types"] - all_goal_tags
    )
    for goal_type in missing_functional_goals:
        errors.append(
            "$.sessions[*].goal_tags: functional goal type "
            f"{goal_type!r} has no session coverage"
        )

    declared_progressions = _validate_progressions(
        data, errors, context["max_variables"]
    )
    if context["red_flags"] and any(declared_progressions.values()):
        errors.append(
            "$profile.health_and_safety.red_flags: non-empty red flags block "
            "automated progression"
        )
    if sessions and context["medical_clearance"] == "required_not_cleared":
        errors.append(
            "$profile.health_and_safety.medical_clearance: current status blocks "
            "a programmed training plan"
        )

    write_control = data.get("write_control")
    if write_control is None:
        errors.append("$.write_control: required field is missing")
    elif not _is_mapping(write_control):
        errors.append("$.write_control: expected an object")
    elif is_offline_plan:
        if write_control.get("remote_write_requested") is not False:
            errors.append(
                "$.write_control.remote_write_requested: expected exactly false "
                "for an offline plan"
            )
    else:
        if is_xunji_plan and "remote_write_requested" in write_control:
            errors.append(
                "$.write_control.remote_write_requested: offline shadow field is "
                f"forbidden in {PLAN_SCHEMA_VERSION}"
            )
        if write_control.get("explicit_confirmation_required") is not True:
            errors.append(
                "$.write_control.explicit_confirmation_required: expected true"
            )
        if write_control.get("confirmed") is not False:
            errors.append("$.write_control.confirmed: expected false for a draft plan")

    if baseline is not None:
        _compare_baseline(
            data,
            baseline,
            errors,
            warnings,
            context["max_variables"],
            declared_progressions,
        )

    return {"errors": errors, "warnings": warnings}


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate a personalised_training_plan_v1 or xunji_weekly_plan_v1 "
            "JSON document locally."
        )
    )
    parser.add_argument("plan", type=Path, help="Path to the weekly plan JSON file")
    parser.add_argument(
        "--profile",
        type=Path,
        help="Optional profile JSON for personalised cross-validation",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        help="Optional prior plan or Xunji JSON for progression comparison",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_argument_parser()
    arguments = parser.parse_args(argv)

    try:
        plan_data = _load_json(arguments.plan)
        profile_data = _load_json(arguments.profile) if arguments.profile else None
        baseline_data = _load_json(arguments.baseline) if arguments.baseline else None
    except (OSError, json.JSONDecodeError) as error:
        result: ValidationResult = {
            "errors": [f"could not read valid JSON: {error}"],
            "warnings": [],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 2

    result = validate_plan_data(plan_data, profile_data, baseline_data)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
