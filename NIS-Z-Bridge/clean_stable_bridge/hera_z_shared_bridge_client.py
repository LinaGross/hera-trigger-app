from __future__ import annotations

import time
import uuid
from pathlib import Path

SHARED_ROOT = Path(r"\\sti-nas1.rcp.epfl.ch\bios\bios-raw\backups\visible\cell\Jiayi_bios-raw\Z control shared")

SUPPORTED_COMMANDS = {
    "GET_Z",
    "MOVE_REL 1.000000",
    "MOVE_REL -1.000000",
    "MOVE_ABS 4100.000000 4050.000000 7000.000000",
    "MOVE_ABS 4200.000000 4000.000000 8100.000000",
    "STOP",
}


def send_nis_z_command(command_text: str, timeout_sec: int = 90, shared_root: Path = SHARED_ROOT) -> str:
    command_text = command_text.strip()
    if command_text not in SUPPORTED_COMMANDS:
        raise RuntimeError(f"Unsupported command for stable bridge: {command_text!r}")

    commands_dir = shared_root / "commands"
    responses_dir = shared_root / "responses"
    commands_dir.mkdir(parents=True, exist_ok=True)
    responses_dir.mkdir(parents=True, exist_ok=True)

    command_id = f"hera_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    command_path = commands_dir / f"{command_id}.txt"
    response_path = responses_dir / f"{command_id}.txt"
    tmp_path = command_path.with_suffix(".txt.tmp")

    tmp_path.write_text(command_text + "\n", encoding="ascii", newline="\n")
    tmp_path.replace(command_path)
    print(f"Sent command: {command_path}")

    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if response_path.exists():
            raw = response_path.read_bytes()
            if len(raw) > 1 and raw[1] == 0:
                return raw.decode("utf-16-le", errors="replace").replace("\x00", "").strip()
            return raw.decode("ascii", errors="ignore").strip()
        time.sleep(0.25)

    raise TimeoutError(f"No response received: {response_path}")


if __name__ == "__main__":
    print(send_nis_z_command("GET_Z"))
