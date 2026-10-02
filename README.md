# Leos Lyssnare

A native macOS app for Apple Silicon (M1 and newer) that transcribes meetings **offline, on the device**.

> **Windows and Linux:** there is also a version for Windows (installer or portable .zip) and Linux (AppImage) in [`desktop/`](desktop/README.md). It has the same features, uses faster-whisper and sherpa-onnx instead of Apple's Core ML, and GitHub Actions builds it automatically, together with the Mac app.

- **Transcribe an existing file:** choose or drag in an `.m4a` file (`.mp3`, `.wav` and other common audio formats also work).
- **Record a meeting:** Start → Pause/Resume → Stop. Paused sections are left out of the recording. Everything you record while unpaused is saved to **one** `.m4a` file.
- **Record both sides of a call:** choose the microphone, and under *Also record sound from* choose the app the meeting is in (Teams, Zoom, a browser…) or *All sound from this Mac*. The app's sound is mixed with the microphone into the same recording, so the people you're talking to are transcribed too. An app shows up in the list once it plays sound; if you choose it before the call starts, it's picked up as soon as it does. Wear headphones, otherwise the microphone hears the call from the speakers as well. Recording an app's sound needs macOS 14.2 or later.
- When you stop recording, the app asks **“Transcribe the recording?”**. Choose *Transcribe* to start right away.
- **Who said what:** the app identifies different speakers (for example 8 people in a meeting) and labels each part of the transcript *Speaker 1*, *Speaker 2*, and so on. You can rename them, for example to *Anna*, and the transcript updates everywhere. If one person was split into two speakers, merge them with the button next to the name (*Undo* is offered right after). If part of a line was said by someone else, select it, right-click and choose *Move to speaker* or *Move to a new speaker*: it becomes a line of its own, and what came after it gets a new line with the original speaker. The times follow the words.
- **Listen back:** press play under the transcript. The line being spoken is highlighted (word by word when speakers are identified) and kept in the middle of the view as it plays. Click any line to play from there. Scroll away to read elsewhere and *Back to playback* takes you back. The timeline is coloured by who speaks when. Recordings from the Linux app (`.ogg`) can't be played or transcribed on the Mac.
- Transcripts are shown with timestamps and saved automatically as `.txt`, in the same format as the Windows and Linux app.
- **Open a saved transcript:** *Open transcript…* (or drop a `.txt` on the window) shows it again. Its recording is looked for by the file name on the transcript's first line: where it was, next to the transcript, in the recordings folder and in Downloads; otherwise choose it with *Find audio file…*. The exact word timings are kept in `~/Library/Application Support/LeosLyssnare/Transcript data`, so they come back when a transcript is reopened.

