import Foundation
import SpeakerKit
import WhisperKit

struct ModelOption: Identifiable, Hashable {
    let id: String
    let name: String
    let size: String

    static let all: [ModelOption] = [
        ModelOption(id: "openai_whisper-large-v3-v20240930_turbo", name: "Large v3 Turbo – best quality", size: "≈1.6 GB"),
        ModelOption(id: "openai_whisper-small", name: "Small – faster", size: "≈480 MB"),
        ModelOption(id: "openai_whisper-base", name: "Base – fastest, lower quality", size: "≈150 MB"),
    ]
}

struct LanguageOption: Identifiable, Hashable {
    let id: String
    let name: String

    static let auto = "auto"
    static let all: [LanguageOption] = [
        LanguageOption(id: auto, name: "Auto-detect"),
        LanguageOption(id: "sv", name: "Swedish"),
        LanguageOption(id: "en", name: "English"),
        LanguageOption(id: "no", name: "Norwegian"),
        LanguageOption(id: "da", name: "Danish"),
        LanguageOption(id: "fi", name: "Finnish"),
        LanguageOption(id: "de", name: "German"),
        LanguageOption(id: "fr", name: "French"),
        LanguageOption(id: "es", name: "Spanish"),
    ]
}

struct TranscriptWord: Equatable {
    var start: Double
    var end: Double
    var text: String
}

/// One line of the transcript. With speaker detection on, each line is a
/// speaker turn: consecutive speech by the same person is merged together.
struct TranscriptSegment: Identifiable {
    let id = UUID()
    var start: Double
    var end: Double
    /// 1-based, numbered in the order people first speak. nil means unknown.
    var speaker: Int?
    var text: String
    /// When each word is said, for following along during playback. Only
    /// known with speaker detection on; the texts appear in order in `text`.
    var words: [TranscriptWord] = []
}

/// Same model and .txt format as `Transcript` in the Windows and Linux app
/// (desktop/leoslyssnare/transcript.py); TranscriptFile.swift reads it back.
struct Transcript {
    var sourceURL: URL
    var segments: [TranscriptSegment]
    let language: String?
    let processingTime: TimeInterval
    let hasSpeakers: Bool
    var speakerNames: [Int: String] = [:]
    var savedURL: URL?

    var speakers: [Int] {
        Array(Set(segments.compactMap(\.speaker))).sorted()
    }

    func name(for speaker: Int?) -> String {
        guard let speaker else { return "Unknown" }
        let custom = speakerNames[speaker]?.trimmingCharacters(in: .whitespaces) ?? ""
        return custom.isEmpty ? "Speaker \(speaker)" : custom
    }

    /// The first thing a speaker says, to help work out who it is.
    func sample(for speaker: Int) -> String {
        guard let segment = segments.first(where: { $0.speaker == speaker }) else { return "" }
        let text = segment.text
        return text.count > 90 ? String(text.prefix(90)) + "…" : text
    }

    /// Total speaking time per speaker, in seconds.
    func speakingTime(for speaker: Int) -> Double {
        segments.filter { $0.speaker == speaker }.reduce(0) { $0 + ($1.end - $1.start) }
    }

    func text(withTimestamps: Bool) -> String {
        if hasSpeakers {
            return segments
                .map { segment in
                    let line = "\(name(for: segment.speaker)): \(segment.text)"
                    return withTimestamps ? "[\(Self.timestamp(segment.start))] \(line)" : line
                }
                .joined(separator: "\n\n")
        }
        if withTimestamps {
            return segments
                .map { "[\(Self.timestamp($0.start))] \($0.text)" }
                .joined(separator: "\n")
        }
        // Without timestamps, begin a new paragraph after pauses over 2 s.
        var output = ""
        var previousEnd: Double?
        for segment in segments {
            if let previousEnd {
                output += segment.start - previousEnd > 2 ? "\n\n" : " "
            }
            output += segment.text
            previousEnd = segment.end
        }
        return output
    }

