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
