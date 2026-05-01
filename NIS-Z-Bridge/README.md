# NIS Z Bridge

This folder is the NIS-PC side of the Nikon Z bridge.

The bridge is split into two parts:

- `nis_z_sync_shared_to_local.py`
  A normal Windows Python script that watches the shared NAS folder, maps supported shared commands into fixed local slot files, and publishes local responses back to the shared `responses\` folder.
- `nis_z_local_text_bridge_watcher.mac`
  A NIS macro that only touches local paths under `E:\Jiayi\NISZBridge` and calls Nikon stage APIs.

This separation is intentional. NIS should not read from or write to the UNC path directly.

## Stable scope of this version

This README describes the last pushed and end-to-end verified version.

Verified end-to-end commands:

- `GET_Z`
- `MOVE_REL 1.000000`
- `MOVE_REL -1.000000`
- `MOVE_ABS 4100.000000 4050.000000 7000.000000`
- `MOVE_ABS 4200.000000 4000.000000 8100.000000`
- `STOP`

Not included in this stable version:

- continuous auto-listening macro
- arbitrary `MOVE_ABS z min max`
- arbitrary `MOVE_REL dz`

The stable version is a fixed-slot bridge. Only the exact commands above are forwarded.

## Folder layout on the NIS PC

Main local root:

`E:\Jiayi\NISZBridge`

Local folders:

- `commands\`
  Fixed local command slot files consumed by the NIS macro.
- `responses\`
  Fixed local response files produced by the NIS macro.
- `processed\`
  Local archive for processed command and response files.
- `errors\`
  Local archive for failed command files.
- `state\`
  Local mapping files that let the Python sync script map a fixed local slot back to the original shared command id.

Main files:

- [nis_z_sync_shared_to_local.py](</E:/Jiayi/NISZBridge/nis_z_sync_shared_to_local.py>)
- [nis_z_local_text_bridge_watcher.mac](</E:/Jiayi/NISZBridge/nis_z_local_text_bridge_watcher.mac>)
- [README.md](</E:/Jiayi/NISZBridge/README.md>)
- `nis_z_sync.log`

## Shared-folder contract

Shared root:

`\\sti-nas1.rcp.epfl.ch\bios\bios-raw\backups\visible\cell\Jiayi_bios-raw\Z control shared`

Shared folders:

- `commands\<id>.txt`
  The other PC writes a new plain-text command file here.
- `forwarded\<id>.txt`
  After Python sync accepts a command, it moves the original shared file here as an archive.
- `responses\<id>.txt`
  After NIS produces a local response, Python sync writes the final response here using the same original `<id>`.

Important:

- New commands must be created in shared `commands\`.
- Do not edit files in `forwarded\`.
- `forwarded\` is only history, not an active queue.

## Fixed-slot mapping used by the stable version

Shared command text to local slot:

- shared `GET_Z`
  local `commands\current_getz.txt`
- shared `MOVE_REL 1.000000`
  local `commands\current_move_rel_p1.txt`
- shared `MOVE_REL -1.000000`
  local `commands\current_move_rel_m1.txt`
- shared `MOVE_ABS 4100.000000 4050.000000 7000.000000`
  local `commands\current_move_abs_4100_4050_7000.txt`
- shared `MOVE_ABS 4200.000000 4000.000000 8100.000000`
  local `commands\current_move_abs_4200_4000_8100.txt`
- shared `STOP`
  local `commands\current_stop.txt`

Local response slot to shared response:

- `current_getz_response.txt`
- `current_move_rel_p1_response.txt`
- `current_move_rel_m1_response.txt`
- `current_move_abs_4100_4050_7000_response.txt`
- `current_move_abs_4200_4000_8100_response.txt`
- `current_stop_response.txt`

Each local response is mapped back to the original shared `<id>.txt` using `state\*.id`.

## What the other PC should do

The other PC only needs access to the shared NAS folder.

To send a command:

1. Create a new plain-text file with a unique filename ending in `.txt`.
2. Put it into:
   `\\sti-nas1.rcp.epfl.ch\bios\bios-raw\backups\visible\cell\Jiayi_bios-raw\Z control shared\commands\`
3. Write exactly one supported command line inside.

Examples:

```text
GET_Z
```

```text
MOVE_REL 1.000000
```

```text
MOVE_REL -1.000000
```

```text
MOVE_ABS 4100.000000 4050.000000 7000.000000
```

```text
MOVE_ABS 4200.000000 4000.000000 8100.000000
```

```text
STOP
```

Then wait for the result in:

`\\sti-nas1.rcp.epfl.ch\bios\bios-raw\backups\visible\cell\Jiayi_bios-raw\Z control shared\responses\<same_id>.txt`

Expected response format:

- `OK <z_um>`
- `ERROR <message>`

## Recommended PowerShell example on the other PC

```powershell
$id = [guid]::NewGuid().ToString("N")
$root = "\\sti-nas1.rcp.epfl.ch\bios\bios-raw\backups\visible\cell\Jiayi_bios-raw\Z control shared"
$cmd = Join-Path $root "commands\$id.txt"
$resp = Join-Path $root "responses\$id.txt"

Set-Content -LiteralPath $cmd -Value "GET_Z" -Encoding ascii
Write-Host "Command sent: $cmd"

