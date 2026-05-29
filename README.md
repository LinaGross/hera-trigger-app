# Hera Trigger App

Python desktop app for triggering hyperspectral acquisitions on a NIREOS HERA system through the Hera SDK.

## Overview

This project provides a simple Windows GUI for:

- discovering connected HERA devices
- connecting to a camera through the Hera SDK
- validating license and environment setup
- applying supported acquisition parameters
- starting a hyperspectral acquisition
- receiving SDK progress and completion callbacks
- converting raw acquisition data into a hypercube
- exporting results in ENVI format

The current implementation is focused on software-triggered acquisition and is structured so hardware-trigger workflows can be added later.

## Features

- Tkinter desktop UI
- Hera SDK device enumeration and connection
- license status check
- scan mode and trigger mode support checks
- graceful handling of read-only camera parameters
- live-safe parameter apply that pauses live capture before changing gain, exposure, or ROI, then restarts it
- live cursor readout that maps image pixels to sample/stage X/Y using a configurable pixel scale and axis orientation
- live-view ROI workflow: click two corners on the live image or edit ROI fields, then use that selected area for ROI export and hyperspectral display
- live-view exposure helpers: auto-contrast display, red saturation overlay, crosshair row/column intensity plots, and PNG snapshot export of the current displayed frame
- adjustable three-pane interface with light/dark mode switching
- editable ROI corners, ROI width/height, and ROI area helpers
- asynchronous acquisition callback handling
- hypercube generation with `HeraAPI_GetHyperCubeEx`, plus ENVI post-export ROI cropping when the SDK returns a full-frame cube
- flatfield baseline acquisition with an explicit `Use flatfield correction` toggle; native ENVI export is always saved, and `_nrm` is added only when correction is enabled and compatible
- HyperLAB shortcut support for opening the latest exported `.hdr` through Nireos HyperLAB
- ENVI export to a user-selected output folder

## Project Files

- `AppHeraTriggerPython0417.py`: 4-line launcher — do not edit; all logic is in `hera_app/`
- `requirements.txt`: Python dependency note
- `.gitignore`: ignore rules for local runtime files and outputs

### Package Structure

```
hera_app/
    app.py                        HeraTriggerApp class + __init__ + on_close + main()
    controllers/
        hera.py                   HeraDeviceInfo, HeraController (SDK DLL wrapper)
        tango.py                  TangoController (stage DLL wrapper)
        nis_z.py                  NISZBridgeController (file-bridge TCP wrapper)
    mixins/
        theme.py                  _configure_theme, _apply_theme_recursive, toggle_theme_mode
        ui_builder.py             all _build_* UI construction methods
        device.py                 connect/disconnect Hera + Tango, license, preflight, HDR
        nis_z_mixin.py            NIS Z bridge polling and control
        stage.py                  stage motion, position management, Z moves
        export.py                 ENVI file helpers, tag sanitisation, ROI crop
        flatfield.py              flatfield acquisition, normalization, clear
        acquisition.py            parameter apply, arm/start acquisition, worker
        timelapse.py              timelapse/cycle worker, site acquisition
        live_view.py              live capture, rendering, zoom, pan, snapshots
        roi.py                    ROI selection, overlays, cursor readout
        hyperspectral_viewer.py   band viewer, spectrum panel
        utils.py                  _safe_after, _log_async, _set_var_async
```

When making changes, open the relevant mixin file directly. Use Ctrl+Shift+F to search across files if unsure where a method lives.

## System Requirements

- Windows x64
- Python 3.x
- NIREOS Hera SDK available locally
- valid HERA SDK license
- `HERA_DEVICES` environment variable configured correctly
- camera drivers and HERA device configuration files installed

## Runtime Dependencies

This repository includes the local Hera SDK and runtime binaries that were available in the development workspace at packaging time.

The app expects access to:

- bundled SDK/runtime DLLs in the repository folder
- HERA device configuration files referenced by `HERA_DEVICES`

You can still browse to a different SDK DLL from the application UI if needed.

## Installation

1. Clone or copy this repository to a Windows machine with the HERA system installed.
2. Ensure Python 3 is installed.
3. Ensure the Hera SDK DLLs are available locally.
4. Set `HERA_DEVICES` to the correct device configuration folder.
5. Confirm the SDK license is active.

## What Is Bundled In This Repository

The GitHub-ready folder includes:

- the Python application
- Hera SDK DLLs found in the local workspace
- related runtime DLLs, headers, and import libraries found in the local workspace

## What Is Still Machine Specific

The following are still specific to the target installation:

- `HERA_DEVICES` path and its device configuration files
- installed camera drivers and vendor services
- physical HERA/GEMINI-X/Kinetix hardware setup
- valid SDK license activation on the target machine

