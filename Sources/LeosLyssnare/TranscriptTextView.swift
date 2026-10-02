import AppKit
import SwiftUI

/// Where each line, and each timed word, sits in the transcript text.
struct TranscriptLayout {
    var lines: [NSRange] = []
    var words: [[(start: Double, range: NSRange)]] = []
}

typealias TextPosition = (line: Int, offset: Int)

/// The transcript as selectable text (an NSTextView, which SwiftUI's Text
/// can't stand in for): the line and word being played are highlighted and
/// kept in the middle of the view, a click plays from the word clicked, and
/// selected text can be given to another speaker from the right-click menu.
struct TranscriptTextView: NSViewRepresentable {
    let transcript: Transcript
    /// Transcriber.revision: the text is only rebuilt when it changes.
    let revision: Int
    let showTimestamps: Bool
    /// The playback position to highlight, or nil before the user has played or jumped.
    let position: Double?
    let follow: Bool
    /// Whether a click plays from there (there's a recording to play).
    let canJump: Bool
    var onJump: (Double) -> Void
    var onUserScroll: () -> Void
    var onMove: (TextPosition, TextPosition, Int?) -> Void

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSView(context: Context) -> NSScrollView {
        let scrollView = NSScrollView()
        scrollView.hasVerticalScroller = true
        scrollView.drawsBackground = false
        scrollView.borderType = .noBorder

        // TextKit 1, built by hand: the highlighting uses the layout manager's
        // temporary attributes. The text storage is the root that owns the rest.
        let storage = NSTextStorage()
        let layoutManager = NSLayoutManager()
        storage.addLayoutManager(layoutManager)
        let container = NSTextContainer(containerSize: NSSize(width: scrollView.contentSize.width,
                                                              height: CGFloat.greatestFiniteMagnitude))
        container.widthTracksTextView = true
        layoutManager.addTextContainer(container)
        let textView = TranscriptNSTextView(frame: NSRect(origin: .zero, size: scrollView.contentSize),
                                            textContainer: container)
        textView.isEditable = false
        textView.isSelectable = true
        textView.drawsBackground = false
        textView.textContainerInset = NSSize(width: 0, height: 2)
        textView.minSize = .zero
        textView.maxSize = NSSize(width: CGFloat.greatestFiniteMagnitude, height: CGFloat.greatestFiniteMagnitude)
        textView.isVerticallyResizable = true
        textView.isHorizontallyResizable = false
        textView.autoresizingMask = [.width]
        scrollView.documentView = textView

        context.coordinator.attach(scrollView: scrollView, textView: textView, storage: storage)
        return scrollView
    }

    func updateNSView(_ scrollView: NSScrollView, context: Context) {
        context.coordinator.update(self)
    }

    @MainActor
    final class Coordinator: NSObject {
        private var view: TranscriptTextView?
        private weak var scrollView: NSScrollView?
        private weak var textView: TranscriptNSTextView?
        private var storage: NSTextStorage?
        private var layout = TranscriptLayout()
        private var starts: [Double] = []
        private var builtRevision = -1
        private var builtTimestamps = true
        private var highlighted: Playing?
        private var highlightRanges: [NSRange] = []
        private var scrollTarget: CGFloat?
        nonisolated(unsafe) private var liveScrollObserver: NSObjectProtocol?

        struct Playing: Equatable {
            let line: Int
            let word: Int?
        }

        func attach(scrollView: NSScrollView, textView: TranscriptNSTextView, storage: NSTextStorage) {
            self.scrollView = scrollView
            self.textView = textView
            self.storage = storage
            textView.onClick = { [weak self] index in self?.clicked(at: index) }
            textView.onScrollWheel = { [weak self] in self?.userScrolled() }
            textView.extraMenuItems = { [weak self] in self?.moveMenuItems() ?? [] }
            // Dragging the scroll bar.
            liveScrollObserver = NotificationCenter.default.addObserver(
                forName: NSScrollView.willStartLiveScrollNotification, object: scrollView, queue: .main
            ) { [weak self] _ in
                MainActor.assumeIsolated { self?.userScrolled() }
            }
        }