    func fileContents() -> String {
        let date = DateFormatter.localizedString(from: Date(), dateStyle: .medium, timeStyle: .short)
        var header = "Transcript of \(sourceURL.lastPathComponent)\nCreated \(date)"
        if let language { header += " · language: \(language)" }
        if hasSpeakers {
            header += "\nSpeakers: " + speakers.map { name(for: $0) }.joined(separator: ", ")
        }
        return header + "\n\n" + text(withTimestamps: true) + "\n"
    }

    static func timestamp(_ seconds: Double) -> String {
        let total = Int(seconds)
        return String(format: "%02d:%02d:%02d", total / 3600, total / 60 % 60, total % 60)
    }
}

enum TranscriberError: LocalizedError {
    case modelMissing

    var errorDescription: String? {
        switch self {
        case .modelMissing:
            return "The models haven't been downloaded yet. Click “Download models” once while you're online. After that, everything works fully offline."
        }
    }
}

/// Thread-safe stop flag that WhisperKit's callback can read from any thread.
private final class CancelFlag: @unchecked Sendable {
    private let lock = NSLock()
    private var value = false

    var isSet: Bool {
        lock.lock()
        defer { lock.unlock() }
        return value
    }

    func set() {
        lock.lock()
        value = true
        lock.unlock()
    }
}

/// Runs Whisper (speech to text) and SpeakerKit (who spoke when) on this Mac
/// via Core ML. Models are downloaded once; after that no network is needed.
@MainActor
final class Transcriber: ObservableObject {
    @Published var selectedModel: String {
        didSet {
            UserDefaults.standard.set(selectedModel, forKey: "selectedModel")
            if selectedModel != loadedModel { whisper = nil }
        }
    }

    @Published var language: String {
        didSet { UserDefaults.standard.set(language, forKey: "language") }
    }

    @Published var identifySpeakers: Bool {
        didSet { UserDefaults.standard.set(identifySpeakers, forKey: "identifySpeakers") }
    }

    /// Expected number of speakers; 0 means detect automatically.
    @Published var speakerCount: Int {
        didSet { UserDefaults.standard.set(speakerCount, forKey: "speakerCount") }
    }

    @Published private(set) var downloadedModels: Set<String> = []
    @Published private(set) var speakerModelReady: Bool
    @Published private(set) var isBusy = false
    @Published private(set) var statusText = ""
    /// 0...1 while working; nil when idle. 0 shows an indeterminate spinner.
    @Published private(set) var progress: Double?
    @Published private(set) var transcript: Transcript?
    @Published var errorMessage: String?

    private var whisper: WhisperKit?
    private var loadedModel: String?
    private var speakerKit: SpeakerKit?
    private var cancelFlag: CancelFlag?

    init() {
        let defaults = UserDefaults.standard
        selectedModel = defaults.string(forKey: "selectedModel") ?? ModelOption.all[0].id
        language = defaults.string(forKey: "language") ?? "sv"
        identifySpeakers = defaults.object(forKey: "identifySpeakers") as? Bool ?? true
        speakerCount = defaults.integer(forKey: "speakerCount")
        speakerModelReady = defaults.bool(forKey: "speakerModelReady")
        downloadedModels = Set(ModelOption.all.map(\.id).filter { Self.modelFolder(for: $0) != nil })
    }

    /// True when everything needed for the current settings is on disk.
    var isReadyOffline: Bool {
        downloadedModels.contains(selectedModel) && (!identifySpeakers || speakerModelReady)
    }

    // MARK: - Model management

