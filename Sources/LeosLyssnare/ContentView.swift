import AppKit
import Combine
import SwiftUI
import UniformTypeIdentifiers

struct ContentView: View {
    @StateObject private var recorder = AudioRecorder()
    @StateObject private var sources = SourcesModel()
    @StateObject private var player = TranscriptPlayer()
    @StateObject private var transcriber = Transcriber()
    @StateObject private var updater = UpdateChecker()

    @StateObject private var ui = ViewState()

    /// Drives the waveform and the blinking recording dot.
    private let tick = Timer.publish(every: 0.1, on: .main, in: .common).autoconnect()

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            header
            if updater.available != nil {
                updateBanner
            }
            HStack(alignment: .top, spacing: 20) {
                sidebar
                VStack(spacing: 14) {
                    if transcriber.isBusy || !transcriber.statusText.isEmpty {
                        statusBar
                    }
                    transcriptSection
                }
            }
            AboutFooter()
        }
        .padding(EdgeInsets(top: 16, leading: 24, bottom: 16, trailing: 24))
        .frame(minWidth: 1000, idealWidth: 1180, minHeight: 640, idealHeight: 780)
        .background(Theme.canvas)
        // Files dropped anywhere on the window, not only on the file card.
        .onDrop(of: [.fileURL], isTargeted: nil, perform: handleDrop)
        .background(WindowCloseGuard())
        .onAppear { RecordingGuard.recorder = recorder }
        .task { await updater.checkPeriodically() }
        // A new transcript, or its recording found: get the recording ready to play.
        .onChange(of: transcriber.transcript.map { [$0.sourceURL, $0.savedURL] }) { _, _ in
            openRecording()
            ui.hiddenSpeakers = []
            ui.revealed = nil
        }
        // A speaker who has no text left (merged away, or all of it moved) can't stay hidden.
        .onChange(of: transcriber.revision) { _, _ in
            guard let transcript = transcriber.transcript else { return }
            ui.hiddenSpeakers.formIntersection(transcript.speakers)
        }
        .onReceive(tick) { _ in
            ui.record(level: recorder.level, state: recorder.state)
            sources.tick(idle: recorder.state == .idle)
        }
        .fileImporter(isPresented: $ui.showFileImporter, allowedContentTypes: [.audio]) { result in
            if case .success(let url) = result {
                Task { await transcriber.transcribe(url: url) }
            }
        }
        .alert("Transcribe the recording?", isPresented: $ui.askToTranscribe, presenting: ui.finishedRecording) { url in
            Button("Transcribe") {
                Task { await transcriber.transcribe(url: url) }
            }
            Button("Not now", role: .cancel) {}
        } message: { url in
            Text("The recording was saved as “\(url.lastPathComponent)”. Do you want to transcribe it now?")
        }
        .alert("Something went wrong", isPresented: hasError) {
            Button("OK") {
                recorder.errorMessage = nil
                transcriber.errorMessage = nil
            }
        } message: {
            Text(recorder.errorMessage ?? transcriber.errorMessage ?? "")
        }
    }

    private var hasError: Binding<Bool> {
        Binding(
            get: { recorder.errorMessage != nil || transcriber.errorMessage != nil },
            set: { if !$0 { recorder.errorMessage = nil; transcriber.errorMessage = nil } }
        )
    }

    // MARK: - Header

    private var header: some View {
        HStack(spacing: 12) {
            BrandMark().frame(width: 32, height: 32)
            Text("Leos Lyssnare")
                .font(.system(size: 17, weight: .semibold))
                .foregroundColor(Theme.ink)
            Spacer()

            if transcriber.isReadyOffline {
                Label("Available offline", systemImage: "checkmark.shield")
                    .font(.system(size: 13, weight: .medium))
                    .foregroundColor(Theme.ok)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 6)
                    .background(Capsule().fill(Theme.okSoft))
                    .help("The speech models are on this Mac. Audio and text never leave it.")
            } else {
                Button {
                    Task { await transcriber.downloadModels() }
                } label: {
                    Label("Download models", systemImage: "arrow.down.circle")
                }
                .buttonStyle(FilledButtonStyle(fill: Theme.brand, foreground: Theme.brandInk, height: 34))
                .disabled(transcriber.isBusy)
                .help("One-time download that needs internet. After that, everything runs offline.")
            }

            Button {
                chooseTranscript()
            } label: {
                Label("Open transcript…", systemImage: "doc.text")
            }
            .buttonStyle(GhostButtonStyle())
            .help("Open a saved transcript, with its recording if it can be found")

            Button {
                NSWorkspace.shared.open(AppPaths.recordings)
            } label: {
                Label("Recordings", systemImage: "folder")
            }
            .buttonStyle(GhostButtonStyle())
            .help("Open the recordings folder")
        }
    }

    // MARK: - Sidebar

    private var sidebar: some View {
        ScrollView(.vertical, showsIndicators: false) {
            VStack(spacing: 14) {
                recordCard
                fileCard
                modelSection
            }
        }
        .frame(width: 372)
    }

    // MARK: - Model

    private var modelSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Speech recognition")
                .font(.system(size: 15, weight: .semibold))
                .foregroundColor(Theme.ink)
                .padding(.bottom, 4)

            Text("Model").font(.system(size: 13)).foregroundColor(Theme.muted)
            Picker("Model", selection: $transcriber.selectedModel) {
                ForEach(ModelOption.all) { option in
                    Text("\(option.name) (\(option.size))").tag(option.id)
                }
            }
            .labelsHidden()
            .pickerStyle(.menu)
            .frame(maxWidth: .infinity)

            Text("Language")
                .font(.system(size: 13))
                .foregroundColor(Theme.muted)
                .padding(.top, 6)
            Picker("Language", selection: $transcriber.language) {
                ForEach(LanguageOption.all) { option in
                    Text(option.name).tag(option.id)
                }
            }
            .labelsHidden()
            .pickerStyle(.menu)
            .frame(maxWidth: .infinity)

            HStack(spacing: 10) {
                Toggle("Identify speakers", isOn: $transcriber.identifySpeakers)
                    .toggleStyle(.switch)
                Spacer()
                Picker("Number of speakers", selection: $transcriber.speakerCount) {
                    Text("Automatic").tag(0)
                    ForEach(2...12, id: \.self) { count in
                        Text("\(count) people").tag(count)
                    }
                }
                .labelsHidden()
                .pickerStyle(.menu)
                .frame(width: 128)
                .disabled(!transcriber.identifySpeakers)
                .help("Number of speakers")
            }
            .padding(.top, 10)

            Text("Know how many people took part? Choose the number.")
                .font(.system(size: 12))
                .foregroundColor(Theme.muted)
                .padding(.top, 2)
        }
        .disabled(transcriber.isBusy)
        .padding(EdgeInsets(top: 16, leading: 20, bottom: 18, trailing: 20))
        .frame(maxWidth: .infinity, alignment: .leading)
        .card()
    }

    // MARK: - Recording

    private var recordCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                Text("Record a meeting")
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundColor(Theme.ink)
                Spacer()
                Circle()
                    .fill(recordingIndicatorColor)
                    .frame(width: 10, height: 10)
                    .opacity(recorder.state == .recording && ui.blink ? 0.55 : 1)
                    .animation(.easeInOut(duration: 0.6), value: ui.blink)
                Text(recordingStatusText)
                    .font(.system(size: 13))
                    .foregroundColor(Theme.muted)
            }

            Text(Transcript.timestamp(recorder.elapsed))
                .font(.system(size: 42, weight: .medium))
                .monospacedDigit()
                .foregroundColor(Theme.ink)

            WaveformView(levels: ui.levels, state: recorder.state)
                .frame(height: 64)

            SourcePickers(sources: sources, recorder: recorder)

            HStack(spacing: 10) {
                switch recorder.state {
                case .idle:
                    Button {
                        // Otherwise the microphone would pick up the playback.
                        player.pause()
                        let microphone = sources.microphone.isEmpty ? nil : sources.microphone
                        let application = sources.application.isEmpty ? nil : sources.application
                        Task { await recorder.start(microphone: microphone, application: application) }
                    } label: {
                        Label("Start recording", systemImage: "record.circle")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(FilledButtonStyle(fill: Theme.record))
                case .recording:
                    Button {
                        recorder.pause()
                    } label: {
                        Label("Pause", systemImage: "pause.fill").frame(maxWidth: .infinity)
                    }
                    .buttonStyle(OutlineButtonStyle(height: 46))
                    stopButton
                case .paused:
                    Button {
                        recorder.resume()
                    } label: {
                        Label("Resume", systemImage: "record.circle").frame(maxWidth: .infinity)
                    }
                    .buttonStyle(FilledButtonStyle(fill: Theme.record))
                    stopButton
                }
            }
        }
        .padding(EdgeInsets(top: 16, leading: 20, bottom: 18, trailing: 20))
        .frame(maxWidth: .infinity, alignment: .leading)
        .card()
    }

    private var stopButton: some View {
        Button {
            if let url = recorder.stop() {
                ui.finishedRecording = url
                ui.askToTranscribe = true
            }
        } label: {
            Label("Stop", systemImage: "stop.fill").frame(maxWidth: .infinity)
        }
        .buttonStyle(FilledButtonStyle(fill: Theme.ink, foreground: Theme.canvas))
    }

    private var recordingStatusText: String {
        switch recorder.state {
        case .idle: "Ready"
        case .recording: "Recording"
        case .paused: "Paused – left out of the recording"
        }
    }

    private var recordingIndicatorColor: Color {
        switch recorder.state {
        case .idle: Theme.faint
        case .recording: Theme.record
        case .paused: Theme.warn
        }
    }

    // MARK: - Existing file

    private var fileCard: some View {
        VStack(spacing: 12) {
            HStack(spacing: 12) {
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .fill(Theme.brandSoft)
                    .frame(width: 44, height: 44)
                    .overlay(
                        Image(systemName: "waveform")
                            .font(.system(size: 18, weight: .medium))
                            .foregroundColor(Theme.brand)
                    )
                VStack(alignment: .leading, spacing: 1) {
                    Text("Transcribe an existing file")
                        .font(.system(size: 14, weight: .medium))
                        .foregroundColor(Theme.ink)
                    Text("Drop it here (.m4a, .mp3, .wav…)")
                        .font(.system(size: 12))
                        .foregroundColor(Theme.muted)
                }
                Spacer(minLength: 0)
            }

            Button {
                ui.showFileImporter = true
            } label: {
                Label("Choose file…", systemImage: "folder")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(OutlineButtonStyle())
            .disabled(transcriber.isBusy)
        }
        .padding(EdgeInsets(top: 14, leading: 16, bottom: 16, trailing: 16))
        .background(
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .fill(ui.isDropTargeted ? Theme.brandSoft : Color.clear)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .strokeBorder(
                    ui.isDropTargeted ? Theme.brand : Theme.lineStrong,
                    style: StrokeStyle(lineWidth: 1.5, dash: [4, 4])
                )
        )
        .onDrop(of: [.fileURL], isTargeted: $ui.isDropTargeted, perform: handleDrop)
    }

    /// A .txt is a saved transcript to show; anything else is audio to transcribe.
    private func handleDrop(_ providers: [NSItemProvider]) -> Bool {
        guard let provider = providers.first else { return false }
        _ = provider.loadObject(ofClass: URL.self) { url, _ in
            guard let url else { return }
            Task { @MainActor in
                if url.pathExtension.lowercased() == "txt" {
                    transcriber.openTranscript(url: url)
                } else {
                    await transcriber.transcribe(url: url)
                }
            }
        }
        return true
    }

    private func chooseTranscript() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.plainText]
        panel.directoryURL = AppPaths.transcripts
        panel.message = "Open a transcript"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        transcriber.openTranscript(url: url)
    }

    // MARK: - Status

    private var statusBar: some View {
        VStack(spacing: 10) {
            HStack(spacing: 12) {
                Text(transcriber.statusText)
                    .font(.system(size: 14, weight: .medium))
                    .foregroundColor(Theme.ink)
                    .lineLimit(2)
                Spacer()
                if let progress = transcriber.progress, progress > 0 {
                    Text("\(Int(progress * 100)) %")
                        .font(.system(size: 14, weight: .medium))
                        .monospacedDigit()
                        .foregroundColor(Theme.ink)
                }
                if transcriber.canCancel {
                    Button("Stop") { transcriber.cancel() }
                        .buttonStyle(OutlineButtonStyle())
                }
            }
            if transcriber.isBusy {
                if let progress = transcriber.progress, progress > 0 {
                    ProgressView(value: progress)
                        .tint(Theme.brand)
                } else {
                    ProgressView()
                        .progressViewStyle(.linear)
                        .tint(Theme.brand)
                }
            }
        }
        .padding(EdgeInsets(top: 12, leading: 18, bottom: 14, trailing: 14))
        .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Theme.brandSoft))
    }

    // MARK: - Update

    private var updateBanner: some View {
        HStack(spacing: 10) {
            Group {
                if let message = updater.errorMessage {
                    Text(message).foregroundColor(Theme.warn)
                } else if updater.isInstalling {
                    Text(updater.progress.map { "Downloading the update… \(Int($0 * 100)) %" } ?? "Downloading the update…")
                } else if let release = updater.available {
                    Text("Version \(release.version) is available (you have \(UpdateChecker.currentVersion)).")
                }
            }
            .font(.system(size: 14, weight: .medium))
            .foregroundColor(Theme.ink)
            .lineLimit(2)
            Spacer()
            if updater.isInstalling {
                Button("Stop") { updater.cancelInstall() }
                    .buttonStyle(OutlineButtonStyle())
            } else if let release = updater.available {
                Button("What's new") { NSWorkspace.shared.open(release.page) }
                    .buttonStyle(GhostButtonStyle())
                Button("Later") { updater.skip() }
                    .buttonStyle(GhostButtonStyle())
                Button(updater.canInstallInPlace ? "Update and restart" : "Download") {
                    if updater.canInstallInPlace {
                        if recorder.state != .idle || transcriber.isBusy {
                            updater.errorMessage = "Finish the recording or transcription first, then update."
                        } else {
                            Task { await updater.install() }
                        }
                    } else {
                        NSWorkspace.shared.open(release.page)
                    }
                }
                .buttonStyle(FilledButtonStyle(fill: Theme.brand, foreground: Theme.brandInk, height: 34))
            }
        }
        .padding(EdgeInsets(top: 8, leading: 18, bottom: 8, trailing: 10))
        .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Theme.brandSoft))
    }

    // MARK: - Transcript

    private var transcriptSection: some View {
        Group {
            if let transcript = transcriber.transcript {
                transcriptView(transcript)
            } else {
                emptyTranscript
            }
        }
        .padding(EdgeInsets(top: 20, leading: 24, bottom: 22, trailing: 24))
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .card()
    }

    private var emptyTranscript: some View {
        VStack(spacing: 8) {
            MotifBars()
            Text("Your transcript will appear here")
                .font(.system(size: 18, weight: .semibold))
                .foregroundColor(Theme.ink)
                .padding(.top, 12)
            Text("Record a meeting or drop in an audio file.")
                .font(.system(size: 13))
                .foregroundColor(Theme.muted)
            Text("Everything stays on this computer.")
                .font(.system(size: 13))
                .foregroundColor(Theme.muted)
            Button("Open a saved transcript") {
                chooseTranscript()
            }
            .buttonStyle(.link)
            .padding(.top, 6)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func transcriptView(_ transcript: Transcript) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                Text(transcript.sourceURL.lastPathComponent)
                    .font(.system(size: 18, weight: .semibold))
                    .foregroundColor(Theme.ink)
                    .lineLimit(1)
                    .truncationMode(.middle)
                Spacer(minLength: 8)
                Button {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(transcript.text(withTimestamps: ui.showTimestamps), forType: .string)
                } label: {
                    Label("Copy", systemImage: "doc.on.doc")
                }
                .buttonStyle(OutlineButtonStyle())
                Button {
                    saveAs(transcript)
                } label: {
                    Label("Save as…", systemImage: "square.and.arrow.down")
                }
                .buttonStyle(OutlineButtonStyle())
                if let saved = transcript.savedURL {
                    Button {
                        NSWorkspace.shared.activateFileViewerSelecting([saved])
                    } label: {
                        Label("Show in Finder", systemImage: "folder")
                    }
                    .buttonStyle(GhostButtonStyle())
                }
            }

            HStack(spacing: 6) {
                ForEach(chips(for: transcript), id: \.self) { chip in
                    Text(chip)
                        .font(.system(size: 12))
                        .foregroundColor(Theme.muted)
                        .padding(.horizontal, 10)
                        .padding(.vertical, 3)
                        .background(Capsule().fill(Theme.sunken))
                }
                Spacer()
                Toggle("Timestamps", isOn: $ui.showTimestamps)
                    .toggleStyle(.switch)
                    .controlSize(.small)
                if transcript.hasSpeakers {
                    Button {
                        ui.showSpeakers.toggle()
                    } label: {
                        Label("Speakers (\(transcript.speakers.count))", systemImage: "person.2")
                    }
                    .buttonStyle(GhostButtonStyle(selected: ui.showSpeakers))
                }
            }

            Rectangle().fill(Theme.line).frame(height: 1)

            HStack(alignment: .top, spacing: 20) {
                TranscriptTextView(
                    transcript: transcript,
                    revision: transcriber.revision,
                    showTimestamps: ui.showTimestamps,
                    hiddenSpeakers: ui.hiddenSpeakers,
                    reveal: ui.revealed.map { TranscriptTextView.Reveal(line: $0.line, id: $0.id) },
                    position: player.shownPosition,
                    follow: player.follow,
                    canJump: player.url != nil,
                    onJump: { player.seek($0) },
                    onUserScroll: { player.follow = false },
                    onMove: { first, last, speaker in
                        let movedTo = transcriber.moveText(from: first, to: last, to: speaker)
                        // Ready to type the new speaker's name.
                        if movedTo != nil, speaker == nil { ui.showSpeakers = true }
                    }
                )
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                if transcript.hasSpeakers && ui.showSpeakers {
                    speakersPanel(transcript)
                }
            }

            Rectangle().fill(Theme.line).frame(height: 1)

            PlayerBar(player: player, transcript: transcript, canPlay: recorder.state == .idle,
                      onFindAudio: chooseRecording)
        }
    }

    private func openRecording() {
        if let transcript = transcriber.transcript {
            player.open(transcript.sourceURL, transcriptLength: transcript.segments.last?.end ?? 0)
        } else {
            player.close()
        }
    }

    /// Connects a transcript to its recording by hand, when it wasn't found.
    private func chooseRecording() {
        guard let transcript = transcriber.transcript else { return }
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.audio]
        panel.directoryURL = transcript.savedURL?.deletingLastPathComponent() ?? AppPaths.recordings
        panel.message = "Find “\(transcript.sourceURL.lastPathComponent)”"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        transcriber.setRecording(url)
    }

    private func chips(for transcript: Transcript) -> [String] {
        var result: [String] = []
        if let code = transcript.language {
            result.append(LanguageOption.all.first { $0.id == code }?.name ?? code)
        }
        if let last = transcript.segments.last {
            result.append("\(Transcript.timestamp(last.end)) long")
        }
        if transcript.processingTime > 0 {  // unknown for an opened .txt
            result.append("done in \(Transcript.timestamp(transcript.processingTime))")
        }
        return result
    }

    /// Lists the detected speakers so they can be given real names.
    private func speakersPanel(_ transcript: Transcript) -> some View {
        let total = max(transcript.speakers.reduce(0.0) { $0 + transcript.speakingTime(for: $1) }, 1)
        return VStack(alignment: .leading, spacing: 4) {
            Text("Speakers (\(transcript.speakers.count))")
                .font(.system(size: 15, weight: .semibold))
                .foregroundColor(Theme.ink)
            Text("Type a name to replace “Speaker N” everywhere. Two speakers that are really one person can be merged. Text that someone else said: select it and right-click.")
                .font(.system(size: 12))
                .foregroundColor(Theme.muted)
                .fixedSize(horizontal: false, vertical: true)

            if let undoText = transcriber.undoText {
                HStack(spacing: 8) {
                    Text(undoText)
                        .font(.system(size: 12))
                        .foregroundColor(Theme.muted)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 0)
                    Button("Undo") { transcriber.undo() }
                        .buttonStyle(.link)
                }
                .padding(EdgeInsets(top: 6, leading: 10, bottom: 6, trailing: 8))
                .background(RoundedRectangle(cornerRadius: 8, style: .continuous).fill(Theme.brandSoft))
                .padding(.top, 8)
            }

            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    ForEach(transcript.speakers, id: \.self) { speaker in
                        speakerRow(transcript, speaker: speaker, total: total)
                    }
                }
                .padding(.top, 8)
                .padding(.trailing, 4)
            }
        }
        .padding(EdgeInsets(top: 16, leading: 16, bottom: 14, trailing: 12))
        .frame(width: 268)
        .frame(maxHeight: .infinity, alignment: .top)
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Theme.sunken))
    }

    private func speakerRow(_ transcript: Transcript, speaker: Int, total: Double) -> some View {
        let seconds = transcript.speakingTime(for: speaker)
        let share = seconds / total
        return HStack(alignment: .top, spacing: 10) {
            SpeakerAvatar(speaker: speaker, name: transcript.speakerNames[speaker] ?? "")
                .opacity(ui.hiddenSpeakers.contains(speaker) ? 0.4 : 1)
            VStack(alignment: .leading, spacing: 5) {
                HStack(spacing: 6) {
                    TextField("Speaker \(speaker)", text: speakerNameBinding(speaker))
                        .textFieldStyle(.roundedBorder)
                    visibilityButton(transcript, speaker: speaker)
                    mergeMenu(transcript, speaker: speaker)
                }
                ShareBar(speaker: speaker, share: share)
                Text("\(Transcript.timestamp(seconds)) · \(Int((share * 100).rounded())) %")
                    .font(.system(size: 12))
                    .monospacedDigit()
                    .foregroundColor(Theme.muted)
                    .help("Total speaking time")
                turnNavigator(transcript, speaker: speaker)
                Text("“\(transcript.sample(for: speaker))”")
                    .font(.system(size: 12))
                    .foregroundColor(Theme.muted)
                    .lineLimit(3)
            }
        }
    }

    /// The eye beside a name: fades everything this speaker says in the transcript.
    private func visibilityButton(_ transcript: Transcript, speaker: Int) -> some View {
        let hidden = ui.hiddenSpeakers.contains(speaker)
        let name = transcript.name(for: speaker)
        return Button {
            if hidden { ui.hiddenSpeakers.remove(speaker) } else { ui.hiddenSpeakers.insert(speaker) }
        } label: {
            Image(systemName: hidden ? "eye.slash" : "eye")
                .font(.system(size: 13))
                .frame(width: 22, height: 22)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .foregroundColor(hidden ? Theme.brand : Theme.muted)
        .help(hidden ? "Show what \(name) says" : "Fade what \(name) says in the transcript")
    }

    /// Previous and next time this speaker talks, to jump straight to it.
    private func turnNavigator(_ transcript: Transcript, speaker: Int) -> some View {
        let turns = transcript.segments.indices.filter { transcript.segments[$0].speaker == speaker }
        // The turn being played or last jumped to, if it is this speaker's.
        var current: Int?
        if let time = currentReference {
            current = turns.firstIndex { (index: Int) -> Bool in
                let segment = transcript.segments[index]
                return segment.start - 0.05 <= time && time < segment.end
            }
        }
        let label: String
        if let current {
            label = "\(current + 1) of \(turns.count)"
        } else {
            label = turns.count == 1 ? "1 turn" : "\(turns.count) turns"
        }
        let name = transcript.name(for: speaker)
        return HStack(spacing: 2) {
            turnButton("chevron.left", help: "Previous time \(name) speaks") {
                jump(transcript, turns: turns, forward: false)
            }
            Text(label)
                .font(.system(size: 12))
                .monospacedDigit()
                .foregroundColor(Theme.muted)
                .frame(minWidth: 58)
            turnButton("chevron.right", help: "Next time \(name) speaks") {
                jump(transcript, turns: turns, forward: true)
            }
        }
    }

    private func turnButton(_ symbol: String, help: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.system(size: 11, weight: .semibold))
                .frame(width: 22, height: 22)
                .background(RoundedRectangle(cornerRadius: 6, style: .continuous).fill(Theme.surface))
                .overlay(RoundedRectangle(cornerRadius: 6, style: .continuous).strokeBorder(Theme.lineStrong, lineWidth: 1))
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .foregroundColor(Theme.ink)
        .help(help)
    }

    /// Where the user is in the recording: the playback position, or else
    /// the turn last jumped to when there's no recording to play.
    private var currentReference: Double? {
        player.shownPosition ?? ui.revealed?.start
    }

    /// Goes to the speaker's next (or previous) turn counted from where the
    /// user is, and from the other end after the last (or before the first).
    /// Plays from there when there's a recording; the transcript scrolls to it either way.
    private func jump(_ transcript: Transcript, turns: [Int], forward: Bool) {
        guard !turns.isEmpty else { return }
        let start = { (index: Int) in transcript.segments[index].start }
        let target: Int
        if let here = currentReference {
            if forward {
                target = turns.first { start($0) > here + 0.05 } ?? turns[0]
            } else {
                target = turns.last { start($0) < here - 0.05 } ?? turns[turns.count - 1]
            }
        } else {
            target = forward ? turns[0] : turns[turns.count - 1]
        }
        ui.revealed = Revealed(line: target, start: start(target), id: (ui.revealed?.id ?? 0) + 1)
        if player.url != nil { player.seek(start(target)) }
    }

    /// "Same person as another speaker? Merge them."
    @ViewBuilder
    private func mergeMenu(_ transcript: Transcript, speaker: Int) -> some View {
        let others = transcript.speakers.filter { $0 != speaker }
        if !others.isEmpty {
            Menu {
                Text("\(transcript.name(for: speaker)) is the same person as…")
                Divider()
                ForEach(others, id: \.self) { other in
                    Button(transcript.name(for: other)) {
                        transcriber.mergeSpeakers(speaker, into: other)
                    }
                }
            } label: {
                Image(systemName: "arrow.triangle.merge")
            }
            .menuStyle(.borderlessButton)
            .menuIndicator(.hidden)
            .fixedSize()
            .help("Same person as another speaker? Merge them")
        }
    }

    private func speakerNameBinding(_ speaker: Int) -> Binding<String> {
        Binding(
            get: { transcriber.transcript?.speakerNames[speaker] ?? "" },
            set: { transcriber.renameSpeaker(speaker, to: $0) }
        )
    }

    private func saveAs(_ transcript: Transcript) {
        let panel = NSSavePanel()
        panel.allowedContentTypes = [.plainText]
        panel.nameFieldStringValue = transcript.sourceURL.deletingPathExtension().lastPathComponent + ".txt"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do {
            try transcript.fileContents().write(to: url, atomically: true, encoding: .utf8)
        } catch {
            transcriber.errorMessage = "Couldn't save: \(error.localizedDescription)"
        }
    }
}

