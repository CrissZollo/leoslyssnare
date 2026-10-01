# Leos Lyssnare

A native macOS app for Apple Silicon (M1 and newer) that transcribes meetings **offline, on the device**.

> **Windows and Linux:** there is also a version for Windows (installer or portable .zip) and Linux (AppImage) in [`desktop/`](desktop/README.md). It has the same features, uses faster-whisper and sherpa-onnx instead of Apple's Core ML, and is built automatically by GitHub Actions.

- **Transcribe an existing file:** choose or drag in an `.m4a` file (`.mp3`, `.wav` and other common audio formats also work).
- **Record a meeting:** Start → Pause/Resume → Stop. Paused sections are left out of the recording. Everything you record while unpaused is saved to **one** `.m4a` file.
- When you stop recording, the app asks **“Transcribe the recording?”**. Choose *Yes* to start transcribing right away.
- **Who said what:** the app identifies different speakers (for example 8 people in a meeting) and labels each part of the transcript *Speaker 1*, *Speaker 2*, and so on. You can rename them, for example to *Anna*, and the transcript updates everywhere.
- Transcripts are shown with timestamps and saved automatically as `.txt`.

Speech recognition uses [WhisperKit](https://github.com/argmaxinc/WhisperKit), which runs OpenAI's Whisper model on the Mac's Neural Engine through Core ML. Speaker identification uses SpeakerKit from the same package, which runs the pyannote speaker model the same way. No audio or text ever leaves the computer.

## Requirements

- Mac with Apple Silicon (M1 or newer)
- macOS 14 Sonoma or later
- Xcode 15 or later (or the Xcode Command Line Tools: `xcode-select --install`)

## Build

```sh
cd leoslyssnare
./scripts/build-app.sh
open "build/Leos Lyssnare.app"
```

You can drag `build/Leos Lyssnare.app` into **Applications**.

To work on the code, run `open Package.swift` to open the project in Xcode, then press ⌘R.

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
- **Number of speakers:** if you know how many people took part, for example 8, choose that number. It noticeably improves the result. *Detect automatically* also works but can merge or split people.
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

## Tips

- Set the language explicitly (for example **Swedish**) rather than *Auto-detect*. It's faster and gives better results.
- The Mac won't go to sleep by itself while it's recording or transcribing. Closing the lid still stops the recording.
- One hour of audio takes about 5–10 minutes with *Large v3 Turbo* on an M1, plus a minute or two for identifying speakers. The exact time varies.
- Memory: the whole recording is loaded at 16 kHz, about 230 MB per hour of audio. Recordings several hours long work fine on 16 GB.

## Project structure

```
Package.swift                 Swift package (WhisperKit + SpeakerKit)
Sources/LeosLyssnare/
  LeosLyssnareApp.swift       App entry point
  ContentView.swift           The UI
  AudioRecorder.swift         Recording with pause/resume into one .m4a
  Transcriber.swift           Model download/loading, transcription and speaker identification
  AppPaths.swift              File locations
Support/Info.plist            App bundle settings, including microphone permission text
scripts/build-app.sh          Builds and signs the .app
desktop/                      Windows and Linux version (see desktop/README.md)
```
