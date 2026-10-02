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

    /// Gives a stretch of text to another speaker: `speaker`, or with nil a new
    /// one. `first` and `last` are (line, offset) in the lines' text, in
    /// UTF-16 units as NSString counts them, `last` just after the end; a cut
    /// word counts as wholly inside.
    ///
    /// The text becomes a line of its own, joined with the line before or
    /// after if that's already the speaker's. What came before it stays where
    /// it was, and what came after it gets a line of its own with the speaker
    /// it had. Times are split with the words when they're known, otherwise
    /// in proportion to the text. Returns the speaker the text went to, or nil
    /// when nothing was selected.
    mutating func moveText(from first: (line: Int, offset: Int), to last: (line: Int, offset: Int),
                           to speaker: Int?) -> Int? {
        let (a, b) = (first.line, last.line)
        guard a <= b, segments.indices.contains(a), segments.indices.contains(b) else { return nil }
        let firstOffset = TextPiece.wordStart(segments[a].text, first.offset)
        let lastOffset = TextPiece.wordEnd(segments[b].text, last.offset)

        let before = TextPiece.piece(segments[a], 0, firstOffset)
        let after = TextPiece.piece(segments[b], lastOffset, TextPiece.length(segments[b].text))
        let chosen = (a...b).compactMap { i in
            TextPiece.piece(segments[i], i == a ? firstOffset : 0, i == b ? lastOffset : TextPiece.length(segments[i].text))
        }
        guard !chosen.isEmpty else { return nil }
        let target = speaker ?? (speakers.max() ?? 0) + 1
        let moved = TextPiece.join(chosen, speaker: target)

        // Joined with the neighbours when they're the same speaker, like speaker turns.
        var lines: [(segment: TranscriptSegment, isMoved: Bool)] = []
        if let before { lines.append((before, false)) }
        lines.append((moved, true))
        if let after { lines.append((after, false)) }
        if b + 1 < segments.count { lines.append((segments[b + 1], false)) }
        var result = Array(segments[..<a])
        var lastIsMoved = false
        for (line, isMoved) in lines {
            if let previous = result.last, previous.speaker == line.speaker, isMoved || lastIsMoved {
                result[result.count - 1] = TextPiece.join([previous, line], speaker: line.speaker)
                lastIsMoved = true
            } else {
                result.append(line)
                lastIsMoved = isMoved
            }
        }
        segments = result + segments.dropFirst(b + 2)
        return target
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

/// Cutting a line's text by UTF-16 offsets, as NSTextView counts them.
enum TextPiece {
    static func length(_ text: String) -> Int { (text as NSString).length }

    private static func isSpace(_ text: NSString, _ index: Int) -> Bool {
        guard let scalar = Unicode.Scalar(text.character(at: index)) else { return false }
        return CharacterSet.whitespacesAndNewlines.contains(scalar)
    }

    /// Where a selection starting at `index` really starts: at the beginning
    /// of the word it cuts, or of the next word when it starts between words.
    static func wordStart(_ text: String, _ index: Int) -> Int {
        let ns = text as NSString
        var i = max(0, min(index, ns.length))
        if i < ns.length, isSpace(ns, i) {
            while i < ns.length, isSpace(ns, i) { i += 1 }
            return i
        }
        while i > 0, !isSpace(ns, i - 1) { i -= 1 }
        return i
    }

    /// Where a selection ending at `index` really ends: after the word it
    /// cuts, or after the previous word when it ends between words.
    static func wordEnd(_ text: String, _ index: Int) -> Int {
        let ns = text as NSString
        var i = max(0, min(index, ns.length))
        if i > 0, isSpace(ns, i - 1) {
            while i > 0, isSpace(ns, i - 1) { i -= 1 }
            return i
        }
        while i < ns.length, !isSpace(ns, i) { i += 1 }
        return i
    }

    /// Where each timed word starts in the line's text. One that can't be
    /// found counts as where the previous one ended.
    static func wordOffsets(_ segment: TranscriptSegment) -> [Int] {
        let ns = segment.text as NSString
        var offsets: [Int] = []
        var offset = 0
        for word in segment.words {
            let text = word.text.trimmingCharacters(in: .whitespacesAndNewlines)
            let found = text.isEmpty
                ? NSNotFound
                : ns.range(of: text, range: NSRange(location: offset, length: ns.length - offset)).location
            if found != NSNotFound {
                offset = found + (text as NSString).length
                offsets.append(found)
            } else {
                offsets.append(offset)
            }
        }
        return offsets
    }

    /// Offsets first..<last of a line as a line of its own, timed by its
    /// words, or else in proportion to its text.
    static func piece(_ segment: TranscriptSegment, _ first: Int, _ last: Int) -> TranscriptSegment? {
        let ns = segment.text as NSString
        let lower = max(0, min(first, ns.length))
        let upper = max(lower, min(last, ns.length))
        let text = ns.substring(with: NSRange(location: lower, length: upper - lower))
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return nil }
        if lower == 0 && upper >= ns.length { return segment }
        let words = zip(segment.words, wordOffsets(segment)).filter { lower <= $0.1 && $0.1 < upper }.map(\.0)
        let start: Double
        let end: Double
        if let firstWord = words.first, let lastWord = words.last {
            start = firstWord.start
            end = lastWord.end
        } else {
            let duration = segment.end - segment.start
            let length = Double(ns.length)
            start = segment.start + duration * Double(lower) / length
            end = segment.start + duration * Double(upper) / length
        }
        return TranscriptSegment(start: start, end: end, speaker: segment.speaker, text: text, words: words)
    }

    static func join(_ segments: [TranscriptSegment], speaker: Int?) -> TranscriptSegment {
        TranscriptSegment(start: segments.map(\.start).min() ?? 0, end: segments.map(\.end).max() ?? 0,
                          speaker: speaker, text: segments.map(\.text).joined(separator: " "),
                          words: segments.flatMap(\.words))
    }
}