/// A turn jumped to from the speakers panel: its line, when it starts, and a
/// number that grows with every jump so the same turn can be asked for again.
private struct Revealed {
    let line: Int
    let start: Double
    let id: Int
}

/// Local UI state. Kept in an ObservableObject instead of `@State` because
/// `@State` is a macro in newer SDKs, and its plugin (SwiftUIMacros) ships
/// only with full Xcode, not with the Command Line Tools.
@MainActor
private final class ViewState: ObservableObject {
    @Published var showFileImporter = false
    @Published var finishedRecording: URL?
    @Published var askToTranscribe = false
    @Published var showTimestamps = true
    @Published var showSpeakers = true
    /// Speakers whose text is faded in the transcript.
    @Published var hiddenSpeakers: Set<Int> = []
    /// The turn last jumped to from the speakers panel.
    @Published var revealed: Revealed?
    @Published var isDropTargeted = false

    /// Recent input levels for the waveform, newest last.
    @Published var levels: [Float] = []
    @Published var blink = false
    private var ticks = 0

    func record(level: Float, state: AudioRecorder.State) {
        switch state {
        case .recording:
            levels.append(level)
            if levels.count > 600 { levels.removeFirst(levels.count - 600) }
            ticks += 1
            if ticks % 7 == 0 { blink.toggle() }
        case .paused:
            break
        case .idle:
            if !levels.isEmpty { levels.removeAll() }
            if blink { blink = false }
        }
    }
}
