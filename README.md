# GateGuard

> Camera-based unauthorized vehicle detection and physical alarm system for residential complexes.

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-green.svg)](https://fastapi.tiangolo.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

GateGuard watches a gate camera feed in real time, reads license plates via ALPR (Automatic License Plate Recognition), checks them against a local database of authorized vehicles, and triggers a physical siren + strobe via an ESP32 relay when an unauthorized vehicle passes through.

The authorized vehicle database is synced from an existing **Moonwell MW-305** UHF RFID access control system's `.mdb` file — no manual data entry needed.

---

## Table of Contents

- [Problem Statement](#problem-statement)
- [How It Works](#how-it-works)
- [Features](#features)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running the Application](#running-the-application)
- [Windows Auto-Start (Production Deployment)](#windows-auto-start-production-deployment)
- [Web UI](#web-ui)
- [API Reference](#api-reference)
- [ESP32 Alarm Module](#esp32-alarm-module)
- [Using a Fine-Tuned YOLOv8 Model](#using-a-fine-tuned-yolov8-model)
- [YOLOv8 Training Pipeline](#yolov8-training-pipeline)
- [Plate Normalization](#plate-normalization)
- [Project Structure](#project-structure)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)

---

## Problem Statement

The residential complex has a Moonwell MW-305 UHF RFID barrier system. Vehicles with registered UHF tags are identified and the barrier opens automatically. However, **vehicles without tags can tailgate** — they follow closely behind an authorized vehicle and pass through before the barrier closes. The Moonwell system cannot detect this because no tag is read.

GateGuard fills this gap by **watching every vehicle that passes with a camera**, regardless of whether they have a tag.

## How It Works

```
IP Camera (RTSP)
      │
      ▼
  FastAPI Backend
  OpenCV ─► ALPR (EasyOCR or YOLOv8 + OCR)
      │
      ▼
  SQLite Plate Lookup
  (synced daily from Moonwell .mdb)
      │
  Authorized? ─── Yes ──► Log passage
      │
      No
      │
      ▼
  ┌──────────────────────────────────────┐
  │  1. Save screenshot with overlay     │
  │  2. POST http://ESP32/alarm/on       │
  │  3. WebSocket broadcast to browser   │
  └──────────────────────────────────────┘
      │
      ▼
  Security guard sees alarm on screen
  Presses "SILENCE ALARM" button
      │
      ▼
  POST /alarm/off ──► ESP32 relay OFF
```

## Features

- **Real-time ALPR** — reads license plates from a live RTSP camera stream using EasyOCR or a custom fine-tuned YOLOv8 model
- **Moonwell MDB sync** — automatically imports authorized plates from the existing MW-305 access control database
- **Physical alarm** — triggers a siren + strobe light via an ESP32 relay module over HTTP
- **Live web dashboard** — real-time updates via WebSocket, full-screen red flashing alarm overlay, one-button silence
- **Evidence capture** — automatically saves timestamped screenshots of unauthorized passages with a "KACAK GECIS" watermark
- **Fuzzy matching** — tolerates common OCR errors (0↔O, 1↔I, 8↔B) using Levenshtein distance
- **Deduplication** — prevents repeated alarms for the same plate within a configurable cooldown window
- **Mock mode** — full development and testing without a real camera, ESP32, or MDB file
- **Windows auto-start** — runs as a background service, automatically restarts on crash or reboot

## Architecture

```
┌──────────────┐     RTSP      ┌──────────────────────────────────────┐
│  IP Camera   │──────────────►│  FastAPI Backend (Python)            │
│  at the gate │               │                                      │
└──────────────┘               │  Camera Thread ──► frame_queue       │
                               │       │                               │
                               │       ▼                               │
                               │  Detection Engine (async loop)        │
                               │   ├─ EasyOCR or YOLOv8 + OCR         │
                               │   ├─ Plate Normalizer                 │
                               │   ├─ SQLite Lookup (exact + fuzzy)    │
                               │   ├─ Screenshot Manager               │
                               │   └─ Alarm Manager (ESP32 HTTP)       │
                               │       │                               │
                               │       ▼                               │
                               │  WebSocket Broadcast ──► Browser UI   │
                               └──────────────────────────────────────┘
                                          │
                                     HTTP GET
                                          │
                               ┌──────────▼──────────┐
                               │  ESP32 + Relay       │
                               │  Siren + Strobe      │
                               └─────────────────────┘
```

## Prerequisites

| Requirement | Notes |
|---|---|
| **Windows 10/11** | Tested on Windows 10/11 x64 |
| **Python 3.10+** | 3.10, 3.11, or 3.12 recommended |
| **Microsoft Access Database Engine** | Must match your Python architecture (32-bit or 64-bit). [Download here](https://www.microsoft.com/en-us/download/details.aspx?id=54920) |
| **IP Camera** | Any camera with RTSP support (Dahua, Hikvision, etc.) |
| **ESP32 + Relay module** | Optional — the system works in software-only mode without it |
| **Git** | For cloning the repository |

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/ysfzkn/plate-track-guard-alert.git
cd plate-track-guard-alert

# 2. (Recommended) Create a virtual environment
python -m venv venv
venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy and edit the environment file
copy .env.example .env
# Edit .env with your actual camera URL, ESP32 IP, MDB path, etc.
```

> **Note:** EasyOCR downloads its model (~100 MB) on first run. This is a one-time download.

## Configuration

All settings are loaded from the `.env` file in the project root.

```env
# ── Camera ──────────────────────────────────────────────
RTSP_URL=rtsp://admin:password@192.168.1.100:554/Streaming/Channels/101

# ── ESP32 Alarm Module ──────────────────────────────────
ESP32_IP=192.168.1.50

# ── Databases ───────────────────────────────────────────
MDB_PATH=moonwel_db/MW305_DB200.mdb     # Moonwell Access database (read-only)
SQLITE_PATH=data/gateguard.db            # Local cache (auto-created)

# ── Screenshots ─────────────────────────────────────────
SCREENSHOT_DIR=static/screenshots

# ── Detection ───────────────────────────────────────────
MOCK_MODE=false              # true = fake camera + mock detections (no hardware needed)
PROCESS_FPS=2                # Frames analyzed per second (2 is a good balance)
CONFIDENCE_THRESHOLD=0.4     # Minimum OCR confidence (0.0 – 1.0)
ALARM_COOLDOWN_SEC=60        # Seconds before the same plate can trigger again
FUZZY_TOLERANCE=1            # Levenshtein distance tolerance for fuzzy matching

# ── YOLO (optional) ─────────────────────────────────────
USE_YOLO=false               # true = use fine-tuned YOLOv8 instead of EasyOCR
YOLO_WEIGHTS=models/plate_detector.pt

# ── General ─────────────────────────────────────────────
LOG_LEVEL=INFO
```

### Key settings explained

| Setting | What it does |
|---|---|
| `PROCESS_FPS=2` | Analyzes 2 frames per second. Higher = more CPU. Lower = might miss fast vehicles. |
| `CONFIDENCE_THRESHOLD=0.4` | OCR results below this confidence are discarded. Increase if you get too many false positives. |
| `ALARM_COOLDOWN_SEC=60` | After an alarm fires for plate X, the same plate won't trigger another alarm for 60 seconds. Prevents repeated alarms for a car waiting at the gate. |
| `FUZZY_TOLERANCE=1` | Allows 1 character difference when matching plates (catches OCR errors like `0` vs `O`). Set to `0` for exact matching only. |

## Running the Application

### Quick start (mock mode for testing)

```bash
# Make sure MOCK_MODE=true in .env
python main.py
```

Open **http://localhost:8000** in your browser. You'll see fake vehicle passages appearing in real time.

### Production mode

```bash
# Set MOCK_MODE=false in .env and configure your real camera URL, ESP32 IP, MDB path
python main.py
```

The server starts on `http://0.0.0.0:8000`. Open it from any device on the same network.

---

## Windows Auto-Start (Production Deployment)

In a production environment, GateGuard needs to:

1. **Start automatically** when the PC boots
2. **Restart automatically** if it crashes
3. **Run in the background** without requiring a terminal window
4. **Open the dashboard** in the browser for the security guard

Three scripts are provided in the project root:

### Option 1: Simple Start (`start.bat`)

Double-click to start the server. A console window stays open.

```
start.bat
```

### Option 2: Windows Service via NSSM (`install_service.bat`)

**Recommended for production.** Installs GateGuard as a Windows service that starts on boot and auto-restarts on crashes.

```
# Run as Administrator
install_service.bat
```

This uses [NSSM (Non-Sucking Service Manager)](https://nssm.cc/download). Download `nssm.exe` and place it in the project root or in your PATH.

Once installed:
- The service starts automatically on Windows boot
- If the process crashes, NSSM restarts it within 10 seconds
- Logs are written to `logs/service.log`
- No console window is visible

**Service management:**
```cmd
nssm start GateGuard       # Start the service
nssm stop GateGuard        # Stop the service
nssm restart GateGuard     # Restart the service
nssm remove GateGuard      # Uninstall the service
```

### Option 3: Startup Folder Shortcut

1. Press `Win + R`, type `shell:startup`, press Enter
2. Create a shortcut to `start.bat` in that folder
3. The server starts every time you log in

### Accessing the Dashboard

After the server starts, the security guard opens a browser (Chrome recommended) and navigates to:

```
http://localhost:8000
```

> **Tip:** Create a desktop shortcut or set the browser to open this URL on startup. In Chrome: Settings → On startup → Open a specific page → Add `http://localhost:8000`.

---

## Web UI

The dashboard is a single-page web application served by FastAPI.

### Normal State
- **Left panel:** Recent vehicle passages (plate, time, status, owner name, confidence)
- **Right panel:** Today's statistics, system status, alarm indicator

### Alarm State
When an unauthorized vehicle is detected:
- Full-screen red flashing overlay appears
- Large plate number displayed
- Screenshot of the vehicle shown
- Audio beep plays (if browser allows)
- A large **"ALARMI SUSTUR"** (Silence Alarm) button appears
- Pressing the button sends `/alarm/off` to the backend, which turns off the ESP32 relay

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | Serves the web dashboard |
| `WebSocket` | `/ws` | Real-time event stream |
| `POST` | `/alarm/off` | Silence the active alarm |
| `POST` | `/api/sync` | Trigger manual MDB → SQLite sync |
| `GET` | `/api/passages?limit=50` | Get recent passage records |
| `GET` | `/api/stats` | Get today's statistics |
| `GET` | `/api/status` | Get system status (camera, alarm, sync) |

### WebSocket Message Types

```jsonc
// Authorized vehicle passed
{"type": "passage", "data": {"plate": "34TV3409", "is_authorized": true, "owner_name": "MUSTAFA C.", ...}}

// Unauthorized vehicle — alarm triggered
{"type": "alarm_on", "data": {"plate": "34ZZ9999", "is_authorized": false, "screenshot_url": "/static/screenshots/...", ...}}

// Alarm silenced by operator
{"type": "alarm_off", "data": {}}

// MDB sync completed
{"type": "sync_complete", "data": {"total": 332, "new": 5, "updated": 327, ...}}
```

---

## ESP32 Alarm Module

The ESP32 runs a simple HTTP server that controls a relay connected to a siren and/or strobe light.

### Hardware Setup

```
ESP32 GPIO 4 ──► Relay Module IN
                 Relay COM ──► 12V Power Supply (+)
                 Relay NO  ──► Siren / Strobe (+)
                 Siren (-) ──► 12V Power Supply (-)
```

### Flashing the Firmware

1. Open `esp32/esp32_relay.ino` in Arduino IDE
2. Set your WiFi credentials:
   ```cpp
   const char* WIFI_SSID     = "YourWiFiName";
   const char* WIFI_PASSWORD = "YourWiFiPassword";
   ```
3. Select your ESP32 board and COM port
4. Upload

### ESP32 Endpoints

| Endpoint | Action |
|---|---|
| `GET /alarm/on` | Turn relay ON (siren active) |
| `GET /alarm/off` | Turn relay OFF (siren silent) |
| `GET /status` | Returns `{"relay": "on/off", "uptime": 1234}` |

---

## Using a Fine-Tuned YOLOv8 Model

By default, GateGuard uses **EasyOCR + OpenCV contour detection** for plate recognition. This works out of the box but accuracy depends heavily on lighting and camera angle.

For better accuracy, you can train a custom **YOLOv8** model on frames from your actual gate camera and use it instead. The system supports a hybrid pipeline:

```
YOLOv8 (plate localization) ──► crop plate region ──► EasyOCR (text reading)
```

### Switching to YOLO

After training (see [YOLOv8 Training Pipeline](#yolov8-training-pipeline) below), you'll have a `best.pt` weights file.

1. **Copy the weights file** to the project:
   ```bash
   mkdir models
   copy yolo_training\runs\detect\plate_detector\weights\best.pt models\plate_detector.pt
   ```

2. **Update `.env`:**
   ```env
   USE_YOLO=true
   YOLO_WEIGHTS=models/plate_detector.pt
   CONFIDENCE_THRESHOLD=0.25
   ```

3. **Install ultralytics** (if not already):
   ```bash
   pip install ultralytics
   ```

4. **Restart the server.** The detection engine will now use YOLO for plate localization and EasyOCR for text extraction.

### How the YOLO Pipeline Works

| Stage | Default (EasyOCR only) | YOLO + EasyOCR (hybrid) |
|---|---|---|
| **Plate localization** | OpenCV Canny edges + contour filtering | YOLOv8 object detection |
| **Text extraction** | EasyOCR on cropped candidates | EasyOCR on YOLO-cropped regions |
| **Accuracy** | Moderate (depends on contrast/lighting) | High (trained on your actual camera) |
| **Speed** | ~200ms per frame (CPU) | ~100ms per frame (GPU) / ~300ms (CPU) |
| **Setup effort** | None (works out of the box) | Requires 2–4 hours of labeling + training |

### When to Use YOLO

- Your camera has challenging lighting (glare, shadows, night)
- The default contour detection produces too many false positives
- You have a GPU (even a GTX 1650 is enough)
- You can invest 2–4 hours in labeling ~200–500 frames

---

## YOLOv8 Training Pipeline

A complete training pipeline is included in the `yolo_training/` directory. It is optimized for a **GTX 1650 (4 GB VRAM)**.

### Step-by-Step

#### 1. Record Gate Camera Footage

Record 10–15 minutes of video from your gate camera covering:
- Daytime, dusk, and nighttime
- Various vehicle types (cars, SUVs, motorcycles)
- Different weather conditions

Save the videos to `yolo_training/data/raw_videos/`.

#### 2. Extract Frames

```bash
cd yolo_training
python extract_frames.py
```

This extracts ~2 frames per second, skipping near-duplicate frames. Output: `data/extracted_frames/`.

#### 3. Label the Frames

You need to draw bounding boxes around license plates in each frame. Use one of these tools:

| Tool | Type | Recommended for |
|---|---|---|
| [Roboflow](https://roboflow.com) | Web-based | Easiest, auto-exports in YOLO format |
| [CVAT](https://cvat.ai) | Web-based | Free, powerful, open-source |
| [LabelImg](https://github.com/HumanSignal/labelImg) | Desktop app | Offline, lightweight |

**Class name:** `plate` (single class)

Export in **YOLOv8 format** and place the files in `data/dataset/`.

#### 4. Prepare the Dataset

If you used Roboflow, it exports a ready-to-use dataset with `dataset.yaml`. Otherwise:

```bash
python setup_dataset.py
```

This splits images into train/val (85/15) and generates `dataset.yaml`.

#### 5. Train

```bash
python train_yolo.py
```

| Parameter | Value | Notes |
|---|---|---|
| Model | `yolov8n.pt` (nano) | Smallest YOLOv8, ideal for GTX 1650 |
| Batch size | 8 | Reduce to 4 if you get OOM errors |
| Image size | 640 | Standard YOLO resolution |
| Epochs | 100 | Early stopping at patience=20 |
| Mixed precision | Enabled (FP16) | Halves VRAM usage |

Training time: ~30–60 minutes for 200–500 images.

Output: `runs/detect/plate_detector/weights/best.pt`

#### 6. Test

```bash
# Test on a video
python test_model.py --source test_video.mp4 --show

# Test on webcam
python test_model.py --source 0 --show
```

#### 7. Deploy

Copy `best.pt` to the main project and enable YOLO mode — see [Using a Fine-Tuned YOLOv8 Model](#using-a-fine-tuned-yolov8-model) above.

---

## Plate Normalization

Turkish license plates come in various formats. GateGuard normalizes both stored and detected plates before comparison:

| Input | Normalized |
|---|---|
| `34 TV 3409` | `34TV3409` |
| `34-LB-2317` | `34LB2317` |
| `34PB5705` | `34PB5705` |

**Normalization rules:**
1. Convert to uppercase
2. Remove all spaces, dashes, and dots
3. Translate Turkish characters: `İ→I`, `Ş→S`, `Ç→C`, `Ğ→G`, `Ö→O`, `Ü→U`
4. Validate against Turkish plate regex: `^(01-81)[A-Z]{1-3}[0-9]{2-4}$`

**Fuzzy matching:** If exact match fails, a Levenshtein distance check (default tolerance: 1) catches common OCR misreads:
- `0` ↔ `O`
- `1` ↔ `I`
- `8` ↔ `B`
- `5` ↔ `S`

---

## Project Structure

```
plate-track-guard-alert/
│
├── main.py                       # FastAPI application entry point
├── config.py                     # Configuration loader (.env → Settings)
├── requirements.txt              # Python dependencies
├── .env                          # Environment variables (not in git)
├── .env.example                  # Template for .env
├── start.bat                     # Simple start script (double-click)
├── install_service.bat           # Install as Windows service (NSSM)
├── uninstall_service.bat         # Remove Windows service
│
├── app/
│   ├── database.py               # SQLite + plate normalization + fuzzy matching
│   ├── mdb_sync.py               # Moonwell MDB → SQLite sync worker
│   ├── camera.py                 # RTSP camera reader + MockCamera
│   ├── plate_detector.py         # ALPR (EasyOCR / YOLOv8 / Mock)
│   ├── alarm_manager.py          # ESP32 HTTP alarm control
│   ├── detection_engine.py       # Core orchestrator (camera → detect → lookup → alarm)
│   ├── screenshot.py             # Screenshot capture with overlay/watermark
│   ├── websocket_manager.py      # WebSocket connection manager
│   ├── routes.py                 # API endpoints
│   └── models.py                 # Pydantic + dataclass models
│
├── static/
│   ├── index.html                # Web dashboard (Tailwind CSS + vanilla JS)
│   └── screenshots/              # Saved unauthorized passage images (auto-created)
│
├── esp32/
│   └── esp32_relay.ino           # ESP32 Arduino firmware for relay control
│
├── models/                       # Trained model weights (not in git)
│   └── plate_detector.pt         # Fine-tuned YOLOv8 weights (optional)
│
├── yolo_training/                # YOLOv8 training pipeline
│   ├── extract_frames.py         # Extract frames from gate camera videos
│   ├── setup_dataset.py          # Prepare train/val split + dataset.yaml
│   ├── train_yolo.py             # Train YOLOv8 (GTX 1650 optimized)
│   ├── test_model.py             # Run inference on video/image/camera
│   └── README.md                 # Detailed training guide
│
├── data/                         # SQLite database (auto-created, not in git)
├── logs/                         # Application logs (auto-created, not in git)
└── moonwel_db/                   # Moonwell MDB file (not in git)
```

---

## Troubleshooting

### Camera not connecting

- Verify the RTSP URL is correct. Test with VLC: Media → Open Network Stream → paste URL.
- Make sure the camera and PC are on the same network.
- Common Dahua RTSP format: `rtsp://admin:password@IP:554/cam/realmonitor?channel=1&subtype=0`
- Check that port 554 is not blocked by Windows Firewall.

### MDB sync fails

- Ensure **Microsoft Access Database Engine** is installed and matches your Python architecture (32-bit Python needs 32-bit driver, 64-bit Python needs 64-bit driver).
- Check that `MDB_PATH` in `.env` points to the correct file.
- If Moonwell software has the file locked, GateGuard retries 3 times with 5-second intervals.

### ESP32 not responding

- Verify the ESP32 is connected to WiFi (check serial monitor output).
- Ping the ESP32 IP: `ping 192.168.1.50`
- Test the endpoint directly: `curl http://192.168.1.50/status`
- GateGuard works without an ESP32 — it still captures screenshots and shows alarms in the web UI.

### Low OCR accuracy

- Ensure the camera is positioned so plates are clearly visible (not angled too much).
- Add IR illumination for nighttime operation.
- Increase `CONFIDENCE_THRESHOLD` in `.env` if you get too many false positives.
- Consider training a custom YOLOv8 model — see [YOLOv8 Training Pipeline](#yolov8-training-pipeline).

### Server won't start

- Check that port 8000 is not in use: `netstat -ano | findstr :8000`
- Verify Python and all dependencies are installed: `pip install -r requirements.txt`
- Check `logs/app.log` for error details.

---

## Contributing

Contributions are welcome. Please open an issue first to discuss what you'd like to change.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/my-feature`)
3. Commit your changes (`git commit -m 'Add my feature'`)
4. Push to the branch (`git push origin feature/my-feature`)
5. Open a Pull Request

---

## License

This project is licensed under the [MIT License](LICENSE).
