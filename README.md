# ClipLearn

> **Clip to Learn, Learn in Use** — 截即学，用中学

A cross-platform desktop app that lets you select any area of your screen (via `Ctrl+Shift+C`), extracts text using OCR, translates it between Chinese and English, and reads it aloud three times for spaced learning.

Built with **Electron** (frontend shell) + **Python Flask** (backend) + **Tesseract OCR** (offline text recognition).

---

<p align="center">
  <a href="https://github.com/cliplearn/cliplearn/releases/latest">
    <img src="https://img.shields.io/badge/Download-Windows-0078D6?style=for-the-badge&logo=windows&logoColor=white" alt="Download for Windows">
  </a>
  <br>
  <sub>No admin required &middot; Auto-start &middot; Minimize to tray &middot; 2-click install</sub>
</p>

---

## Features

- 📸 **Screen Capture** — Press `Ctrl+Shift+C`, drag to select any screen area, and ClipLearn extracts the text
- 🌐 **OCR + Translation** — Offline Tesseract OCR (`chi_sim+eng`) + Google Translate via `deep-translator`
- 🔊 **Triple-play Audio** — Each snippet is read aloud 3 times (♀ Jenny → ♂ Guy → ♀ Jenny) using Microsoft Edge TTS
- 🃏 **Card System** — Texts are stored as flash cards with navigation, deletion (to Windows Recycle Bin), and archive
- 🎨 **3 Themes** — Dark, Light, and Cool (purple/pink gradient)
- 🖥️ **System Tray** — Minimizes to a floating badge; stays in the system tray
- 🔄 **Auto-start** — Pass `--autostart` to launch minimized to tray on boot

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Desktop Shell | Electron 28 |
| Frontend | Vanilla HTML/CSS/JS (no framework) |
| Backend | Python Flask 3.x |
| OCR | Tesseract (`chi_sim+eng`) via `pytesseract` |
| Translation | `deep-translator` (Google Translate) |
| TTS | `edge-tts` (Jenny + Guy voices) |
| Database | SQLite (4 tables, WAL mode) |
| Auth (optional) | AWS Textract / Translate / Polly |

---

## Project Structure

```
cliplearn/
├── main.js               # Electron main process (window, tray, shortcuts)
├── preload.js            # Secure IPC bridge between renderer and main
├── package.json          # Node.js metadata & electron dependency
├── requirements.txt      # Python dependencies
├── server/
│   ├── app.py            # Flask app factory + entry point
│   ├── database.py       # SQLite schema, queries, cleanup routines
│   ├── routes.py         # 12 API routes (OCR, translation, TTS, trash, etc.)
│   ├── static/           # Icons, overlay HTML for screenshot mode
│   └── templates/
│       └── index.html    # Main UI (500+ lines of CSS + vanilla JS)
└── .gitignore
```

---

## Getting Started

### Prerequisites

1. **Node.js** ≥ 18
2. **Python** ≥ 3.10 (with a virtual environment recommended)
3. **Tesseract-OCR** — [Download from UB Mannheim](https://github.com/UB-Mannheim/tesseract/wiki)
   - Install with Chinese Simplified language pack (`chi_sim`)
   - The app auto-detects Tesseract via PATH, registry, and common install locations

### Install

```bash
# 1. Clone the repo
git clone https://github.com/cliplearn/cliplearn.git
cd cliplearn

# 2. Set up Python venv and install deps
python -m venv .venv
.venv\Scripts\activate      # Windows
# source .venv/bin/activate # macOS/Linux
pip install -r requirements.txt

# 3. Install Electron
npm install
```

### Run

```bash
npm start
```

The ClipLearn window appears on the right side of your screen. Press `Ctrl+Shift+C` to capture text from anywhere.

---

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl+Shift+C` | Start screen area capture |
| `Esc` | Cancel capture |

---

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

---

<p align="center">
  <a href="https://www.cliplearn.ai">cliplearn.ai</a>
  &nbsp;·&nbsp;
  © 2026 ClipLearn
</p>
