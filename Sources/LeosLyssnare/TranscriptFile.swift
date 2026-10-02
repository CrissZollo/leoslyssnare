import Foundation

enum TranscriptFileError: LocalizedError {
    case notATranscript

    var errorDescription: String? {
        "This file isn't a transcript from Leos Lyssnare."
    }
}

/// Saving, reading back and editing transcripts, as the Windows and Linux app
/// does (desktop/leoslyssnare/transcript.py), so files move freely between them.
extension Transcript {
    // MARK: - Editing

    /// `source` turned out to be the same person as `target`. Their lines
    /// become `target`'s, and lines that now follow each other are joined.
    mutating func mergeSpeakers(_ source: Int, into target: Int) {
        guard source != target else { return }
        var merged: [TranscriptSegment] = []
        for var segment in segments {
            if segment.speaker == source { segment.speaker = target }
            if var previous = merged.last, previous.speaker == target, segment.speaker == target {
                previous.text += " " + segment.text
                previous.end = max(previous.end, segment.end)
                previous.words += segment.words
                merged[merged.count - 1] = previous
            } else {
                merged.append(segment)
            }
        }
        segments = merged
        let name = (speakerNames.removeValue(forKey: source) ?? "").trimmingCharacters(in: .whitespaces)
        if !name.isEmpty, (speakerNames[target] ?? "").trimmingCharacters(in: .whitespaces).isEmpty {
            speakerNames[target] = name
        }
    }

    // MARK: - Saving

    /// Writes the .txt file, and the exact timings (which the .txt rounds to
    /// whole seconds) for following along later, out of sight.
    func save(to url: URL) throws {
        try fileContents().write(to: url, atomically: true, encoding: .utf8)
        let data = try JSONSerialization.data(withJSONObject: dataObject())
        try data.write(to: AppPaths.transcriptData(for: url), options: .atomic)
    }

    func dataObject() -> [String: Any] {
        [
            "version": 1,
            "source_path": sourceURL.path,
            "language": language.map { $0 as Any } ?? NSNull(),
            "processing_time": processingTime,
            "has_speakers": hasSpeakers,
            "speaker_names": Dictionary(uniqueKeysWithValues: speakerNames.map { (String($0.key), $0.value) }),
            "segments": segments.map { segment -> [String: Any] in
                [
                    "start": segment.start,
                    "end": segment.end,
                    "speaker": segment.speaker.map { $0 as Any } ?? NSNull(),
                    "text": segment.text,
                    "words": segment.words.map { [$0.start, $0.end, $0.text] as [Any] },
                ]
            },
        ]
    }

    init?(dataObject data: [String: Any]) {
        guard let path = data["source_path"] as? String,
              let rawSegments = data["segments"] as? [[String: Any]]
        else { return nil }
        var segments: [TranscriptSegment] = []
        for raw in rawSegments {
            guard let start = (raw["start"] as? NSNumber)?.doubleValue,
                  let end = (raw["end"] as? NSNumber)?.doubleValue,
                  let text = raw["text"] as? String
            else { return nil }
            let words = (raw["words"] as? [[Any]] ?? []).compactMap { word -> TranscriptWord? in
                guard word.count == 3,
                      let start = (word[0] as? NSNumber)?.doubleValue,
                      let end = (word[1] as? NSNumber)?.doubleValue,
                      let text = word[2] as? String
                else { return nil }
                return TranscriptWord(start: start, end: end, text: text)
            }
            segments.append(TranscriptSegment(start: start, end: end, speaker: (raw["speaker"] as? NSNumber)?.intValue,
                                              text: text, words: words))
        }
        var names: [Int: String] = [:]
        for (key, value) in data["speaker_names"] as? [String: String] ?? [:] {
            if let number = Int(key) { names[number] = value }
        }
        self.init(sourceURL: URL(fileURLWithPath: path),
                  segments: segments,
                  language: data["language"] as? String,
                  processingTime: (data["processing_time"] as? NSNumber)?.doubleValue ?? 0,
                  hasSpeakers: data["has_speakers"] as? Bool ?? false,
                  speakerNames: names)
    }

    // MARK: - Reading

    /// Opens a saved .txt transcript. If the exact timings saved alongside it
    /// still match the text, those are used instead of the rounded ones.
    static func load(url: URL) throws -> Transcript {
        var contents = try String(contentsOf: url, encoding: .utf8)
        if contents.hasPrefix("\u{FEFF}") { contents.removeFirst() }
        let parsed = try parse(contents, url: url)
        if let data = try? Data(contentsOf: AppPaths.transcriptData(for: url)),
           let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           var exact = Transcript(dataObject: object),
           // Edited by hand since? Then the .txt is what counts.
           exact.text(withTimestamps: true) == parsed.text(withTimestamps: true) {
            exact.savedURL = url
            return exact
        }
        return parsed
    }