for($i = 0; $i -lt 30; $i++) {
    if(Test-Path $resp) {
        Write-Host "Response received:"
        Get-Content $resp
        break
    }
    Start-Sleep -Seconds 1
}
```

## What the NIS PC operator should do

The NIS PC has two roles:

- keep the Python sync script running
- open NIS and run the macro when a command should be processed

### 1. Start the Python sync script

From Windows PowerShell on the NIS PC:

```powershell
& 'C:\Users\adminbios\AppData\Local\Programs\Python\Python312\python.exe' .\nis_z_sync_shared_to_local.py
```

Run it from:

`E:\Jiayi\NISZBridge`

The script will:

- ensure local folders and shared folders exist
- watch shared `commands\`
- map supported shared commands into fixed local command slot files
- store the original shared command id into local `state\`
- move accepted shared commands into shared `forwarded\`
- watch local `responses\`
- publish local responses back into shared `responses\<id>.txt`
- archive published local responses into local `processed\`
- append runtime logs to `nis_z_sync.log`

### 2. Run the NIS macro

1. Open NIS-Elements on the NIS PC.
2. Make sure NIS is connected to the microscope and stage.
3. Open [nis_z_local_text_bridge_watcher.mac](</E:/Jiayi/NISZBridge/nis_z_local_text_bridge_watcher.mac>).
4. Run the macro once to process the current local command slot.

Important:

- In this stable version, the macro is a single-run worker, not a continuous listener.
- That means one `Run` handles one currently present fixed-slot command and then exits.
- If a new command arrives later, run the macro again.

## End-to-end flow

The stable end-to-end flow is:

1. The other PC creates `shared\commands\<id>.txt`.
2. Python sync reads the command text.
3. Python sync maps it into a fixed local slot file under `E:\Jiayi\NISZBridge\commands\`.
4. Python sync writes `state\<slot>.id` containing the original shared command id.
5. Python sync moves the original shared file into shared `forwarded\`.
6. The NIS operator runs the macro.
7. The macro checks the fixed slot file and executes the matching Nikon stage action.
8. The macro writes a fixed local response file under `responses\`.
9. The macro moves the consumed local command into `processed\` or `errors\`.
10. Python sync sees the local response, reads the matching `state\*.id`, and writes the final result into shared `responses\<id>.txt`.
11. Python sync archives the local response into local `processed\`.

## Known good tests from validation

During validation, these command families worked end-to-end:

- `GET_Z`
  Returned around `OK 4099.35`
- `MOVE_REL 1.000000`
  Returned around `OK 4100.35`
- `MOVE_REL -1.000000`
  Returned around `OK 4099.3`
- `MOVE_ABS 4100.000000 4050.000000 7000.000000`
  Returned `OK 4100`

The `4200 / 4000 / 8100` absolute move was also verified successfully.

## Troubleshooting checklist

If a new shared command stays in shared `commands\`:

- Python sync is probably not running.
- Or the local slot for that command is still busy.

If a shared command quickly moves into shared `forwarded\`:

- That is normal.
- It means Python sync accepted it and moved it into the next stage.

If the command reached local `commands\` but no shared response appears:

- Check whether the NIS macro has been run.
- Check local `responses\`, `processed\`, and `errors\`.

If a local response exists but no shared response appears:

- Check `nis_z_sync.log`
- Check whether the matching `state\*.id` file still exists

Useful places to inspect:

- `E:\Jiayi\NISZBridge\commands`
- `E:\Jiayi\NISZBridge\responses`
- `E:\Jiayi\NISZBridge\processed`
- `E:\Jiayi\NISZBridge\errors`
- `E:\Jiayi\NISZBridge\state`
- `E:\Jiayi\NISZBridge\nis_z_sync.log`

## NIS macro pitfalls and lessons learned

These points were learned during debugging and should be kept in mind if the macro is upgraded later.

### Architecture pitfalls

- Do not let the NIS macro read or write the UNC path directly.
- Keep the NIS macro local-only.
- Let Python handle shared-folder movement and shared-folder bookkeeping.

### Nikon API pitfalls

- Do not use `StgZ_GetLimits`.
  In earlier testing this caused NIS to close.
- `STOP` should be treated conservatively.
  In this bridge it returns the current Z only; it does not forcibly interrupt a running `StgMoveZ`.

### Macro language pitfalls

- Keep the macro simple.
- Prefer a single `main()`.
- Avoid helper functions unless they are proven safe in this exact NIS environment.
- Avoid complex C-style abstractions. They were much less reliable than direct linear code.

Observed pain points:

- `ReadFile(...)` for command text was unreliable in this workflow.
- Dynamic command parsing was fragile.
- `sprintf(..., "%s", ...)` caused failures in string path building.
- Pointer-heavy string handling such as `strrchr(...)` based path parsing was fragile.
- Comment-heavy or more complex macro files sometimes behaved unexpectedly during testing.
- A macro that looked syntactically fine could still silently fail in the interpreter.

### Stable pattern that worked

The most reliable NIS pattern found during debugging was:

- fixed local file names
- one command family per explicit branch
- direct `ExistFile(...)` checks
- direct `WriteFile(...)`
- direct `StgGetPosZ(...)` and `StgMoveZ(...)`
- minimal string handling

### Why the bridge is fixed-slot

The fixed-slot design is not the final ideal architecture, but it was the stable one in this environment.

It avoids:

- command text parsing inside NIS
- dynamic filename parsing inside NIS
- complicated runtime string construction inside NIS

That is why only specific command values are supported in this stable version.

### Notes for a future continuous-listener upgrade

A future auto-listening macro is still possible, but should start from the stable constraints above.

Recommended principles for later work:

- keep the macro local-only
- keep a single `main()`
- introduce looping only after the single-run version is still preserved
- add one new feature at a time and validate it immediately
- avoid rebuilding a generic command parser too early
- prefer a very small data protocol if dynamic parameters are needed later

## Safety notes

- The NIS macro only reads and writes `E:/Jiayi/NISZBridge/...`.
- Do not modify it to access the shared UNC path directly.
- Do not use `StgZ_GetLimits`.
- Keep relative validation moves small.
- Only use absolute moves whose ranges have been reviewed by the microscope operator.
