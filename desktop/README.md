# Leos Lyssnare for Windows and Linux

The same app as the macOS version, for **Windows 10/11 (64-bit)** and **Linux (x86-64)**. It transcribes meetings **offline, on the computer**, and works out who said what.

- **Transcribe an existing file:** choose or drag in an `.m4a`, `.mp3`, `.wav` or other audio file.
- **Record a meeting:** Start → Pause/Resume → Stop. Paused sections are left out. Everything is saved to **one** audio file, as **MP3 on Windows** and **Ogg (Opus) on Linux**, and when you stop, the app asks **“Transcribe the recording?”**.
- **Record both sides of a call:** choose the microphone, and under *Also record sound from* choose the app the meeting is in (Teams, Zoom, a browser…) or *All sound from this computer*. The app's sound is mixed with the microphone into the same recording, so the people you're talking to are transcribed too. An app shows up in the list once it plays sound; if you choose it before the call starts, it's picked up as soon as it does. Wear headphones, otherwise the microphone hears the call from the speakers as well. On Windows, recording an app's sound needs Windows 11 (on Windows 10 only the microphone can be chosen).
- **Who said what:** each part of the transcript is labelled *Speaker 1*, *Speaker 2*… You can rename them, for example to *Anna*, and the transcript updates everywhere. If one person was split into two speakers, merge them with the button next to the name (*Undo* is offered right after). If part of a line was said by someone else, select it, right-click and choose *Move to speaker* or *Move to a new speaker*: it becomes a line of its own, and what came after it gets a new line with the original speaker. The times follow the words.
- Transcripts get timestamps and are saved automatically as `.txt`, in the same format as the Mac app.
- **Open a saved transcript:** *Open transcript…* (or drop a `.txt` on the window) shows it again, with its recording. The recording is found by the file name on the transcript's first line, next to the transcript, in the recordings folder or in Downloads; otherwise choose it with *Find audio file…*. Exact timings are kept in the app's data folder (`~/.local/share/LeosLyssnare/Transcript data`, `%LOCALAPPDATA%\LeosLyssnare\Transcript data`), so word-by-word following works after reopening too.
- **Listen back:** press play under the transcript. The line being spoken is highlighted (word by word when speakers are identified) and kept in the middle of the view as it plays. Click any line to play from there. Scroll away to read elsewhere and *Back to playback* takes you back.

On the Mac, the app uses Apple's Neural Engine through WhisperKit. That only exists on Apple hardware, so this version uses:

| Job | Library | Model |
|---|---|---|
| Speech to text | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (CTranslate2, int8 on the CPU) | OpenAI Whisper: Large v3 Turbo, Small or Base |
| Who is speaking | [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) (ONNX Runtime) | pyannote segmentation 3.0 + WeSpeaker ResNet34 voice embeddings |
| Interface | Qt 6 (PySide6) | – |
| Recording | Windows: PortAudio (sounddevice) and WASAPI process loopback for apps. Linux: PulseAudio/PipeWire through `pactl` and `parec`. MP3/Opus encoding with FFmpeg (PyAV) | – |

No audio or text ever leaves the computer.

## Download and install

Ready-made builds come from GitHub Actions (`.github/workflows/build.yml`):

- Every merge to `main` runs the **Build apps** workflow, which attaches the builds to the run. Look under *Actions* and download the artifacts.
- Pushing a tag such as `desktop-v1.0.0` also publishes them as a GitHub Release.

### Windows

- **Installer:** run `Leos_Lyssnare-<version>-windows-x64-setup.exe`. It installs for your user only, without administrator rights, and adds Leos Lyssnare to the Start menu.
- **Portable:** unzip `Leos_Lyssnare-<version>-windows-x64.zip` anywhere and run `LeosLyssnare.exe`.

The builds aren't code-signed, so Windows SmartScreen may warn the first time. Choose *More info › Run anyway*.

### Linux (AppImage)

```sh
chmod +x Leos_Lyssnare-x86_64.AppImage
./Leos_Lyssnare-x86_64.AppImage
```

The AppImage contains everything the app needs and runs on Ubuntu 22.04, Debian 12, Fedora 36 and newer, Arch Linux, and similar distributions. It runs on X11 and on Wayland desktops. Recording goes through ALSA, which reaches PulseAudio and PipeWire on normal desktops.

If double-clicking doesn't start it, your distribution may lack FUSE 2 (Ubuntu 24.04: `sudo apt install libfuse2t64`, Arch: `sudo pacman -S fuse2`). Alternatively, run it with `--appimage-extract-and-run`.

## First run: download the models once (the only step that needs internet)

1. Pick a model:
   - **Large v3 Turbo** (≈1.6 GB): best quality. Recommended for Swedish and for meetings.
   - **Small** (≈480 MB): faster, somewhat less accurate.
   - **Base** (≈150 MB): fastest, lowest quality.
2. Click **Download models**. This downloads the speech model, plus the speaker recognition models (≈35 MB) if *Identify speakers* is on.
3. When the green **“Available offline”** badge appears, the app works without internet.

Speech models come from Hugging Face and speaker models from the sherpa-onnx GitHub releases. Behind a company proxy that inspects HTTPS, set `SSL_CERT_FILE` to your company's CA bundle.

## Appearance

The window follows the system's light or dark setting and looks the same on Windows and Linux (the Mac app uses the same palette and layout). The font is [Inter](https://rsms.me/inter/) (SIL Open Font License, included in `leoslyssnare/resources/fonts`).

If your Linux desktop doesn't tell applications whether it's dark or light, choose the look yourself: `LEOSLYSSNARE_THEME=dark ./Leos_Lyssnare-x86_64.AppImage` (or `light`).

