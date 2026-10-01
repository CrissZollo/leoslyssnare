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
}