        deinit {
            if let liveScrollObserver { NotificationCenter.default.removeObserver(liveScrollObserver) }
        }

        func update(_ view: TranscriptTextView) {
            self.view = view
            if view.revision != builtRevision || view.showTimestamps != builtTimestamps {
                rebuild(view)
            }
            highlight(view.position)
            if view.follow, let position = view.position {
                scrollToPlayback(position)
            }
        }

        // MARK: - Building the text

        private func rebuild(_ view: TranscriptTextView) {
            guard let textView, let scrollView, let storage else { return }
            builtRevision = view.revision
            builtTimestamps = view.showTimestamps
            let origin = scrollView.contentView.bounds.origin
            let (text, layout) = Self.build(view.transcript, timestamps: view.showTimestamps)
            storage.setAttributedString(text)
            self.layout = layout
            starts = view.transcript.segments.map(\.start)
            highlighted = nil
            highlightRanges = []
            scrollView.contentView.scroll(to: origin)
            scrollView.reflectScrolledClipView(scrollView.contentView)
        }

        static func build(_ transcript: Transcript, timestamps: Bool) -> (NSAttributedString, TranscriptLayout) {
            let text = NSMutableAttributedString()
            var layout = TranscriptLayout()

            func style(lineSpacing: CGFloat = 4, after: CGFloat) -> NSParagraphStyle {
                let style = NSMutableParagraphStyle()
                style.lineSpacing = lineSpacing
                style.paragraphSpacing = after
                return style
            }
            let body: [NSAttributedString.Key: Any] = [
                .font: NSFont.systemFont(ofSize: 15),
                .foregroundColor: Theme.NS.ink,
                .paragraphStyle: style(after: transcript.hasSpeakers ? 20 : 10),
            ]
            let small: [NSAttributedString.Key: Any] = [
                .font: NSFont.monospacedDigitSystemFont(ofSize: 12, weight: .regular),
                .foregroundColor: Theme.NS.muted,
            ]
            func append(_ string: String, _ attributes: [NSAttributedString.Key: Any]) {
                text.append(NSAttributedString(string: string, attributes: attributes))
            }
            func appendLine(_ segment: TranscriptSegment) {
                let start = text.length
                append(segment.text, body)
                layout.lines.append(NSRange(location: start, length: (segment.text as NSString).length))
                // The words, found in order in the line's text.
                let line = segment.text as NSString
                var offset = 0
                var words: [(start: Double, range: NSRange)] = []
                for word in segment.words {
                    let wordText = word.text.trimmingCharacters(in: .whitespacesAndNewlines)
                    guard !wordText.isEmpty else { continue }
                    let found = line.range(of: wordText, range: NSRange(location: offset, length: line.length - offset))
                    guard found.location != NSNotFound else { continue }
                    words.append((word.start, NSRange(location: start + found.location, length: found.length)))
                    offset = NSMaxRange(found)
                }
                layout.words.append(words)
            }

            let segments = transcript.segments
            if transcript.hasSpeakers {
                for segment in segments {
                    let head: [NSAttributedString.Key: Any] = [
                        .font: NSFont.systemFont(ofSize: 14, weight: .semibold),
                        .foregroundColor: Theme.NS.speakerColor(segment.speaker),
                        .paragraphStyle: style(lineSpacing: 0, after: 3),
                    ]
                    append(transcript.name(for: segment.speaker), head)
                    if timestamps { append("  " + Transcript.timestamp(segment.start), small) }
                    append("\n", head)
                    appendLine(segment)
                    append("\n", body)
                }
            } else if timestamps {
                for segment in segments {
                    append(Transcript.timestamp(segment.start) + "   ", small)
                    appendLine(segment)
                    append("\n", body)
                }
            } else {
                // A new paragraph after pauses over 2 s, like Transcript.text(withTimestamps:).
                for (i, segment) in segments.enumerated() {
                    if i > 0 { append(segment.start - segments[i - 1].end > 2 ? "\n" : " ", body) }
                    appendLine(segment)
                }
            }
            return (text, layout)
        }

