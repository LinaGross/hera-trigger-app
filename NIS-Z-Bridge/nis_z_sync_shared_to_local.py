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
SHARED_COMMAND_MAX_AGE_SECONDS = 180.0
STALE_LOCAL_SLOT_SECONDS = 120.0
COMMAND_TIMESTAMP_RE = re.compile(r"(20\d{6}_\d{6})")
COMPLETE_RESPONSE_RE = re.compile(r"^(ERROR\b.*|OK\s+[-+]?\d+\.\d+.*)$")

COMMAND_SLOT_MAP = {
    "GET_Z": "current_getz",
    "MOVE_REL 1.000000": "current_move_rel_p1",
    "MOVE_REL -1.000000": "current_move_rel_m1",
    "MOVE_ABS 4100.000000 4050.000000 7000.000000": "current_move_abs_4100_4050_7000",
    "MOVE_ABS 4200.000000 4000.000000 8100.000000": "current_move_abs_4200_4000_8100",
    "STOP": "current_stop",
}

RESPONSE_SLOT_MAP = {
    "current_getz_response.txt": "current_getz",
    "current_move_rel_p1_response.txt": "current_move_rel_p1",
    "current_move_rel_m1_response.txt": "current_move_rel_m1",
    "current_move_abs_4100_4050_7000_response.txt": "current_move_abs_4100_4050_7000",
    "current_move_abs_4200_4000_8100_response.txt": "current_move_abs_4200_4000_8100",
    "current_stop_response.txt": "current_stop",
}
SLOT_RESPONSE_MAP = {slot_name: response_name for response_name, slot_name in RESPONSE_SLOT_MAP.items()}

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

    if stale_count and now - _LAST_STALE_BACKLOG_LOG_AT > 30.0:
        logging.info(
            "Ignoring %d stale shared command file(s); %d fresh file(s), %d without timestamp.",
            stale_count,
            len(fresh_commands),
            unknown_timestamp_count,
        )
        _LAST_STALE_BACKLOG_LOG_AT = now

    if not fresh_commands:
        return None

    return max(
        fresh_commands,
        key=lambda path: (command_timestamp_seconds(path) or 0.0, path.name),
    )


def copy_text_file(source: Path, destination: Path) -> None:
    temp_destination = destination.with_suffix(destination.suffix + ".tmp")
    shutil.copyfile(source, temp_destination)
    temp_destination.replace(destination)


def write_text_file(destination: Path, text: str) -> None:
    temp_destination = destination.with_suffix(destination.suffix + ".tmp")
    temp_destination.write_text(text, encoding="ascii", newline="\n")
    temp_destination.replace(destination)


def archive_name_conflict(destination: Path) -> Path:
    if not destination.exists():
        return destination

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    return destination.with_name(f"{destination.stem}_{timestamp}{destination.suffix}")


def state_file_for_slot(slot_name: str) -> Path:
    return LOCAL_STATE_DIR / f"{slot_name}.id"


def response_file_for_slot(slot_name: str) -> Path | None:
    response_name = SLOT_RESPONSE_MAP.get(slot_name)
    if response_name is None:
        return None
    return LOCAL_RESPONSES_DIR / response_name


def age_seconds(path: Path) -> float:
    return max(0.0, time.time() - path.stat().st_mtime)


def archive_local_file(source: Path, archive_root: Path, reason: str) -> Path:
    destination = archive_name_conflict(archive_root / f"{source.stem}__{reason}{source.suffix}")
    source.replace(destination)
    return destination


