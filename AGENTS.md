# Agent Notes: HERA App And NIS Z Bridge

This repo controls the HERA camera/stage app and the NIS-Elements Z-axis bridge. Work carefully, make small changes, and keep GitHub as the source of truth.

## Repo Rules

- Preserve user changes. Do not reset or revert the repo unless the user explicitly asks.
- Keep meaningful fixes committed/pushed to GitHub when the user asks to publish or when NIS-side files need deployment.
- `AppHeraTriggerPython0417.py` is only the launcher. Do not put app logic there.
- After every code change, tell the user exactly how to verify it: concrete app actions, expected results, and logs/files to inspect. If hardware testing was not possible locally, say that clearly.

## App Structure

Most logic lives under `hera_app/`.

```text
hera_app/
    app.py                        HeraTriggerApp init, state, logging, shutdown, main()
    controllers/
        hera.py                   Hera SDK / HeraAPI.dll wrapper
        tango.py                  Tango stage / Tango_DLL.dll wrapper
        nis_z.py                  NIS Z shared-folder bridge client
    mixins/
        ui_builder.py             UI construction and widget behavior
        device.py                 Hera/Tango connection, license, preflight, HDR
        acquisition.py            parameter apply, acquisition, callback worker, saving
        timelapse.py              cycle/site/timelapse worker
        live_view.py              live capture, rendering, zoom/pan/snapshot
        roi.py                    ROI selection, coordinate mapping, overlays
        flatfield.py              flatfield reference, normalization helpers
        hyperspectral_viewer.py   band display and spectrum panel
        export.py                 ENVI export, ROI crop, HyperLAB header patching
        stage.py                  stage motion and saved positions
        nis_z_mixin.py            NIS Z polling and controls
        theme.py                  light/dark theme
        utils.py                  safe Tk scheduling and async UI helpers
```

Open the relevant mixin/controller directly before editing. Use search if a method location is unclear.

## Core App Invariants

- The active UI is a three-pane layout: left status/exposure/ROI/XYZ/saved positions/NIS Z, center spectral/live/hyperspectral views, right acquisition/timelapse/export controls. Add controls to the correct pane and avoid duplicate ROI or saved-position controls.
- UI controls should be immediate where safe: buttons/checkbuttons focus and invoke on Enter, entries commit on Enter or changed FocusOut, and connected camera option variables auto-apply with debounce. Do not add generic Apply buttons for camera parameters or ROI fields.
- Keep camera parameter apply off the Tk main thread. Stop live capture in a worker when needed, apply settings, then schedule UI updates/live restart back on Tk.
- Background logging is always on. Keep the Tk/Python/thread exception hooks and the `Open Log` button wired to `hera_last_issues.log`; the full log is `hera_background_status.log`.
- Light/dark mode uses `theme_mode`, `theme_button_var`, `_configure_theme`, and `toggle_theme_mode`. New widgets should use `self.theme[...]` colors where practical.

## ROI And Saved Positions

- Live ROI is display-driven: two clicks on the rendered live image are inverse-mapped to Hera live-frame pixels and copied into ROI fields.
- Keep the user-selected export ROI separate from camera ROI readback. Hera can report ROI as read-only/full-frame even when the user selected a smaller region.
- ROI can be edited by corners, size fields, or area helper, but Hera ultimately receives rectangular `x, y, width, height`. Normalize corner edits back to rectangular ROI fields and update `roi_selection_active` / `selected_export_roi`.
- Saved positions include per-site ROI in `SavedPosition.roi`. Adding/updating/saving a site captures the active ROI; selecting a site restores it.
- Manual site runs, `Run First 2 Sites`, and timelapse use the saved ROI for each site, falling back to the timelapse-start ROI only when the site has no saved ROI.
- The saved positions list starts empty. Do not seed a default `Start` or `0,0` position.
- If NIS Z is unavailable, save `dummy_z_position` (`0.000`) so XY sites remain usable.

## Hyperspectral, Export, And HyperLAB

- Hyperspectral ROI is enforced after SDK acquisition when needed. The SDK may return a full-frame hypercube even for a selected ROI.
- For post-export ROI, export a temporary full-frame ENVI cube, scale the live-frame ROI into returned hypercube dimensions, crop binary/header on disk, remove temp files, and crop displayed bands so the viewer matches saved files.
- Do not crop a returned smaller hypercube with unscaled 3200x3200 live-frame coordinates.
- Export options must respect the right-side Export panel. `_raw` is always possible; `_ref` and `_nrm` require a loaded, compatible flatfield and are still checked at save time.
- ENVI exports must stay HyperLAB-friendly: keep `file type = ENVI Standard` and `data file = <matching data filename>` in headers after SDK export, ROI crop, or normalized export.
- Export naming uses `export_name_var` and `export_append_time_var`; saving notes use `saving_notes_var` and go into ENVI descriptions.
- HyperLAB opening uses `hyperlab_shortcut_var`, default `C:\Users\Public\Desktop\Nireos HyperLAB.lnk`. Resolve it to `HyperLAB.exe`, pass the selected/latest `.hdr`, and copy the path to clipboard. If `last_export_path` is empty, search output for the newest `_raw`, `_ref`, or `_nrm` `.hdr`.

## Flatfield

