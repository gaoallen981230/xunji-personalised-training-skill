#!/usr/bin/env python3
"""Run deterministic, offline release checks for the public Skill bundle."""

from __future__ import annotations

import ast
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
SKILL_NAME = "xunji-personalised-training"
SKILL_DIR = ROOT / "skill" / SKILL_NAME
ALLOWED_API_ORIGIN = "https://trains.xunjiapp.cn"
SYNTHETIC_PROFILE_ID = "synthetic-example-athlete"
SYNTHETIC_SOURCE = "synthetic_example"
EARLIEST_SYNTHETIC_DATE = date(2030, 1, 1)

_PERSONAL_HOME_PATH = re.compile(
    r"(?:/(?:Users|home)/+|[A-Za-z]:[\\/]+Users[\\/]+|"
    r"\\\\[^\\/]+[\\/]+Users[\\/]+)"
    r"(?P<account>[^\\/]+)(?=[\\/]|$)",
    flags=re.IGNORECASE,
)
_PUBLIC_ACCOUNT_PLACEHOLDERS = frozenset(
    {
        "<user>",
        "<username>",
        "{user}",
        "{username}",
        "${user}",
        "${username}",
        "%user%",
        "%username%",
        "example-user",
        "placeholder-user",
    }
)

ASSET_SCHEMAS = {
    "user-profile.example.json": "xunji_training_profile_v1",
    "weekly-check-in.example.json": "xunji_weekly_checkin_v1",
    "offline-weekly-plan.example.json": "personalised_training_plan_v1",
    "weekly-plan.example.json": "xunji_weekly_plan_v1",
}

CREDENTIAL_FIELD_NAMES = frozenset(
    {
        "accesstoken",
        "apikey",
        "apitoken",
        "auth",
        "authorisation",
        "authorization",
        "authorizationheader",
        "bearer",
        "clientsecret",
        "cookie",
        "credential",
        "credentials",
        "key",
        "password",
        "passwd",
        "privatekey",
        "refreshtoken",
        "secret",
        "sessiontoken",
        "token",
        "trainingdatakey",
        "xunjiapikey",
        "xunjikey",
        "xunjitrainingdatakey",
    }
)
CREDENTIAL_FIELD_FRAGMENTS = (
    "accesstoken",
    "apikey",
    "clientsecret",
    "credential",
    "password",
    "privatekey",
    "refreshtoken",
    "trainingdatakey",
)

PII_FIELD_NAMES = frozenset(
    {
        "accountid",
        "address",
        "birthdate",
        "birthday",
        "dateofbirth",
        "dob",
        "email",
        "emailaddress",
        "familyname",
        "firstname",
        "fullname",
        "givenname",
        "lastname",
        "mobile",
        "mobilenumber",
        "phone",
        "phonenumber",
        "postaladdress",
        "postcode",
        "streetaddress",
        "surname",
        "userid",
        "username",
    }
)
PII_FIELD_SUFFIXES = (
    "address",
    "email",
    "emailaddress",
    "mobile",
    "mobilenumber",
    "phone",
    "phonenumber",
)

ALLOWED_RELEASE_FILES = frozenset(
    {
        ".github/workflows/validate.yml",
        ".gitignore",
        "LICENSE",
        "PRIVACY.md",
        "README.md",
        "REVIEW_CHECKLIST.md",
        "SECURITY.md",
        "scripts/release_gate.py",
        f"skill/{SKILL_NAME}/SKILL.md",
        f"skill/{SKILL_NAME}/agents/openai.yaml",
        f"skill/{SKILL_NAME}/assets/user-profile.example.json",
        f"skill/{SKILL_NAME}/assets/weekly-check-in.example.json",
        f"skill/{SKILL_NAME}/assets/offline-weekly-plan.example.json",
        f"skill/{SKILL_NAME}/assets/weekly-plan.example.json",
        f"skill/{SKILL_NAME}/references/profile-and-onboarding.md",
        f"skill/{SKILL_NAME}/references/programme-design.md",
        f"skill/{SKILL_NAME}/references/safety-and-evidence.md",
        f"skill/{SKILL_NAME}/references/xunji-api-and-writeback.md",
        f"skill/{SKILL_NAME}/scripts/validate_plan.py",
        f"skill/{SKILL_NAME}/scripts/validate_profile.py",
        f"skill/{SKILL_NAME}/scripts/xunji_client.py",
        "tests/test_offline_profile.py",
        "tests/test_validators.py",
        "tests/test_xunji_client.py",
    }
)
REQUIRED_PATHS = tuple(ROOT / path for path in sorted(ALLOWED_RELEASE_FILES))

