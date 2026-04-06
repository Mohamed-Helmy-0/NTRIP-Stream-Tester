# NTRIP Stream Tester

A desktop GUI tool for testing NTRIP (Networked Transport of RTCM via Internet Protocol) caster connections, validating credentials, and inspecting RTCM correction streams in real time.

![Python](https://img.shields.io/badge/Python-3.8%2B-blue)
![PyQt5](https://img.shields.io/badge/GUI-PyQt5-green)
![License](https://img.shields.io/badge/License-MIT-yellow)
![Open Source](https://img.shields.io/badge/Open%20Source-%E2%9D%A4-red)

## Features

- Connect to any NTRIP caster and stream RTCM corrections
- One-click credential testing before streaming
- Real-time RTCM message type breakdown table
- Live stats: bytes received, frame count, unique message types, elapsed time
- Auto-generates GGA position string from configurable lat/lon/alt
- Dark themed UI
- Configurable stream duration (or unlimited)

## Download

Go to [Releases](../../releases) and download **NTRIP_Stream_Tester.exe** — no Python installation required.

## Run from Source

```bash
pip install PyQt5
python PyQt/ntrip_stream_tester.py
```

## Included Tools

| Tool | Path | Description |
|------|------|-------------|
| **Desktop GUI** | `PyQt/ntrip_stream_tester.py` | Full-featured PyQt5 desktop app (the main tool) |
| **CLI Script** | `Script/ntrip_test.py` | Command-line NTRIP tester |
| **Web UI** | `Web/ntrip_server.py` | Browser-based NTRIP tester with HTML frontend |

## Supported RTCM Messages

GPS (1001-1004, 1019, 1074-1077), GLONASS (1009-1012, 1020, 1084-1087), Galileo (1094-1097), BeiDou (1124-1127), QZSS (1114-1117), Station info (1005-1008, 1033), and GLONASS bias (1230).

## Building the Executable

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name NTRIP_Stream_Tester PyQt/ntrip_stream_tester.py
```

The executable will be in the `dist/` folder.

## License

MIT License — free to use, modify, and distribute.