## Run

```powershell
python AppHeraTriggerPython0417.py
```

## Typical Workflow

1. Launch the app.
2. Verify the SDK DLL path and `HERA_DEVICES`.
3. Refresh devices and connect to the HERA camera.
4. Run the preflight check.
5. Apply acquisition parameters. If live view is running, the app temporarily stops live capture, applies gain/exposure/ROI, reads back actual values, and restarts live view.
6. Start acquisition.
7. Wait for the hypercube export to finish.

## Live View Cursor Coordinates

The Live View tab shows cursor coordinates over the rendered camera frame. The preview is rotated 90 degrees clockwise so a Tango right move reads as rightward motion in the display. The app inverse-maps the mouse location, crosshair, ROI overlay, and ROI clicks back to the live-frame pixel returned by `HeraAPI_GetLiveCaptureInfo`, then converts the raw pixel offset from the image center into a sample/stage X/Y estimate.

The **Stage Control > Live Cursor Sample Mapping** panel controls this conversion:

- `Stage units / pixel`: physical stage units represented by one live-image pixel.
- `Invert X` and `Invert Y`: flip the image-to-stage direction for either axis.
- `Swap XY`: swap image X/Y before applying the stage conversion.

The conversion assumes the current Tango stage X/Y corresponds to the center of the live frame. If the stage moves while the mouse stays over the same live pixel, the displayed sample X/Y updates with the latest stage position.

## Interface Layout

The app uses a resizable three-pane layout:

- left pane: status, exposure, ROI, XYZ/stage controls, saved positions, and NIS Z bridge controls
- center pane: spectral settings, live view, hyperspectral view, and run messages
- right pane: acquisition/timelapse controls and saving options

Use the top-right `Light Mode` / `Dark Mode` button to switch the UI palette. Drag the vertical pane dividers to resize the left, center, and right areas.

## Live View ROI And Exposure Checks

The Live View tab includes controls for choosing an ROI and judging exposure before a hyperspectral acquisition.

- `Select ROI`: click two opposite corners on the live image. The app converts those clicks into image-pixel ROI values and fills the `ROI X`, `ROI Y`, `ROI W`, and `ROI H` parameter fields. That selected ROI is kept separately from the camera ROI readback, because some Hera devices report ROI as read-only/full-frame during hyperspectral acquisition.
- `Use Corners`: reads the top-left and bottom-right corner fields and updates the rectangular Hera ROI. The other two corners are recalculated from that rectangle.
- `Use Size`: reads `ROI X`, `ROI Y`, `ROI Width`, and `ROI Height`, then refreshes the corner fields and ROI area.
- `Set Area`: creates a near-square ROI with the requested pixel area, centered around the current ROI.
- `Clear ROI`: resets the ROI workflow to full-image export/display when a live frame is available.
- `Auto Contrast`: display-only contrast stretching for the live preview. It helps make dim frames visible and does not change camera exposure, gain, or saved acquisition data.
- `Show Saturation`: overlays saturated live-preview pixels in red using the saturation threshold returned by `HeraAPI_GetLiveCaptureInfo`.
- `Cross`: enables a fixed green point on the live image and displays the selected row and column intensity cuts. The bottom plot shows the horizontal cut, the right plot shows the vertical cut, and the red line marks the SDK live-frame saturation threshold.
- `Gamma`: display-only brightness response control applied after auto-contrast. `1.0` is neutral; higher values brighten shadows and lower values darken the display. `Reset Gamma` returns it to `1.0`.
- `Snapshot`: saves the latest live frame as a PNG. The file uses the current display choices, so auto-contrast and the red saturation overlay are included when enabled. It saves the live image content, not the canvas text labels or ROI outline.

The `Live View HDR` checkbox controls the camera HDR mode used by live preview. On the tested Hera Kinetix MC setup, the SDK accepts HDR for live frames (`Mono16`, HDR on), but hyperspectral data and hypercubes still report `HDR=off` through `HeraAPI_GetHyperspectralDataIsHDR` and `HeraAPI_GetHyperCubeIsHDR`. The app logs that downgrade and writes the actual hyperspectral HDR flag into the ENVI description, so saved cubes are not mislabeled as HDR.

The saving panel includes a notes field. These notes are written into the ENVI export description when a hyperspectral cube is saved.

When an ROI is selected, the SDK acquisition still runs through the normal full-frame hyperspectral path. The app then exports the full cube temporarily, crops the ENVI binary/header on disk to the selected ROI, and removes the temporary full-frame export. The Hyperspectral View uses the same selected ROI by cropping each displayed SDK band in memory, so the viewer and saved `.hdr`/data file match.

## Flatfield

