# Agent Notes: HERA App And NIS Z Bridge

This repo controls a HERA camera/stage app and a separate NIS-Elements Z-axis bridge. Work carefully and keep GitHub as the source of truth.

## Overall Goal

Coordinate these files so the HERA PC and NIS PC can work together:

- `AppHeraTriggerPython0417.py`
- `NIS-Z-Bridge/nis_z_sync_shared_to_local.py`
- `NIS-Z-Bridge/nis_z_local_text_bridge_watcher.mac`
- `NIS-Z-Bridge/nis_z_macro_hotkey_runner.ps1`

The immediate blocker is reliable GET Z from NIS into the HERA UI. The larger goal is full XYZ support:

- display Z everywhere XY is displayed
- display Z beside GET Z
- allow arbitrary Z moves, not only predefined increments
- include X, Y, and Z in acquisition loops
- after each hyperspectral image, move back to the correct XYZ position

## Machines And Paths

The HERA PC runs the HERA app and writes requests to a shared NAS folder.

The NIS PC controls the microscope and runs a local bridge from:

```text
E:\Jiayi\NISZBridge
```

The only shared communication path is:

```text
\\sti-nas1.rcp.epfl.ch\bios\bios-raw\backups\visible\cell\Jiayi_bios-raw\Z control shared
```

Do not make the NIS macro read or write the UNC path directly. The macro should only touch local files under `E:\Jiayi\NISZBridge`.

## Current Bridge Architecture

HERA writes request files into:

```text
Z control shared\commands\
```

The NIS sync script maps those request files into fixed local slots:

```text
E:\Jiayi\NISZBridge\commands\current_getz.txt
E:\Jiayi\NISZBridge\state\current_getz.id
```

The hotkey runner notices local command files and sends F4 to NIS-Elements. The NIS macro runs once per F4, calls Nikon/NIS stage APIs, and writes local responses:

```text
E:\Jiayi\NISZBridge\responses\current_getz_response.txt
```

The sync script publishes the response back to:

```text
Z control shared\responses\<hera_request_id>.txt
```

## GET Z Flow

1. HERA writes `shared\commands\hera_YYYYMMDD_HHMMSS_xxxxxxxx.txt` containing `GET_Z`.
2. `nis_z_sync_shared_to_local.py` forwards the newest fresh command to `commands\current_getz.txt`.
3. The sync writes `state\current_getz.id` containing the HERA request id.
4. `nis_z_macro_hotkey_runner.ps1` sees `current_getz.txt` and sends F4 to NIS-Elements.
5. `nis_z_local_text_bridge_watcher.mac` runs once.
6. The macro calls `StgGetPosZ(&z, 0)`.
7. The macro writes `responses\current_getz_response.txt`.
8. The sync publishes to `shared\responses\<hera_request_id>.txt`.
9. HERA reads the response and displays Z in micrometers.

Response format:

```text
OK 5726.400000
```

or:

```text
ERROR message here
```

## Known Fragile Areas

- The NIS macro is sensitive. Make small, deliberate edits.
- `StgZ_GetLimits` is unstable and has caused NIS to close. Do not use it.
- `Python_RunFile(...)` is not available in this NIS installation. Do not use it.
- NIS macros cannot reliably read/write the UNC NAS path. Keep macro I/O local.
- `WriteFile(...)` in the NIS macro has been fragile. Previous small byte counts truncated responses. The current code used larger byte counts, but partial values like `OK 57` have appeared, so response completeness must be guarded.
- `RenameFile(...)` argument order and destination collisions are important. Confirm behavior before changing it.
- If `commands\current_getz.txt` remains without `state\current_getz.id`, new requests are blocked.
- If `commands\current_getz.txt` and `state\current_getz.id` remain too long without a valid response, the slot is stale and should be archived by the sync.
- The hotkey runner must not hammer F4 or delete responses while the macro is writing.
- The HERA app must reset pending Z request state after timeout/failure and should fully exit on close to avoid duplicate HERA app instances.
- HERA live capture can make gain/exposure/ROI read-only or slow to stop. Keep parameter apply off the Tk main thread: pause live capture in a worker, apply camera settings, then schedule UI updates and live restart back on Tk.
- Live cursor sample coordinates are not provided by the Hera SDK directly. The app maps canvas mouse coordinates to live-frame pixels, then converts pixel offset from image center into Tango sample X/Y using `Stage units / pixel`, `Invert X`, `Invert Y`, and `Swap XY`.
- The active UI is a resizable three-pane layout: left for status/exposure/ROI/XYZ/saved positions/NIS Z, center for spectral settings and live/hyperspectral views, and right for acquisition/timelapse/saving. Keep new controls in the appropriate pane and avoid reintroducing duplicate saved-position or ROI controls elsewhere.
- Light/dark mode is controlled by `theme_mode`, `theme_button_var`, `_configure_theme`, and `toggle_theme_mode`. When adding widgets, prefer `self.theme[...]` colors so the switch can recolor them.
- Live ROI selection is display-driven: two clicks on the rendered live image are mapped back to Hera live-frame pixels and copied into the ROI parameter fields. Keep the user-selected export ROI separate from camera ROI readback; this matters because Hera can report ROI as read-only/full-frame even after the user selected a smaller region.
- ROI can also be edited through top-left/bottom-right corners, size fields, or a near-square area helper. Hera still accepts rectangular `x, y, width, height`, so corner edits must be normalized back to rectangular ROI fields and should mark `roi_selection_active` / `selected_export_roi`.
- Hyperspectral ROI is enforced after SDK acquisition, not by relying on camera hardware ROI. The SDK may return a full-frame hypercube even when an ROI was selected. The app exports a temporary full-frame ENVI cube, crops the binary/header on disk to `selected_export_roi`, removes temporary files, and crops bands in `render_current_hyper_band` so the Hyperspectral View matches the saved ROI cube.
- Live exposure helpers are display-only. `Auto Contrast` stretches the rendered preview, `Gamma` remaps brightness after auto-contrast, `Show Saturation` paints saturated pixels red from the SDK live-frame saturation threshold, and `Snapshot` writes the latest live frame as a PNG with those display choices applied. These controls must not alter camera exposure, gain, ROI, or acquisition data.
- The live cursor readout should stay compact and stable in the left Status panel. Avoid adding pixel-coordinate text there if it makes the panel jump while moving the mouse over live view.
- Saving notes are stored in `saving_notes_var` and appended to the ENVI export description.
- HyperLAB opening uses `hyperlab_shortcut_var`, defaulting to `C:\Users\Public\Desktop\Nireos HyperLAB.lnk`. `Open Current` should use the latest exported `.hdr`; if Windows cannot pass the file argument through the shortcut, open HyperLAB and copy the `.hdr` path to the clipboard.

