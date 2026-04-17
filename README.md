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
- asynchronous acquisition callback handling
- hypercube generation with `HeraAPI_GetHyperCubeEx`
- ENVI export to a user-selected output folder

## Project Files

- `AppHeraTriggerPython0417.py`: main application
- `requirements.txt`: Python dependency note
- `.gitignore`: ignore rules for local runtime files and outputs

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
5. Apply acquisition parameters.
6. Start acquisition.
7. Wait for the hypercube export to finish.

## Notes

- Some camera parameters, such as gain or ROI, may be read-only depending on the connected hardware configuration.
- The app logs read-only parameters instead of failing the entire acquisition sequence.
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
