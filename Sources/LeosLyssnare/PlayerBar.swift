import SwiftUI

/// Under the transcript: play/pause, the position along the recording, and
/// what to do when the recording can't be found.
struct PlayerBar: View {
    @ObservedObject var player: TranscriptPlayer
    let transcript: Transcript
    /// Playing while recording would put the playback in the recording.
    let canPlay: Bool
    var onFindAudio: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            Button {
                player.toggle()
            } label: {
                Image(systemName: player.isPlaying ? "pause.fill" : "play.fill")
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundColor(Theme.brandInk)
                    .frame(width: 36, height: 36)
                    .background(Circle().fill(Theme.brand))
            }
            .buttonStyle(.plain)
            .disabled(player.url == nil || !canPlay)
            .opacity(player.url == nil || !canPlay ? 0.45 : 1)
            .help(player.isPlaying ? "Pause" : "Play the recording")

            Text(Transcript.timestamp(player.scrubPosition ?? player.position))
                .font(.system(size: 14, weight: .medium))
                .monospacedDigit()
                .foregroundColor(Theme.ink)

            TimelineScrubber(
                duration: player.duration,
                position: player.scrubPosition ?? player.position,
                turns: transcript.hasSpeakers
                    ? transcript.segments.map { (start: $0.start, end: $0.end, speaker: $0.speaker) } : [],
                enabled: player.url != nil,
                onScrub: { player.scrub($0) },
                onSeek: { player.seek($0) }
            )

            Text(Transcript.timestamp(player.duration))
                .font(.system(size: 13))
                .monospacedDigit()
                .foregroundColor(Theme.muted)

            // Short texts only: a long file name here would squeeze the scrubber.
            if player.url == nil {
                Text(FileManager.default.fileExists(atPath: transcript.sourceURL.path)
                     ? "Can't play the recording" : "Recording not found")
                    .font(.system(size: 12))
                    .foregroundColor(Theme.muted)
                    .help(player.problem ?? "")
                Button("Find audio file…", action: onFindAudio)
                    .buttonStyle(.link)
                    .help("Choose the recording this transcript was made from")
            } else if player.playheadShown && !player.follow {
                Button("Back to playback") { player.follow = true }
                    .buttonStyle(.link)
                    .help("Scroll back to what is being played")
            } else {
                Text("Click the text to play from there")
                    .font(.system(size: 12))
                    .foregroundColor(Theme.muted)
            }
        }
        .padding(.top, 2)
    }
}

/// The playback position along the recording. Click or drag to jump. With
/// speakers, the track is coloured by who speaks when.
struct TimelineScrubber: View {
    let duration: Double
    let position: Double
    let turns: [(start: Double, end: Double, speaker: Int?)]
    let enabled: Bool
    var onScrub: (Double) -> Void
    var onSeek: (Double) -> Void

    private let knob: CGFloat = 14

    var body: some View {
        GeometryReader { geometry in
            let width = geometry.size.width
            Canvas { context, size in
                let track = CGRect(x: knob / 2, y: size.height / 2 - 2.5, width: max(1, size.width - knob), height: 5)
                let fraction = duration > 0 ? min(1, position / duration) : 0
                let playedX = track.minX + track.width * fraction

                var inside = context
                inside.clip(to: Path(roundedRect: track, cornerRadius: 2.5))
                inside.fill(Path(track), with: .color(Theme.line))
                if !turns.isEmpty, duration > 0 {
                    for turn in turns {
                        let left = track.minX + track.width * turn.start / duration
                        let right = track.minX + track.width * min(turn.end, duration) / duration
                        let color = Theme.speakerColor(turn.speaker)
                        inside.fill(Path(CGRect(x: left, y: track.minY, width: max(1, right - left), height: track.height)),
                                    with: .color(color.opacity(0.3)))
                        if left < playedX {
                            inside.fill(Path(CGRect(x: left, y: track.minY, width: max(1, min(right, playedX) - left),
                                                    height: track.height)),
                                        with: .color(color))
                        }
                    }
                } else {
                    inside.fill(Path(CGRect(x: track.minX, y: track.minY, width: playedX - track.minX, height: track.height)),
                                with: .color(Theme.brand))
                }

                let circle = Path(ellipseIn: CGRect(x: playedX - knob / 2 + 1.5, y: size.height / 2 - knob / 2 + 1.5,
                                                    width: knob - 3, height: knob - 3))
                context.fill(circle, with: .color(Theme.surface))
                context.stroke(circle, with: .color(Theme.brand), lineWidth: 3)
            }
            .contentShape(Rectangle())
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { onScrub(seconds(at: $0.location.x, width: width)) }
                    .onEnded { onSeek(seconds(at: $0.location.x, width: width)) }
            )
        }
        .frame(height: 22)
        .opacity(enabled ? 1 : 0.45)
        .allowsHitTesting(enabled && duration > 0)
    }

    private func seconds(at x: CGFloat, width: CGFloat) -> Double {
        let track = max(1, width - knob)
        return Double(max(0, min(1, (x - knob / 2) / track))) * duration
    }
}
