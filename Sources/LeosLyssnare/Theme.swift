import AppKit
import SwiftUI

/// The app's palette. The same values are used by the Windows and Linux app
/// (desktop/leoslyssnare/theme.py), so all three look alike. Every colour
/// switches between its light and dark value with the system appearance.
enum Theme {
    static func dynamic(_ light: UInt32, _ dark: UInt32) -> Color {
        Color(nsColor: NSColor(name: nil, dynamicProvider: { appearance in
            let isDark = appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
            return NSColor(hex: isDark ? dark : light)
        }))
    }

    static let canvas = dynamic(0xF1F3F8, 0x0D1117)
    static let surface = dynamic(0xFFFFFF, 0x161B24)
    static let sunken = dynamic(0xF3F5F9, 0x1D2330)
    static let ink = dynamic(0x131A26, 0xE8ECF4)
    static let muted = dynamic(0x5F6B7F, 0x98A2B5)
    static let faint = dynamic(0xA3ACBB, 0x5A6478)
    static let line = dynamic(0xE4E8F0, 0x242B38)
    static let lineStrong = dynamic(0xD3D9E4, 0x313A4A)

    static let brand = dynamic(0x2F5BD8, 0x7C9BFF)
    static let brandInk = dynamic(0xFFFFFF, 0x0D1117)
    static let brandSoft = dynamic(0xE8EEFD, 0x1E2A4D)
    static let record = dynamic(0xE5484D, 0xFF6369)
    static let ok = dynamic(0x0F7B57, 0x4CC79A)
    static let okSoft = dynamic(0xE2F4EC, 0x16332A)
    static let warn = dynamic(0xC77A0A, 0xF0A93B)

    /// One colour per speaker, in the order they first talk.
    static let speakers: [Color] = [
        dynamic(0x3D63DD, 0x7C9BFF), dynamic(0x0E8F7E, 0x4CC7B5), dynamic(0xC2610C, 0xF0A05A),
        dynamic(0xC03A8E, 0xE879B9), dynamic(0x7B4FD9, 0xA98BFF), dynamic(0xCF3F3F, 0xFF7B7B),
        dynamic(0x5B7083, 0x9DB0C4), dynamic(0x2E8B3E, 0x6FD17E),
    ]
    /// Text on top of a speaker colour.
    static let speakerInk = dynamic(0xFFFFFF, 0x0D1117)

    static func speakerColor(_ speaker: Int?) -> Color {
        guard let speaker else { return muted }
        return speakers[(speaker - 1 + speakers.count * 100) % speakers.count]
    }
}

extension NSColor {
    convenience init(hex: UInt32) {
        self.init(
            srgbRed: CGFloat((hex >> 16) & 0xFF) / 255,
            green: CGFloat((hex >> 8) & 0xFF) / 255,
            blue: CGFloat(hex & 0xFF) / 255,
            alpha: 1
        )
    }
}

extension View {
    /// A white (or dark) rounded panel with a hairline border, no shadow.
    func card() -> some View {
        background(RoundedRectangle(cornerRadius: 16, style: .continuous).fill(Theme.surface))
            .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).strokeBorder(Theme.line, lineWidth: 1))
    }
}

// MARK: - Buttons

/// A solid button: red to record, dark to stop, blue for the main action.
struct FilledButtonStyle: ButtonStyle {
    var fill: Color
    var foreground: Color = .white
    var height: CGFloat = 46

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: height > 40 ? 15 : 14, weight: .medium))
            .foregroundColor(foreground)
            .padding(.horizontal, 16)
            .frame(height: height)
            .background(RoundedRectangle(cornerRadius: height > 40 ? 12 : 10, style: .continuous).fill(fill))
            .opacity(configuration.isPressed ? 0.85 : 1)
    }
}

/// A white button with a thin border.
struct OutlineButtonStyle: ButtonStyle {
    var height: CGFloat = 34

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: height > 40 ? 15 : 14, weight: .medium))
            .foregroundColor(Theme.ink)
            .padding(.horizontal, 14)
            .frame(height: height)
            .background(
                RoundedRectangle(cornerRadius: height > 40 ? 12 : 10, style: .continuous)
                    .fill(configuration.isPressed ? Theme.line : Theme.surface)
            )
            .overlay(
                RoundedRectangle(cornerRadius: height > 40 ? 12 : 10, style: .continuous)
                    .strokeBorder(Theme.lineStrong, lineWidth: 1)
            )
    }
}

/// A text-only button that gets a soft fill when pressed or switched on.
struct GhostButtonStyle: ButtonStyle {
    var selected = false

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 14, weight: .medium))
            .foregroundColor(selected ? Theme.brand : Theme.muted)
            .padding(.horizontal, 12)
            .frame(height: 34)
            .background(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .fill(selected ? Theme.brandSoft : (configuration.isPressed ? Theme.line : Color.clear))
            )
    }
}