def recover_stale_local_slots() -> None:
    for local_command in iter_txt_files(LOCAL_COMMANDS_DIR):
        slot_name = local_command.stem
        slot_state = state_file_for_slot(slot_name)
        if age_seconds(local_command) <= STALE_LOCAL_SLOT_SECONDS:
            continue

        slot_response = response_file_for_slot(slot_name)
        if slot_response is not None and slot_response.exists() and is_complete_response(slot_response):
            continue

        if slot_state.exists():
            archived_command = archive_local_file(local_command, LOCAL_ERRORS_DIR, "stale_local_command")
            archived_state = archive_local_file(slot_state, LOCAL_ERRORS_DIR, "state_for_stale_command")
            logging.warning(
                "Archived stale local command %s -> %s and state %s -> %s",
                local_command,
                archived_command,
                slot_state,
                archived_state,
            )
            if slot_response is not None and slot_response.exists():
                archived_response = archive_local_file(slot_response, LOCAL_ERRORS_DIR, "response_for_stale_command")
                logging.warning("Archived stale local response %s -> %s", slot_response, archived_response)
            continue

        archived = archive_local_file(local_command, LOCAL_ERRORS_DIR, "orphan_local_command")
        logging.warning("Archived orphan local command %s -> %s", local_command, archived)


def response_text(local_response: Path) -> str:
    return local_response.read_text(encoding="ascii", errors="ignore").replace("\x00", "").strip()


def is_complete_response(local_response: Path) -> bool:
    try:
        text = response_text(local_response)
    except OSError:
        return False

    return bool(COMPLETE_RESPONSE_RE.match(text))


def forward_shared_commands() -> int:
    forwarded = 0

    shared_command = newest_fresh_shared_command()
    if shared_command is None:
        return 0

    for shared_command in [shared_command]:
        archived_command = archive_name_conflict(SHARED_FORWARDED_DIR / shared_command.name)

        try:
            command_text = shared_command.read_text(encoding="ascii").strip()
            slot_name = COMMAND_SLOT_MAP[command_text]
        except KeyError:
            logging.error("Unsupported shared command text: %s", shared_command)
            continue
        except Exception as exc:
            logging.exception("Failed to parse shared command %s: %s", shared_command, exc)
            continue

        local_command = LOCAL_COMMANDS_DIR / f"{slot_name}.txt"
        slot_state = state_file_for_slot(slot_name)
        if local_command.exists() or slot_state.exists():
            logging.warning("Local slot is busy, leaving shared command in place: %s", slot_name)
            continue

        # Clear any leftover processed command file so the macro's RenameFile
        # doesn't collide with a stale copy from a previous cycle.
        processed_command = LOCAL_PROCESSED_DIR / f"{slot_name}.txt"
        if processed_command.exists():
            try:
                processed_command.unlink()
                logging.info("Cleared stale processed command file: %s", processed_command)
            except OSError as exc:
                logging.warning("Could not clear stale processed command %s: %s", processed_command, exc)

        try:
            write_text_file(local_command, command_text + "\n")
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

        if not is_complete_response(local_response):
            try:
                incomplete_text = response_text(local_response)
            except OSError as exc:
                incomplete_text = f"<unreadable: {exc}>"
            logging.warning(
                "Waiting for complete local response in %s: %r",
                local_response,
                incomplete_text,
            )
            continue

        slot_state = state_file_for_slot(slot_name)
        if not slot_state.exists():
            logging.warning("Ignoring local response without slot state: %s", local_response)
            continue

        try:
            response_id = slot_state.read_text(encoding="ascii").strip()
            shared_response = SHARED_RESPONSES_DIR / f"{response_id}.txt"
            processed_response = archive_name_conflict(LOCAL_PROCESSED_DIR / f"{response_id}__{local_response.name}")

            copy_text_file(local_response, shared_response)
            local_response.replace(processed_response)
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
    raise KeyboardInterrupt


def main() -> int:
    ensure_directories()
    configure_logging()

    logging.info("Starting NIS Z fixed-slot shared/local sync bridge.")
    logging.info("Shared root: %s", SHARED_ROOT)
    logging.info("Local root: %s", LOCAL_ROOT)

    signal.signal(signal.SIGINT, request_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, request_stop)

    try:
        while not _STOP_REQUESTED:
            recover_stale_local_slots()
            forwarded = forward_shared_commands()
            published = publish_local_responses()

            if forwarded == 0 and published == 0:
                time.sleep(POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        logging.info("NIS Z fixed-slot shared/local sync bridge interrupted by user.")

    logging.info("NIS Z fixed-slot shared/local sync bridge stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