Speech recognition uses [WhisperKit](https://github.com/argmaxinc/WhisperKit), which runs OpenAI's Whisper model on the Mac's Neural Engine through Core ML. Speaker identification uses SpeakerKit from the same package, which runs the pyannote speaker model the same way. No audio or text ever leaves the computer.

## Build

Each version must be built on the system it's for: the Mac app on a Mac, the Windows app on Windows, and the Linux AppImage on Linux. If you don't want to build it yourself, GitHub builds all three for you ([see below](#let-github-build-the-apps)).

First get the code:

```sh
git clone https://github.com/CrissZollo/leoslyssnare.git
cd leoslyssnare
```

### macOS

Requirements:

- Mac with Apple Silicon (M1 or newer)
- macOS 14 Sonoma or later
- Xcode 15 or later (or the Xcode Command Line Tools: `xcode-select --install`)

```sh
./scripts/build-app.sh
open "build/Leos Lyssnare.app"
```

You can drag `build/Leos Lyssnare.app` into **Applications**.

An app you build yourself opens without any warning. If you downloaded the app from GitHub instead, see [Opening the Mac app from GitHub](#opening-the-mac-app-from-github).

To work on the code, run `open Package.swift` to open the project in Xcode, then press ⌘R.

### Windows (installer and portable .zip)

Requirements: Windows 10 or 11 (64-bit).

1. Install **Python 3.11 or newer (64-bit)** from [python.org](https://www.python.org/downloads/windows/). In the installer, tick **“Add python.exe to PATH”**.
2. Optional, for an installer (`setup.exe`): install **Inno Setup 6** from [jrsoftware.org](https://jrsoftware.org/isinfo.php), or run `winget install JRSoftware.InnoSetup`. Without it you still get a portable `.zip`.
3. Open **PowerShell** in the `leoslyssnare` folder and run:

   ```powershell
   cd desktop
   powershell -ExecutionPolicy Bypass -File packaging\build-windows.ps1
   ```

The first build downloads the dependencies and takes a few minutes. The results are in `desktop\build\`:

- `Leos_Lyssnare-<version>-windows-x64-setup.exe`: the installer. It installs for your user only, without administrator rights.
- `Leos_Lyssnare-<version>-windows-x64.zip`: the portable version. Unzip it anywhere and run `LeosLyssnare.exe`.

### Linux (AppImage)

Requirements: a 64-bit x86 Linux system. The AppImage runs on the distribution you build it on and on newer ones, so build on the oldest one you want to support.

1. Install the build tools. On **Ubuntu or Debian** (tested on Ubuntu):

   ```sh
   sudo apt install python3-venv cmake build-essential libasound2-dev curl git \
     libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 libxcb-xkb1 libxkbcommon-x11-0
   ```

   On **Fedora** (untested):

   ```sh
   sudo dnf install python3 cmake gcc gcc-c++ alsa-lib-devel curl git \
     xcb-util-cursor xcb-util-wm xcb-util-keysyms libxkbcommon-x11
   ```

   On **Arch Linux**, Manjaro and EndeavourOS (untested):

   ```sh
   sudo pacman -S --needed python cmake base-devel alsa-lib curl git \
     xcb-util-cursor xcb-util-wm xcb-util-keysyms libxkbcommon-x11
   ```

   Arch's own Python works: all the dependencies have packages for the newest Python versions.

2. Build:

   ```sh
   cd desktop
   ./packaging/build-appimage.sh
   ```

3. Run it:

   ```sh
   ./build/Leos_Lyssnare-x86_64.AppImage
   ```

The first build takes a few minutes, because it also downloads the dependencies and compiles the PortAudio audio library. The AppImage contains everything, so you can copy that one file to other Linux computers. If it doesn't start there, the computer may lack FUSE 2 (Ubuntu 24.04: `sudo apt install libfuse2t64`, Arch: `sudo pacman -S fuse2`). Alternatively, run it with `--appimage-extract-and-run`.

### Let GitHub build the apps

The workflow in `.github/workflows/build.yml` builds the Mac app, the Windows installer and `.zip`, and the Linux AppImage on GitHub's computers:

- **Pull requests** (and runs started by hand from the **Actions** tab): all three are built and checked, nothing is published. Open the run under the repository's **Actions** tab. When it's done, the files are under **Artifacts** at the bottom of the page.
- **Every merge to `main`:** all three are built, and once they all pass they're published under the repository's **Releases** as `desktop-v<version>`. The version comes from the code: `desktop/leoslyssnare/__init__.py` (`__version__`) and `Support/Info.plist` (`CFBundleShortVersionString` and `CFBundleVersion`), which must say the same, and the files are named after it. Raise it before merging: a version that's already released isn't published again, and the run says so with a warning.

### Opening the Mac app from GitHub

Apple hasn't notarized the Mac app that GitHub builds (notarizing needs a paid Apple Developer account). So the first time you open it, macOS stops it with a message like *“Leos Lyssnare” can't be opened*, *Apple could not verify…* or *is damaged and can't be opened*. The app works fine. macOS just doesn't know who made it. You only have to do this once for each version you download:

1. Download `Leos_Lyssnare-<version>-macos-arm64.zip` from the run's **Artifacts** or from **Releases**. A download from **Artifacts** is a zip inside a zip, so unzip until you have `Leos Lyssnare.app`.
2. Drag `Leos Lyssnare.app` into **Applications**.
3. Let macOS run it. Either way works:
   - **Without Terminal:** double-click the app. When macOS stops it, click **Done** (or **OK**). Open **System Settings › Privacy & Security**, scroll down to *“Leos Lyssnare” was blocked…* and click **Open Anyway**. Enter your password, then click **Open Anyway** again. On macOS 14 Sonoma you can instead right-click the app, choose **Open**, then click **Open** again.
   - **With Terminal:** run

     ```sh
     xattr -dr com.apple.quarantine "/Applications/Leos Lyssnare.app"
     ```

     and then open the app as usual. Use this if macOS says the app *is damaged*, because in that case **Open Anyway** doesn't appear.

From then on, the app opens normally.

### Checking a build

`--self-test` loads every library the app needs and records and reads back a short test file, without opening a window:

```sh
./desktop/build/Leos_Lyssnare-x86_64.AppImage --self-test     # Linux: prints "Self-test passed."
```

On Windows, the app has no console. Run the check like this instead:

```powershell
$env:LEOSLYSSNARE_SELFTEST_LOG = "$PWD\selftest.log"
Start-Process desktop\build\windows\dist\LeosLyssnare\LeosLyssnare.exe -ArgumentList "--self-test" -Wait
Get-Content selftest.log
```

More about the Windows and Linux version, including how to run it from source while developing, is in [`desktop/README.md`](desktop/README.md).

## First run: download the models once (the only step that needs internet)

1. Pick a model:
   - **Large v3 Turbo** (≈1.6 GB): best quality. Recommended for Swedish and for meetings.
   - **Small** (≈480 MB): faster, somewhat less accurate.
   - **Base** (≈150 MB): fastest, lowest quality.
2. Click **Download models**. The app downloads the speech model, plus the speaker recognition model if *Identify speakers* is on. It then loads each one once, which stores all supporting files locally.
   On an M1, the first load of *Large* can take a few minutes while Core ML optimizes the model for this Mac.
3. When **“Available offline”** appears, the model works without internet. To check, turn off Wi-Fi and transcribe a file.

Models are stored in `~/Library/Application Support/LeosLyssnare/Models`.

## Identifying speakers

- Turn on **Identify speakers**. It is on by default.
- **Number of speakers:** if you know how many people took part, for example 8, choose that number. It noticeably improves the result. *Automatic* also works but can merge or split people.
- Transcription then runs in two steps: first speech to text, then working out who is speaking. The second step takes much less time than the first.
- The **Speakers** panel on the right lists every person found, with their total speaking time and the first thing they said. Type a name in the field and *Speaker 3* is replaced with that name everywhere. The saved `.txt` file is updated too.

Result:

```
[00:00:04] Anna: Welcome everyone, let's get started.

[00:00:09] Erik: Thanks. I'll begin with the status update…
```

Good to know:
- It works best when people take turns, as in your meetings. When people talk over each other, the text is given to whoever dominates.
- With many people around one computer, audio quality matters most. One microphone in the middle of the table works better than a laptop at one end. People who sit far away and speak quietly are the most likely to be mixed up with someone else.
- People with similar voices can be merged into one speaker. If that happens, set the number of speakers explicitly.

## Where files are saved

| What | Location |
|---|---|
| Recordings | `~/Documents/LeosLyssnare/Recordings/Meeting <date time>.m4a` |
| Transcripts | `~/Documents/LeosLyssnare/Transcripts/<file name>.txt` |

## Permissions

The first time you record, macOS asks for microphone access. If you said no, turn it on again under
**System Settings › Privacy & Security › Microphone**.

The first time you record another app's sound, macOS asks whether Leos Lyssnare may record the sound of other apps. If you said no, the app's sound isn't recorded; turn it on under
**System Settings › Privacy & Security › Screen & System Audio Recording**, in the *System Audio Recording Only* list.

## Tips

- Set the language explicitly (for example **Swedish**) rather than *Auto-detect*. It's faster and gives better results.
- The Mac won't go to sleep by itself while it's recording or transcribing. Closing the lid still stops the recording. Quitting or closing the window while recording asks first, and saves the recording.
- One hour of audio takes about 5–10 minutes with *Large v3 Turbo* on an M1, plus a minute or two for identifying speakers. The exact time varies.
- Memory: the whole recording is loaded at 16 kHz, about 230 MB per hour of audio. Recordings several hours long work fine on 16 GB.

## Project structure

```
Package.swift                 Swift package (WhisperKit + SpeakerKit)
Sources/LeosLyssnare/
  LeosLyssnareApp.swift       App entry point
  ContentView.swift           The UI
  AudioRecorder.swift         Recording with pause/resume into one .m4a
  MixingRecorder.swift        Recording a chosen microphone with an app's sound mixed in
  AppAudioTap.swift           Capturing an app's sound (Core Audio process tap)
  AudioSources.swift          Lists the microphones and the apps playing sound
  SourcePickers.swift         The microphone and app pickers
  Transcriber.swift           Model download/loading, transcription and speaker identification
  TranscriptFile.swift        Saving and reading back transcripts, merging speakers, moving text
  TranscriptTextView.swift    The transcript text: highlighting, following playback, click to play
  TranscriptPlayer.swift      Playing the recording along with the transcript
  PlayerBar.swift             Play/pause and the timeline under the transcript
  AppPaths.swift              File locations
Support/Info.plist            App bundle settings, including the permission texts
scripts/build-app.sh          Builds and signs the .app
desktop/                      Windows and Linux version (see desktop/README.md)
```