TEXT_SUFFIXES = {".md", ".py", ".json", ".yaml", ".yml", ".toml", ".txt"}
IGNORED_PARTS = {".git"}


def repository_files() -> list[Path]:
    return sorted(
        path
        for path in ROOT.rglob("*")
        if path.is_file() and not any(part in IGNORED_PARTS for part in path.parts)
    )


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def check_required(errors: list[str]) -> None:
    for path in REQUIRED_PATHS:
        if not path.is_file():
            errors.append(f"missing required file: {relative(path)}")


def check_file_boundary(files: Iterable[Path], errors: list[str]) -> None:
    for path in files:
        rel = path.relative_to(ROOT)
        if rel.as_posix() not in ALLOWED_RELEASE_FILES:
            errors.append(f"file is outside the release whitelist: {rel.as_posix()}")
        if path.name in {"AGENTS.md", "MEMORY.md"}:
            errors.append(f"private context file is forbidden: {rel.as_posix()}")
        if path.suffix.lower() in {".pyc", ".pyo", ".log"} or path.name.startswith(".env"):
            errors.append(f"generated or secret-bearing file is forbidden: {rel.as_posix()}")
        if path.is_symlink():
            errors.append(f"symbolic links are forbidden in the release: {rel.as_posix()}")

    for forbidden in (SKILL_DIR / "README.md", SKILL_DIR / "LICENSE"):
        if forbidden.exists():
            errors.append(f"repository documentation must stay outside the Skill: {relative(forbidden)}")


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise ValueError("SKILL.md must start with YAML frontmatter")
    try:
        closing = lines.index("---", 1)
    except ValueError as exc:
        raise ValueError("SKILL.md frontmatter is not closed") from exc
    metadata: dict[str, str] = {}
    for line in lines[1:closing]:
        if not line.strip():
            continue
        key, separator, value = line.partition(":")
        if not separator or not key.strip() or not value.strip():
            raise ValueError(f"unsupported frontmatter line: {line}")
        metadata[key.strip()] = value.strip().strip('"')
    return metadata, "\n".join(lines[closing + 1 :])


def check_skill_metadata(errors: list[str]) -> None:
    path = SKILL_DIR / "SKILL.md"
    if not path.is_file():
        return
    text = read_text(path)
    if len(text.splitlines()) >= 500:
        errors.append("SKILL.md must remain below 500 lines")
    try:
        metadata, body = parse_frontmatter(text)
    except ValueError as exc:
        errors.append(str(exc))
        return
    if set(metadata) != {"name", "description"}:
        errors.append("SKILL.md frontmatter must contain only name and description")
    if metadata.get("name") != SKILL_NAME or SKILL_DIR.name != SKILL_NAME:
        errors.append("Skill folder and frontmatter name must match")
    if len(metadata.get("description", "")) < 80:
        errors.append("Skill description is too short to provide reliable triggering context")
    if not body.strip():
        errors.append("SKILL.md body is empty")

    for target in re.findall(r"\[[^\]]+\]\((references/[^)]+)\)", body):
        candidate = SKILL_DIR / target
        if not candidate.is_file():
            errors.append(f"SKILL.md reference is missing: {target}")
        elif candidate.parent != SKILL_DIR / "references":
            errors.append(f"Skill references must be one level deep: {target}")


def quoted_yaml_value(text: str, key: str) -> str | None:
    match = re.search(rf"^\s*{re.escape(key)}:\s*\"([^\"]*)\"\s*$", text, re.MULTILINE)
    return match.group(1) if match else None


def check_openai_yaml(errors: list[str]) -> None:
    path = SKILL_DIR / "agents" / "openai.yaml"
    if not path.is_file():
        return
    text = read_text(path)
    display_name = quoted_yaml_value(text, "display_name")
    short_description = quoted_yaml_value(text, "short_description")
    default_prompt = quoted_yaml_value(text, "default_prompt")
    if not display_name:
        errors.append("agents/openai.yaml is missing quoted display_name")
    if not short_description or not 25 <= len(short_description) <= 64:
        errors.append("short_description must contain 25 to 64 characters")
    if not default_prompt or f"${SKILL_NAME}" not in default_prompt:
        errors.append("default_prompt must explicitly mention the Skill")
    if not re.search(r"^\s*allow_implicit_invocation:\s*false\s*$", text, re.MULTILINE):
        errors.append("implicit invocation must remain disabled for this write-capable Skill")


