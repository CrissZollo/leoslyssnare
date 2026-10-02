import Foundation

/// Where the app keeps its files. Everything stays on this Mac.
enum AppPaths {
    /// ~/Library/Application Support/LeosLyssnare/Models
    static var models: URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        return ensure(base.appendingPathComponent("LeosLyssnare/Models", isDirectory: true))
    }

    /// ~/Documents/LeosLyssnare
    static var documents: URL {
        let base = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        return ensure(base.appendingPathComponent("LeosLyssnare", isDirectory: true))
    }

    static var recordings: URL {
        ensure(documents.appendingPathComponent("Recordings", isDirectory: true))
    }

    static var transcripts: URL {
        ensure(documents.appendingPathComponent("Transcripts", isDirectory: true))
    }

    /// Where the exact timings for a .txt transcript are kept, out of sight:
    /// ~/Library/Application Support/LeosLyssnare/Transcript data
    static func transcriptData(for transcript: URL) -> URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        let folder = ensure(base.appendingPathComponent("LeosLyssnare/Transcript data", isDirectory: true))
        return folder.appendingPathComponent(transcript.lastPathComponent + ".json")
    }

    static let audioExtensions = ["m4a", "mp3", "wav", "aac", "flac", "ogg", "opus", "mp4", "mov", "webm", "wma",
                                  "aiff", "aif", "caf"]

    /// The recording a transcript was made from. Looks where it was, next to
    /// the transcript, among the recordings and in Downloads: first for the
    /// same file name, then for the same name with another audio extension.
    static func findAudio(_ source: URL, transcript: URL) -> URL? {
        let manager = FileManager.default
        if manager.fileExists(atPath: source.path) { return source }
        let name = source.lastPathComponent
        let stem = source.deletingPathExtension().lastPathComponent
        let downloads = manager.urls(for: .downloadsDirectory, in: .userDomainMask).first
        var folders: [URL] = []
        for folder in [source.deletingLastPathComponent(), transcript.deletingLastPathComponent(), recordings,
                       downloads, documents].compactMap({ $0 }) {
            var isFolder: ObjCBool = false
            if !folders.contains(folder), manager.fileExists(atPath: folder.path, isDirectory: &isFolder), isFolder.boolValue {
                folders.append(folder)
            }
        }
        let candidates = folders.map { $0.appendingPathComponent(name) }
            + folders.flatMap { folder in audioExtensions.map { folder.appendingPathComponent(stem).appendingPathExtension($0) } }
        return candidates.first { manager.fileExists(atPath: $0.path) }
    }

    static func newRecordingURL() -> URL {
        recordings.appendingPathComponent("Meeting \(timestamp()).m4a")
    }

    static func transcriptURL(for audio: URL) -> URL {
        let name = audio.deletingPathExtension().lastPathComponent
        var url = transcripts.appendingPathComponent("\(name).txt")
        if FileManager.default.fileExists(atPath: url.path) {
            url = transcripts.appendingPathComponent("\(name) \(timestamp()).txt")
        }
        return url
    }

    private static func timestamp() -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd HH.mm.ss"
        return formatter.string(from: Date())
    }

    private static func ensure(_ url: URL) -> URL {
        try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }
}