    /// One-time download of whatever is missing (requires internet). Each model
    /// is also loaded once so all supporting files are cached for offline use.
    func downloadModels() async {
        guard !isBusy else { return }
        isBusy = true
        defer { finishWork() }

        do {
            let model = selectedModel
            if !downloadedModels.contains(model) {
                progress = 0
                statusText = "Downloading speech model…"
                let folder = try await WhisperKit.download(
                    variant: model,
                    downloadBase: AppPaths.models,
                    progressCallback: { [weak self] value in
                        let fraction = value.fractionCompleted
                        Task { @MainActor in self?.progress = fraction }
                    }
                )
                UserDefaults.standard.set(folder.path, forKey: Self.folderKey(model))
                downloadedModels.insert(model)
                whisper = nil
                progress = 0
                _ = try await loadedWhisper()
            }

            if identifySpeakers && !speakerModelReady {
                progress = 0
                statusText = "Downloading speaker recognition model…"
                let config = PyannoteConfig(
                    downloadBase: AppPaths.models.path,
                    download: true,
                    load: true,
                    verbose: false
                )
                speakerKit = try await SpeakerKit(config)
                UserDefaults.standard.set(true, forKey: "speakerModelReady")
                speakerModelReady = true
            }
        } catch {
            errorMessage = "Download failed: \(error.localizedDescription)"
        }
    }

    private func loadedWhisper() async throws -> WhisperKit {
        if let whisper, loadedModel == selectedModel { return whisper }
        guard let folder = Self.modelFolder(for: selectedModel) else { throw TranscriberError.modelMissing }

        statusText = "Loading speech model (the first time can take a few minutes while it's optimized for this Mac)…"
        let config = WhisperKitConfig(
            model: selectedModel,
            downloadBase: AppPaths.models,
            modelFolder: folder.path,
            tokenizerFolder: AppPaths.models,
            verbose: false,
            prewarm: true,
            load: true,
            download: false
        )
        let whisper = try await WhisperKit(config)
        self.whisper = whisper
        loadedModel = selectedModel
        return whisper
    }

    private func loadedSpeakerKit() async throws -> SpeakerKit {
        if let speakerKit { return speakerKit }
        guard speakerModelReady else { throw TranscriberError.modelMissing }

        statusText = "Loading speaker recognition model…"
        // download: false, so this only ever reads the local copy.
        let config = PyannoteConfig(
            downloadBase: AppPaths.models.path,
            download: false,
            load: true,
            verbose: false
        )
        let speakerKit = try await SpeakerKit(config)
        self.speakerKit = speakerKit
        return speakerKit
    }

    private static func folderKey(_ model: String) -> String { "modelFolder.\(model)" }

    private static func modelFolder(for model: String) -> URL? {
        guard let path = UserDefaults.standard.string(forKey: folderKey(model)),
              FileManager.default.fileExists(atPath: path) else { return nil }
        return URL(fileURLWithPath: path)
    }

    // MARK: - Transcription