def check_python_and_json(files: Iterable[Path], errors: list[str]) -> None:
    for path in files:
        if path.suffix == ".py":
            try:
                ast.parse(read_text(path), filename=relative(path))
            except (SyntaxError, UnicodeDecodeError) as exc:
                errors.append(f"invalid Python in {relative(path)}: {exc}")
        elif path.suffix == ".json":
            try:
                json.loads(read_text(path))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                errors.append(f"invalid JSON in {relative(path)}: {exc}")


def compact_field_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.casefold())


def walk_json(value: Any, location: str = "$") -> Iterable[tuple[str, str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_location = f"{location}.{key}"
            yield child_location, key, child
            yield from walk_json(child, child_location)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            child_location = f"{location}[{index}]"
            yield child_location, "", child
            yield from walk_json(child, child_location)


def contains_synthetic_marker(value: Any) -> bool:
    if isinstance(value, str):
        return "synthetic" in value.casefold() or "合成示例" in value
    if isinstance(value, list):
        return any(contains_synthetic_marker(item) for item in value)
    if isinstance(value, dict):
        return any(contains_synthetic_marker(item) for item in value.values())
    return False


def looks_like_phone_number(value: str) -> bool:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
        return False
    if not re.fullmatch(r"\s*\+?[\d(). -]+\s*", value):
        return False
    digit_count = len(re.sub(r"\D", "", value))
    return 8 <= digit_count <= 15


def contains_personal_home_path(value: str) -> bool:
    """Detect concrete macOS, Linux, or Windows home paths, not placeholders."""

    return any(
        match.group("account").casefold() not in _PUBLIC_ACCOUNT_PLACEHOLDERS
        for match in _PERSONAL_HOME_PATH.finditer(value)
    )


def check_synthetic_asset_content(
    path: Path,
    data: dict[str, Any],
    errors: list[str],
) -> None:
    rel = relative(path)
    email_pattern = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
    for location, key, value in walk_json(data):
        compact_key = compact_field_name(key)
        is_credential_field = compact_key in CREDENTIAL_FIELD_NAMES or any(
            fragment in compact_key for fragment in CREDENTIAL_FIELD_FRAGMENTS
        )
        if is_credential_field:
            errors.append(f"credential field is forbidden in {rel}: {location}")
        if compact_key.endswith("localid"):
            errors.append(f"localid is forbidden in a synthetic asset: {rel}: {location}")
        is_pii_field = (
            compact_key in PII_FIELD_NAMES
            or any(compact_key.endswith(suffix) for suffix in PII_FIELD_SUFFIXES)
            or (
                compact_key.endswith("name")
                and compact_key not in {"name", "movementname"}
            )
            or (
                compact_key == "name"
                and ".movements[" not in location
            )
        )
        if is_pii_field:
            errors.append(f"PII field is forbidden in {rel}: {location}")

        if isinstance(value, str):
            if email_pattern.search(value):
                errors.append(f"possible email address in {rel}: {location}")
            if looks_like_phone_number(value):
                errors.append(f"possible phone number in {rel}: {location}")
            if contains_personal_home_path(value):
                errors.append(f"possible personal home path in {rel}: {location}")

        is_date_field = (
            compact_key in {"date", "datestr", "weekstart"}
            or key.casefold().endswith("_date")
            or key.endswith("Date")
        )
        if not is_date_field:
            continue
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            errors.append(f"synthetic date must use YYYY-MM-DD in {rel}: {location}")
            continue
        try:
            parsed_date = date.fromisoformat(value)
        except ValueError:
            errors.append(f"invalid synthetic date in {rel}: {location}")
            continue
        if parsed_date < EARLIEST_SYNTHETIC_DATE:
            errors.append(
                f"synthetic date precedes {EARLIEST_SYNTHETIC_DATE.isoformat()} "
                f"in {rel}: {location}"
            )


def load_synthetic_asset(path: Path, errors: list[str]) -> dict[str, Any] | None:
    if not path.is_file():
        errors.append(f"missing synthetic asset: {relative(path)}")
        return None
    try:
        data = json.loads(read_text(path))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        errors.append(f"unreadable synthetic asset {relative(path)}: {exc}")
        return None
    if not isinstance(data, dict):
        errors.append(f"synthetic asset root must be an object: {relative(path)}")
        return None
    return data


def check_synthetic_assets(errors: list[str]) -> None:
    assets_dir = SKILL_DIR / "assets"
    loaded: dict[str, dict[str, Any]] = {}

    for filename, expected_schema in ASSET_SCHEMAS.items():
        path = assets_dir / filename
        data = load_synthetic_asset(path, errors)
        if data is None:
            continue
        loaded[filename] = data
        if data.get("schema_version") != expected_schema:
            errors.append(
                f"unexpected schema_version in {relative(path)}; expected {expected_schema}"
            )
        if data.get("profile_id") != SYNTHETIC_PROFILE_ID:
            errors.append(
                f"synthetic profile_id must be {SYNTHETIC_PROFILE_ID} in {relative(path)}"
            )
        check_synthetic_asset_content(path, data, errors)

    profile = loaded.get("user-profile.example.json")
    if profile is not None and not contains_synthetic_marker(profile.get("unconfirmed")):
        errors.append("synthetic profile must declare its provenance in unconfirmed")

    check_in = loaded.get("weekly-check-in.example.json")
    if check_in is not None:
        if check_in.get("source") != SYNTHETIC_SOURCE:
            errors.append(f"synthetic check-in source must be {SYNTHETIC_SOURCE}")
        if not contains_synthetic_marker(check_in.get("unconfirmed")):
            errors.append("synthetic check-in must declare its provenance in unconfirmed")
        if "week_start" not in check_in:
            errors.append("synthetic check-in is missing week_start")

    plan = loaded.get("weekly-plan.example.json")
    if plan is not None:
        if "week_start" not in plan:
            errors.append("synthetic weekly plan is missing week_start")
        if not contains_synthetic_marker(plan.get("change_summary")):
            errors.append(
                "synthetic weekly plan must declare its provenance in change_summary"
            )

        sessions = plan.get("sessions")
        if not isinstance(sessions, list) or not sessions:
            errors.append("synthetic weekly plan sessions must be a non-empty list")
        else:
            han_character = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
            for session_index, session in enumerate(sessions):
                prefix = f"synthetic weekly plan session {session_index}"
                if not isinstance(session, dict):
                    errors.append(f"{prefix} must be an object")
                    continue
                session_key = session.get("session_key")
                if not isinstance(session_key, str) or not session_key.startswith(
                    "synthetic-"
                ):
                    errors.append(f"{prefix} must use a synthetic- session_key")
                payload = session.get("xunji_payload")
                if not isinstance(payload, dict):
                    errors.append(f"{prefix} xunji_payload must be an object")
                    continue
                if "datestr" not in payload:
                    errors.append(f"{prefix} xunji_payload is missing datestr")
                if not contains_synthetic_marker(payload.get("title")):
                    errors.append(
                        f"{prefix} title must explicitly identify the synthetic example"
                    )
                movements = payload.get("movements")
                if not isinstance(movements, list) or not movements:
                    errors.append(f"{prefix} movements must be a non-empty list")
                    continue
                for movement_index, movement in enumerate(movements):
                    if not isinstance(movement, dict):
                        errors.append(
                            f"{prefix} movement {movement_index} must be an object"
                        )
                        continue
                    name = movement.get("name")
                    if not isinstance(name, str) or not han_character.search(name):
                        errors.append(
                            f"{prefix} movement {movement_index} name must contain Chinese text"
                        )

    offline_plan = loaded.get("offline-weekly-plan.example.json")
    if offline_plan is None:
        return
    if "week_start" not in offline_plan:
        errors.append("synthetic offline weekly plan is missing week_start")
    if not contains_synthetic_marker(offline_plan.get("change_summary")):
        errors.append(
            "synthetic offline weekly plan must declare its provenance in change_summary"
        )
    write_control = offline_plan.get("write_control")
    if not isinstance(write_control, dict) or write_control.get(
        "remote_write_requested"
    ) is not False:
        errors.append("synthetic offline weekly plan must disable remote writes")
    for location, key, _value in walk_json(offline_plan):
        if compact_field_name(key) in {"xunjipayload", "originalxunjipayload"}:
            errors.append(
                "synthetic offline weekly plan contains a forbidden Xunji payload: "
                f"{location}"
            )

    offline_sessions = offline_plan.get("sessions")
    if not isinstance(offline_sessions, list) or not offline_sessions:
        errors.append("synthetic offline weekly plan sessions must be a non-empty list")
        return
    for session_index, session in enumerate(offline_sessions):
        prefix = f"synthetic offline weekly plan session {session_index}"
        if not isinstance(session, dict):
            errors.append(f"{prefix} must be an object")
            continue
        session_key = session.get("session_key")
        if not isinstance(session_key, str) or not session_key.startswith("synthetic-"):
            errors.append(f"{prefix} must use a synthetic- session_key")
        if "date" not in session:
            errors.append(f"{prefix} is missing date")
        if not contains_synthetic_marker(session.get("title")):
            errors.append(f"{prefix} title must explicitly identify the synthetic example")
        programme = session.get("programme")
        movements = programme.get("movements") if isinstance(programme, dict) else None
        if not isinstance(movements, list) or not movements:
            errors.append(f"{prefix} movements must be a non-empty list")
            continue
        for movement_index, movement in enumerate(movements):
            if not isinstance(movement, dict):
                errors.append(f"{prefix} movement {movement_index} must be an object")
                continue
            if not isinstance(movement.get("name"), str) or not movement["name"].strip():
                errors.append(
                    f"{prefix} movement {movement_index} name must be non-empty"
                )


def check_private_content(files: Iterable[Path], errors: list[str]) -> None:
    literal_credential = re.compile(
        r"XUNJI_API_KEY\s*[:=]\s*[\"']([^\"']+)[\"']",
        flags=re.IGNORECASE,
    )
    bearer_token = re.compile(r"Bearer\s+[A-Za-z0-9_-]{20,}", flags=re.IGNORECASE)
    real_localid = re.compile(
        r'[\"\']localid[\"\']\s*:\s*[\"\'][0-9a-f]{8}-[0-9a-f-]{20,}[\"\']',
        flags=re.IGNORECASE,
    )
    prohibited_directions = (("foot" + "ball"), ("hy" + "rox"), ("\u8db3" + "\u7403"))

    for path in files:
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {"LICENSE", ".gitignore"}:
            continue
        text = read_text(path)
        rel = relative(path)
        unfinished_marker = "TO" + "DO"
        if unfinished_marker in text or f"[{unfinished_marker}" in text:
            errors.append(f"unfinished marker in {rel}")
        if contains_personal_home_path(text):
            errors.append(f"absolute personal path in {rel}")
        if bearer_token.search(text):
            errors.append(f"possible literal bearer credential in {rel}")
        if real_localid.search(text):
            errors.append(f"possible real localid in {rel}")
        for match in literal_credential.finditer(text):
            value = match.group(1).lower()
            if not any(marker in value for marker in ("synthetic", "example", "placeholder", "test")):
                errors.append(f"possible literal Xunji credential in {rel}")
        lowered = text.lower()
        if path != Path(__file__).resolve() and any(term.lower() in lowered for term in prohibited_directions):
            errors.append(f"personal training direction is forbidden in the general release: {rel}")


def check_network_boundary(files: Iterable[Path], errors: list[str]) -> None:
    allowed = urlsplit(ALLOWED_API_ORIGIN)
    url_pattern = re.compile("http" + r"s?://[^\s\"')]+")
    for path in files:
        if path.suffix != ".py":
            continue
        for url in url_pattern.findall(read_text(path)):
            cleaned = url.rstrip(".,;:")
            parsed = urlsplit(cleaned)
            try:
                port = parsed.port
            except ValueError:
                port = -1
            if (
                parsed.scheme != "https"
                or parsed.hostname != allowed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or port not in (None, 443)
            ):
                errors.append(f"unexpected network origin in {relative(path)}: {cleaned}")


def main() -> int:
    errors: list[str] = []
    files = repository_files()
    check_required(errors)
    check_file_boundary(files, errors)
    check_skill_metadata(errors)
    check_openai_yaml(errors)
    check_python_and_json(files, errors)
    check_synthetic_assets(errors)
    check_private_content(files, errors)
    check_network_boundary(files, errors)

    report = {
        "status": "pass" if not errors else "fail",
        "file_count": len(files),
        "skill": SKILL_NAME,
        "errors": errors,
        "manual_review_required": True,
        "manual_review_note": (
            "Automated checks reduce accidental disclosure but do not prove that examples "
            "are private-data-free; a human must review all release files before publication."
        ),
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