        // MARK: - Following playback

        /// The line and word being said at `seconds`. In a pause, the last
        /// thing said stays current.
        private func playing(at seconds: Double) -> Playing? {
            let line = Self.lastIndex(in: starts, atMost: seconds)
            guard let line, line < layout.words.count else { return nil }
            let word = Self.lastIndex(in: layout.words[line].map { $0.start }, atMost: seconds)
            return Playing(line: line, word: word)
        }

        private static func lastIndex(in values: [Double], atMost limit: Double) -> Int? {
            var low = 0
            var high = values.count
            while low < high {
                let middle = (low + high) / 2
                if values[middle] <= limit { low = middle + 1 } else { high = middle }
            }
            return low > 0 ? low - 1 : nil
        }

        private func highlight(_ position: Double?) {
            let now = position.flatMap(playing(at:))
            guard now != highlighted, let layoutManager = textView?.layoutManager else { return }
            highlighted = now
            for range in highlightRanges {
                layoutManager.removeTemporaryAttribute(.backgroundColor, forCharacterRange: range)
            }
            highlightRanges = []
            guard let now else { return }
            let line = layout.lines[now.line]
            layoutManager.addTemporaryAttribute(.backgroundColor, value: Theme.NS.playing, forCharacterRange: line)
            highlightRanges.append(line)
            if let word = now.word {
                let range = layout.words[now.line][word].range
                layoutManager.addTemporaryAttribute(.backgroundColor, value: Theme.NS.playingWord, forCharacterRange: range)
                highlightRanges.append(range)
            }
        }

        /// Keeps what is being said in the middle of the view. Near the start
        /// and end of the transcript the scroll range runs out, so it sits
        /// higher or lower instead.
        private func scrollToPlayback(_ seconds: Double) {
            guard let now = playing(at: seconds), let view, let textView, let scrollView,
                  let layoutManager = textView.layoutManager, let container = textView.textContainer
            else { return }
            let line = layout.lines[now.line]
            let index: Int
            if let word = now.word {
                index = layout.words[now.line][word].range.location
            } else {
                // Without word timings, move through the line at an even pace.
                let segment = view.transcript.segments[now.line]
                let progress = segment.end > segment.start ? (seconds - segment.start) / (segment.end - segment.start) : 0
                index = line.location + Int((Double(line.length) * max(0, min(1, progress))).rounded())
            }
            layoutManager.ensureLayout(for: container)
            let glyph = layoutManager.glyphIndexForCharacter(at: min(index, max(0, (textView.string as NSString).length - 1)))
            let rect = layoutManager.lineFragmentRect(forGlyphAt: glyph, effectiveRange: nil)
            let clip = scrollView.contentView
            let visible = clip.bounds.height
            let middle = rect.midY + textView.textContainerOrigin.y
            let target = max(0, min(textView.frame.height - visible, middle - visible / 2))
            guard abs(target - (scrollTarget ?? clip.bounds.origin.y)) >= 2 else { return }
            scrollTarget = target
            NSAnimationContext.runAnimationGroup { context in
                context.duration = 0.38
                context.timingFunction = CAMediaTimingFunction(name: .easeOut)
                clip.animator().setBoundsOrigin(NSPoint(x: clip.bounds.origin.x, y: target))
            } completionHandler: { [weak self, weak scrollView] in
                MainActor.assumeIsolated {
                    if self?.scrollTarget == target { self?.scrollTarget = nil }
                    if let scrollView { scrollView.reflectScrolledClipView(scrollView.contentView) }
                }
            }
        }

        /// Reading elsewhere while it plays: stop pulling the view back.
        private func userScrolled() {
            guard let view, view.follow, view.position != nil, let clip = scrollView?.contentView else { return }
            scrollTarget = nil
            NSAnimationContext.runAnimationGroup { context in
                context.duration = 0
                clip.animator().setBoundsOrigin(clip.bounds.origin)
            }
            view.onUserScroll()
        }

        // MARK: - Clicks and the menu

