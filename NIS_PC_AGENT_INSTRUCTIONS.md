# NIS PC Agent Instructions: Nikon Z Bridge

## Goal

Build the NIS-PC side of a Z-position bridge so the HERA PC can request Nikon Eclipse Ti Z actions through a shared folder.

The HERA PC already has a standalone text-protocol tester:

```text
c:\BIOS DATA\Lina\PYTHON\hera-trigger-app\nis_z_text_bridge_test.py
```

Do not modify the main HERA app yet:

```text
AppHeraTriggerPython0417.py
```

## Shared Folder

Use this shared folder for communication between PCs:

```text
\\sti-nas1.rcp.epfl.ch\bios\bios-raw\backups\visible\cell\Jiayi_bios-raw\Z control shared
```

Current intended shared layout:

```text
Z control shared\
  commands\
  responses\
```

The HERA PC writes command files:

```text
commands\<id>.txt
```

The NIS PC must write matching response files:

```text
responses\<id>.txt
```

## Important Findings So Far

These tests were already done on the NIS PC:

- Local NIS macro with `StgGetPosZ` works.
- Local NIS macro with `StgMoveZ(1.0, 1)` works.
- `StgZ_GetLimits` is unstable and caused NIS to close. Do not use it.
- NIS macro files do not run correctly from the UNC shared folder. Copy macros locally before running them.
- `Python_RunFile(...)` is not available in this NIS installation. Do not use it.
- NIS macro `WriteFile(...)` works for local files, but not for the UNC shared folder.
- For `WriteFile`, use a generous byte count such as `strlen(text) * 2`; small counts truncate output.

Because NIS macros cannot write to the shared UNC path, the final NIS-PC side needs two local components:

1. A normal Windows Python sync script running outside NIS.
2. A local NIS macro watcher running inside NIS.

## Required Architecture

Use this data flow:

```text
HERA PC
  writes shared\commands\<id>.txt
        |
        v
NIS PC normal Python sync script
  copies shared command to C:\ZBridge\commands\<id>.txt
        |
        v
NIS local macro watcher
  reads C:\ZBridge\commands\<id>.txt
  calls StgGetPosZ / StgMoveZ
  writes C:\ZBridge\responses\<id>.txt
        |
        v
NIS PC normal Python sync script
  copies local response to shared\responses\<id>.txt
```

Do not make NIS read or write the UNC path directly.

## Text Protocol

Command file contents are plain ASCII text.

Supported commands:

```text
GET_Z
MOVE_REL 1.000000
MOVE_ABS 1500.000000 1400.000000 1600.000000
STOP
```

Response file contents:

```text
OK 1520.400000
```

or:

```text
ERROR message here
```

Units are micrometers.

## NIS PC Local Folder

Create this folder structure on the NIS PC:

```text
C:\ZBridge\
  commands\
  responses\
  processed\
  errors\
```

The NIS macro watcher should only use these local paths.

## What To Implement On The NIS PC

### 1. Normal Windows Python Sync Script

Create a script, for example:

```text
C:\ZBridge\nis_z_sync_shared_to_local.py
```

It should:

- Ensure local folders exist.
- Ensure shared `commands` and `responses` folders exist.
- Poll `shared\commands\*.txt`.
- Copy each new command to `C:\ZBridge\commands\<id>.txt`.
- Avoid copying the same command repeatedly; move copied shared commands to a shared `forwarded\` folder or track copied names in memory.
- Poll `C:\ZBridge\responses\*.txt`.
- Copy each local response to `shared\responses\<id>.txt`.
- Move copied local responses to `C:\ZBridge\processed\`.
- Log to `C:\ZBridge\nis_z_sync.log`.

This script does not call NIS and does not move Z. It only syncs files.

### 2. Local NIS Macro Watcher

Create or adapt a pure NIS macro, for example:

```text
C:\ZBridge\nis_z_local_text_bridge_watcher.mac
```

It should:

- Watch `C:/ZBridge/commands/*.txt`.
- Read the first pending command with `ReadFile`.
- Parse command text with `sscanf`.
- For `GET_Z`: call `StgGetPosZ(&z, 0)`.
- For `MOVE_REL`: check `abs(dz_um) <= 5.0`, then call `StgMoveZ(dz_um, 1)`, then read back Z.
- For `MOVE_ABS`: check command target is inside command min/max, then call `StgMoveZ(z_um, 0)`, then read back Z.
- For `STOP`: return current Z. Do not rely on interrupting a blocking `StgMoveZ`.
- Write `OK <z_um>` or `ERROR <message>` to `C:/ZBridge/responses/<id>.txt`.
- Move processed commands to `C:/ZBridge/processed/`.
- Move failed commands to `C:/ZBridge/errors/`.

Do not call `StgZ_GetLimits`.
Do not call `Python_RunFile`.
Do not read/write the UNC path from NIS.

## HERA PC Test Commands

From the HERA PC, test with:

```powershell
python .\hera-trigger-app\nis_z_text_bridge_test.py status
python .\hera-trigger-app\nis_z_text_bridge_test.py get
python .\hera-trigger-app\nis_z_text_bridge_test.py move-rel --dz-um 1 --yes
python .\hera-trigger-app\nis_z_text_bridge_test.py move-rel --dz-um -1 --yes
```

Absolute Z should only be tested after relative moves are confirmed:

```powershell
python .\hera-trigger-app\nis_z_text_bridge_test.py move-abs --z-um <safe_z> --min-z-um <min_safe_z> --max-z-um <max_safe_z> --yes
```

## Acceptance Criteria

The bridge is working when:

- HERA `get` returns `OK <z_um>` without timeout.
- HERA `move-rel --dz-um 1 --yes` moves Nikon Z by about `+1 um` and returns `OK <z_um>`.
- HERA `move-rel --dz-um -1 --yes` moves Nikon Z back by about `-1 um` and returns `OK <z_um>`.
- No NIS crashes occur.
- No changes are needed in `AppHeraTriggerPython0417.py` yet.

## Safety Notes

- Keep relative test moves small: `+1 um`, then `-1 um`.
- Keep the objective/sample in a safe position before movement.
- Do not use `StgZ_GetLimits`.
- Keep NIS-Elements open and connected while the local macro watcher runs.
- Stop the NIS watcher with the NIS macro stop/abort control.