- Flatfield follows the Hera Acquisition App model: acquire a white diffuse/reference surface with `Acquire`, store it in `flatfield_hypercube_handle`, and use it until the user acquires a new flatfield, clears it, disconnects Hera, or closes the app.
- Compatible sample cubes display/export normalized data as `sample / flatfield`.
- Compatibility currently requires matching source size, displayed ROI coverage, band count, and data type.
- Hyperspectral View has `Normalized`, `Raw`, and `Flatfield` modes. `Normalized` shows the current sample divided by the compatible flatfield, `Raw` shows the native sample cube, and `Flatfield` shows the stored reference cube.
- Keep selected/cursor spectra tied to the active display mode, with a separate flatfield spectrum for comparison when showing a sample cube.
- Flatfield saving uses the same right-side `Export` button and shared output folder/name/stamp controls. Do not reintroduce a separate `Save Flatfield Ref` button. A pending flatfield acquisition saves as `_ref`.

## Live View

- Live cursor coordinates are display-only. The live preview is presentation-rotated with `live_display_rotation_degrees = 90` so Tango right/left motion is horizontal in the display.
- Inverse-map cursor, crosshair, ROI overlay, ROI clicks, and snapshots through the same live-frame orientation helpers. Do not reintroduce pixel-scale, invert-axis, or swap-axis controls unless the user explicitly asks.
- Live exposure helpers are display-only: `Auto Contrast`, `Gamma`, `Show Saturation`, `Cross`, and `Snapshot` must not alter camera exposure, gain, ROI, or acquisition data.
- `Live View HDR` is a live-preview aid, not a guaranteed hyperspectral HDR acquisition mode. Record SDK-reported raw/cube HDR flags in logs/export descriptions instead of assuming HDR cubes were saved.
- Keep the live cursor readout compact and stable in the left Status panel.

## NIS Z Bridge Rules

The HERA PC writes requests through the shared NAS folder. The NIS PC controls the microscope and runs the local bridge from:

```text
E:\Jiayi\NISZBridge
```

The only shared path is:

```text
\\sti-nas1.rcp.epfl.ch\bios\bios-raw\backups\visible\cell\Jiayi_bios-raw\Z control shared
```

Rules:

- Do not make the NIS macro read/write the UNC path directly. The macro should only touch local files under `E:\Jiayi\NISZBridge`.
- The NIS macro is sensitive. Make small edits and inspect local/GitHub context before changing it.
- Do not use `StgZ_GetLimits`; it has caused NIS to close.
- Do not use `Python_RunFile(...)`; it is unavailable in this NIS installation.
- `WriteFile(...)` can truncate responses if byte counts are too small. Guard response completeness; partial values like `OK 57` have appeared.
- `RenameFile(...)` argument order and destination collisions matter. Confirm behavior before changing it.
- The hotkey runner must not hammer F4 or delete responses while the macro is writing.
- HERA must reset pending Z request state after timeout/failure and fully exit on close to avoid duplicate app instances.

GET Z flow summary:

1. HERA writes `Z control shared\commands\hera_YYYYMMDD_HHMMSS_xxxxxxxx.txt` containing `GET_Z`.
2. `nis_z_sync_shared_to_local.py` forwards it to local fixed slots: `commands\current_getz.txt` and `state\current_getz.id`.
3. `nis_z_macro_hotkey_runner.ps1` sends F4 to NIS-Elements.
4. `nis_z_local_text_bridge_watcher.mac` calls `StgGetPosZ(&z, 0)` and writes `responses\current_getz_response.txt`.
5. The sync publishes `Z control shared\responses\<hera_request_id>.txt`.
6. HERA reads `OK 5726.400000` or `ERROR message here`.

Stale local slots block new requests. If `current_getz.txt` and/or `current_getz.id` remain too long without a valid response, the sync should archive them.

## NIS PC Update From GitHub

When changing NIS-side files, push to GitHub and give the user exact NIS PC pull commands:

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

## NIS Test Session Startup

1. On the NIS PC, open NIS-Elements and load `E:\Jiayi\NISZBridge\nis_z_local_text_bridge_watcher.mac`.
2. Start the sync:

```powershell
cd E:\Jiayi\NISZBridge
& C:\Users\adminbios\AppData\Local\Programs\Python\Python312\python.exe .\nis_z_sync_shared_to_local.py
```

3. Start the hotkey runner in another PowerShell:

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

## Recent NIS Debug Context

Recent symptoms included pending GET_Z requests not clearing, shared-response timeouts, truncated Z values such as `57.000 um` instead of `5726.xxx`, slow Ctrl+C exit in the sync script, and repeated/destructive F4 behavior in the hotkey runner.

Recent fixes:

```text
fcb0ba9 Make NIS Z sync recover orphan slots
5bce7ec Stop repeated NIS Z macro retries
48291bc Recover stale paired NIS Z slots
```

Those fixes made the sync exit on Ctrl+C, archive stale local slots, ignore old shared backlog, wait for complete decimal responses, and made the hotkey runner less destructive.

## Validation

- For Python-only changes, at minimum run `python -m py_compile` on touched Python files.
- For UI/hardware behavior, give the user a manual validation recipe with exact buttons/actions and expected state/log/output.
- For NIS bridge changes, include NIS PC startup/update steps when relevant.

## Commit Template

Use this template unless the user asks for a different format:

```text
<area>: <short imperative summary>

Why:
- <user-visible problem or goal>

What changed:
- <main code/UI behavior change>
- <important side effect or compatibility note>

Validation:
- <command or manual test performed>
- <hardware/app test the user should perform, if not run locally>
```