## Speed

Everything runs on the CPU, so it's slower than on an M1's Neural Engine. Speech is transcribed in batches of eight stretches at a time, which is about twice as fast as one at a time. With *Large v3 Turbo* on a desktop Intel Core i7-13700K, 45 minutes of audio takes about **6 minutes** to transcribe plus about **3 minutes** to identify speakers; laptops take longer. *Small* is about three times faster. Because of the batches, *Stop* can take up to half a minute to take effect. The computer won't go to sleep by itself while it records or transcribes.

## Recording format

Recordings use the format each system handles best:

| | Windows | Linux |
|---|---|---|
| File | `Meeting <date time>.mp3` | `Meeting <date time>.ogg` |
| Codec | MP3, 128 kbit/s mono | Opus, 64 kbit/s mono |
| Size per hour | ≈58 MB | ≈29 MB |
| Opens in | Media Player, Groove, browsers, any audio app | GNOME/KDE players, VLC, Audacity, browsers |

For speech, Opus at 64 kbit/s sounds as good as MP3 at twice the size. Both formats are written as a stream, so if the computer crashes or loses power during a meeting, everything recorded until then can still be played and transcribed. (The Mac app records `.m4a`; files from all three apps can be transcribed on any of them.)

## Where files are saved

| What | Windows | Linux |
|---|---|---|
| Recordings | `Documents\LeosLyssnare\Recordings` | `~/Documents/LeosLyssnare/Recordings` |
| Transcripts | `Documents\LeosLyssnare\Transcripts` | `~/Documents/LeosLyssnare/Transcripts` |
| Models | `%LOCALAPPDATA%\LeosLyssnare\Models` | `~/.local/share/LeosLyssnare/Models` |
| Settings | Registry: `HKCU\Software\LeosLyssnare\LeosLyssnare` | `~/.config/LeosLyssnare/LeosLyssnare.conf` |

## Microphone access

- **Windows:** if recording fails, turn on **Settings › Privacy & security › Microphone › Let desktop apps access your microphone**. Choose the microphone under *Microphone* (*Default* follows your sound settings). An app is recorded together with the processes it started (browsers, Teams and other apps play sound from helper processes), at the volume it plays at.
- **Linux:** choose the microphone under *Microphone* (*Default* follows your sound settings). The list and the choice of app need PulseAudio or PipeWire and their tools `pactl` and `parec`, which desktops normally have; if they're missing (`sudo apt install pulseaudio-utils`, Fedora: `pulseaudio-utils`, Arch: `libpulse`), the app records from the default input device and the choices are hidden. An app is recorded at the volume it plays at, so don't mute it in the volume mixer.

## Building it yourself

Builds must be made on the target system: Windows builds on Windows, Linux builds on Linux.

**Linux AppImage** (Debian/Ubuntu shown):

```sh
sudo apt install python3-venv cmake build-essential libasound2-dev curl git \
  libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 libxcb-xkb1 libxkbcommon-x11-0
cd desktop
./packaging/build-appimage.sh            # → build/Leos_Lyssnare-x86_64.AppImage
```

On Arch Linux, install the tools with:

```sh
sudo pacman -S --needed python cmake base-devel alsa-lib curl git \
  xcb-util-cursor xcb-util-wm xcb-util-keysyms libxkbcommon-x11
```

An AppImage built on Arch only runs on systems as new as the one it was built on. For one that runs everywhere, use the build from GitHub Actions, which is made on Ubuntu 22.04.

The script builds its own PortAudio without JACK, so the AppImage doesn't depend on the user's audio packages.

**Windows**: install 64-bit Python 3.11 or newer from python.org and, for the installer, [Inno Setup 6](https://jrsoftware.org/isinfo.php). Then:

```powershell
cd desktop
powershell -ExecutionPolicy Bypass -File packaging\build-windows.ps1
# → build\Leos_Lyssnare-<version>-windows-x64-setup.exe and a portable .zip
```

**Running from source** for development (Linux needs PortAudio installed: `libportaudio2` on Debian/Ubuntu, `portaudio` on Arch):

```sh
cd desktop
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt pytest
python packaging/make_icons.py                       # renders the app icon, once
python run.py
python -m pytest tests
```

`LeosLyssnare --self-test` (or `python run.py --self-test`) loads every native library and round-trips a short recording without opening a window. CI uses it to check the packaged apps.

## Project structure

```
desktop/
  run.py                        Entry point
  requirements.txt
  leoslyssnare/
    app.py                      The window (Qt)
    engine.py                   Model downloads, transcription, speaker identification
    transcript.py               Transcript model, speaker turns, .txt format
    recorder.py                 Recording with pause/resume into one .mp3 / .ogg
    sources.py                  Choosing the microphone and an app; follows and mixes the app's sound
    pulse.py                    Linux: lists microphones and apps playing sound, captures them
    wasapi.py                   Windows: the same, through WASAPI (ctypes, no compiling)
    paths.py                    File locations
    keepawake.py                Stops the computer from sleeping while working
    resources/icon.png          Rendered by make_icons.py (not in git)
  packaging/
    leoslyssnare.spec           PyInstaller build (both platforms)
    build-appimage.sh           Linux: PortAudio + PyInstaller + appimagetool
    build-windows.ps1           Windows: PyInstaller + zip + Inno Setup installer
    windows-installer.iss       Inno Setup script
    leoslyssnare.desktop        Linux desktop entry
    icon.svg, icon.ico          App icon (make_icons.py renders the PNG and ICO; they're not in git)
  tests/
```
