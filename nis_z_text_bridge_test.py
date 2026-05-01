import argparse
import os
import shutil
import sys
import time
from datetime import datetime
from uuid import uuid4


DEFAULT_SHARED_FOLDER = (
    r"\\sti-nas1.rcp.epfl.ch\bios\bios-raw\backups\visible\cell"
    r"\Jiayi_bios-raw\Z control shared"
)
DEFAULT_TIMEOUT_SEC = 30.0
DEFAULT_MAX_RELATIVE_STEP_UM = 5.0


class BridgeError(RuntimeError):
    pass


def now_iso():
    return datetime.now().isoformat(timespec="milliseconds")


def bridge_paths(shared_folder):
    return {
        "shared": os.path.abspath(shared_folder),
        "commands": os.path.join(shared_folder, "commands"),
        "responses": os.path.join(shared_folder, "responses"),
    }


def ensure_bridge_folders(shared_folder):
    paths = bridge_paths(shared_folder)
    os.makedirs(paths["commands"], exist_ok=True)
    os.makedirs(paths["responses"], exist_ok=True)

    probe_path = os.path.join(paths["shared"], ".hera_z_text_bridge_probe")
    with open(probe_path, "w", encoding="utf-8") as handle:
        handle.write(now_iso())
    os.remove(probe_path)
    return paths


def write_text_atomic(path, text):
    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="ascii") as handle:
        handle.write(text)
        if not text.endswith("\n"):
            handle.write("\n")
    os.replace(temp_path, path)


def read_text(path):
    with open(path, "r", encoding="ascii", errors="replace") as handle:
        return handle.read().strip()


def command_file(paths, command_id):
    return os.path.join(paths["commands"], f"{command_id}.txt")


def response_file(paths, command_id):
    return os.path.join(paths["responses"], f"{command_id}.txt")


def list_txt_files(folder):
    if not os.path.isdir(folder):
        return []
    return sorted(name for name in os.listdir(folder) if name.lower().endswith(".txt"))


def build_command_text(action, *values):
    parts = [action]
    parts.extend(str(value) for value in values)
    return " ".join(parts)


def send_command(shared_folder, command_text, timeout_sec):
    paths = ensure_bridge_folders(shared_folder)
    command_id = uuid4().hex
    command_path = command_file(paths, command_id)
    response_path = response_file(paths, command_id)

    write_text_atomic(command_path, command_text)
    print(f"Command id: {command_id}")
    print(f"Command text: {command_text}")
    print(f"Wrote command: {command_path}")
    print(f"Waiting for response: {response_path}")

    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if os.path.exists(response_path):
            response = read_text(response_path)
            validate_response(response)
            return response
        time.sleep(0.1)

    raise TimeoutError(f"Timed out after {timeout_sec:.1f} s waiting for {response_path}")


def validate_response(response):
    if response.startswith("OK"):
        return
    if response.startswith("ERROR"):
        raise BridgeError(response)
    raise BridgeError(f"Unexpected response: {response}")


def require_yes(args, command_text):
    if args.yes:
        return
    print("Motion command preview; no file was written because --yes is missing:")
    print(command_text)
    raise BridgeError("Add --yes to send this motion command.")


def status(args):
    paths = ensure_bridge_folders(args.shared_folder)
    pending_commands = list_txt_files(paths["commands"])
    pending_responses = list_txt_files(paths["responses"])
    total, _used, free = shutil.disk_usage(paths["shared"])

    print("NIS Z text bridge shared folder is accessible.")
    print(f"Shared:    {paths['shared']}")
    print(f"Commands:  {paths['commands']}")
    print(f"Responses: {paths['responses']}")
    print(f"Pending text command files: {len(pending_commands)}")
    print(f"Existing text response files: {len(pending_responses)}")
    print(f"Free space: {free / (1024 ** 3):.1f} GB of {total / (1024 ** 3):.1f} GB")


