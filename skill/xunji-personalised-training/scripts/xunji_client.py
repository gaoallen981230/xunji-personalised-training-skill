#!/usr/bin/env python3
"""Safe, review-gated client for the Xunji training Open API.

The module deliberately separates deterministic plan preparation from network
operations.  A prepared manifest contains a digest and a minimal review
summary, never the training payload itself.  Writes require the reviewed
digest, an explicit confirmation flag, a fresh full-data safety read, and a
separate post-write verification step.
"""

from __future__ import annotations

import argparse
import copy
from contextlib import contextmanager
import datetime as dt
from decimal import Decimal, InvalidOperation
import errno
import gzip
import hashlib
import hmac
import inspect
import io
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from typing import Any, Callable, Iterable, Mapping, Sequence
import unicodedata
import urllib.error
import urllib.request

_SCRIPT_DIRECTORY = str(Path(__file__).resolve().parent)
if _SCRIPT_DIRECTORY not in sys.path:
    sys.path.insert(0, _SCRIPT_DIRECTORY)
from validate_plan import (  # noqa: E402
    _movement_changes as _validator_movement_changes,
    _normalise_name as _validator_normalise_name,
    _normalise_variable as _validator_normalise_variable,
    _summarise_movements as _validator_summarise_movements,
    validate_plan_data,
)


BASE_URL = "https://trains.xunjiapp.cn"
READ_ENDPOINT = "/api_trains_for_llm_v2"
WRITE_ENDPOINT = "/api_upsert_trains_for_llm_v2"
SCHEMA_VERSION = "train_open_api_v2"
PLAN_SCHEMA_VERSION = "xunji_weekly_plan_v1"
OFFLINE_PLAN_SCHEMA_VERSION = "personalised_training_plan_v1"
MANIFEST_VERSION = "xunji_write_manifest_v2"
READ_CACHE_VERSION = 3
DEFAULT_TIMEOUT_SECONDS = 30
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_PLAN_BYTES = 8 * 1024 * 1024
MAX_SESSIONS_PER_REQUEST = 4
MAX_MOVEMENTS_PER_SESSION = 15
MAX_SETS_PER_MOVEMENT = 20
HAN_CHARACTER = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
VALID_DIFFICULTIES = frozenset({"easy", "normal", "hard"})
REQUIRED_XUNJI_WRITE_POLICY = "review_then_explicit_confirm"
XUNJI_SHADOW_SESSION_FIELDS = frozenset({"programme", "date", "title", "movements"})

SET_VALUE_FIELDS = (
    "weight",
    "weight_kg",
    "reps",
    "time",
    "duration_s",
    "selfWeight",
)
FORBIDDEN_CREDENTIAL_KEYS = {
    "apikey",
    "xapikey",
    "authorization",
    "authorisation",
    "credential",
    "credentials",
    "token",
    "accesstoken",
}


class XunjiClientError(RuntimeError):
    """Base class for concise, non-sensitive client errors."""

    exit_code = 2


class PlanValidationError(XunjiClientError):
    """Raised when a local plan cannot form a safe API payload."""


class WriteSafetyError(XunjiClientError):
    """Raised when write authorisation or live-record safety checks fail."""


class VerificationError(XunjiClientError):
    """Raised when a forced full-data read does not match the reviewed plan."""

    exit_code = 3

    def __init__(self, mismatches: Sequence[str]) -> None:
        self.mismatches = list(mismatches)
        preview = "; ".join(self.mismatches[:10])
        if len(self.mismatches) > 10:
            preview += f"; and {len(self.mismatches) - 10} more"
        super().__init__(
            f"Verification failed with {len(self.mismatches)} mismatch(es): {preview}"
        )


def _value_is_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _normalised_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def _normalised_title(value: Any) -> str:
    """Normalise session titles for conservative duplicate detection."""

    return unicodedata.normalize("NFKC", str(value)).strip().casefold()


def _find_forbidden_credential_path(value: Any, path: str = "payload") -> str | None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if _normalised_key(key) in FORBIDDEN_CREDENTIAL_KEYS:
                return child_path
            found = _find_forbidden_credential_path(child, child_path)
            if found:
                return found
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found = _find_forbidden_credential_path(child, f"{path}[{index}]")
            if found:
                return found
    return None


def _contains_credential_value(value: Any, credential: str) -> bool:
    """Detect an accidentally embedded credential before JSON escaping."""

    if isinstance(value, Mapping):
        return any(
            _contains_credential_value(child, credential) for child in value.values()
        )
    if isinstance(value, list):
        return any(_contains_credential_value(child, credential) for child in value)
    if isinstance(value, str):
        return value == credential or (len(credential) >= 8 and credential in value)
    return False


def canonical_json_bytes(value: Any) -> bytes:
    """Return a deterministic, UTF-8 JSON representation."""

    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise PlanValidationError(
            "The plan contains a value that canonical JSON cannot represent."
        ) from error
    return encoded.encode("utf-8")


