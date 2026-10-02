import AVFoundation
import Foundation

/// Plays a transcript's recording, so the transcript can be followed along
/// with the audio. Also keeps what the transcript view needs to follow it:
/// whether to show the playback position yet, and whether to keep it in view.
@MainActor
final class TranscriptPlayer: ObservableObject {
    @Published private(set) var position: Double = 0
    @Published private(set) var duration: Double = 0
    @Published private(set) var isPlaying = false
    /// The recording being played, or nil when there's none to play.
    @Published private(set) var url: URL?
    /// Why there's nothing to play, when a transcript has no playable recording.
    @Published private(set) var problem: String?
    /// The position shown while the scrubber is dragged; the audio jumps on release.
    @Published private(set) var scrubPosition: Double?
    /// Highlight what's being said only once the user has played or jumped.
    @Published private(set) var playheadShown = false
    /// Keep what's being said in the middle of the view, until the user scrolls away.
    @Published var follow = true

    private var player: AVAudioPlayer?
    private var timer: Timer?

    /// Where to show the playback position, or nil before the user has played or jumped.
    var shownPosition: Double? {
        playheadShown ? scrubPosition ?? position : nil
    }

    func open(_ url: URL, transcriptLength: Double) {
        close()
        duration = transcriptLength
        guard FileManager.default.fileExists(atPath: url.path) else {
            problem = "“\(url.lastPathComponent)” wasn't found at \(url.deletingLastPathComponent().path)."
            return
        }
        do {
            let player = try AVAudioPlayer(contentsOf: url)
            player.prepareToPlay()
            self.player = player
            self.url = url
            duration = player.duration > 0 ? player.duration : transcriptLength
        } catch {
            problem = "“\(url.lastPathComponent)” can't be played on this Mac: \(error.localizedDescription)"
        }
    }

    func close() {
        stopTimer()
        player?.stop()
        player = nil
        url = nil
        problem = nil
        isPlaying = false
        position = 0
        scrubPosition = nil
        playheadShown = false
        follow = true
    }

    func toggle() {
        guard let player else { return }
        if player.isPlaying {
            pause()
        } else {
            if position >= duration - 0.05 { player.currentTime = 0 }
            player.play()
            isPlaying = true
            playheadShown = true
            follow = true
            startTimer()
        }
    }

    func pause() {
        player?.pause()
        isPlaying = false
        stopTimer()
        if let player { position = player.currentTime }
    }

    /// Jumps to `seconds`, playing on from there if playback was running.
    func seek(_ seconds: Double) {
        let target = max(0, min(seconds, duration))
        player?.currentTime = target
        position = target
        scrubPosition = nil
        playheadShown = true
        follow = true
    }

    /// While the scrubber is dragged.
    func scrub(_ seconds: Double) {
        scrubPosition = max(0, min(seconds, duration))
        playheadShown = true
        follow = true
    }

    private func startTimer() {
        stopTimer()
        let timer = Timer(timeInterval: 0.04, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.tick() }
        }
        RunLoop.main.add(timer, forMode: .common)
        self.timer = timer
    }

    private func stopTimer() {
        timer?.invalidate()
        timer = nil
    }

    private func tick() {
        guard let player else { return }
        if player.isPlaying {
            position = player.currentTime
        } else {
            // Played to the end.
            position = duration
            isPlaying = false
            stopTimer()
        }
    }
}