    func transcribe(url: URL) async {
        guard !isBusy else {
            errorMessage = "A transcription is already running. Wait for it to finish or stop it first."
            return
        }
        isBusy = true
        progress = 0
        errorMessage = nil
        let activity = ProcessInfo.processInfo.beginActivity(
            options: [.userInitiated, .idleSystemSleepDisabled],
            reason: "Transcribing audio"
        )
        defer {
            ProcessInfo.processInfo.endActivity(activity)
            finishWork()
        }

        let accessing = url.startAccessingSecurityScopedResource()
        defer { if accessing { url.stopAccessingSecurityScopedResource() } }

        do {
            let withSpeakers = identifySpeakers
            let whisper = try await loadedWhisper()
            var speakerKit: SpeakerKit?
            if withSpeakers {
                speakerKit = try await loadedSpeakerKit()
            }

            statusText = "Reading “\(url.lastPathComponent)”…"
            let path = url.path
            // 16 kHz mono samples, shared by transcription and speaker detection.
            let audio = try await Task.detached(priority: .userInitiated) {
                try AudioProcessor.loadAudioAsFloatArray(fromPath: path)
            }.value

            var options = DecodingOptions()
            options.task = .transcribe
            if language == LanguageOption.auto {
                options.language = nil
                options.detectLanguage = true
            } else {
                options.language = language
                options.detectLanguage = false
            }
            options.temperature = 0
            options.skipSpecialTokens = true
            options.withoutTimestamps = false
            // Word timings let speakers be matched word by word, so a speaker
            // change in the middle of a sentence is placed correctly.
            options.wordTimestamps = withSpeakers
            // Split long recordings at silences and decode the pieces in parallel.
            options.chunkingStrategy = .vad

            let flag = CancelFlag()
            cancelFlag = flag
            statusText = withSpeakers ? "Step 1 of 2: Transcribing speech…" : "Transcribing “\(url.lastPathComponent)”…"
            let poller = Task { @MainActor [weak self] in
                while !Task.isCancelled {
                    self?.progress = whisper.progress.fractionCompleted
                    try? await Task.sleep(nanoseconds: 500_000_000)
                }
            }

            let started = Date()
            let results: [TranscriptionResult]
            do {
                results = try await whisper.transcribe(
                    audioArray: audio,
                    decodeOptions: options,
                    callback: { _ in flag.isSet ? false : nil }
                )
                poller.cancel()
            } catch {
                poller.cancel()
                throw error
            }
            guard !flag.isSet else {
                statusText = "Transcription stopped."
                return
            }

            let segments: [TranscriptSegment]
            if let speakerKit {
                statusText = "Step 2 of 2: Working out who is speaking…"
                progress = 0
                let diarizationOptions = PyannoteDiarizationOptions(
                    numberOfSpeakers: speakerCount > 0 ? speakerCount : nil
                )
                let diarization = try await speakerKit.diarize(
                    audioArray: audio,
                    options: diarizationOptions,
                    progressCallback: { [weak self] value in
                        let fraction = value.fractionCompleted
                        Task { @MainActor in self?.progress = fraction }
                    }
                )
                guard !flag.isSet else {
                    statusText = "Transcription stopped."
                    return
                }
                segments = Self.withWords(Self.speakerTurns(diarization: diarization, transcription: results),
                                          from: results)
            } else {
                segments = Self.plainSegments(results)
            }

            beforeEdit = nil
            var transcript = Transcript(
                sourceURL: url,
                segments: segments,
                language: results.first?.language,
                processingTime: Date().timeIntervalSince(started),
                hasSpeakers: speakerKit != nil
            )

            // Save automatically to ~/Documents/LeosLyssnare/Transcripts.
            let saveURL = AppPaths.transcriptURL(for: url)
            do {
                try transcript.save(to: saveURL)
                transcript.savedURL = saveURL
            } catch {
                errorMessage = "Transcription finished but couldn't be saved: \(error.localizedDescription)"
            }
            self.transcript = transcript
        } catch {
            errorMessage = "Transcription failed: \(error.localizedDescription)"
        }
    }

    /// True while a transcription is running and can be stopped.
    var canCancel: Bool { cancelFlag != nil }

    func cancel() {
        cancelFlag?.set()
        statusText = "Stopping…"
    }

    /// Gives a speaker a real name and updates the saved transcript file.
    func renameSpeaker(_ speaker: Int, to name: String) {
        guard var transcript else { return }
        transcript.speakerNames[speaker] = name
        self.transcript = transcript
        saveQuietly()
    }

    // MARK: - Opening and editing

    /// Shows a saved .txt transcript, with its exact timings when they were
    /// kept, and its recording when it can be found.
    func openTranscript(url: URL) {
        let accessing = url.startAccessingSecurityScopedResource()
        defer { if accessing { url.stopAccessingSecurityScopedResource() } }
        do {
            var transcript = try Transcript.load(url: url)
            // The recording usually has the same name as in the transcript's first line.
            if let audio = AppPaths.findAudio(transcript.sourceURL, transcript: url) {
                transcript.sourceURL = audio
            }
            beforeEdit = nil
            self.transcript = transcript
        } catch {
            errorMessage = (error as? TranscriptFileError)?.errorDescription
                ?? "The transcript couldn't be opened: \(error.localizedDescription)"
        }
    }

    /// What Undo would bring back, and what it says it undoes.
    @Published private(set) var undoText: String?
    private var beforeEdit: (segments: [TranscriptSegment], names: [Int: String])? {
        didSet { if beforeEdit == nil { undoText = nil } }
    }

