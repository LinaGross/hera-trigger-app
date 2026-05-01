from __future__ import annotations

import logging
import shutil
import signal
import sys
import time
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

_STOP_REQUESTED = False


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
    return sorted(
        (path for path in folder.glob(f"*{COMMAND_SUFFIX}") if path.is_file()),
        key=lambda path: (path.stat().st_mtime, path.name),
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


def forward_shared_commands() -> int:
    forwarded = 0
    for shared_command in iter_txt_files(SHARED_COMMANDS_DIR):
        archived_command = archive_name_conflict(SHARED_FORWARDED_DIR / shared_command.name)
        try:
            command_text = shared_command.read_text(encoding="ascii").strip()
            slot_name = COMMAND_SLOT_MAP[command_text]
        except KeyError:
            logging.error("Unsupported shared command text in %s", shared_command)
            continue
        except Exception as exc:
            logging.exception("Failed to parse shared command %s: %s", shared_command, exc)
            continue

        local_command = LOCAL_COMMANDS_DIR / f"{slot_name}.txt"
        slot_state = state_file_for_slot(slot_name)
        if local_command.exists() or slot_state.exists():
            logging.warning("Local slot is busy, leaving shared command in place: %s", slot_name)
            continue

        try:
            write_text_file(local_command, command_text + "\n")
            write_text_file(slot_state, shared_command.stem + "\n")
            shared_command.replace(archived_command)
            logging.info("Forwarded %s into local slot %s", shared_command.name, slot_name)
            forwarded += 1
        except Exception as exc:
            logging.exception("Failed to forward shared command %s: %s", shared_command, exc)
            for path in (local_command, slot_state):
                try:
                    if path.exists():
                        path.unlink()
                except OSError:
                    pass
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
        try:
            response_id = slot_state.read_text(encoding="ascii").strip()
            shared_response = SHARED_RESPONSES_DIR / f"{response_id}.txt"
            processed_response = archive_name_conflict(LOCAL_PROCESSED_DIR / f"{response_id}__{local_response.name}")
            copy_text_file(local_response, shared_response)
            local_response.replace(processed_response)
            slot_state.unlink()
            logging.info("Published response %s -> %s", local_response.name, shared_response.name)
            published += 1
        except Exception as exc:
            logging.exception("Failed to publish local response %s: %s", local_response, exc)
    return published


def request_stop(signum: int, _frame: object) -> None:
    global _STOP_REQUESTED
    _STOP_REQUESTED = True
    logging.info("Stop requested by signal %s", signum)


def main() -> int:
    ensure_directories()
    configure_logging()
    signal.signal(signal.SIGINT, request_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, request_stop)

    logging.info("Starting NIS Z shared/local sync bridge")
    logging.info("Shared root: %s", SHARED_ROOT)
    logging.info("Local root: %s", LOCAL_ROOT)

    while not _STOP_REQUESTED:
        forwarded = forward_shared_commands()
        published = publish_local_responses()
        if forwarded == 0 and published == 0:
            time.sleep(POLL_INTERVAL_SECONDS)

    logging.info("Stopped NIS Z shared/local sync bridge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
