import SwiftUI

/// The bar heights of the app icon, as a share of its height. Used for the
/// header mark, the resting waveform and the empty transcript.
private let motif: [CGFloat] = [0.26, 0.60, 1.0, 0.74, 0.46, 0.20]

/// The app icon, drawn in code so it is always sharp.
struct BrandMark: View {
    private let heights: [CGFloat] = [0.15, 0.35, 0.58, 0.43, 0.27, 0.12]

    var body: some View {
        GeometryReader { proxy in
            let side = min(proxy.size.width, proxy.size.height)
            ZStack {
                RoundedRectangle(cornerRadius: side * 0.22, style: .continuous)
                    .fill(LinearGradient(
                        colors: [Color(red: 0.231, green: 0.435, blue: 0.847), Color(red: 0.137, green: 0.275, blue: 0.627)],
                        startPoint: .top,
                        endPoint: .bottom
                    ))
                HStack(spacing: side * 0.045) {
                    ForEach(0..<6, id: \.self) { index in
                        Capsule().fill(Color.white).frame(width: side * 0.075, height: side * heights[index])
                    }
                }
            }
            .frame(width: side, height: side)
        }
    }
}

/// The icon's bars at rest, shown on the empty transcript.
struct MotifBars: View {
    var body: some View {
        HStack(spacing: 6) {
            ForEach(0..<6, id: \.self) { index in
                Capsule()
                    .fill(Theme.brand.opacity(0.35 + 0.65 * motif[index]))
                    .frame(width: 8, height: 6 + motif[index] * 50)
            }
        }
        .frame(height: 56)
    }
}

/// Live input level as a scrolling row of bars, like the app icon. At rest it
/// shows the icon's own bars on a dotted baseline.
struct WaveformView: View {
    let levels: [Float]
    let state: AudioRecorder.State

    private static let barWidth: CGFloat = 4
    private static let gap: CGFloat = 3

    var body: some View {
        Canvas { context, size in
            let step = Self.barWidth + Self.gap
            let count = max(1, Int((size.width + Self.gap) / step))
            let left = (size.width - (CGFloat(count) * step - Self.gap)) / 2
            let recent: [Float] = state == .idle ? [] : Array(levels.suffix(count))
            let first = count - recent.count

            for index in 0..<first {
                Self.drawBar(&context, size: size, x: left + CGFloat(index) * step, height: 4,
                             color: Theme.lineStrong.opacity(0.9))
            }
            if state == .idle {
                let start = count / 2 - motif.count / 2
                for (offset, scale) in motif.enumerated() {
                    Self.drawBar(&context, size: size, x: left + CGFloat(start + offset) * step,
                                 height: 4 + scale * (size.height - 12), color: Theme.brand.opacity(0.9))
                }
                return
            }
            let base = state == .recording ? Theme.record : Theme.warn
            for (offset, level) in recent.enumerated() {
                let index = first + offset
                var fade = 0.3 + 0.7 * Double(index) / Double(max(1, count - 1))
                if state == .paused { fade *= 0.6 }
                let height = max(4, CGFloat(pow(Double(level), 1.4)) * (size.height - 4))
                Self.drawBar(&context, size: size, x: left + CGFloat(index) * step, height: height,
                             color: base.opacity(fade))
            }
        }
    }

    private static func drawBar(_ context: inout GraphicsContext, size: CGSize, x: CGFloat, height: CGFloat, color: Color) {
        let rect = CGRect(x: x, y: (size.height - height) / 2, width: barWidth, height: height)
        context.fill(Path(roundedRect: rect, cornerRadius: barWidth / 2), with: .color(color))
    }
}

/// A coloured circle with the speaker's initial.
struct SpeakerAvatar: View {
    let speaker: Int
    let name: String

    private var initial: String {
        let trimmed = name.trimmingCharacters(in: .whitespaces)
        return trimmed.isEmpty ? String(speaker) : String(trimmed.prefix(1)).uppercased()
    }

    var body: some View {
        Circle()
            .fill(Theme.speakerColor(speaker))
            .frame(width: 34, height: 34)
            .overlay(Text(initial).font(.system(size: 14, weight: .semibold)).foregroundColor(Theme.speakerInk))
    }
}

/// How much of the meeting one person spoke.
struct ShareBar: View {
    let speaker: Int
    let share: Double

    var body: some View {
        GeometryReader { proxy in
            ZStack(alignment: .leading) {
                Capsule().fill(Theme.line)
                Capsule()
                    .fill(Theme.speakerColor(speaker))
                    .frame(width: max(4, proxy.size.width * CGFloat(share)))
            }
        }
        .frame(height: 4)
    }
}

/// Bottom left of the window: the version, the project and who made it.
struct AboutFooter: View {
    static let project = URL(string: "https://github.com/CrissZollo/leoslyssnare")!
    static let support = URL(string: "https://ko-fi.com/crisszollo")!

    var body: some View {
        HStack(spacing: 8) {
            Text("Leos Lyssnare \(UpdateChecker.currentVersion)")
            separator
            link("GitHub", Self.project)
            separator
            Text("by CrissZollo")
            separator
            link("Support on Ko-fi", Self.support)
        }
        .font(.system(size: 12))
        .foregroundColor(Theme.muted)
    }

    private var separator: some View {
        Text("·")
    }

    private func link(_ title: String, _ url: URL) -> some View {
        Link(destination: url) {
            Text(title).foregroundColor(Theme.brand)
        }
        .help(url.absoluteString)
    }
}
