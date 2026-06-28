import argparse
import json
import subprocess
import sys
import time

from hera_app.controllers import HeraController


def send_command(process, command, **payload):
    request = {"id": f"{command}_{time.time():.6f}", "command": command, **payload}
    process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
    process.stdin.flush()
    while True:
        line = process.stdout.readline()
        if not line:
            raise RuntimeError("Hera service exited before responding.")
        response = json.loads(line)
        if response.get("event") == "ready":
            continue
        if response.get("id") == request["id"]:
            return response


def print_response(label, response):
    print(f"\n[{label}]")
    print(json.dumps(response, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Probe the long-lived Hera helper service.")
    parser.add_argument("--connect", action="store_true", help="Actually connect to Hera during the probe.")
    parser.add_argument("--dll-path", default=HeraController.default_dll_path(), help="Hera SDK DLL path.")
    parser.add_argument("--device-index", type=int, default=0, help="Hera device index.")
    args = parser.parse_args(argv)

    command = [sys.executable, "-m", "hera_app.helpers.hera_service", "--ready"]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        creationflags=creationflags,
    )
    try:
        ready = json.loads(process.stdout.readline())
        print_response("ready", ready)
        print_response("status-before", send_command(process, "status"))
        if args.connect:
            print_response(
                "connect",
                send_command(
                    process,
                    "connect",
                    dll_path=args.dll_path,
                    device_index=args.device_index,
                ),
            )
            print_response("status-after-connect", send_command(process, "status"))
            print_response("disconnect", send_command(process, "disconnect"))
        print_response("shutdown", send_command(process, "shutdown"))
        return process.wait(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
