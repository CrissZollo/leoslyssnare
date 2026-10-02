import AppKit
import SwiftUI

@main
struct LeosLyssnareApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    init() {
        // Makes `swift run` behave like a normal app (Dock icon, focus).
        NSApplication.shared.setActivationPolicy(.regular)
    }

    var body: some Scene {
        Window("Leos Lyssnare", id: "main") {
            ContentView()
        }
        .windowResizability(.contentMinSize)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApplication.shared.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        MainActor.assumeIsolated { RecordingGuard.allowsClosing() } ? .terminateNow : .terminateCancel
    }
}

/// Asks before quitting or closing the window would cut off a recording.
@MainActor
enum RecordingGuard {
    /// Set by ContentView.
    static weak var recorder: AudioRecorder?

    /// True when there's no recording, or the user chose to stop and save it.
    static func allowsClosing() -> Bool {
        guard let recorder, recorder.state != .idle else { return true }
        let alert = NSAlert()
        alert.messageText = "Stop recording?"
        alert.informativeText = "A recording is in progress. Stop and save it before quitting?"
        alert.addButton(withTitle: "Stop and quit")
        alert.addButton(withTitle: "Keep recording")
        guard alert.runModal() == .alertFirstButtonReturn else { return false }
        _ = recorder.stop()
        return true
    }
}

/// Put in the window's view hierarchy: asks RecordingGuard before the window
/// closes. SwiftUI owns the window's delegate, so this stands in front of it
/// and passes everything else on.
struct WindowCloseGuard: NSViewRepresentable {
    func makeNSView(context: Context) -> NSView { WindowWatcher() }
    func updateNSView(_ nsView: NSView, context: Context) {}

    final class WindowWatcher: NSView {
        private var proxy: DelegateProxy?  // the window only holds its delegate weakly

        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            guard let window, proxy == nil || window.delegate !== proxy else { return }
            let proxy = DelegateProxy(original: window.delegate)
            window.delegate = proxy
            self.proxy = proxy
        }
    }

    final class DelegateProxy: NSObject, NSWindowDelegate {
        private weak var original: NSWindowDelegate?

        init(original: NSWindowDelegate?) {
            self.original = original
        }

        func windowShouldClose(_ sender: NSWindow) -> Bool {
            guard MainActor.assumeIsolated({ RecordingGuard.allowsClosing() }) else { return false }
            return original?.windowShouldClose?(sender) ?? true
        }

        override func responds(to selector: Selector!) -> Bool {
            super.responds(to: selector) || original?.responds(to: selector) == true
        }

        override func forwardingTarget(for selector: Selector!) -> Any? {
            original?.responds(to: selector) == true ? original : super.forwardingTarget(for: selector)
        }
    }
}