    /// Reads a transcript back from its .txt file. Times are whole seconds,
    /// and each line is taken to last until the next one starts.
    static func parse(_ contents: String, url: URL) throws -> Transcript {
        let lines = contents.replacingOccurrences(of: "\r\n", with: "\n").components(separatedBy: "\n")
        guard let first = lines.first, first.hasPrefix("Transcript of ") else {
            throw TranscriptFileError.notATranscript
        }
        let sourceName = String(first.dropFirst("Transcript of ".count)).trimmingCharacters(in: .whitespaces)
        var language: String?
        var names: [String]?
        var index = 1
        while index < lines.count, !trimmed(lines[index]).isEmpty {
            let line = lines[index]
            if let marker = line.range(of: "· language: ") {
                language = line[marker.upperBound...].split(whereSeparator: \.isWhitespace).first.map(String.init)
            }
            if line.hasPrefix("Speakers:") {
                names = line.dropFirst("Speakers:".count).split(separator: ",").map { trimmed(String($0)) }
                    .filter { !$0.isEmpty }
            }
            index += 1
        }

        var entries: [(start: Double, text: String)] = []
        for line in lines.dropFirst(index) {
            if let entry = timedLine(line) {
                entries.append(entry)
            } else if !trimmed(line).isEmpty, !entries.isEmpty {  // a line broken by hand
                entries[entries.count - 1].text += " " + trimmed(line)
            }
        }

        let hasSpeakers = names != nil
        var numbers: [String: Int] = [:]
        var custom: [Int: String] = [:]
        let reserved = Set((names ?? []).compactMap(speakerNumber))
        // Keeps "Speaker N" as number N; real names get the free numbers.
        func number(for name: String) -> Int? {
            if name == "Unknown" { return nil }
            if let known = numbers[name] { return known }
            let value: Int
            if let numbered = speakerNumber(name) {
                value = numbered
            } else {
                let taken = Set(numbers.values).union(reserved)
                value = (1...).first { !taken.contains($0) }!
                custom[value] = name
            }
            numbers[name] = value
            return value
        }
        for name in names ?? [] { _ = number(for: name) }

        var segments: [TranscriptSegment] = []
        for (start, rest) in entries {
            var speaker: Int?
            var text = rest
            if hasSpeakers {
                let candidates = (Array(numbers.keys) + ["Unknown"]).sorted { $0.count > $1.count }
                var known = candidates.first { rest.hasPrefix($0 + ":") }
                if known == nil, let colon = rest.range(of: ": ") {
                    known = String(rest[..<colon.lowerBound])
                }
                if let known {
                    speaker = number(for: known)
                    text = String(rest.dropFirst(known.count + 1))
                }
            }
            text = text.split(whereSeparator: \.isWhitespace).joined(separator: " ")
            if !text.isEmpty {
                segments.append(TranscriptSegment(start: start, end: start, speaker: speaker, text: text))
            }
        }
        for i in segments.indices {
            // Speaking pace of about 150 words a minute for the last line.
            segments[i].end = i + 1 < segments.count
                ? segments[i + 1].start
                : segments[i].start + max(2, Double(segments[i].text.split(separator: " ").count) / 2.5)
        }

        var transcript = Transcript(
            sourceURL: url.deletingLastPathComponent().appendingPathComponent(sourceName),
            segments: segments,
            language: language,
            processingTime: 0,
            hasSpeakers: hasSpeakers,
            speakerNames: custom
        )
        transcript.savedURL = url
        return transcript
    }

    private static func trimmed(_ text: String) -> String {
        text.trimmingCharacters(in: .whitespaces)
    }

    private static let timedPattern = try! NSRegularExpression(pattern: #"^\[(\d+):(\d{2}):(\d{2})\] ?(.*)$"#)
    private static let numberedPattern = try! NSRegularExpression(pattern: #"^Speaker (\d+)$"#)

    /// "[00:01:05] text" as (65, "text").
    private static func timedLine(_ line: String) -> (start: Double, text: String)? {
        let ns = line as NSString
        guard let match = timedPattern.firstMatch(in: line, range: NSRange(location: 0, length: ns.length)) else {
            return nil
        }
        let part = { (i: Int) in ns.substring(with: match.range(at: i)) }
        guard let hours = Int(part(1)), let minutes = Int(part(2)), let seconds = Int(part(3)) else { return nil }
        return (Double(hours * 3600 + minutes * 60 + seconds), part(4))
    }

    /// 3 for "Speaker 3".
    private static func speakerNumber(_ name: String) -> Int? {
        let ns = name as NSString
        guard let match = numberedPattern.firstMatch(in: name, range: NSRange(location: 0, length: ns.length)) else {
            return nil
        }
        return Int(ns.substring(with: match.range(at: 1)))
    }
}