def get_z(args):
    response = send_command(args.shared_folder, "GET_Z", args.timeout_sec)
    print(f"Received response: {response}")


def move_rel(args):
    dz_um = float(args.dz_um)
    max_step = abs(float(args.max_relative_step_um))
    if abs(dz_um) > max_step:
        raise BridgeError(f"Relative move {dz_um:.3f} um exceeds max allowed step {max_step:.3f} um.")

    command_text = build_command_text("MOVE_REL", f"{dz_um:.6f}")
    require_yes(args, command_text)
    response = send_command(args.shared_folder, command_text, args.timeout_sec)
    print(f"Received response: {response}")


def move_abs(args):
    z_um = float(args.z_um)
    min_z_um = float(args.min_z_um)
    max_z_um = float(args.max_z_um)
    if min_z_um >= max_z_um:
        raise BridgeError("--min-z-um must be smaller than --max-z-um.")
    if not (min_z_um <= z_um <= max_z_um):
        raise BridgeError(f"Absolute target {z_um:.3f} um is outside safe range {min_z_um:.3f}..{max_z_um:.3f} um.")

    command_text = build_command_text("MOVE_ABS", f"{z_um:.6f}", f"{min_z_um:.6f}", f"{max_z_um:.6f}")
    require_yes(args, command_text)
    response = send_command(args.shared_folder, command_text, args.timeout_sec)
    print(f"Received response: {response}")


def stop_z(args):
    command_text = "STOP"
    require_yes(args, command_text)
    response = send_command(args.shared_folder, command_text, args.timeout_sec)
    print(f"Received response: {response}")


def add_common_args(parser):
    parser.add_argument("--shared-folder", default=DEFAULT_SHARED_FOLDER, help="Shared folder visible to both HERA and NIS PCs.")
    parser.add_argument("--timeout-sec", type=float, default=DEFAULT_TIMEOUT_SEC, help="Seconds to wait for a NIS-side response.")


def build_parser():
    parser = argparse.ArgumentParser(description="HERA-side tester for the pure NIS macro Z text bridge.")
    subparsers = parser.add_subparsers(dest="command_name", required=True)

    status_parser = subparsers.add_parser("status", help="Verify shared folder access and bridge folders.")
    add_common_args(status_parser)
    status_parser.set_defaults(func=status)

    get_parser = subparsers.add_parser("get", help="Request current Z from the NIS macro bridge.")
    add_common_args(get_parser)
    get_parser.set_defaults(func=get_z)

    rel_parser = subparsers.add_parser("move-rel", help="Request a relative Z move in micrometers.")
    add_common_args(rel_parser)
    rel_parser.add_argument("--dz-um", type=float, required=True, help="Relative Z move in micrometers.")
    rel_parser.add_argument("--max-relative-step-um", type=float, default=DEFAULT_MAX_RELATIVE_STEP_UM)
    rel_parser.add_argument("--yes", action="store_true", help="Actually write the motion command.")
    rel_parser.set_defaults(func=move_rel)

    abs_parser = subparsers.add_parser("move-abs", help="Request an absolute Z move in micrometers.")
    add_common_args(abs_parser)
    abs_parser.add_argument("--z-um", type=float, required=True, help="Absolute Z target in micrometers.")
    abs_parser.add_argument("--min-z-um", type=float, required=True, help="Minimum safe absolute Z in micrometers.")
    abs_parser.add_argument("--max-z-um", type=float, required=True, help="Maximum safe absolute Z in micrometers.")
    abs_parser.add_argument("--yes", action="store_true", help="Actually write the motion command.")
    abs_parser.set_defaults(func=move_abs)

    stop_parser = subparsers.add_parser("stop", help="Request Z stop acknowledgement.")
    add_common_args(stop_parser)
    stop_parser.add_argument("--yes", action="store_true", help="Actually write the stop command.")
    stop_parser.set_defaults(func=stop_z)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