    /// `source` is the same person as `target`: one speaker from now on.
    func mergeSpeakers(_ source: Int, into target: Int) {
        guard var transcript else { return }
        beforeEdit = (transcript.segments, transcript.speakerNames)
        undoText = "Merged \(transcript.name(for: source)) into \(transcript.name(for: target))."
        transcript.mergeSpeakers(source, into: target)
        self.transcript = transcript
        saveQuietly()
    }

    func undo() {
        guard var transcript, let beforeEdit else { return }
        transcript.segments = beforeEdit.segments
        transcript.speakerNames = beforeEdit.names
        self.beforeEdit = nil
        self.transcript = transcript
        saveQuietly()
    }

    /// Keeps the saved file in step with renames and merges.
    private func saveQuietly() {
        if let transcript, let savedURL = transcript.savedURL {
            try? transcript.save(to: savedURL)
        }
    }

    private func finishWork() {
        isBusy = false
        progress = nil
        cancelFlag = nil
        if statusText != "Transcription stopped." { statusText = "" }
    }

    // MARK: - Building the transcript

    private static func plainSegments(_ results: [TranscriptionResult]) -> [TranscriptSegment] {
        results
            .flatMap(\.segments)
            .sorted { $0.start < $1.start }
            .compactMap { segment in
                let text = segment.text.trimmingCharacters(in: .whitespacesAndNewlines)
                guard !text.isEmpty else { return nil }
                return TranscriptSegment(start: Double(segment.start), end: Double(segment.end), speaker: nil, text: text)
            }
    }

    /// Gives each speaker turn the timed words said during it, so playback can
    /// follow word by word and text can be split between speakers by time.
    /// A word belongs to the turn its middle falls in, or else the nearest one.
    private static func withWords(_ turns: [TranscriptSegment], from results: [TranscriptionResult]) -> [TranscriptSegment] {
        let words = results
            .flatMap(\.segments)
            .flatMap { $0.words ?? [] }
            .map { TranscriptWord(start: Double($0.start), end: Double($0.end),
                                  text: $0.word.trimmingCharacters(in: .whitespacesAndNewlines)) }
            .filter { !$0.text.isEmpty }
            .sorted { $0.start < $1.start }
        guard !turns.isEmpty else { return turns }
        var result = turns
        for word in words {
            let middle = (word.start + word.end) / 2
            let index = result.firstIndex { middle >= $0.start && middle <= $0.end }
                ?? result.indices.min { distance(middle, result[$0]) < distance(middle, result[$1]) }!
            result[index].words.append(word)
        }
        return result
    }

    private static func distance(_ time: Double, _ segment: TranscriptSegment) -> Double {
        time < segment.start ? segment.start - time : max(0, time - segment.end)
    }

    /// Matches words to speakers, renumbers speakers in the order they first
    /// talk, and merges consecutive speech by the same person into one turn.
    private static func speakerTurns(
        diarization: DiarizationResult,
        transcription: [TranscriptionResult]
    ) -> [TranscriptSegment] {
        let pieces = diarization
            .addSpeakerInfo(to: transcription)
            .flatMap { $0 }
            .sorted { $0.startTime < $1.startTime }

        var numbering: [Int: Int] = [:]
        var turns: [TranscriptSegment] = []
        for piece in pieces {
            let text = piece.text.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !text.isEmpty else { continue }

            var speaker: Int?
            if let rawId = piece.speaker.speakerId ?? piece.speaker.speakerIds.first {
                if numbering[rawId] == nil { numbering[rawId] = numbering.count + 1 }
                speaker = numbering[rawId]
            }

            if var last = turns.last, last.speaker == speaker {
                last.text += " " + text
                last.end = Double(piece.endTime)
                turns[turns.count - 1] = last
            } else {
                turns.append(TranscriptSegment(
                    start: Double(piece.startTime),
                    end: Double(piece.endTime),
                    speaker: speaker,
                    text: text
                ))
            }
        }
        return turns
    }
}