        /// A plain click plays from the word clicked, or from the start of its
        /// line (also for a click on the speaker name or time above it).
        private func clicked(at index: Int) {
            guard let view, view.canJump, !layout.lines.isEmpty else { return }
            guard let line = layout.lines.indices.first(where: { index <= NSMaxRange(layout.lines[$0]) }) else { return }
            let words = layout.words[line]
            if index >= layout.lines[line].location,
               let word = words.lastIndex(where: { $0.range.location <= index }) {
                view.onJump(words[word].start)
            } else {
                view.onJump(view.transcript.segments[line].start)
            }
        }

        /// The selection as (line, offset) at each end. A selection that starts
        /// in a speaker's name or time starts with the line below them, and
        /// one that ends there ends with the line above.
        private func selection() -> (TextPosition, TextPosition)? {
            guard let range = textView?.selectedRange(), range.length > 0 else { return nil }
            let start = range.location
            let end = NSMaxRange(range)
            let lines = layout.lines
            guard let a = lines.indices.first(where: { start <= NSMaxRange(lines[$0]) }),
                  let b = lines.indices.last(where: { end >= lines[$0].location })
            else { return nil }
            let first: TextPosition = (a, max(0, start - lines[a].location))
            let last: TextPosition = (b, min(NSMaxRange(lines[b]), end) - lines[b].location)
            guard (first.line, first.offset) <= (last.line, last.offset) else { return nil }
            return (first, last)
        }

        private func moveMenuItems() -> [NSMenuItem] {
            guard let view, view.transcript.hasSpeakers, let selected = selection() else { return [] }
            let (first, last) = selected
            let transcript = view.transcript
            let speakers = NSMenu()
            for speaker in transcript.speakers {
                let item = ClosureMenuItem(title: transcript.name(for: speaker)) { view.onMove(first, last, speaker) }
                item.image = Self.dot(Theme.NS.speakerColor(speaker))
                speakers.addItem(item)
            }
            let move = NSMenuItem(title: "Move to speaker", action: nil, keyEquivalent: "")
            move.submenu = speakers
            let new = ClosureMenuItem(title: "Move to a new speaker") { view.onMove(first, last, nil) }
            return [move, new]
        }

        private static func dot(_ color: NSColor) -> NSImage {
            NSImage(size: NSSize(width: 10, height: 10), flipped: false) { rect in
                color.setFill()
                NSBezierPath(ovalIn: rect).fill()
                return true
            }
        }
    }
}

/// Reports plain clicks and scrolling, and adds items to the right-click menu.
final class TranscriptNSTextView: NSTextView {
    var onClick: ((Int) -> Void)?
    var onScrollWheel: (() -> Void)?
    var extraMenuItems: (() -> [NSMenuItem])?

    override func mouseDown(with event: NSEvent) {
        // Returns once the mouse is let go; a drag has selected text by then.
        super.mouseDown(with: event)
        guard event.clickCount == 1, selectedRange().length == 0 else { return }
        onClick?(characterIndexForInsertion(at: convert(event.locationInWindow, from: nil)))
    }

    override func scrollWheel(with event: NSEvent) {
        onScrollWheel?()
        super.scrollWheel(with: event)
    }

    override func menu(for event: NSEvent) -> NSMenu? {
        let menu = super.menu(for: event) ?? NSMenu()
        let items = extraMenuItems?() ?? []
        guard !items.isEmpty else { return menu }
        for (index, item) in items.enumerated() { menu.insertItem(item, at: index) }
        menu.insertItem(.separator(), at: items.count)
        return menu
    }
}

/// A menu item that runs a closure.
final class ClosureMenuItem: NSMenuItem {
    private let handler: () -> Void

    init(title: String, handler: @escaping () -> Void) {
        self.handler = handler
        super.init(title: title, action: #selector(run), keyEquivalent: "")
        target = self
    }

    @available(*, unavailable)
    required init(coder: NSCoder) {
        fatalError("init(coder:) is not supported")
    }

    @objc private func run() {
        handler()
    }
}
