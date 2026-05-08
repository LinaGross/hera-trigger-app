from __future__ import annotations

import logging
import os
import re
import shutil
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable

SHARED_ROOT = Path(r"\\sti-nas1.rcp.epfl.ch\bios\bios-raw\backups\visible\cell\Jiayi_bios-raw\Z control shared")
LOCAL_ROOT = Path(r"E:\Jiayi\NISZBridge")

SHARED_COMMANDS_DIR = SHARED_ROOT / "commands"
SHARED_RESPONSES_DIR = SHARED_ROOT / "responses"
SHARED_FORWARDED_DIR = SHARED_ROOT / "forwarded"

LOCAL_COMMANDS_DIR = LOCAL_ROOT / "commands"
LOCAL_RESPONSES_DIR = LOCAL_ROOT / "responses"
LOCAL_PROCESSED_DIR = LOCAL_ROOT / "processed"
LOCAL_ERRORS_DIR = LOCAL_ROOT / "errors"
LOCAL_STATE_DIR = LOCAL_ROOT / "state"

LOG_PATH = LOCAL_ROOT / "nis_z_sync.log"
POLL_INTERVAL_SECONDS = 1.0
COMMAND_SUFFIX = ".txt"
STALE_SLOT_SECONDS = 120.0
SHARED_COMMAND_MAX_AGE_SECONDS = 180.0
COMMAND_TIMESTAMP_RE = re.compile(r"(20\d{6}_\d{6})")
COMPLETE_OK_RESPONSE_RE = re.compile(r"^OK\s+[-+]?\d+\.\d+\s*$")
LOCAL_RESPONSE_MIN_AGE_SECONDS = 0.75

COMMAND_SLOT_MAP = {
    "GET_Z": "current_getz",
    # MOVE_REL handled dynamically below (any step value → current_move_rel_custom)
    "MOVE_ABS 4100.000000 4050.000000 7000.000000": "current_move_abs_4100_4050_7000",
    "MOVE_ABS 4200.000000 4000.000000 8100.000000": "current_move_abs_4200_4000_8100",
    "STOP": "current_stop",
}

RESPONSE_SLOT_MAP = {
    "current_getz_response.txt": "current_getz",
    "current_move_rel_custom_response.txt": "current_move_rel_custom",
    "current_move_abs_4100_4050_7000_response.txt": "current_move_abs_4100_4050_7000",
    "current_move_abs_4200_4000_8100_response.txt": "current_move_abs_4200_4000_8100",
    "current_stop_response.txt": "current_stop",
}

ACTIVE_LOCAL_COMMAND_NAMES = {
    "current_getz.txt",
    "current_move_rel_custom.txt",
    "current_move_abs_4100_4050_7000.txt",
    "current_move_abs_4200_4000_8100.txt",
    "current_stop.txt",
}

EXPECTED_LOCAL_RESPONSE_NAMES = set(RESPONSE_SLOT_MAP)

_STOP_REQUESTED = False
_LAST_STALE_BACKLOG_LOG_AT = 0.0


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def ensure_directories() -> None:
    for path in (
        SHARED_COMMANDS_DIR,
        SHARED_RESPONSES_DIR,
        SHARED_FORWARDED_DIR,
        LOCAL_COMMANDS_DIR,
        LOCAL_RESPONSES_DIR,
        LOCAL_PROCESSED_DIR,
        LOCAL_ERRORS_DIR,
        LOCAL_STATE_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)


def iter_txt_files(folder: Path) -> Iterable[Path]:
    # Avoid Path.is_file()/stat() here. On this NAS UNC path, Python stat calls
    # can hang even though direct file reads work. Filenames are enough because
    # the bridge only creates .txt command/response files in these folders.
    try:
        names = os.listdir(folder)
    except FileNotFoundError:
        return []

    return [
        folder / name
        for name in sorted(names)
        if name.lower().endswith(COMMAND_SUFFIX)
    ]


