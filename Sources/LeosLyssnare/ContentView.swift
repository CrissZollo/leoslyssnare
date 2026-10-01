import AppKit
import SwiftUI
import UniformTypeIdentifiers

struct ContentView: View {
    @StateObject private var recorder = AudioRecorder()
    @StateObject private var transcriber = Transcriber()

    @StateObject private var ui = ViewState()

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            modelSection
            HStack(alignment: .top, spacing: 16) {
                recordCard
                fileCard
            }
            .fixedSize(horizontal: false, vertical: true)
            if transcriber.isBusy || !transcriber.statusText.isEmpty {
                statusBar
            }
            transcriptSection
        }
        .padding(20)
        .frame(minWidth: 940, minHeight: 700)
        .fileImporter(isPresented: $ui.showFileImporter, allowedContentTypes: [.audio]) { result in
            if case .success(let url) = result {
                Task { await transcriber.transcribe(url: url) }
            }
        }
        .alert("Transcribe the recording?", isPresented: $ui.askToTranscribe, presenting: ui.finishedRecording) { url in
            Button("Yes, transcribe") {
                Task { await transcriber.transcribe(url: url) }
            }
            Button("No", role: .cancel) {}
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

    // MARK: - Model

    private var modelSection: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 12) {
                    Picker("Model", selection: $transcriber.selectedModel) {
                        ForEach(ModelOption.all) { option in
                            Text("\(option.name) (\(option.size))").tag(option.id)
                        }
                    }
                    .frame(maxWidth: 380)

                    Picker("Language", selection: $transcriber.language) {
                        ForEach(LanguageOption.all) { option in
                            Text(option.name).tag(option.id)
                        }
                    }
                    .frame(maxWidth: 220)

                    Spacer()

                    if transcriber.isReadyOffline {
                        Label("Available offline", systemImage: "checkmark.seal.fill")
                            .foregroundStyle(.green)
                    } else {
                        Button {
                            Task { await transcriber.downloadModels() }
                        } label: {
                            Label("Download models", systemImage: "arrow.down.circle")
                        }
                        .help("One-time download that needs internet. After that, everything runs offline.")
                    }
                }

                HStack(spacing: 12) {
                    Toggle("Identify speakers", isOn: $transcriber.identifySpeakers)
                        .toggleStyle(.checkbox)

                    Picker("Number of speakers", selection: $transcriber.speakerCount) {
                        Text("Detect automatically").tag(0)
                        Divider()
                        ForEach(2...12, id: \.self) { count in
                            Text("\(count)").tag(count)
                        }
                    }
                    .frame(maxWidth: 280)
                    .disabled(!transcriber.identifySpeakers)

                    Text("If you know how many people took part, choose the number. It gives better results.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .disabled(transcriber.isBusy)
            .padding(6)
        } label: {
            Label("Speech recognition – runs on this Mac", systemImage: "cpu")
        }
    }

    // MARK: - Recording

    private var recordCard: some View {
        GroupBox {
            VStack(spacing: 14) {
                HStack(spacing: 8) {
                    Circle()
                        .fill(recordingIndicatorColor)
                        .frame(width: 10, height: 10)
                        .opacity(recorder.state == .recording ? 1 : 0.6)
                    Text(recordingStatusText)
                        .foregroundStyle(.secondary)
                    Spacer()
                    Text(Transcript.timestamp(recorder.elapsed))
                        .font(.system(.title2, design: .monospaced))
                }

                LevelMeter(level: recorder.level)

                HStack {
                    switch recorder.state {
                    case .idle:
                        Button {
                            Task { await recorder.start() }
                        } label: {
                            Label("Start recording", systemImage: "record.circle")
                                .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.borderedProminent)
                        .tint(.red)
                    case .recording:
                        Button {
                            recorder.pause()
                        } label: {
                            Label("Pause", systemImage: "pause.fill").frame(maxWidth: .infinity)
                        }
                        stopButton
                    case .paused:
                        Button {
                            recorder.resume()
                        } label: {
                            Label("Resume", systemImage: "record.circle").frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.borderedProminent)
                        .tint(.red)
                        stopButton
                    }
                }
                .controlSize(.large)

                Text("Paused parts are left out of the recording.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .padding(6)
        } label: {
            Label("Record a meeting", systemImage: "mic")
        }
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
    }

    private var recordingStatusText: String {
        switch recorder.state {
        case .idle: "Ready"
        case .recording: "Recording…"
        case .paused: "Paused"
        }
    }

    private var recordingIndicatorColor: Color {
        switch recorder.state {
        case .idle: .gray
        case .recording: .red
        case .paused: .orange
        }
    }

    // MARK: - Existing file

    private var fileCard: some View {
        GroupBox {
            VStack(spacing: 12) {
                RoundedRectangle(cornerRadius: 10)
                    .strokeBorder(style: StrokeStyle(lineWidth: 2, dash: [6]))
                    .foregroundStyle(ui.isDropTargeted ? Color.accentColor : Color.secondary.opacity(0.5))
                    .overlay {
                        VStack(spacing: 6) {
                            Image(systemName: "waveform")
                                .font(.largeTitle)
                                .foregroundStyle(.secondary)
                            Text("Drop an audio file here (.m4a, .mp3, .wav…)")
                                .foregroundStyle(.secondary)
                        }
                    }
                    .frame(minHeight: 90)
                    .onDrop(of: [.fileURL], isTargeted: $ui.isDropTargeted, perform: handleDrop)

                Button {
                    ui.showFileImporter = true
                } label: {
                    Label("Choose file…", systemImage: "folder")
                        .frame(maxWidth: .infinity)
                }
                .controlSize(.large)
                .disabled(transcriber.isBusy)
            }
            .padding(6)
        } label: {
            Label("Transcribe an existing file", systemImage: "doc.text.magnifyingglass")
        }
    }

    private func handleDrop(_ providers: [NSItemProvider]) -> Bool {
        guard let provider = providers.first else { return false }
        _ = provider.loadObject(ofClass: URL.self) { url, _ in
            guard let url else { return }
            Task { @MainActor in
                await transcriber.transcribe(url: url)
            }
        }
        return true
    }

    // MARK: - Status

    private var statusBar: some View {
        HStack(spacing: 12) {
            if let progress = transcriber.progress, progress > 0 {
                ProgressView(value: progress)
                    .frame(maxWidth: 240)
                Text("\(Int(progress * 100)) %")
                    .monospacedDigit()
                    .foregroundStyle(.secondary)
            } else if transcriber.isBusy {
                ProgressView().controlSize(.small)
            }
            Text(transcriber.statusText)
                .foregroundStyle(.secondary)
                .lineLimit(2)
            Spacer()
            if transcriber.canCancel {
                Button("Stop") { transcriber.cancel() }
            }
        }
    }

    // MARK: - Transcript

    private var transcriptSection: some View {
        GroupBox {
            if let transcript = transcriber.transcript {
                VStack(alignment: .leading, spacing: 10) {
                    HStack {
                        Text(transcript.sourceURL.lastPathComponent).bold()
                        if let language = transcript.language {
                            Text("· \(language)").foregroundStyle(.secondary)
                        }
                        Text("· done in \(Transcript.timestamp(transcript.processingTime))")
                            .foregroundStyle(.secondary)
                        Spacer()
                        Toggle("Timestamps", isOn: $ui.showTimestamps)
                            .toggleStyle(.switch)
                            .controlSize(.small)
                    }

                    HStack(alignment: .top, spacing: 12) {
                        ScrollView {
                            Text(transcript.text(withTimestamps: ui.showTimestamps))
                                .textSelection(.enabled)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(8)
                        }
                        .background(Color(nsColor: .textBackgroundColor))
                        .clipShape(RoundedRectangle(cornerRadius: 6))

                        if transcript.hasSpeakers {
                            speakersPanel(transcript)
                        }
                    }

                    HStack {
                        Button {
                            NSPasteboard.general.clearContents()
                            NSPasteboard.general.setString(transcript.text(withTimestamps: ui.showTimestamps), forType: .string)
                        } label: {
                            Label("Copy", systemImage: "doc.on.doc")
                        }
                        Button {
                            saveAs(transcript)
                        } label: {
                            Label("Save as…", systemImage: "square.and.arrow.down")
                        }
                        if let saved = transcript.savedURL {
                            Button {
                                NSWorkspace.shared.activateFileViewerSelecting([saved])
                            } label: {
                                Label("Show in Finder", systemImage: "magnifyingglass")
                            }
                        }
                        Spacer()
                        Button("Open recordings folder") {
                            NSWorkspace.shared.open(AppPaths.recordings)
                        }
                    }
                }
                .padding(6)
            } else {
                VStack(spacing: 8) {
                    Image(systemName: "text.quote")
                        .font(.largeTitle)
                        .foregroundStyle(.tertiary)
                    Text("The transcript will appear here.")
                        .foregroundStyle(.secondary)
                    Button("Open recordings folder") {
                        NSWorkspace.shared.open(AppPaths.recordings)
                    }
                    .buttonStyle(.link)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        } label: {
            Label("Transcript", systemImage: "text.alignleft")
        }
        .frame(maxHeight: .infinity)
    }

    /// Lists the detected speakers so they can be given real names.
    private func speakersPanel(_ transcript: Transcript) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Speakers (\(transcript.speakers.count))")
                .font(.headline)
            Text("Type a name to replace “Speaker N” everywhere.")
                .font(.caption)
                .foregroundStyle(.secondary)

            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    ForEach(transcript.speakers, id: \.self) { speaker in
                        VStack(alignment: .leading, spacing: 3) {
                            HStack {
                                TextField("Speaker \(speaker)", text: speakerNameBinding(speaker))
                                    .textFieldStyle(.roundedBorder)
                                Text(Transcript.timestamp(transcript.speakingTime(for: speaker)))
                                    .font(.caption.monospacedDigit())
                                    .foregroundStyle(.secondary)
                                    .help("Total speaking time")
                            }
                            Text("“\(transcript.sample(for: speaker))”")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .lineLimit(2)
                        }
                    }
                }
                .padding(.trailing, 4)
            }
        }
        .frame(width: 260)
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

private struct LevelMeter: View {
    let level: Float

    var body: some View {
        GeometryReader { geometry in
            ZStack(alignment: .leading) {
                Capsule().fill(Color.secondary.opacity(0.2))
                Capsule()
                    .fill(level > 0.85 ? Color.red : Color.green)
                    .frame(width: geometry.size.width * CGFloat(level))
                    .animation(.linear(duration: 0.1), value: level)
            }
        }
        .frame(height: 6)
    }
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
    @Published var isDropTargeted = false
}