## NIS PC Update From GitHub

When changing NIS-side files in this repo, push to GitHub and give the user exact pull commands for the NIS PC.

Use:

```powershell
cd E:\Jiayi\NISZBridge

Invoke-WebRequest `
  -Uri "https://raw.githubusercontent.com/LinaGross/hera-trigger-app/main/NIS-Z-Bridge/nis_z_sync_shared_to_local.py" `
  -OutFile ".\nis_z_sync_shared_to_local.py"

Invoke-WebRequest `
  -Uri "https://raw.githubusercontent.com/LinaGross/hera-trigger-app/main/NIS-Z-Bridge/nis_z_macro_hotkey_runner.ps1" `
  -OutFile ".\nis_z_macro_hotkey_runner.ps1"

Invoke-WebRequest `
  -Uri "https://raw.githubusercontent.com/LinaGross/hera-trigger-app/main/NIS-Z-Bridge/nis_z_local_text_bridge_watcher.mac" `
  -OutFile ".\nis_z_local_text_bridge_watcher.mac"
```

If the macro changed, tell the user to reload `E:\Jiayi\NISZBridge\nis_z_local_text_bridge_watcher.mac` in NIS-Elements.

## Startup Order

Use this order for each test session:

1. On the NIS PC, open NIS-Elements and load `E:\Jiayi\NISZBridge\nis_z_local_text_bridge_watcher.mac`.
2. Start the sync script:

```powershell
cd E:\Jiayi\NISZBridge
& C:\Users\adminbios\AppData\Local\Programs\Python\Python312\python.exe .\nis_z_sync_shared_to_local.py
```

3. In another PowerShell window, start the hotkey runner:

```powershell
cd E:\Jiayi\NISZBridge
Remove-Item -LiteralPath .\stop_hotkey_runner.txt -ErrorAction SilentlyContinue

powershell -ExecutionPolicy Bypass `
  -File .\nis_z_macro_hotkey_runner.ps1 `
  -RunHotkey "{F4}"
```

4. Restart the HERA app.
5. Press GET Z once.

## Diagnostics To Ask For

On the NIS PC:

```powershell
cd E:\Jiayi\NISZBridge

Get-ChildItem .\commands | Select-Object Name,Length,LastWriteTime
Get-ChildItem .\state | Select-Object Name,Length,LastWriteTime
Get-ChildItem .\responses | Sort-Object LastWriteTime -Descending | Select-Object -First 10 Name,Length,LastWriteTime
Get-ChildItem .\processed | Sort-Object LastWriteTime -Descending | Select-Object -First 10 Name,Length,LastWriteTime
Get-ChildItem .\errors | Sort-Object LastWriteTime -Descending | Select-Object -First 10 Name,Length,LastWriteTime
Get-Content .\nis_z_sync.log -Tail 40
```

On the shared NAS:

```powershell
$root = "\\sti-nas1.rcp.epfl.ch\bios\bios-raw\backups\visible\cell\Jiayi_bios-raw\Z control shared"

Get-ChildItem -LiteralPath "$root\commands" |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 5 Name,Length,LastWriteTime

Get-ChildItem -LiteralPath "$root\forwarded" |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 5 Name,Length,LastWriteTime

Get-ChildItem -LiteralPath "$root\responses" |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 5 Name,Length,LastWriteTime
```

## Recent Debug History

Recent symptoms:

- HERA logs `NIS Z GET_Z ignored because another Z request is still waiting`.
- HERA logs timeout waiting for a shared response.
- Sometimes HERA displays a truncated/wrong Z such as `57.000 um` while NIS shows a longer value like `5726.xxx`.
- The sync script previously did not stop promptly with Ctrl+C.
- The hotkey runner previously sent F4 repeatedly and deleted incomplete response files.

Recent fixes pushed:

```text
fcb0ba9 Make NIS Z sync recover orphan slots
5bce7ec Stop repeated NIS Z macro retries
48291bc Recover stale paired NIS Z slots
```

Those commits made the sync exit on Ctrl+C, archive stale local slots, ignore old shared backlog, wait for complete decimal responses, and made the hotkey runner less destructive.

## Working Style

- Work step by step.
- Prefer small changes and immediate diagnostics.
- Preserve user changes and do not reset the repo.
- Keep GitHub updated after meaningful fixes.
- When pushing NIS-side changes, include exact `Invoke-WebRequest` commands for the NIS PC.
- Treat the macro as sensitive: inspect current GitHub and local context before editing it.