def command_timestamp_seconds(path: Path) -> float | None:
    match = COMMAND_TIMESTAMP_RE.search(path.stem)
    if not match:
        return None

    try:
        return datetime.strptime(match.group(1), "%Y%m%d_%H%M%S").timestamp()
    except ValueError:
        return None


def shared_command_sort_key(path: Path) -> tuple[float, str]:
    timestamp = command_timestamp_seconds(path)
    if timestamp is None:
        return (0.0, path.name)
    return (timestamp, path.name)


def newest_fresh_shared_command() -> Path | None:
    global _LAST_STALE_BACKLOG_LOG_AT

    commands = list(iter_txt_files(SHARED_COMMANDS_DIR))
    if not commands:
        return None

    now = time.time()
    fresh_commands = []
    stale_count = 0
    unknown_timestamp_count = 0

    for command in commands:
        timestamp = command_timestamp_seconds(command)
        if timestamp is None:
            unknown_timestamp_count += 1
            fresh_commands.append(command)
            continue

        if now - timestamp > SHARED_COMMAND_MAX_AGE_SECONDS:
            stale_count += 1
            continue

        fresh_commands.append(command)

    if stale_count:
        elapsed = now - _LAST_STALE_BACKLOG_LOG_AT
        if elapsed > 30.0:
            logging.info(
                "Ignoring %d stale shared command file(s); %d fresh file(s), %d without timestamp.",
                stale_count,
                len(fresh_commands),
                unknown_timestamp_count,
            )
            _LAST_STALE_BACKLOG_LOG_AT = now

    if not fresh_commands:
        return None

    return max(fresh_commands, key=shared_command_sort_key)


def copy_text_file(source: Path, destination: Path) -> None:
    temp_destination = destination.with_suffix(destination.suffix + ".tmp")
    shutil.copyfile(source, temp_destination)
    temp_destination.replace(destination)


def write_text_file(destination: Path, text: str) -> None:
    temp_destination = destination.with_suffix(destination.suffix + ".tmp")
    temp_destination.write_text(text, encoding="ascii", newline="\n")
    temp_destination.replace(destination)


def decode_response_bytes(raw: bytes) -> str:
    if len(raw) > 1 and raw[1] == 0:
        text = raw.decode("utf-16-le", errors="replace")
    else:
        text = raw.decode("ascii", errors="replace")
    return text.replace("\x00", "").strip()


def read_response_text(path: Path) -> str:
    return decode_response_bytes(path.read_bytes())


def is_complete_response(text: str) -> bool:
    if text.startswith("ERROR "):
        return True
    if text.startswith("OK "):
        return COMPLETE_OK_RESPONSE_RE.match(text) is not None
    return False


def archive_name_conflict(destination: Path) -> Path:
    if not destination.exists():
        return destination

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    return destination.with_name(f"{destination.stem}_{timestamp}{destination.suffix}")


def state_file_for_slot(slot_name: str) -> Path:
    return LOCAL_STATE_DIR / f"{slot_name}.id"


def age_seconds(path: Path) -> float:
    return max(0.0, time.time() - path.stat().st_mtime)


def archive_local_file(source: Path, archive_root: Path, reason: str) -> Path:
    archive_name = f"{source.stem}__{reason}{source.suffix}"
    destination = archive_name_conflict(archive_root / archive_name)
    source.replace(destination)
    return destination


def describe_optional_file(path: Path) -> str:
    if not path.exists():
        return "missing"

    try:
        age = age_seconds(path)
        text = path.read_text(encoding="ascii", errors="replace").strip()
    except Exception as exc:
        return f"present but unreadable: {exc}"

    return f"present age={age:.1f}s text={text!r}"


