# Getting Started finextract

Financial document extraction and SharePoint reclassification pipeline.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Python backend setup](#2-python-backend-setup)
3. [Flutter UI setup](#3-flutter-ui-setup)
4. [Running the API server](#4-running-the-api-server)
5. [CLI usage](#5-cli-usage)
6. [SharePoint sync](#6-sharepoint-sync)
7. [Running tests](#7-running-tests)
8. [Running evaluations](#8-running-evaluations)
9. [Configuration reference](#9-configuration-reference)

---

## 1. Prerequisites

### System packages

These are required by the Python backend for OCR and MIME detection.

**Linux (Debian / Ubuntu)**
```bash
sudo apt install tesseract-ocr ghostscript libmagic1
```

**macOS**
```bash
brew install tesseract ghostscript libmagic
```

**Windows**

Install each manually:

1. **Tesseract** — download the installer from [UB Mannheim](https://github.com/UB-Mannheim/tesseract/wiki) and install to `C:\Program Files\Tesseract-OCR`. Then add that folder to your system PATH.
2. **Ghostscript** — download from [ghostscript.com/releases](https://www.ghostscript.com/releases/gsdnld.html) and install (64-bit). Its `bin\` folder is added to PATH by the installer.
3. **libmagic** — install via pip after activating the venv (the `python-magic-bin` wheel bundles the DLL on Windows):

```powershell
pip install python-magic-bin
```

> Note: `python-magic` is listed in `pyproject.toml` for Linux/macOS. On Windows, `python-magic-bin` replaces it — install it manually after `pip install -e ".[dev]"` if you see `MagicException` errors.

- **Tesseract** — OCR engine used when PDFs have no embedded text
- **Ghostscript** — required by ocrmypdf for PDF/A conversion
- **libmagic** — MIME type detection

### Python

Python **3.11 or newer** is required.

```bash
python3 --version   # must be 3.11+
```

### Flutter (UI only)

Flutter **3.22+** with Dart **3.3+**. Install via [flutter.dev](https://flutter.dev/docs/get-started/install).

```bash
flutter --version
```

---

## 2. Python backend setup

```bash
# Linux / macOS
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

```powershell
# Windows PowerShell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
# If you get an execution policy error:
#   Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

This installs the `finextract` CLI and all runtime + dev dependencies.

Verify the install:

```bash
finextract version
```

---

## 3. Flutter UI setup

### Install Flutter

#### Windows

1. Download the Flutter SDK zip from [flutter.dev/docs/get-started/install/windows](https://docs.flutter.dev/get-started/install/windows/desktop)
2. Extract to a folder without spaces or special characters, e.g. `C:\dev\flutter`
3. Add `C:\dev\flutter\bin` to your **System PATH**:
   - Search → "Edit the system environment variables" → Environment Variables → Path → New
4. Open a new PowerShell window and verify:

```powershell
flutter doctor
```

Flutter on Windows also requires **Visual Studio 2022** (with the "Desktop development with C++" workload) for the Windows desktop target, and **Chrome** for the web target. `flutter doctor` will tell you exactly what's missing.

#### Linux

```bash
cd ~
git clone https://github.com/flutter/flutter.git -b stable
export PATH="$HOME/flutter/bin:$PATH"   # add to ~/.bashrc to persist
flutter doctor
```

Linux desktop builds also need:

```bash
sudo apt install clang cmake ninja-build libgtk-3-dev
```

#### macOS

```bash
brew install --cask flutter
flutter doctor
```

---

### Install UI dependencies

```bash
cd flutter_ui
flutter pub get
```

---

### Run in Chrome (fastest, works on all OSes)

The UI talks to the API server at `http://localhost:8000`. **Start the API server first** (see section 4), then:

```powershell
# Windows PowerShell
cd flutter_ui
flutter run -d chrome
```

```bash
# Linux / macOS
cd flutter_ui
flutter run -d chrome
```

Chrome opens automatically. The app bar shows a **green dot** when the server is reachable, grey when it isn't — click it to retry.

---

### Run as a native desktop app

```powershell
# Windows
flutter run -d windows
```

```bash
# Linux
flutter run -d linux

# macOS
flutter run -d macos
```

---

### What the UI does

- **Drop zone** — drag a PDF, PNG, JPG, TIFF, BMP, or WebP onto it, or click to open a file picker
- **Extraction** — file is sent to `POST /extract` on the local API server (120 s timeout)
- **Results panel** — shows extracted fields, confidence bars, proposed filename, and proposed SharePoint category
- **Server status** — green/grey indicator in the top-right corner; click to re-check

---

### Troubleshooting

| Symptom | Fix |
|---|---|
| Grey "Server offline" banner | API server isn't running — start it with `uvicorn finextract.server:app --reload` |
| CORS error in browser console | The API already allows all origins — confirm the server is on port 8000 |
| `flutter: command not found` | Flutter `bin/` folder not on `$PATH` — re-open the terminal after adding it |
| Windows desktop build fails | Install Visual Studio 2022 with "Desktop development with C++" workload |
| Linux build fails on missing libs | `sudo apt install clang cmake ninja-build libgtk-3-dev` |
| macOS build fails | Run `xcode-select --install` and accept the license |

---

## 4. Running the API server

Start the FastAPI server from the repo root (with the venv active):

```bash
uvicorn finextract.server:app --reload --port 8000
```

Check it's up:

```bash
curl http://localhost:8000/health
# {"status":"ok","version":"0.1.0"}
```

### Extract a document via the API

```bash
curl -X POST http://localhost:8000/extract \
  -F "file=@files/20181130-SiVEST-Inv40705.pdf"
```

The response includes extracted fields, a proposed filename, a proposed SharePoint category, and confidence scores.

---

## 5. CLI usage

All commands require the venv to be active (`source .venv/bin/activate`).

### Plan (dry run)

Discover documents in a local folder, run extraction, and output a rename plan without moving anything:

```bash
finextract plan \
  --input ./files \
  --config configs/policies/default-v1.yaml \
  --output plan.json \
  --db audit.db
```

### Apply

Execute a plan produced by `plan`:

```bash
finextract apply \
  --plan plan.json \
  --db audit.db
```

### Version

```bash
finextract version
```

### Global flag

`--verbose` enables structured debug logging on any command:

```bash
finextract --verbose plan --input ./files --config configs/policies/default-v1.yaml
```

---

## 6. SharePoint sync

The `sync` command pulls documents from SharePoint, runs extraction, and reclassifies them in-place.

### Required environment variables

```bash
export SHAREPOINT_TENANT_ID="<your-tenant-id>"
export SHAREPOINT_CLIENT_ID="<your-app-client-id>"
export SHAREPOINT_SITE_ID="<your-site-id>"
export SHAREPOINT_DRIVE_ID="<your-drive-id>"
```

### Optional variables

| Variable | Description | Default |
|---|---|---|
| `SHAREPOINT_SOURCE_FOLDERS` | Comma-separated folder paths to scan | (all) |
| `SHAREPOINT_CERT_PATH` | Path to a certificate for client-credential auth | — |

If `SHAREPOINT_CERT_PATH` is not set, the sync uses **device-code interactive auth** (a browser window will open).

### Run (dry run — inspect without moving files)

```bash
finextract sync \
  --config configs/policies/default-v1.yaml \
  --dry-run \
  --db audit.db
```

### Run (apply — actually reclassify)

```bash
finextract sync \
  --config configs/policies/default-v1.yaml \
  --apply \
  --db audit.db
```

---

## 7. Running tests

```bash
# Unit tests
pytest tests/unit

# Integration tests (requires real files in ./files)
pytest tests/integration

# All tests with coverage report
pytest
```

Code quality:

```bash
ruff check src tests
mypy src
```

---

## 8. Running evaluations

Evaluations measure per-field extraction accuracy against a golden manifest (JSONL).

See `evals/README.md` for the manifest format. Quick start:

```bash
finextract evaluate \
  --manifest evals/manifests/invoice-v1.jsonl \
  --config configs/policies/default-v1.yaml
```

The command exits with code `1` if any required field falls below **90% exact-match accuracy**.

---

## 9. Configuration reference

### Policy file — `configs/policies/default-v1.yaml`

| Key | Description |
|---|---|
| `naming.template` | Filename template using `{field}` placeholders |
| `naming.max_length` | Maximum total filename length |
| `naming.collision_strategy` | What to do when a name already exists (`content_hash_suffix`) |
| `categories[].destination` | SharePoint folder path template for a document type |
| `categories[].when` | Expression that selects which documents match this category |
| `thresholds.auto_apply` | Confidence above which renames are applied automatically (0–1) |
| `thresholds.manual_review` | Confidence below which documents go to the review queue |
| `thresholds.ocr_min_coverage` | Minimum text coverage ratio before OCR is triggered |
| `thresholds.ocr_min_confidence` | Minimum Tesseract confidence score to trust OCR output |

### Schema file — `configs/schemas/invoice-v1.yaml`

Defines fields (id, required flag, extraction rules, normalizers) used during the extraction pass.

---

## Project layout

```
financial-ocr/
├── configs/
│   ├── policies/default-v1.yaml   # naming rules, categories, thresholds
│   └── schemas/invoice-v1.yaml    # field definitions
├── evals/                         # evaluation manifests and README
├── files/                         # sample documents for local testing
├── flutter_ui/                    # Flutter front-end
│   └── lib/
│       ├── main.dart
│       ├── screens/home_screen.dart
│       └── services/api_service.dart
├── src/finextract/
│   ├── cli.py                     # Click CLI (plan / apply / sync / evaluate)
│   ├── server.py                  # FastAPI server (/health, /extract)
│   └── ...                        # domain, extraction, documents, policies, …
├── tests/
│   ├── unit/
│   ├── integration/
│   └── contract/
├── pyproject.toml                 # Python project config and dependencies
└── audit.db                       # SQLite audit log (created on first run)
```