def _validate_date(value: Any, path: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise PlanValidationError(f"{path} must use YYYY-MM-DD.")
    try:
        dt.date.fromisoformat(value)
    except ValueError as error:
        raise PlanValidationError(f"{path} is not a valid calendar date.") from error
    return value


def _validate_set(set_data: Any, path: str) -> None:
    if not isinstance(set_data, dict):
        raise PlanValidationError(f"{path} must be an object.")
    has_direct_target = any(
        field in set_data
        and (
            set_data[field] is True
            if field == "selfWeight"
            else _value_is_present(set_data[field])
        )
        for field in SET_VALUE_FIELDS
    )
    items = set_data.get("items")
    has_nested_targets = isinstance(items, list) and bool(items)
    if not has_direct_target and not has_nested_targets:
        fields = ", ".join(SET_VALUE_FIELDS)
        raise PlanValidationError(f"{path} needs at least one target field: {fields}.")
    if items is not None:
        if not isinstance(items, list) or not items:
            raise PlanValidationError(f"{path}.items must be a non-empty array.")
        for item_index, item in enumerate(items):
            item_path = f"{path}.items[{item_index}]"
            if not isinstance(item, dict) or not isinstance(item.get("set"), dict):
                raise PlanValidationError(f"{item_path}.set must be an object.")
            _validate_set(item["set"], f"{item_path}.set")


def _validate_movement(movement: Any, path: str) -> None:
    if not isinstance(movement, dict):
        raise PlanValidationError(f"{path} must be an object.")
    name = movement.get("name")
    if not isinstance(name, str) or not name.strip():
        raise PlanValidationError(f"{path}.name must be a non-empty string.")
    if not HAN_CHARACTER.search(name):
        raise PlanValidationError(
            f"{path}.name must contain a Chinese Xunji movement name."
        )
    sets = movement.get("sets")
    if not isinstance(sets, list) or not sets:
        raise PlanValidationError(f"{path}.sets must be a non-empty array.")
    if len(sets) > MAX_SETS_PER_MOVEMENT:
        raise PlanValidationError(
            f"{path} has more than {MAX_SETS_PER_MOVEMENT} sets."
        )
    for set_index, set_data in enumerate(sets):
        _validate_set(set_data, f"{path}.sets[{set_index}]")


def _validate_session(payload: Any, path: str) -> None:
    if not isinstance(payload, dict):
        raise PlanValidationError(f"{path} must be an object.")
    _validate_date(payload.get("datestr"), f"{path}.datestr")
    title = payload.get("title")
    if not isinstance(title, str) or not title.strip():
        raise PlanValidationError(f"{path}.title must be a non-empty string.")
    if "localid" in payload and not _value_is_present(payload.get("localid")):
        raise PlanValidationError(f"{path}.localid cannot be empty on an update.")
    if "localid" in payload:
        for field in ("start", "end"):
            if field not in payload or not _value_is_present(payload.get(field)):
                raise PlanValidationError(
                    f"{path}.{field} is required on an update so it can be preserved."
                )
    elif ("start" in payload) != ("end" in payload):
        raise PlanValidationError(
            f"{path}.start and {path}.end must either both be present or both be omitted."
        )
    movements = payload.get("movements")
    if not isinstance(movements, list) or not movements:
        raise PlanValidationError(f"{path}.movements must be a non-empty array.")
    if len(movements) > MAX_MOVEMENTS_PER_SESSION:
        raise PlanValidationError(
            f"{path} has more than {MAX_MOVEMENTS_PER_SESSION} movements."
        )
    for movement_index, movement in enumerate(movements):
        _validate_movement(movement, f"{path}.movements[{movement_index}]")


def _contains_hidden_note(value: Any, *, parent_key: str = "") -> bool:
    """Detect API placeholders whose real note content is unavailable."""

    if isinstance(value, Mapping):
        return any(
            _contains_hidden_note(child, parent_key=str(key).casefold())
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_hidden_note(child, parent_key=parent_key) for child in value)
    if parent_key in {"note", "remark", "comment"} and isinstance(value, str):
        return value.strip().casefold().replace(" ", "") == "[objectobject]"
    return False


def _require_unchanged_except(
    original: Mapping[str, Any],
    proposed: Mapping[str, Any],
    *,
    mutable_keys: frozenset[str],
    path: str,
) -> None:
    """Fail closed when unrelated or unknown metadata changes."""

    for key in set(original) | set(proposed):
        key_text = str(key)
        if key_text in mutable_keys:
            continue
        if key not in proposed:
            raise PlanValidationError(
                f"{path} omits original field {key_text}; start from a full clone."
            )
        if key not in original:
            raise PlanValidationError(
                f"{path}.{key_text} is new unclassified metadata; update it in the app."
            )
        if canonical_json_bytes(original[key]) != canonical_json_bytes(proposed[key]):
            raise PlanValidationError(
                f"{path}.{key_text} changes unrelated or unclassified metadata."
            )


SET_MUTABLE_DOSE_KEYS = frozenset(
    set(SET_VALUE_FIELDS)
    | {
        "unit",
        "metrics",
        "items",
        "distance",
        "distance_m",
        "rest",
        "rest_s",
        "tempo",
        "range",
        "range_of_motion",
    }
)
NEW_SESSION_KEYS = frozenset({"datestr", "title", "note", "movements"})
NEW_MOVEMENT_KEYS = frozenset({"name", "sets", "note", "remark", "difficulty"})
NEW_SET_KEYS = frozenset(set(SET_MUTABLE_DOSE_KEYS) | {"done"})
NEW_ITEM_KEYS = frozenset({"set"})


def _require_allowed_new_keys(
    value: Mapping[str, Any], *, allowed: frozenset[str], path: str
) -> None:
    """Reject unclassified fields in newly created API objects."""

    for key in value:
        if not isinstance(key, str) or key not in allowed:
            raise PlanValidationError(
                f"{path}.{str(key)} is unclassified on a newly created object."
            )


def _require_new_set_shape(set_data: Mapping[str, Any], *, path: str) -> None:
    _require_allowed_new_keys(set_data, allowed=NEW_SET_KEYS, path=path)
    if "done" in set_data and set_data.get("done") is not False:
        raise PlanValidationError(f"{path}.done must be false on a planned set.")
    if "metrics" in set_data and not isinstance(set_data.get("metrics"), Mapping):
        raise PlanValidationError(f"{path}.metrics must be an object.")
    items = set_data.get("items")
    if items is None:
        return
    if not isinstance(items, list):
        raise PlanValidationError(f"{path}.items must be an array.")
    for item_index, item in enumerate(items):
        item_path = f"{path}.items[{item_index}]"
        if not isinstance(item, Mapping):
            raise PlanValidationError(f"{item_path} must be an object.")
        _require_new_item_shape(item, path=item_path)


def _require_new_item_shape(item: Mapping[str, Any], *, path: str) -> None:
    _require_allowed_new_keys(item, allowed=NEW_ITEM_KEYS, path=path)
    nested_set = item.get("set")
    if not isinstance(nested_set, Mapping):
        raise PlanValidationError(f"{path}.set must be an object.")
    _require_new_set_shape(nested_set, path=f"{path}.set")


def _require_valid_difficulty(value: Any, *, path: str) -> None:
    if not isinstance(value, str) or value not in VALID_DIFFICULTIES:
        raise PlanValidationError(
            f"{path}.difficulty must be easy, normal, or hard."
        )


def _require_new_movement_shape(
    movement: Mapping[str, Any], *, path: str
) -> None:
    _require_allowed_new_keys(movement, allowed=NEW_MOVEMENT_KEYS, path=path)
    if "difficulty" in movement:
        _require_valid_difficulty(movement.get("difficulty"), path=path)
    sets = movement.get("sets")
    if not isinstance(sets, list):
        raise PlanValidationError(f"{path}.sets must be an array.")
    for set_index, set_data in enumerate(sets):
        if not isinstance(set_data, Mapping):
            raise PlanValidationError(f"{path}.sets[{set_index}] must be an object.")
        _require_new_set_shape(set_data, path=f"{path}.sets[{set_index}]")


def _require_new_session_shape(
    session: Mapping[str, Any], *, path: str
) -> None:
    _require_allowed_new_keys(session, allowed=NEW_SESSION_KEYS, path=path)
    movements = session.get("movements")
    if not isinstance(movements, list):
        raise PlanValidationError(f"{path}.movements must be an array.")
    for movement_index, movement in enumerate(movements):
        if not isinstance(movement, Mapping):
            raise PlanValidationError(
                f"{path}.movements[{movement_index}] must be an object."
            )
        _require_new_movement_shape(
            movement, path=f"{path}.movements[{movement_index}]"
        )


def _require_set_update_clone(
    original: Mapping[str, Any], proposed: Mapping[str, Any], *, path: str
) -> None:
    _require_unchanged_except(
        original,
        proposed,
        mutable_keys=SET_MUTABLE_DOSE_KEYS,
        path=path,
    )
    original_items = original.get("items")
    if original_items is None:
        proposed_items = proposed.get("items")
        if proposed_items is not None:
            if not isinstance(proposed_items, list):
                raise PlanValidationError(f"{path}.items must remain an array.")
            for item_index, proposed_item in enumerate(proposed_items):
                item_path = f"{path}.items[{item_index}]"
                if not isinstance(proposed_item, Mapping):
                    raise PlanValidationError(f"{item_path} must be an object.")
                _require_new_item_shape(proposed_item, path=item_path)
        return
    proposed_items = proposed.get("items")
    if not isinstance(original_items, list) or not isinstance(proposed_items, list):
        raise PlanValidationError(f"{path}.items must remain an array.")
    if len(proposed_items) < len(original_items):
        raise PlanValidationError(
            f"{path}.items cannot remove original nested sets in a normal update."
        )
    for item_index, (original_item, proposed_item) in enumerate(
        zip(original_items, proposed_items)
    ):
        item_path = f"{path}.items[{item_index}]"
        if not isinstance(original_item, Mapping) or not isinstance(
            proposed_item, Mapping
        ):
            if canonical_json_bytes(original_item) != canonical_json_bytes(proposed_item):
                raise PlanValidationError(f"{item_path} must remain unchanged.")
            continue
        _require_unchanged_except(
            original_item,
            proposed_item,
            mutable_keys=frozenset({"set"}),
            path=item_path,
        )
        original_set = original_item.get("set")
        proposed_set = proposed_item.get("set")
        if not isinstance(original_set, Mapping) or not isinstance(
            proposed_set, Mapping
        ):
            raise PlanValidationError(f"{item_path}.set must remain an object.")
        _require_set_update_clone(
            original_set, proposed_set, path=f"{item_path}.set"
        )
    for item_index in range(len(original_items), len(proposed_items)):
        proposed_item = proposed_items[item_index]
        item_path = f"{path}.items[{item_index}]"
        if not isinstance(proposed_item, Mapping):
            raise PlanValidationError(f"{item_path} must be an object.")
        _require_new_item_shape(proposed_item, path=item_path)


def _movement_occurrences(
    movements: Sequence[Any],
) -> dict[tuple[str, int], Mapping[str, Any]]:
    """Index movements by case-sensitive name and occurrence, including duplicates."""

    counts: dict[str, int] = {}
    indexed: dict[tuple[str, int], Mapping[str, Any]] = {}
    for movement in movements:
        if not isinstance(movement, Mapping):
            continue
        name = str(movement.get("name", ""))
        occurrence = counts.get(name, 0)
        counts[name] = occurrence + 1
        indexed[(name, occurrence)] = movement
    return indexed


def _require_update_clone(
    original: Mapping[str, Any], proposed: Mapping[str, Any], *, session_index: int
) -> None:
    """Require update payloads to be full clones with only deliberate value changes."""

    base = f"sessions[{session_index}]"
    if _contains_hidden_note(original):
        raise PlanValidationError(
            f"{base}.original_xunji_payload contains a hidden note placeholder; "
            "refresh or resolve it before updating."
        )
    if has_completion_signal(original):
        raise PlanValidationError(
            f"{base}.original_xunji_payload contains completion evidence and cannot be updated."
        )
    if hmac.compare_digest(
        hashlib.sha256(canonical_json_bytes(original)).digest(),
        hashlib.sha256(canonical_json_bytes(proposed)).digest(),
    ):
        raise PlanValidationError(
            f"{base}.xunji_payload has no reviewed change; a no-op update is refused."
        )
    _require_unchanged_except(
        original,
        proposed,
        path=f"{base}.xunji_payload",
        mutable_keys=frozenset({"title", "movements"}),
    )

    original_movements = original.get("movements", [])
    proposed_movements = proposed.get("movements", [])
    if not isinstance(original_movements, list) or not isinstance(proposed_movements, list):
        return
    original_index = _movement_occurrences(original_movements)
    proposed_index = _movement_occurrences(proposed_movements)
    original_order = list(original_index)
    proposed_existing_order = [
        identity for identity in proposed_index if identity in original_index
    ]
    if (
        len(proposed_existing_order) == len(original_order)
        and proposed_existing_order != original_order
    ):
        raise PlanValidationError(
            f"{base}.xunji_payload cannot reorder original movements in a normal update."
        )
    for identity, original_movement in original_index.items():
        proposed_movement = proposed_index.get(identity)
        if proposed_movement is None:
            name, occurrence = identity
            raise PlanValidationError(
                f"{base}.xunji_payload cannot remove original movement {name!r} "
                f"occurrence {occurrence + 1} in a normal update."
            )
        name, occurrence = identity
        movement_path = (
            f"{base}.xunji_payload movement {name!r} occurrence {occurrence + 1}"
        )
        _require_unchanged_except(
            original_movement,
            proposed_movement,
            path=movement_path,
            mutable_keys=frozenset({"sets", "difficulty"}),
        )
        difficulty_presence_changed = (
            "difficulty" in original_movement
        ) != ("difficulty" in proposed_movement)
        difficulty_value_changed = (
            "difficulty" in original_movement
            and "difficulty" in proposed_movement
            and not hmac.compare_digest(
                canonical_json_bytes(original_movement["difficulty"]),
                canonical_json_bytes(proposed_movement["difficulty"]),
            )
        )
        if difficulty_presence_changed or difficulty_value_changed:
            _require_valid_difficulty(
                proposed_movement.get("difficulty"), path=movement_path
            )
        original_sets = original_movement.get("sets", [])
        proposed_sets = proposed_movement.get("sets", [])
        if not isinstance(original_sets, list) or not isinstance(proposed_sets, list):
            continue
        if len(proposed_sets) < len(original_sets):
            raise PlanValidationError(
                f"{movement_path}.sets cannot remove original sets in a normal update."
            )
        for set_index, (original_set, proposed_set) in enumerate(
            zip(original_sets, proposed_sets)
        ):
            if isinstance(original_set, Mapping) and isinstance(proposed_set, Mapping):
                _require_set_update_clone(
                    original_set,
                    proposed_set,
                    path=f"{movement_path}.sets[{set_index}]",
                )
        for set_index in range(len(original_sets), len(proposed_sets)):
            proposed_set = proposed_sets[set_index]
            if not isinstance(proposed_set, Mapping):
                raise PlanValidationError(
                    f"{movement_path}.sets[{set_index}] must be an object."
                )
            _require_new_set_shape(
                proposed_set, path=f"{movement_path}.sets[{set_index}]"
            )
    for identity, proposed_movement in proposed_index.items():
        if identity in original_index:
            continue
        name, occurrence = identity
        _require_new_movement_shape(
            proposed_movement,
            path=(
                f"{base}.xunji_payload new movement {name!r} "
                f"occurrence {occurrence + 1}"
            ),
        )


def _validated_plan_entries(plan: Any) -> list[dict[str, Any]]:
    """Validate and detach reviewed payloads plus update baselines."""

    if not isinstance(plan, dict):
        raise PlanValidationError("The plan must be a JSON object.")
    if plan.get("schema_version") == OFFLINE_PLAN_SCHEMA_VERSION:
        raise PlanValidationError(
            "An offline personalised_training_plan_v1 plan cannot be prepared "
            "for Xunji writeback; convert it to a reviewed xunji_weekly_plan_v1 "
            "draft first."
        )
    if plan.get("schema_version") != PLAN_SCHEMA_VERSION:
        raise PlanValidationError(
            f"The plan schema_version must be {PLAN_SCHEMA_VERSION}."
        )
    if plan.get("status") != "draft":
        raise PlanValidationError("The plan status must remain draft before writeback.")
    write_control = plan.get("write_control")
    if not isinstance(write_control, dict):
        raise PlanValidationError("The plan needs a write_control object.")
    if write_control.get("explicit_confirmation_required") is not True:
        raise PlanValidationError(
            "The plan must require explicit confirmation before writeback."
        )
    if write_control.get("confirmed") is not False:
        raise PlanValidationError(
            "The plan cannot mark itself confirmed before conversational review."
        )
    if "remote_write_requested" in write_control:
        raise PlanValidationError(
            "A Xunji writeback plan cannot contain the offline-only "
            "write_control.remote_write_requested field."
        )
    sessions = plan.get("sessions")
    if not isinstance(sessions, list) or not sessions:
        raise PlanValidationError("The plan must contain a non-empty sessions array.")

    entries: list[dict[str, Any]] = []
    seen_updates: set[str] = set()
    seen_titles: set[tuple[str, str]] = set()
    sessions_by_date: dict[str, int] = {}
    for index, wrapper in enumerate(sessions):
        if not isinstance(wrapper, dict):
            raise PlanValidationError(f"sessions[{index}] must be an object.")
        shadow_fields = sorted(XUNJI_SHADOW_SESSION_FIELDS & set(wrapper))
        if shadow_fields:
            raise PlanValidationError(
                f"sessions[{index}] contains offline shadow field(s) "
                f"{', '.join(shadow_fields)}; Xunji drafts must have one "
                "unambiguous xunji_payload."
            )
        if not isinstance(wrapper.get("xunji_payload"), dict):
            raise PlanValidationError(
                f"sessions[{index}].xunji_payload must be an object."
            )
        payload = copy.deepcopy(wrapper["xunji_payload"])
        _validate_session(payload, f"sessions[{index}].xunji_payload")
        forbidden_path = _find_forbidden_credential_path(payload)
        if forbidden_path:
            raise PlanValidationError(
                f"{forbidden_path} is not allowed; credentials belong in headers only."
            )

        title_identity = (
            str(payload["datestr"]),
            _normalised_title(payload["title"]),
        )
        if title_identity in seen_titles:
            raise PlanValidationError(
                "The plan would produce duplicate case-insensitive titles on one date."
            )
        seen_titles.add(title_identity)

        entry: dict[str, Any] = {"xunji_payload": payload}
        original = wrapper.get("original_xunji_payload")
        if "localid" in payload:
            if not isinstance(original, dict):
                raise PlanValidationError(
                    f"sessions[{index}].original_xunji_payload is required for updates."
                )
            original_copy = copy.deepcopy(original)
            _validate_session(
                original_copy, f"sessions[{index}].original_xunji_payload"
            )
            forbidden_original = _find_forbidden_credential_path(original_copy)
            if forbidden_original:
                raise PlanValidationError(
                    f"{forbidden_original} is not allowed; credentials belong in headers only."
                )
            for field in ("datestr", "localid", "start", "end"):
                if not _same_identity_value(original_copy.get(field), payload.get(field)):
                    raise PlanValidationError(
                        f"sessions[{index}].xunji_payload must preserve original {field}."
                    )
            _require_update_clone(original_copy, payload, session_index=index)
            entry["original_xunji_payload"] = original_copy
            identity = str(payload["localid"])
            if identity in seen_updates:
                raise PlanValidationError("The plan repeats an update localid.")
            seen_updates.add(identity)
        else:
            if original is not None:
                raise PlanValidationError(
                    f"sessions[{index}].original_xunji_payload is only valid for updates."
                )
            _require_new_session_shape(
                payload, path=f"sessions[{index}].xunji_payload"
            )
        if has_completion_signal(payload):
            raise PlanValidationError(
                f"sessions[{index}].xunji_payload contains completion evidence; "
                "a planned payload must remain unstarted."
            )
        entries.append(entry)
        datestr = str(payload["datestr"])
        sessions_by_date[datestr] = sessions_by_date.get(datestr, 0) + 1

    for datestr, count in sessions_by_date.items():
        if count > MAX_SESSIONS_PER_REQUEST:
            raise PlanValidationError(
                f"The plan contains more than {MAX_SESSIONS_PER_REQUEST} "
                f"sessions on {datestr}."
            )

    canonical_json_bytes(entries)
    return entries


def validate_plan(plan: Any) -> list[dict[str, Any]]:
    """Validate and return detached ``sessions[].xunji_payload`` objects."""

    return [entry["xunji_payload"] for entry in _validated_plan_entries(plan)]


def _declared_progression_map(plan: Mapping[str, Any]) -> dict[str, set[str]]:
    """Return validated progression variables keyed like the plan validator."""

    declared: dict[str, set[str]] = {}
    progressions = plan.get("progressions", [])
    if not isinstance(progressions, list):
        return declared
    for progression in progressions:
        if not isinstance(progression, Mapping):
            continue
        movement_name = next(
            (
                progression.get(key)
                for key in ("movement_name", "movement", "name")
                if key in progression
            ),
            None,
        )
        raw_variables: Any = None
        for key in (
            "variables",
            "changed_variables",
            "variables_changed",
            "variable",
        ):
            if key in progression:
                raw_variables = progression.get(key)
                break
        if raw_variables is None and isinstance(progression.get("changes"), Mapping):
            raw_variables = list(progression["changes"])
        values = [raw_variables] if isinstance(raw_variables, str) else raw_variables
        if not isinstance(movement_name, str) or not isinstance(values, list):
            continue
        normalised_name = _validator_normalise_name(movement_name)
        declared.setdefault(normalised_name, set()).update(
            _validator_normalise_variable(value)
            for value in values
            if isinstance(value, str)
        )
    return declared


def _require_comparable_progression_baseline(
    plan: Mapping[str, Any], baseline: Any
) -> None:
    """Require every declared progression to match comparable baseline targets."""

    declared = _declared_progression_map(plan)
    if not declared:
        return
    proposed = _validator_summarise_movements(plan)
    previous = _validator_summarise_movements(baseline)
    for movement_name, variables in declared.items():
        if movement_name in proposed and movement_name not in previous:
            if variables == {"exercise_selection"}:
                continue
            raise PlanValidationError(
                "A new movement must declare only exercise_selection."
            )
        if movement_name not in proposed or movement_name not in previous:
            raise PlanValidationError(
                "Every declared progression needs the same comparable movement in "
                "the proposed and baseline plans."
            )
        previous_summary = previous[movement_name]
        for variable in variables:
            if variable == "sets" and not previous_summary.get("set_paths"):
                raise PlanValidationError(
                    "A declared set progression needs baseline set evidence."
                )
            if variable != "sets" and not previous_summary.get("variables", {}).get(
                variable
            ):
                raise PlanValidationError(
                    f"A declared {variable} progression needs matching baseline targets."
                )
        proposed_summary = proposed[movement_name]
        computed = _validator_movement_changes(proposed_summary, previous_summary)
        if computed != variables:
            raise PlanValidationError(
                "Declared progression variables do not match the proposed baseline change."
            )


def _update_progression_changes(
    entries: Sequence[Mapping[str, Any]],
) -> dict[str, set[str]]:
    """Calculate dose changes from each update's bound original snapshot."""

    actual: dict[str, set[str]] = {}
    for entry in entries:
        original = entry.get("original_xunji_payload")
        proposed = entry.get("xunji_payload")
        if not isinstance(original, Mapping) or not isinstance(proposed, Mapping):
            continue
        previous_summaries = _validator_summarise_movements(original)
        proposed_summaries = _validator_summarise_movements(proposed)
        for movement_name in sorted(set(proposed_summaries) - set(previous_summaries)):
            actual.setdefault(movement_name, set()).add("exercise_selection")
        for movement_name in sorted(set(proposed_summaries) & set(previous_summaries)):
            previous_summary = previous_summaries[movement_name]
            proposed_summary = proposed_summaries[movement_name]
            previous_occurrences = int(previous_summary.get("occurrence_count", 0))
            proposed_occurrences = int(proposed_summary.get("occurrence_count", 0))
            if proposed_occurrences > previous_occurrences:
                actual.setdefault(movement_name, set()).add("exercise_selection")
            proposed_summary = _limit_summary_occurrences(
                proposed_summary, previous_occurrences
            )
            changes = _validator_movement_changes(
                proposed_summary, previous_summary
            )
            previous_variables = previous_summary.get("variables", {})
            for variable in changes - {"sets"}:
                if not previous_variables.get(variable):
                    raise PlanValidationError(
                        "An update dose variable needs comparable evidence in its "
                        "bound original snapshot."
                    )
            if changes:
                actual.setdefault(movement_name, set()).update(changes)
    return actual


def _limit_summary_occurrences(
    summary: Mapping[str, Any], occurrence_limit: int
) -> dict[str, Any]:
    """Keep only movement occurrences that existed in the bound original."""

    def is_existing_path(path: Any) -> bool:
        return (
            isinstance(path, tuple)
            and bool(path)
            and isinstance(path[0], int)
            and path[0] < occurrence_limit
        )

    variables = summary.get("variables", {})
    filtered_variables = {
        variable: tuple(
            signature
            for signature in signatures
            if isinstance(signature, tuple)
            and bool(signature)
            and is_existing_path(signature[0])
        )
        for variable, signatures in variables.items()
        if isinstance(signatures, (list, tuple))
    }
    return {
        "display_name": summary.get("display_name"),
        "movement_paths": tuple(
            path
            for path in summary.get("movement_paths", ())
            if is_existing_path(path)
        ),
        "set_paths": tuple(
            path for path in summary.get("set_paths", ()) if is_existing_path(path)
        ),
        "variables": filtered_variables,
        "occurrence_count": occurrence_limit,
    }


def _create_movement_names(
    entries: Sequence[Mapping[str, Any]],
) -> set[str]:
    """Return normalised movement names present in create operations."""

    names: set[str] = set()
    for entry in entries:
        if isinstance(entry.get("original_xunji_payload"), Mapping):
            continue
        payload = entry.get("xunji_payload")
        if isinstance(payload, Mapping):
            names.update(_validator_summarise_movements(payload))
    return names


def _require_update_progression_declarations(
    plan: Mapping[str, Any], entries: Sequence[Mapping[str, Any]]
) -> dict[str, set[str]]:
    """Make omission of update progression declarations fail closed."""

    actual = _update_progression_changes(entries)
    declared = _declared_progression_map(plan)
    for movement_name, changes in actual.items():
        if declared.get(movement_name, set()) != changes:
            raise PlanValidationError(
                "Every update dose change must exactly match its progression declaration."
            )
    update_movement_names: set[str] = set()
    for entry in entries:
        original = entry.get("original_xunji_payload")
        if isinstance(original, Mapping):
            update_movement_names.update(_validator_summarise_movements(original))
            proposed = entry.get("xunji_payload")
            if isinstance(proposed, Mapping):
                update_movement_names.update(_validator_summarise_movements(proposed))
    for movement_name in update_movement_names:
        if movement_name in declared and movement_name not in actual:
            raise PlanValidationError(
                "An update progression declaration has no matching dose change."
            )
    return actual


def _validated_personalisation_context(
    plan: Any, profile: Any, baseline: Any | None
) -> tuple[str, int]:
    """Validate the complete personalised plan and hash its local safety context."""

    if not isinstance(profile, Mapping):
        raise PlanValidationError(
            "A validated local training profile is required before write preparation."
        )
    xunji_context = profile.get("xunji")
    if not isinstance(xunji_context, Mapping) or xunji_context.get(
        "write_policy"
    ) != REQUIRED_XUNJI_WRITE_POLICY:
        raise PlanValidationError(
            "Xunji write preparation requires profile.xunji.write_policy "
            f"to be {REQUIRED_XUNJI_WRITE_POLICY!r}."
        )

    def require_resolved_string_list(value: Any, path: str) -> None:
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            raise PlanValidationError(f"{path} must be a list of non-empty strings.")
        if value:
            raise PlanValidationError(
                "Unconfirmed profile or plan evidence must be resolved before "
                "write preparation."
            )

    evidence = plan.get("evidence", {}) if isinstance(plan, Mapping) else {}
    plan_unconfirmed = (
        evidence.get("unconfirmed", []) if isinstance(evidence, Mapping) else []
    )
    require_resolved_string_list(profile.get("unconfirmed", []), "profile.unconfirmed")
    require_resolved_string_list(plan_unconfirmed, "plan.evidence.unconfirmed")

    entries = _validated_plan_entries(plan)
    actual_update_changes = _require_update_progression_declarations(plan, entries)
    declared_progressions = plan.get("progressions")
    declared_map = _declared_progression_map(plan)
    create_progression_names = set(declared_map) & _create_movement_names(entries)
    if isinstance(declared_progressions, list) and declared_progressions and baseline is None:
        if declared_map != actual_update_changes or create_progression_names:
            raise PlanValidationError(
                "A local baseline plan is required when progression is not fully "
                "and unambiguously covered by bound update snapshots."
            )
    result = validate_plan_data(plan, profile, baseline)
    errors = result.get("errors", [])
    warnings = result.get("warnings", [])
    if isinstance(plan, Mapping) and baseline is not None:
        _require_comparable_progression_baseline(plan, baseline)
    if errors:
        raise PlanValidationError(
            f"Personalised plan validation failed with {len(errors)} error(s); "
            "run validate_plan.py locally for details."
        )
    context = {
        "context_format": "personalised_training_context_v1",
        "profile": copy.deepcopy(dict(profile)),
        "baseline": copy.deepcopy(baseline),
    }
    context_digest = hashlib.sha256(canonical_json_bytes(context)).hexdigest()
    return context_digest, len(warnings)


def review_digest(
    entries: Sequence[Mapping[str, Any]], *, context_sha256: str
) -> str:
    """Digest outgoing payloads, update snapshots, and personalised safety context."""

    digest_input = {
        "payload_format": "reviewed_xunji_write_envelope_v2",
        "personalisation_context_sha256": context_sha256,
        "sessions": list(entries),
    }
    return hashlib.sha256(canonical_json_bytes(digest_input)).hexdigest()


def _review_summary(entries: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for entry in entries:
        payload = entry["xunji_payload"]
        movements = payload["movements"]
        summary.append(
            {
                "date": payload["datestr"],
                "title": payload["title"],
                "operation": "update" if "localid" in payload else "create",
                "movement_count": len(movements),
                "set_count": sum(len(movement["sets"]) for movement in movements),
            }
        )
    return summary


def prepare_write(
    plan: Any, profile: Any, baseline: Any | None = None
) -> dict[str, Any]:
    """Validate locally and build a deterministic, payload-free review manifest."""

    entries = _validated_plan_entries(plan)
    context_sha256, warning_count = _validated_personalisation_context(
        plan, profile, baseline
    )
    return {
        "manifest_version": MANIFEST_VERSION,
        "digest_algorithm": "sha256",
        "payload_sha256": review_digest(
            entries, context_sha256=context_sha256
        ),
        "personalisation_validation": {
            "status": "passed",
            "context_sha256": context_sha256,
            "warning_count": warning_count,
        },
        "summary": _review_summary(entries),
    }


def validate_write_authorisation(
    plan: Any,
    manifest: Any,
    profile: Any,
    baseline: Any | None = None,
    *,
    expected_digest: str,
    write_confirmed: bool,
) -> tuple[list[dict[str, Any]], str]:
    """Validate the explicit confirmation and bind it to the current payload."""

    if write_confirmed is not True:
        raise WriteSafetyError("Write refused: --write-confirmed is required.")
    if not isinstance(expected_digest, str) or not re.fullmatch(
        r"[0-9a-fA-F]{64}", expected_digest
    ):
        raise WriteSafetyError("Write refused: --expected-digest must be SHA-256 hex.")
    entries, current_digest = validate_manifest_binding(
        plan, manifest, profile, baseline
    )
    expected = expected_digest.casefold()
    if not hmac.compare_digest(current_digest, expected):
        raise WriteSafetyError(
            "Write refused: the current payload no longer matches the reviewed digest."
        )
    return entries, current_digest


def validate_manifest_binding(
    plan: Any,
    manifest: Any,
    profile: Any,
    baseline: Any | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Bind a manifest to the current plan without granting write authority."""

    if not isinstance(manifest, dict):
        raise WriteSafetyError("The manifest is not a JSON object.")
    if manifest.get("manifest_version") != MANIFEST_VERSION:
        raise WriteSafetyError("The manifest version is unsupported.")
    if manifest.get("digest_algorithm") != "sha256":
        raise WriteSafetyError("The manifest digest algorithm is invalid.")
    entries = _validated_plan_entries(plan)
    context_sha256, warning_count = _validated_personalisation_context(
        plan, profile, baseline
    )
    current_digest = review_digest(entries, context_sha256=context_sha256)
    manifest_digest = manifest.get("payload_sha256")
    if not isinstance(manifest_digest, str) or not hmac.compare_digest(
        current_digest, manifest_digest.casefold()
    ):
        raise WriteSafetyError(
            "The current payload no longer matches the reviewed manifest."
        )
    if manifest.get("summary") != _review_summary(entries):
        raise WriteSafetyError("The manifest summary no longer matches the payload.")
    expected_validation = {
        "status": "passed",
        "context_sha256": context_sha256,
        "warning_count": warning_count,
    }
    if manifest.get("personalisation_validation") != expected_validation:
        raise WriteSafetyError(
            "The manifest no longer matches the validated profile or baseline."
        )
    return entries, current_digest


def default_cache_dir(
    *, environ: Mapping[str, str] | None = None, home: Path | None = None
) -> Path:
    """Return a user-local XDG cache directory without creating it."""

    values = os.environ if environ is None else environ
    xdg_value = values.get("XDG_CACHE_HOME", "").strip()
    if xdg_value:
        xdg_path = Path(xdg_value).expanduser()
        if xdg_path.is_absolute():
            return xdg_path / "xunji-personalised-training"
    resolved_home = Path.home() if home is None else Path(home)
    return resolved_home / ".cache" / "xunji-personalised-training"


def _fixed_transaction_cache_dir() -> Path:
    """Return the single per-user transaction root, ignoring mutable cache settings."""

    if os.name == "posix":
        try:
            import pwd

            home_value = pwd.getpwuid(os.getuid()).pw_dir
        except (ImportError, KeyError, OSError) as error:
            raise XunjiClientError(
                "Could not resolve the fixed local transaction directory."
            ) from error
        home_path = Path(home_value)
    else:  # pragma: no cover - current transaction safety is POSIX-only.
        raise XunjiClientError(
            "Write and verification transaction safety currently require a POSIX host."
        )
    if not home_path.is_absolute():
        raise XunjiClientError("The fixed local transaction directory is invalid.")
    return home_path / ".cache" / "xunji-personalised-training" / "transactions"


def _credential_fingerprint(credential: str) -> str:
    return hashlib.sha256(credential.encode("utf-8")).hexdigest()


def _account_cache_dir(cache_root: Path, credential: str) -> Path:
    """Keep records from different Xunji keys in separate local namespaces."""

    fingerprint = _credential_fingerprint(credential)[:24]
    return cache_root / f"account-{fingerprint}"


def _fsync_directory(path: Path) -> None:
    """Persist directory-entry changes on supported POSIX filesystems."""

    if os.name != "posix":  # pragma: no cover - guarded writes are POSIX-only.
        raise OSError(errno.ENOTSUP, "Directory synchronisation requires POSIX.")
    flags = os.O_RDONLY
    flags |= getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _validate_private_directory(path: Path, *, label: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise XunjiClientError(f"The {label} directory is unavailable.") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise XunjiClientError(f"The {label} path is not a private local directory.")
    if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
        raise XunjiClientError(f"The {label} directory has an unexpected owner.")
    mode = stat.S_IMODE(metadata.st_mode)
    if mode & 0o077 or (mode & 0o700) != 0o700:
        raise XunjiClientError(f"The {label} directory permissions are not private.")


def _ensure_private_directory(path: Path, *, durable: bool = False) -> None:
    missing: list[Path] = []
    cursor = path
    while not cursor.exists() and cursor.parent != cursor:
        missing.append(cursor)
        cursor = cursor.parent
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as error:
        raise XunjiClientError("Could not create the private local directory.") from error
    if path.is_symlink() or not path.is_dir():
        raise XunjiClientError("The cache path is not a private local directory.")
    try:
        for directory in reversed(missing):
            directory.chmod(0o700)
            if durable:
                _fsync_directory(directory)
                _fsync_directory(directory.parent)
        path.chmod(0o700)
        if durable and not missing:
            _fsync_directory(path)
    except OSError as error:
        raise XunjiClientError("Could not secure the local cache directory.") from error
    _validate_private_directory(path, label="cache")


def _write_private_json(
    path: Path,
    value: Any,
    *,
    secure_parent: bool = False,
    durable: bool = False,
) -> None:
    parent = path.parent
    if secure_parent:
        _ensure_private_directory(parent, durable=durable)
    else:
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    serialised = json.dumps(
        value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
    ).encode("utf-8")
    temporary_name: str | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(prefix=".xunji-", dir=parent)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(serialised)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
        if durable:
            _fsync_directory(parent)
    except (OSError, TypeError, ValueError) as error:
        raise XunjiClientError("Could not write a protected local JSON file.") from error
    finally:
        if temporary_name:
            try:
                Path(temporary_name).unlink()
            except FileNotFoundError:
                pass


def _read_json_file(path: Path, *, label: str, size_limit: int = MAX_PLAN_BYTES) -> Any:
    try:
        if path.stat().st_size > size_limit:
            raise XunjiClientError(f"The {label} file exceeds the local size limit.")
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream)
    except FileNotFoundError as error:
        raise XunjiClientError(f"The {label} file was not found.") from error
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise XunjiClientError(f"The {label} file is not readable JSON.") from error


def _read_private_cache_json(path: Path) -> Any | None:
    """Read one owner-private regular cache file without following its final link."""

    try:
        link_metadata = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise XunjiClientError("Could not inspect the private read cache.") from error
    if stat.S_ISLNK(link_metadata.st_mode) or not stat.S_ISREG(link_metadata.st_mode):
        raise XunjiClientError("The read cache is not a private regular file.")
    if hasattr(os, "getuid") and link_metadata.st_uid != os.getuid():
        raise XunjiClientError("The read cache has an unexpected owner.")
    mode = stat.S_IMODE(link_metadata.st_mode)
    if mode & 0o077 or not mode & 0o400:
        raise XunjiClientError("The read cache permissions are not private.")
    if link_metadata.st_size > MAX_PLAN_BYTES:
        raise XunjiClientError("The read cache exceeds the local size limit.")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        opened_metadata = os.fstat(descriptor)
        opened_mode = stat.S_IMODE(opened_metadata.st_mode)
        if (
            not stat.S_ISREG(opened_metadata.st_mode)
            or opened_metadata.st_dev != link_metadata.st_dev
            or opened_metadata.st_ino != link_metadata.st_ino
            or (hasattr(os, "getuid") and opened_metadata.st_uid != os.getuid())
            or opened_mode & 0o077
            or not opened_mode & 0o400
            or opened_metadata.st_size > MAX_PLAN_BYTES
        ):
            raise XunjiClientError("The read cache changed during inspection.")
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            descriptor = None
            return json.load(stream)
    except (UnicodeError, json.JSONDecodeError):
        return None
    except OSError as error:
        raise XunjiClientError("The read cache could not be opened safely.") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def read_credential(
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Read the API credential only from the caller-provided environment."""

    values = os.environ if environ is None else environ
    environment_key = values.get("XUNJI_API_KEY", "").strip()
    if environment_key:
        return environment_key
    raise XunjiClientError(
        "Xunji credential unavailable; inject XUNJI_API_KEY into this local process."
    )


def _call_opener(
    opener: Callable[..., Any] | Any,
    request: urllib.request.Request,
    timeout: int,
) -> Any:
    target = opener.open if hasattr(opener, "open") else opener
    try:
        signature = inspect.signature(target)
        signature.bind(request, timeout=timeout)
    except (TypeError, ValueError):
        # Small injected test openers may accept only the request object.
        return target(request)
    return target(request, timeout=timeout)


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Prevent an authentication-bearing request from leaving the fixed host."""

    def redirect_request(
        self,
        request: urllib.request.Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> None:
        del request, file_pointer, code, message, headers, new_url
        return None


def _open_without_redirects(
    request: urllib.request.Request, *, timeout: int
) -> Any:
    opener = urllib.request.build_opener(_RejectRedirects())
    return opener.open(request, timeout=timeout)


def _post_json(
    endpoint: str,
    payload: Mapping[str, Any],
    credential: str,
    *,
    opener: Callable[..., Any] | Any | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    if endpoint not in {READ_ENDPOINT, WRITE_ENDPOINT}:
        raise XunjiClientError("Refusing a request outside the fixed Xunji endpoints.")
    if not credential:
        raise XunjiClientError("The Xunji credential is empty.")
    if _contains_credential_value(payload, credential):
        raise XunjiClientError("Refusing a request whose body contains the credential.")
    body = canonical_json_bytes(payload)
    credential_bytes = credential.encode("utf-8")
    if credential_bytes and credential_bytes in body:
        raise XunjiClientError("Refusing a request whose body contains the credential.")

    request = urllib.request.Request(
        f"{BASE_URL}{endpoint}",
        data=body,
        headers={
            "Authorization": f"Bearer {credential}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    selected_opener = _open_without_redirects if opener is None else opener
    response: Any | None = None
    try:
        response = _call_opener(selected_opener, request, timeout)
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise XunjiClientError("The Xunji response exceeded the local size limit.")
        headers = getattr(response, "headers", {})
        encoding = ""
        if hasattr(headers, "get"):
            encoding = str(headers.get("Content-Encoding", "")).casefold()
        if encoding == "gzip":
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as compressed:
                raw = compressed.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise XunjiClientError("The Xunji response exceeded the local size limit.")
        decoded = json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise XunjiClientError(
            f"The Xunji service rejected the request (HTTP {error.code})."
        ) from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise XunjiClientError("The Xunji service could not be reached safely.") from error
    except (UnicodeError, json.JSONDecodeError, gzip.BadGzipFile) as error:
        raise XunjiClientError("The Xunji service returned an unreadable response.") from error
    finally:
        if response is not None and hasattr(response, "close"):
            response.close()

    if not isinstance(decoded, dict) or decoded.get("success") is not True:
        raise XunjiClientError("The Xunji service rejected the request.")
    return decoded


def extract_sessions(response: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Extract detached session objects from either supported response shape."""

    result = response.get("res")
    if isinstance(result, dict):
        result = result.get("trains")
    if result is None:
        return []
    if not isinstance(result, list) or any(not isinstance(item, dict) for item in result):
        raise XunjiClientError("The Xunji response did not contain a valid session list.")
    return copy.deepcopy(result)


def _cache_path(cache_dir: Path, datestr: str, include_full_data: bool) -> Path:
    suffix = "full" if include_full_data else "summary"
    return cache_dir / f"read-{datestr}-{suffix}.json"


def _prepare_private_cache_directory(cache_root: Path, account_cache: Path) -> None:
    for directory, label in (
        (cache_root, "read-cache root"),
        (account_cache, "read-cache account"),
    ):
        if directory.exists() or directory.is_symlink():
            _validate_private_directory(directory, label=label)
        else:
            _ensure_private_directory(directory)


def _transaction_epoch_for_cache(
    transaction_cache: Path | None, datestr: str
) -> tuple[str, bool]:
    """Return marker digest and whether an ordinary cache may be used."""

    if transaction_cache is None:
        return "transactions-unavailable", True
    marker_path = transaction_cache / f"write-{datestr}-verification.json"
    if not marker_path.exists():
        return "no-transaction", True
    if marker_path.is_symlink() or not marker_path.is_file():
        raise XunjiClientError("The local transaction marker is not a regular file.")
    try:
        if marker_path.stat().st_size > MAX_PLAN_BYTES:
            raise XunjiClientError("The local transaction marker is too large.")
        marker_bytes = marker_path.read_bytes()
    except OSError as error:
        raise XunjiClientError("Could not inspect local transaction state.") from error
    return "sha256:" + hashlib.sha256(marker_bytes).hexdigest(), False


@contextmanager
def _read_transaction_guard(
    credential: str, datestr: str
) -> Iterable[Path | None]:
    """Serialise reads with local transactions when the fixed root is available."""

    try:
        transaction_root = _fixed_transaction_cache_dir()
    except XunjiClientError:
        if os.name == "posix":
            raise
        yield None
        return
    transaction_cache = _account_cache_dir(transaction_root, credential)
    with _date_transaction_lock(transaction_cache, datestr):
        yield transaction_cache


def _load_cached_sessions(
    path: Path,
    *,
    expected_credential_fingerprint: str,
    expected_datestr: str,
    expected_include_full_data: bool,
    expected_transaction_epoch: str,
) -> list[dict[str, Any]] | None:
    value = _read_private_cache_json(path)
    if value is None:
        return None
    if not isinstance(value, dict) or value.get("cache_version") != READ_CACHE_VERSION:
        return None
    if (
        value.get("credential_fingerprint") != expected_credential_fingerprint
        or value.get("datestr") != expected_datestr
        or value.get("include_full_data") is not expected_include_full_data
        or value.get("transaction_epoch") != expected_transaction_epoch
    ):
        return None
    sessions = value.get("sessions")
    if not isinstance(sessions, list) or any(not isinstance(item, dict) for item in sessions):
        return None
    return copy.deepcopy(sessions)


def _store_cached_sessions(
    cache_dir: Path,
    datestr: str,
    include_full_data: bool,
    sessions: Sequence[Mapping[str, Any]],
    *,
    credential_fingerprint: str,
    transaction_epoch: str,
) -> None:
    _write_private_json(
        _cache_path(cache_dir, datestr, include_full_data),
        {
            "cache_version": READ_CACHE_VERSION,
            "credential_fingerprint": credential_fingerprint,
            "datestr": datestr,
            "include_full_data": include_full_data,
            "transaction_epoch": transaction_epoch,
            "sessions": list(sessions),
        },
        secure_parent=True,
    )


def _network_read(
    datestr: str,
    *,
    include_full_data: bool,
    credential: str,
    opener: Callable[..., Any] | Any | None,
    timeout: int,
) -> list[dict[str, Any]]:
    response = _post_json(
        READ_ENDPOINT,
        {
            "schema_version": SCHEMA_VERSION,
            "datestr": datestr,
            "include_full_data": include_full_data,
        },
        credential,
        opener=opener,
        timeout=timeout,
    )
    return extract_sessions(response)


def read_sessions(
    datestr: str,
    *,
    include_full_data: bool = False,
    force_refresh: bool = False,
    use_cache: bool = True,
    cache_dir: Path | str | None = None,
    opener: Callable[..., Any] | Any | None = None,
    environ: Mapping[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    """Read a date, using a protected user-local cache unless forced."""

    valid_date = _validate_date(datestr, "datestr")
    selected_cache_root = (
        default_cache_dir(environ=environ)
        if cache_dir is None
        else Path(cache_dir).expanduser()
    )
    credential = read_credential(environ=environ)
    credential_fingerprint = _credential_fingerprint(credential)
    selected_cache = _account_cache_dir(selected_cache_root, credential)
    with _read_transaction_guard(credential, valid_date) as transaction_cache:
        if not use_cache:
            return _network_read(
                valid_date,
                include_full_data=include_full_data,
                credential=credential,
                opener=opener,
                timeout=timeout,
            )

        _prepare_private_cache_directory(selected_cache_root, selected_cache)
        cache_path = _cache_path(selected_cache, valid_date, include_full_data)
        for attempt in range(2):
            epoch_before, cache_allowed = _transaction_epoch_for_cache(
                transaction_cache, valid_date
            )
            if attempt == 0 and not force_refresh and cache_allowed:
                cached = _load_cached_sessions(
                    cache_path,
                    expected_credential_fingerprint=credential_fingerprint,
                    expected_datestr=valid_date,
                    expected_include_full_data=include_full_data,
                    expected_transaction_epoch=epoch_before,
                )
                epoch_after_cache, still_allowed = _transaction_epoch_for_cache(
                    transaction_cache, valid_date
                )
                if (
                    cached is not None
                    and still_allowed
                    and hmac.compare_digest(epoch_before, epoch_after_cache)
                ):
                    return cached

            sessions = _network_read(
                valid_date,
                include_full_data=include_full_data,
                credential=credential,
                opener=opener,
                timeout=timeout,
            )
            epoch_after, cache_still_allowed = _transaction_epoch_for_cache(
                transaction_cache, valid_date
            )
            if hmac.compare_digest(epoch_before, epoch_after):
                if cache_allowed and cache_still_allowed:
                    _store_cached_sessions(
                        selected_cache,
                        valid_date,
                        include_full_data,
                        sessions,
                        credential_fingerprint=credential_fingerprint,
                        transaction_epoch=epoch_after,
                    )
                return sessions

    raise XunjiClientError(
        "Local transaction state changed during the read; retry the date."
    )


def _truthy_completion_flag(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value > 0
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "done", "completed"}
    return False


def _positive_number(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number > 0


def _started_timestamp(value: Any) -> bool:
    if value is None:
        return False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _value_is_present(value) and str(value).strip() != "-1"
    return math.isfinite(number) and number >= 0


def _iter_set_tree(set_data: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    """Yield a set and every recursively nested child set."""

    yield set_data
    items = set_data.get("items", [])
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, Mapping) or not isinstance(item.get("set"), Mapping):
            continue
        yield from _iter_set_tree(item["set"])


def _iter_nested_sets(session: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    movements = session.get("movements", [])
    if not isinstance(movements, list):
        return
    for movement in movements:
        if not isinstance(movement, Mapping):
            continue
        sets = movement.get("sets", [])
        if not isinstance(sets, list):
            continue
        for set_data in sets:
            if not isinstance(set_data, Mapping):
                continue
            yield from _iter_set_tree(set_data)


def has_completion_signal(session: Mapping[str, Any]) -> bool:
    """Return true when overwriting could destroy recorded training evidence."""

    for field in ("done", "completed", "checked", "status"):
        if _truthy_completion_flag(session.get(field)):
            return True
    if _positive_number(session.get("trainedSeconds")):
        return True
    for field in ("started_at", "ended_at"):
        if _started_timestamp(session.get(field)):
            return True
    for field in ("rpe", "comment", "completionFeeling", "completion_feeling"):
        if _value_is_present(session.get(field)):
            return True
    if "start" in session and "end" in session:
        start = session.get("start")
        end = session.get("end")
        if _value_is_present(start) and _value_is_present(end) and str(start) != str(end):
            return True

    movements = session.get("movements", [])
    if isinstance(movements, list):
        for movement in movements:
            if not isinstance(movement, Mapping):
                continue
            for field in ("done", "completed", "checked", "status"):
                if _truthy_completion_flag(movement.get(field)):
                    return True
            if _positive_number(movement.get("trainedSeconds")):
                return True
            for field in ("rpe", "comment"):
                if _value_is_present(movement.get(field)):
                    return True

    for set_data in _iter_nested_sets(session):
        for field in ("done", "completed", "checked", "status"):
            if _truthy_completion_flag(set_data.get(field)):
                return True
        if _positive_number(set_data.get("trainedSeconds")):
            return True
        for field in (
            "rpe",
            "comment",
            "actualWeight",
            "actualReps",
            "actualDuration",
        ):
            if _value_is_present(set_data.get(field)):
                return True
    return False


def _same_identity_value(left: Any, right: Any) -> bool:
    return str(left) == str(right)


def _preflight_entries(
    indexed_entries: Sequence[tuple[int, Mapping[str, Any]]],
    existing_sessions: Sequence[Mapping[str, Any]],
) -> None:
    """Bind each update to its reviewed full-data snapshot immediately before write."""

    for index, entry in indexed_entries:
        payload = entry["xunji_payload"]
        date = str(payload["datestr"])
        title = str(payload["title"]).strip()
        title_key = _normalised_title(title)
        if "localid" not in payload:
            if any(
                _normalised_title(item.get("title", "")) == title_key
                for item in existing_sessions
            ):
                raise WriteSafetyError(
                    f"Write refused: session[{index}] would duplicate an existing date and title."
                )
            continue

        localid = payload["localid"]
        matches = [
            item
            for item in existing_sessions
            if "localid" in item and _same_identity_value(item.get("localid"), localid)
        ]
        if len(matches) != 1:
            raise WriteSafetyError(
                f"Write refused: session[{index}] update identity was not found uniquely."
            )
        existing = matches[0]
        if str(existing.get("datestr", date)) != date:
            raise WriteSafetyError(
                f"Write refused: session[{index}] update date does not match."
            )
        if has_completion_signal(existing):
            raise WriteSafetyError(
                f"Write refused: session[{index}] contains completion evidence."
            )
        original = entry.get("original_xunji_payload")
        if not isinstance(original, Mapping) or not hmac.compare_digest(
            hashlib.sha256(canonical_json_bytes(existing)).digest(),
            hashlib.sha256(canonical_json_bytes(original)).digest(),
        ):
            raise WriteSafetyError(
                f"Write refused: session[{index}] changed in Xunji after review; "
                "refresh the full record and prepare a new manifest."
            )
        if any(
            item is not existing
            and _normalised_title(item.get("title", "")) == title_key
            for item in existing_sessions
        ):
            raise WriteSafetyError(
                f"Write refused: session[{index}] would duplicate an existing title."
            )


def _group_entries_by_date(
    entries: Sequence[Mapping[str, Any]],
) -> dict[str, list[tuple[int, dict[str, Any]]]]:
    grouped: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for index, entry in enumerate(entries):
        payload = entry["xunji_payload"]
        grouped.setdefault(str(payload["datestr"]), []).append(
            (index, copy.deepcopy(dict(entry)))
        )
    return grouped


def _group_payloads_by_date(
    payloads: Sequence[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for payload in payloads:
        grouped.setdefault(str(payload["datestr"]), []).append(copy.deepcopy(dict(payload)))
    return grouped


def _verification_marker_path(cache_dir: Path, datestr: str) -> Path:
    return cache_dir / f"write-{datestr}-verification.json"


def _transaction_lock_path(cache_dir: Path, datestr: str) -> Path:
    return cache_dir / f"write-{datestr}.lock"


@contextmanager
def _date_transaction_lock(cache_dir: Path, datestr: str) -> Iterable[None]:
    """Serialise all cooperative writers and verifiers for one account/date."""

    _ensure_private_directory(cache_dir, durable=True)
    lock_path = _transaction_lock_path(cache_dir, datestr)
    descriptor: int | None = None
    try:
        descriptor = os.open(
            lock_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        os.write(descriptor, b"active\n")
        os.fsync(descriptor)
        _fsync_directory(cache_dir)
    except FileExistsError as error:
        raise WriteSafetyError(
            f"Transaction refused: {datestr} is already being written or verified; "
            "a stale lock requires local review, not an automatic retry."
        ) from error
    except OSError as error:
        if descriptor is not None:
            os.close(descriptor)
        raise XunjiClientError(
            f"Could not create the protected transaction lock for {datestr}."
        ) from error

    try:
        yield
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            lock_path.unlink()
            _fsync_directory(cache_dir)
        except FileNotFoundError:
            raise XunjiClientError(
                f"The transaction lock for {datestr} disappeared unexpectedly."
            )
        except OSError as error:
            raise XunjiClientError(
                f"Could not release the transaction lock for {datestr}; do not retry."
            ) from error


def _client_request_id(digest: str, datestr: str, batch_index: int = 0) -> str:
    return (
        "xunji-personalised-training-"
        f"{digest[:20]}-{datestr}-{batch_index}"
    )


def _write_transaction_marker(
    cache_dir: Path,
    datestr: str,
    digest: str,
    *,
    status: str,
    client_request_id: str,
    session_count: int,
    successful_write_requests: int,
) -> None:
    _write_private_json(
        _verification_marker_path(cache_dir, datestr),
        {
            "marker_version": 2,
            "datestr": datestr,
            "payload_sha256": digest,
            "status": status,
            "client_request_id": client_request_id,
            "session_count": session_count,
            "successful_write_requests": successful_write_requests,
        },
        secure_parent=True,
        durable=True,
    )


def _mark_write_intent(
    cache_dir: Path,
    datestr: str,
    digest: str,
    client_request_id: str,
    session_count: int,
    successful_write_requests: int,
) -> None:
    _write_transaction_marker(
        cache_dir,
        datestr,
        digest,
        status="write_intent",
        client_request_id=client_request_id,
        session_count=session_count,
        successful_write_requests=successful_write_requests,
    )


def _mark_write_ambiguous(
    cache_dir: Path,
    datestr: str,
    digest: str,
    client_request_id: str,
    session_count: int,
    successful_write_requests: int,
) -> None:
    _write_transaction_marker(
        cache_dir,
        datestr,
        digest,
        status="ambiguous",
        client_request_id=client_request_id,
        session_count=session_count,
        successful_write_requests=successful_write_requests,
    )


def _mark_verification_pending(
    cache_dir: Path,
    datestr: str,
    digest: str,
    successful_requests: int,
    client_request_id: str = "synthetic-or-legacy-request",
    session_count: int = 1,
) -> None:
    _write_transaction_marker(
        cache_dir,
        datestr,
        digest,
        status="verify_pending",
        client_request_id=client_request_id,
        session_count=session_count,
        successful_write_requests=successful_requests,
    )


def _load_transaction_marker(cache_dir: Path, datestr: str) -> Mapping[str, Any] | None:
    path = _verification_marker_path(cache_dir, datestr)
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise WriteSafetyError(
            f"The local transaction marker for {datestr} is not a regular file."
        )
    try:
        marker = _read_json_file(path, label="verification marker")
    except XunjiClientError as error:
        raise WriteSafetyError(
            f"The local transaction marker for {datestr} is unreadable."
        ) from error
    if not isinstance(marker, Mapping):
        raise WriteSafetyError(
            f"The local transaction marker for {datestr} is invalid."
        )
    return marker


def _transaction_state(
    cache_dir: Path,
    datestr: str,
    digest: str,
    *,
    expected_session_count: int,
) -> tuple[str, Mapping[str, Any] | None]:
    """Return the current plan's bound transaction state, failing closed."""

    marker = _load_transaction_marker(cache_dir, datestr)
    if marker is None:
        return "not_dispatched", None
    marker_digest = marker.get("payload_sha256")
    status = marker.get("status")
    request_id = marker.get("client_request_id")
    session_count = marker.get("session_count")
    successful_requests = marker.get("successful_write_requests")
    reconciled_from = marker.get("reconciled_from")
    if (
        marker.get("marker_version") != 2
        or marker.get("datestr") != datestr
        or status not in {
            "write_intent",
            "ambiguous",
            "verify_pending",
            "fully_verified",
            "reverify_pending",
            "drifted",
        }
        or not isinstance(marker_digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", marker_digest.casefold())
        or not isinstance(request_id, str)
        or request_id != _client_request_id(marker_digest.casefold(), datestr)
        or not isinstance(session_count, int)
        or isinstance(session_count, bool)
        or session_count < 1
        or not isinstance(successful_requests, int)
        or isinstance(successful_requests, bool)
        or successful_requests < 0
        or successful_requests > 1
        or (status == "verify_pending" and successful_requests != 1)
        or (status in {"write_intent", "ambiguous"} and successful_requests != 0)
        or (
            status in {"fully_verified", "reverify_pending", "drifted"}
            and successful_requests == 0
            and reconciled_from not in {"write_intent", "ambiguous"}
        )
        or (
            reconciled_from is not None
            and reconciled_from not in {"write_intent", "ambiguous"}
        )
    ):
        raise WriteSafetyError(
            f"The local transaction marker for {datestr} is invalid; local review is required."
        )
    same_digest = hmac.compare_digest(marker_digest.casefold(), digest)
    if not same_digest:
        if status in {"fully_verified", "reverify_pending", "drifted"}:
            return "not_dispatched", None
        raise WriteSafetyError(
            f"Transaction refused: {datestr} has another unresolved reviewed plan."
        )
    if session_count != expected_session_count:
        raise WriteSafetyError(
            f"The local transaction marker for {datestr} does not match this plan."
        )
    return str(status), marker


def _write_date_is_already_verified(
    cache_dir: Path,
    datestr: str,
    digest: str,
    *,
    expected_session_count: int,
) -> bool:
    state, _ = _transaction_state(
        cache_dir,
        datestr,
        digest,
        expected_session_count=expected_session_count,
    )
    if state in {"write_intent", "ambiguous", "verify_pending"}:
        raise WriteSafetyError(
            f"Write refused: {datestr} has an unresolved local transaction; "
            "perform full-data verification before another write."
        )
    if state in {"reverify_pending", "drifted"}:
        raise WriteSafetyError(
            f"Write refused: {datestr} has unresolved reverification or drifted "
            "remote data; perform fresh review and full-data verification."
        )
    return state == "fully_verified"


def write_plan(
    plan: Any,
    manifest: Any,
    profile: Any,
    baseline: Any | None = None,
    *,
    expected_digest: str,
    write_confirmed: bool,
    opener: Callable[..., Any] | Any | None = None,
    environ: Mapping[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Perform review-bound writes and return only a verification-pending status."""

    entries, digest = validate_write_authorisation(
        plan,
        manifest,
        profile,
        baseline,
        expected_digest=expected_digest,
        write_confirmed=write_confirmed,
    )
    credential = read_credential(environ=environ)
    selected_cache = _account_cache_dir(
        _fixed_transaction_cache_dir(), credential
    )
    grouped = _group_entries_by_date(entries)

    request_count = 0
    written_session_count = 0
    dates_pending: list[str] = []
    already_verified_dates: list[str] = []
    for datestr, day_entries in grouped.items():
        day_payloads = [entry["xunji_payload"] for _, entry in day_entries]
        with _date_transaction_lock(selected_cache, datestr):
            if _write_date_is_already_verified(
                selected_cache,
                datestr,
                digest,
                expected_session_count=len(day_payloads),
            ):
                already_verified_dates.append(datestr)
                continue
            existing_sessions = _network_read(
                datestr,
                include_full_data=True,
                credential=credential,
                opener=opener,
                timeout=timeout,
            )
            _preflight_entries(day_entries, existing_sessions)
            day_request_count = 0
            for start in range(0, len(day_payloads), MAX_SESSIONS_PER_REQUEST):
                batch = day_payloads[start : start + MAX_SESSIONS_PER_REQUEST]
                batch_index = start // MAX_SESSIONS_PER_REQUEST
                client_request_id = _client_request_id(digest, datestr, batch_index)
                request_payload = {
                    "schema_version": SCHEMA_VERSION,
                    "client_request_id": client_request_id,
                    "dry_run": False,
                    "include_full_data": False,
                    "res": batch,
                }
                _mark_write_intent(
                    selected_cache,
                    datestr,
                    digest,
                    client_request_id,
                    len(day_payloads),
                    day_request_count,
                )
                try:
                    _post_json(
                        WRITE_ENDPOINT,
                        request_payload,
                        credential,
                        opener=opener,
                        timeout=timeout,
                    )
                except XunjiClientError as error:
                    try:
                        _mark_write_ambiguous(
                            selected_cache,
                            datestr,
                            digest,
                            client_request_id,
                            len(day_payloads),
                            day_request_count,
                        )
                    except XunjiClientError:
                        # The atomically written intent remains the conservative state.
                        pass
                    raise XunjiClientError(
                        f"Write outcome for {datestr} is ambiguous; perform full-data "
                        "verification before any resend."
                    ) from error

                request_count += 1
                day_request_count += 1
                written_session_count += len(batch)
                try:
                    _mark_verification_pending(
                        selected_cache,
                        datestr,
                        digest,
                        day_request_count,
                        client_request_id,
                        len(day_payloads),
                    )
                except XunjiClientError as error:
                    raise XunjiClientError(
                        f"Xunji acknowledged the write for {datestr}, but local "
                        "transaction state could not advance. Perform full-data "
                        "verification before any resend."
                    ) from error
                if datestr not in dates_pending:
                    dates_pending.append(datestr)

    return {
        "status": "verify_pending" if dates_pending else "fully_verified",
        "payload_sha256": digest,
        "dates": dates_pending,
        "already_verified_dates": already_verified_dates,
        "session_count": written_session_count,
        "write_request_count": request_count,
    }


def _normalise_comparison_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _normalise_comparison_value(child)
            for key, child in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, list):
        return [_normalise_comparison_value(child) for child in value]
    if value is None or isinstance(value, bool):
        return value
    text = str(value)
    try:
        number = Decimal(text)
    except InvalidOperation:
        return text
    if not number.is_finite():
        return text
    if number == 0:
        return "0"
    return format(number.normalize(), "f")


def _compare_expected_fields(
    expected: Mapping[str, Any],
    actual: Mapping[str, Any],
    *,
    path: str,
    excluded: frozenset[str],
) -> list[str]:
    """Compare every reviewed field without treating remote-added fields as errors."""

    mismatches: list[str] = []
    for field, expected_value in expected.items():
        field_text = str(field)
        if field_text in excluded:
            continue
        if field not in actual:
            mismatches.append(f"{path}.{field_text} is missing")
        elif _normalise_comparison_value(actual[field]) != _normalise_comparison_value(
            expected_value
        ):
            mismatches.append(f"{path}.{field_text} differs")
    return mismatches


def compare_session_payload(
    expected: Mapping[str, Any],
    actual: Mapping[str, Any],
    *,
    session_index: int,
) -> list[str]:
    """Compare identity, order, counts, and target fields without exposing values."""

    base = f"session[{session_index}]"
    mismatches: list[str] = []
    for field in ("datestr", "title"):
        if _normalise_comparison_value(actual.get(field)) != _normalise_comparison_value(
            expected.get(field)
        ):
            mismatches.append(f"{base}.{field} differs")

    mismatches.extend(
        _compare_expected_fields(
            expected,
            actual,
            path=base,
            excluded=frozenset({"datestr", "title", "localid", "start", "end", "movements"}),
        )
    )

    if "localid" in expected:
        if not _same_identity_value(actual.get("localid"), expected.get("localid")):
            mismatches.append(f"{base}.localid differs")
    elif not _value_is_present(actual.get("localid")):
        mismatches.append(f"{base}.localid is missing after create")

    for field in ("start", "end"):
        if field in expected:
            if field not in actual or not _same_identity_value(
                actual.get(field), expected.get(field)
            ):
                mismatches.append(f"{base}.{field} differs")
        elif field not in actual:
            mismatches.append(f"{base}.{field} is missing after create")
    if (
        "start" not in expected
        and "end" not in expected
        and "start" in actual
        and "end" in actual
        and not _same_identity_value(actual.get("start"), actual.get("end"))
    ):
        mismatches.append(f"{base}.start/end added an unintended duration")

    expected_movements = expected.get("movements", [])
    actual_movements = actual.get("movements", [])
    if not isinstance(actual_movements, list):
        mismatches.append(f"{base}.movements is invalid")
        return mismatches
    expected_names = [
        str(movement.get("name", "")) for movement in expected_movements
    ]
    actual_names = [
        str(movement.get("name", "")) if isinstance(movement, Mapping) else ""
        for movement in actual_movements
    ]
    if actual_names != expected_names:
        mismatches.append(f"{base}.movement order differs")
        return mismatches

    for movement_index, (expected_movement, actual_movement) in enumerate(
        zip(expected_movements, actual_movements)
    ):
        movement_path = f"{base}.movements[{movement_index}]"
        if not isinstance(actual_movement, Mapping):
            mismatches.append(f"{movement_path} is invalid")
            continue
        mismatches.extend(
            _compare_expected_fields(
                expected_movement,
                actual_movement,
                path=movement_path,
                excluded=frozenset({"name", "sets"}),
            )
        )
        expected_sets = expected_movement.get("sets", [])
        actual_sets = actual_movement.get("sets", [])
        if not isinstance(actual_sets, list) or len(actual_sets) != len(expected_sets):
            mismatches.append(f"{movement_path}.set count differs")
            continue
        for set_index, (expected_set, actual_set) in enumerate(
            zip(expected_sets, actual_sets)
        ):
            set_path = f"{movement_path}.sets[{set_index}]"
            if not isinstance(actual_set, Mapping):
                mismatches.append(f"{set_path} is invalid")
                continue
            mismatches.extend(
                _compare_expected_fields(
                    expected_set,
                    actual_set,
                    path=set_path,
                    excluded=frozenset(),
                )
            )
    return mismatches


def _select_actual_session(
    expected: Mapping[str, Any],
    actual_sessions: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    expected_title = _normalised_title(expected.get("title", ""))
    title_matches = [
        item
        for item in actual_sessions
        if _normalised_title(item.get("title", "")) == expected_title
    ]
    if len(title_matches) != 1:
        return None
    if "localid" in expected:
        identity_matches = [
            item
            for item in actual_sessions
            if "localid" in item
            and _same_identity_value(item.get("localid"), expected.get("localid"))
        ]
        if len(identity_matches) != 1 or identity_matches[0] is not title_matches[0]:
            return None
    return title_matches[0]


def _transition_transaction_marker(
    cache_dir: Path,
    datestr: str,
    digest: str,
    *,
    expected_marker: Mapping[str, Any],
    status: str,
) -> dict[str, Any]:
    """Compare-and-swap one marker into a durable, explicitly allowed state."""

    allowed_transitions = {
        "write_intent": frozenset({"fully_verified"}),
        "ambiguous": frozenset({"fully_verified"}),
        "verify_pending": frozenset({"fully_verified"}),
        "fully_verified": frozenset({"reverify_pending"}),
        "reverify_pending": frozenset({"fully_verified", "drifted"}),
        "drifted": frozenset({"reverify_pending"}),
    }
    previous_status = str(expected_marker.get("status", ""))
    if status not in allowed_transitions.get(previous_status, frozenset()):
        raise WriteSafetyError(
            f"Verification refused: {datestr} requested an invalid transaction transition."
        )
    marker = _load_transaction_marker(cache_dir, datestr)
    if marker is None:  # pragma: no cover - guarded by verification readiness.
        raise WriteSafetyError(
            f"Verification refused: {datestr} transaction marker disappeared."
        )
    if not hmac.compare_digest(
        hashlib.sha256(canonical_json_bytes(marker)).digest(),
        hashlib.sha256(canonical_json_bytes(expected_marker)).digest(),
    ):
        raise WriteSafetyError(
            f"Verification refused: {datestr} transaction state changed during read-back."
        )
    next_marker: dict[str, Any] = {
        "marker_version": 2,
        "datestr": datestr,
        "payload_sha256": digest,
        "status": status,
        "client_request_id": expected_marker["client_request_id"],
        "session_count": expected_marker["session_count"],
        "successful_write_requests": expected_marker["successful_write_requests"],
    }
    if previous_status in {"write_intent", "ambiguous"}:
        next_marker["reconciled_from"] = previous_status
    elif expected_marker.get("reconciled_from") in {"write_intent", "ambiguous"}:
        next_marker["reconciled_from"] = expected_marker["reconciled_from"]
    _write_private_json(
        _verification_marker_path(cache_dir, datestr),
        next_marker,
        secure_parent=True,
        durable=True,
    )
    return next_marker


def _mark_verified(
    cache_dir: Path,
    datestr: str,
    digest: str,
    *,
    expected_marker: Mapping[str, Any],
) -> dict[str, Any]:
    return _transition_transaction_marker(
        cache_dir,
        datestr,
        digest,
        expected_marker=expected_marker,
        status="fully_verified",
    )


def verify_plan(
    plan: Any,
    manifest: Any,
    profile: Any,
    baseline: Any | None = None,
    *,
    opener: Callable[..., Any] | Any | None = None,
    environ: Mapping[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Force full reads and fail unless the reviewed payload is observable."""

    entries, digest = validate_manifest_binding(plan, manifest, profile, baseline)
    payloads = [entry["xunji_payload"] for entry in entries]
    credential = read_credential(environ=environ)
    selected_cache = _account_cache_dir(
        _fixed_transaction_cache_dir(), credential
    )
    indexed_by_date: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for index, payload in enumerate(payloads):
        indexed_by_date.setdefault(str(payload["datestr"]), []).append(
            (index, copy.deepcopy(payload))
        )

    mismatches: list[str] = []
    verified_dates: list[str] = []
    already_verified_dates: list[str] = []
    not_dispatched_dates: list[str] = []
    reconciled_dates: list[str] = []
    verified_session_count = 0
    for datestr, indexed_payloads in indexed_by_date.items():
        with _date_transaction_lock(selected_cache, datestr):
            state, marker = _transaction_state(
                selected_cache,
                datestr,
                digest,
                expected_session_count=len(indexed_payloads),
            )
            if state == "not_dispatched":
                not_dispatched_dates.append(datestr)
                continue
            original_state = state
            if state == "fully_verified":
                already_verified_dates.append(datestr)
            if marker is None:  # pragma: no cover - state binding requires a marker.
                raise WriteSafetyError(
                    f"Verification refused: {datestr} transaction marker disappeared."
                )
            if state in {"fully_verified", "drifted"}:
                marker = _transition_transaction_marker(
                    selected_cache,
                    datestr,
                    digest,
                    expected_marker=marker,
                    status="reverify_pending",
                )
                state = "reverify_pending"

            sessions = _network_read(
                datestr,
                include_full_data=True,
                credential=credential,
                opener=opener,
                timeout=timeout,
            )
            date_mismatches: list[str] = []
            for index, expected in indexed_payloads:
                actual = _select_actual_session(expected, sessions)
                if actual is None:
                    date_mismatches.append(
                        f"session[{index}] was not found uniquely"
                    )
                    continue
                date_mismatches.extend(
                    compare_session_payload(expected, actual, session_index=index)
                )
            if date_mismatches:
                if state == "reverify_pending":
                    _transition_transaction_marker(
                        selected_cache,
                        datestr,
                        digest,
                        expected_marker=marker,
                        status="drifted",
                    )
                mismatches.extend(date_mismatches)
                continue
            _mark_verified(
                selected_cache,
                datestr,
                digest,
                expected_marker=marker,
            )
            verified_dates.append(datestr)
            verified_session_count += len(indexed_payloads)
            if original_state in {"write_intent", "ambiguous"}:
                reconciled_dates.append(datestr)

    if mismatches:
        raise VerificationError(mismatches)
    if not verified_dates:
        raise WriteSafetyError(
            "Verification refused: this plan has no matching transaction marker."
        )
    status = "fully_verified" if not not_dispatched_dates else "partially_verified"
    return {
        "status": status,
        "payload_sha256": digest,
        "dates": verified_dates,
        "already_verified_dates": already_verified_dates,
        "not_dispatched_dates": not_dispatched_dates,
        "reconciled_dates": reconciled_dates,
        "session_count": verified_session_count,
        "planned_session_count": len(payloads),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Safely read, prepare, write, and verify Xunji training plans."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    read_parser = subparsers.add_parser("read", help="Read one Xunji training date.")
    read_parser.add_argument("--date", required=True, dest="datestr")
    read_parser.add_argument("--output", required=True, type=Path)
    read_parser.add_argument("--full", action="store_true", dest="include_full_data")
    read_parser.add_argument("--force-refresh", action="store_true")
    read_parser.add_argument("--cache-dir", type=Path)

    prepare_parser = subparsers.add_parser(
        "prepare-write", help="Validate locally and create a review manifest."
    )
    prepare_parser.add_argument("--plan", required=True, type=Path)
    prepare_parser.add_argument("--manifest", required=True, type=Path)
    prepare_parser.add_argument("--profile", required=True, type=Path)
    prepare_parser.add_argument("--baseline", type=Path)

    write_parser = subparsers.add_parser(
        "write", help="Write an explicitly reviewed payload."
    )
    write_parser.add_argument("--plan", required=True, type=Path)
    write_parser.add_argument("--manifest", required=True, type=Path)
    write_parser.add_argument("--profile", required=True, type=Path)
    write_parser.add_argument("--baseline", type=Path)
    write_parser.add_argument("--expected-digest", required=True)
    write_parser.add_argument("--write-confirmed", action="store_true")

    verify_parser = subparsers.add_parser(
        "verify", help="Force full reads and verify the written plan."
    )
    verify_parser.add_argument("--plan", required=True, type=Path)
    verify_parser.add_argument("--manifest", required=True, type=Path)
    verify_parser.add_argument("--profile", required=True, type=Path)
    verify_parser.add_argument("--baseline", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "read":
            sessions = read_sessions(
                arguments.datestr,
                include_full_data=arguments.include_full_data,
                force_refresh=arguments.force_refresh,
                cache_dir=arguments.cache_dir,
            )
            output = {
                "schema_version": SCHEMA_VERSION,
                "datestr": arguments.datestr,
                "include_full_data": arguments.include_full_data,
                "sessions": sessions,
            }
            _write_private_json(arguments.output.expanduser(), output)
            report = {
                "status": "read_saved_locally",
                "date": arguments.datestr,
                "full_data": arguments.include_full_data,
                "session_count": len(sessions),
                "output": str(arguments.output.expanduser()),
            }
        elif arguments.command == "prepare-write":
            plan = _read_json_file(arguments.plan.expanduser(), label="plan")
            profile = _read_json_file(
                arguments.profile.expanduser(), label="profile"
            )
            baseline = (
                _read_json_file(arguments.baseline.expanduser(), label="baseline")
                if arguments.baseline
                else None
            )
            report = prepare_write(plan, profile, baseline)
            _write_private_json(arguments.manifest.expanduser(), report)
        elif arguments.command == "write":
            plan = _read_json_file(arguments.plan.expanduser(), label="plan")
            manifest = _read_json_file(
                arguments.manifest.expanduser(), label="manifest"
            )
            profile = _read_json_file(
                arguments.profile.expanduser(), label="profile"
            )
            baseline = (
                _read_json_file(arguments.baseline.expanduser(), label="baseline")
                if arguments.baseline
                else None
            )
            report = write_plan(
                plan,
                manifest,
                profile,
                baseline,
                expected_digest=arguments.expected_digest,
                write_confirmed=arguments.write_confirmed,
            )
        elif arguments.command == "verify":
            plan = _read_json_file(arguments.plan.expanduser(), label="plan")
            manifest = _read_json_file(
                arguments.manifest.expanduser(), label="manifest"
            )
            profile = _read_json_file(
                arguments.profile.expanduser(), label="profile"
            )
            baseline = (
                _read_json_file(arguments.baseline.expanduser(), label="baseline")
                if arguments.baseline
                else None
            )
            report = verify_plan(
                plan,
                manifest,
                profile,
                baseline,
            )
        else:  # pragma: no cover - argparse enforces the command set.
            parser.error("Unknown command.")
            return 2
    except XunjiClientError as error:
        print(f"Error: {error}", file=sys.stderr)
        return error.exit_code

    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