def recover_stale_local_slots() -> None:
    for local_command in iter_txt_files(LOCAL_COMMANDS_DIR):
        if local_command.name not in ACTIVE_LOCAL_COMMAND_NAMES:
            archived = archive_local_file(local_command, LOCAL_ERRORS_DIR, "unsupported_local_command")
            logging.warning("Archived unsupported local command %s -> %s", local_command, archived)
            continue

        if age_seconds(local_command) <= STALE_SLOT_SECONDS:
            continue

        archived = archive_local_file(local_command, LOCAL_ERRORS_DIR, "stale_local_command")
        logging.warning("Archived stale local command %s -> %s", local_command, archived)

        slot_state = state_file_for_slot(local_command.stem)
        if slot_state.exists():
            archived_state = archive_local_file(slot_state, LOCAL_ERRORS_DIR, "state_for_stale_command")
            logging.warning("Archived state for stale local command %s -> %s", slot_state, archived_state)

    for slot_state in sorted(
        (path for path in LOCAL_STATE_DIR.glob("*.id") if path.is_file()),
        key=lambda path: (path.stat().st_mtime, path.name),
    ):
        slot_name = slot_state.stem
        local_command = LOCAL_COMMANDS_DIR / f"{slot_name}.txt"

        matching_response = None
        for response_name, response_slot in RESPONSE_SLOT_MAP.items():
            if response_slot == slot_name:
                candidate = LOCAL_RESPONSES_DIR / response_name
                if candidate.exists():
                    matching_response = candidate
                    break

        if local_command.exists() or matching_response is not None:
            continue

        if age_seconds(slot_state) <= STALE_SLOT_SECONDS:
            continue

        archived = archive_local_file(slot_state, LOCAL_ERRORS_DIR, "stale_slot_state")
        logging.warning("Archived stale slot state %s -> %s", slot_state, archived)

    for local_response in iter_txt_files(LOCAL_RESPONSES_DIR):
        if local_response.name not in EXPECTED_LOCAL_RESPONSE_NAMES:
            continue

        try:
            response_text = read_response_text(local_response)
        except Exception:
            response_text = ""

        if response_text and not is_complete_response(response_text) and age_seconds(local_response) > LOCAL_RESPONSE_MIN_AGE_SECONDS:
            archived = archive_local_file(local_response, LOCAL_ERRORS_DIR, "incomplete_local_response")
            logging.warning("Archived incomplete local response %s -> %s: %r", local_response, archived, response_text)
            continue

        if age_seconds(local_response) <= STALE_SLOT_SECONDS:
            continue

        slot_name = RESPONSE_SLOT_MAP[local_response.name]
        slot_state = state_file_for_slot(slot_name)
        if slot_state.exists():
            continue

        archived = archive_local_file(local_response, LOCAL_ERRORS_DIR, "orphan_local_response")
        logging.warning("Archived orphan local response %s -> %s", local_response, archived)


def forward_shared_commands() -> int:
    forwarded = 0

    shared_command = newest_fresh_shared_command()
    if shared_command is None:
        return 0

    for shared_command in [shared_command]:
        archived_command = archive_name_conflict(SHARED_FORWARDED_DIR / shared_command.name)

        try:
            command_text = shared_command.read_text(encoding="ascii").strip()
        except Exception as exc:
            logging.exception("Failed to read shared command %s: %s", shared_command, exc)
            continue

        # Route any MOVE_REL to the generic custom slot; write only the numeric delta so
        # the NIS macro can parse it with ReadFile + atof (no hardcoded step required).
        if command_text.startswith("MOVE_REL "):
            slot_name = "current_move_rel_custom"
            local_content = command_text[len("MOVE_REL "):] + "\n"
        elif command_text in COMMAND_SLOT_MAP:
            slot_name = COMMAND_SLOT_MAP[command_text]
            local_content = command_text + "\n"
        else:
            logging.error("Unsupported shared command text in %s: %r", shared_command, command_text)
            continue

        local_command = LOCAL_COMMANDS_DIR / f"{slot_name}.txt"
        slot_state = state_file_for_slot(slot_name)
        if local_command.exists() or slot_state.exists():
            response_name = next(
                (name for name, response_slot in RESPONSE_SLOT_MAP.items() if response_slot == slot_name),
                None,
            )
            local_response = LOCAL_RESPONSES_DIR / response_name if response_name else None
            logging.warning(
                "Local slot is busy, leaving shared command in place: %s; command=%s; state=%s; response=%s",
                slot_name,
                describe_optional_file(local_command),
                describe_optional_file(slot_state),
                describe_optional_file(local_response) if local_response else "not expected",
            )
            continue

        try:
            write_text_file(local_command, local_content)
            write_text_file(slot_state, shared_command.stem + "\n")
            shared_command.replace(archived_command)
            logging.info(
                "Forwarded shared command %s into slot %s and archived to %s",
                shared_command,
                slot_name,
                archived_command,
            )
            forwarded += 1
        except Exception as exc:
            if local_command.exists():
                try:
                    local_command.unlink()
                except OSError:
                    logging.exception("Failed to remove partial local command %s", local_command)
            if slot_state.exists():
                try:
                    slot_state.unlink()
                except OSError:
                    logging.exception("Failed to remove partial slot state %s", slot_state)
            logging.exception("Failed to forward shared command %s: %s", shared_command, exc)

    return forwarded