The Flatfield panel follows the original Hera Acquisition App concept: acquire a white diffusive surface as a baseline/reference, then optionally use it to normalize later sample measurements. `Acquire Baseline` runs a normal Hera acquisition and stores the resulting hypercube as the flatfield reference. `Clear Flatfield` removes it.

The `Use flatfield correction` checkbox controls whether later sample cubes use the stored baseline. Native ENVI export is always written first, with or without the checkbox. When the checkbox is enabled and the later sample cube has matching source size, displayed ROI, band count, and data type, the app also exports an additional `_nrm` ENVI cube where each sample pixel is divided by the matching flatfield pixel. The Hyperspectral View displays normalized bands only when this checkbox is enabled and the flatfield is compatible; otherwise it displays the native cube.

The saving panel also includes a HyperLAB section. `Open in HyperLAB` launches `C:\Users\Public\Desktop\Nireos HyperLAB.lnk` with the latest native exported `.hdr` when Windows accepts the file argument; otherwise it opens HyperLAB and copies the `.hdr` path to the clipboard.

## Saved Positions And Dummy Z

Saved XYZ positions no longer depend on a successful NIS Z bridge read. If a cached real Z value is available it is used; otherwise the app saves `Z=0.000` as a dummy placeholder so XY site saving remains usable while Z integration is being debugged.

## NIS Z Bridge

The HERA app also communicates with the Nikon/NIS Z axis through a file bridge. The HERA PC and the NIS PC do not talk directly; the only shared communication path is the NAS folder:

```text
\\sti-nas1.rcp.epfl.ch\bios\bios-raw\backups\visible\cell\Jiayi_bios-raw\Z control shared
```

The NIS PC local bridge folder is:

```text
E:\Jiayi\NISZBridge
```

The coordinated bridge files are:

- `AppHeraTriggerPython0417.py`: HERA UI, camera/stage control, and shared command writer.
- `NIS-Z-Bridge/nis_z_sync_shared_to_local.py`: NIS-side Python sync between NAS and local fixed slots.
- `NIS-Z-Bridge/nis_z_local_text_bridge_watcher.mac`: NIS macro that reads local commands and calls Nikon Z APIs.
- `NIS-Z-Bridge/nis_z_macro_hotkey_runner.ps1`: NIS-side PowerShell runner that sends F4 to NIS-Elements when a local command appears.

### GET Z Flow

1. HERA writes `shared\commands\hera_YYYYMMDD_HHMMSS_xxxxxxxx.txt` with `GET_Z`.
2. `nis_z_sync_shared_to_local.py` forwards it to `E:\Jiayi\NISZBridge\commands\current_getz.txt`.
3. The sync writes `E:\Jiayi\NISZBridge\state\current_getz.id` with the HERA request id.
4. The hotkey runner sees `current_getz.txt` and sends F4 to NIS-Elements.
5. The NIS macro runs once, calls `StgGetPosZ(&z, 0)`, and writes `responses\current_getz_response.txt`.
6. The sync publishes the local response to `shared\responses\<hera_request_id>.txt`.
7. HERA reads the response and updates the Z display.

The desired response format is:

```text
OK 5726.400000
```

or:

```text
ERROR message here
```

Units are micrometers.

### NIS PC Update From GitHub

When a bridge file changes in GitHub, update the NIS PC with:

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

Reload the macro in NIS-Elements whenever `nis_z_local_text_bridge_watcher.mac` changes.

### NIS Z Startup Order

Use this order for each session:

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

### Useful NIS Diagnostics

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

### Current Z Bridge Goal

The immediate goal is reliable GET Z. The larger goal is full XYZ coordination:

- display Z everywhere XY is displayed in the UI
- display Z beside GET Z
- support arbitrary Z moves, not only fixed increments
- include X, Y, and Z in acquisition loops
- after each hyperspectral image, return the stage to the correct XYZ position

## Notes

- Some camera parameters, such as gain or ROI, may be read-only depending on the connected hardware configuration.
- The app logs read-only parameters instead of failing the entire acquisition sequence. When live view is active, parameter apply is handled in a background worker so the UI does not freeze while the SDK stops and restarts live capture.
- ENVI exports are written to the selected output directory.
- The app has been validated against a working acquisition flow where raw data and hypercube export complete successfully.
- No local device-configuration folder was found in this workspace, so those files are not bundled automatically.

## Publishing To GitHub

Once Git is installed and available on `PATH`, you can publish this folder with:

```powershell
cd "c:\BIOS DATA\Lina\PYTHON\hera-trigger-app"
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin <your-github-repo-url>
git push -u origin main
```

## License

No open-source license file has been added yet. Choose a license before publishing if you want others to reuse the code under defined terms.