def publish_local_responses() -> int:
    published = 0

    for local_response in iter_txt_files(LOCAL_RESPONSES_DIR):
        slot_name = RESPONSE_SLOT_MAP.get(local_response.name)
        if slot_name is None:
            continue

        slot_state = state_file_for_slot(slot_name)
        if not slot_state.exists():
            logging.warning("Ignoring local response without slot state: %s", local_response)
            continue

        if age_seconds(local_response) < LOCAL_RESPONSE_MIN_AGE_SECONDS:
            continue

        try:
            response_text = read_response_text(local_response)
            if not is_complete_response(response_text):
                archived_response = archive_local_file(local_response, LOCAL_ERRORS_DIR, "incomplete_local_response")
                logging.warning(
                    "Archived incomplete local response %s -> %s: %r",
                    local_response,
                    archived_response,
                    response_text,
                )
                continue

            response_id = slot_state.read_text(encoding="ascii").strip()
            shared_response = SHARED_RESPONSES_DIR / f"{response_id}.txt"
            processed_response = archive_name_conflict(LOCAL_PROCESSED_DIR / f"{response_id}__{local_response.name}")
            local_command = LOCAL_COMMANDS_DIR / f"{slot_name}.txt"
            processed_command = archive_name_conflict(LOCAL_PROCESSED_DIR / f"{response_id}__{slot_name}.txt")

            copy_text_file(local_response, shared_response)
            local_response.replace(processed_response)
            if local_command.exists():
                local_command.replace(processed_command)
            slot_state.unlink()
            logging.info(
                "Published local response %s -> %s and archived to %s",
                local_response,
                shared_response,
                processed_response,
            )
            published += 1
        except Exception as exc:
            logging.exception("Failed to publish local response %s: %s", local_response, exc)

    return published


def request_stop(signum: int, _frame: object) -> None:
    global _STOP_REQUESTED
    _STOP_REQUESTED = True
    logging.info("Received signal %s, stopping after current poll cycle.", signum)


def main() -> int:
    ensure_directories()
    configure_logging()

    signal.signal(signal.SIGINT, request_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, request_stop)

    logging.info("Starting NIS Z fixed-slot shared/local sync bridge.")
    logging.info("Shared root: %s", SHARED_ROOT)
    logging.info("Local root: %s", LOCAL_ROOT)
    recover_stale_local_slots()

    while not _STOP_REQUESTED:
        recover_stale_local_slots()
        forwarded = forward_shared_commands()
        published = publish_local_responses()

        if forwarded == 0 and published == 0:
            time.sleep(POLL_INTERVAL_SECONDS)

    logging.info("NIS Z fixed-slot shared/local sync bridge stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
