import SwiftUI

/// The microphone and the app to record, remembered between launches.
@MainActor
final class SourcesModel: ObservableObject {
    @Published private(set) var microphones: [AudioSources.Microphone] = []
    @Published private(set) var defaultMicrophoneName: String?
    @Published private(set) var apps: [AudioSources.App] = []

    /// A Core Audio device UID, or "" for the default microphone.
    @Published var microphone: String {
        didSet {
            defaults.set(microphone, forKey: "microphone")
            defaults.set(microphones.first { $0.id == microphone }?.name, forKey: "microphoneName")
        }
    }

    /// An AudioSources.App id, AudioSources.allSound, or "" for none.
    @Published var application: String {
        didSet {
            defaults.set(application, forKey: "meetingApp")
            defaults.set(apps.first { $0.id == application }?.name, forKey: "meetingAppName")
        }
    }

    private let defaults = UserDefaults.standard
    private var ticks = 0

    init() {
        microphone = defaults.string(forKey: "microphone") ?? ""
        application = AudioSources.canRecordApps ? defaults.string(forKey: "meetingApp") ?? "" : ""
        refresh()
    }

    /// Called ten times a second: the lists follow devices being plugged in
    /// and apps starting to play sound while nothing is being recorded.
    func tick(idle: Bool) {
        ticks += 1
        if idle, ticks % 20 == 0 { refresh() }
    }

    func refresh() {
        var microphones = AudioSources.microphones()
        // Remembered from last time but not connected now: it stays chosen,
        // and the default one records until it's back.
        if !microphone.isEmpty, !microphones.contains(where: { $0.id == microphone }) {
            let name = defaults.string(forKey: "microphoneName") ?? "Microphone"
            microphones.append(.init(id: microphone, name: "\(name) (not connected)"))
        }
        if microphones != self.microphones { self.microphones = microphones }
        let defaultName = AudioSources.defaultMicrophoneName()
        if defaultName != defaultMicrophoneName { defaultMicrophoneName = defaultName }

        guard #available(macOS 14.2, *) else { return }
        var apps = AudioSources.apps()
        // Remembered from last time: it's recorded as soon as it plays sound.
        if !application.isEmpty, application != AudioSources.allSound, !apps.contains(where: { $0.id == application }) {
            apps.append(.init(id: application, name: defaults.string(forKey: "meetingAppName") ?? application))
        }
        if apps != self.apps { self.apps = apps }
    }

    var applicationName: String {
        apps.first { $0.id == application }?.name ?? "The app"
    }
}

/// "Microphone" and "Also record sound from", above the record buttons.
struct SourcePickers: View {
    @ObservedObject var sources: SourcesModel
    @ObservedObject var recorder: AudioRecorder

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Microphone").font(.system(size: 13)).foregroundColor(Theme.muted)
            Picker("Microphone", selection: $sources.microphone) {
                Text(sources.defaultMicrophoneName.map { "Default (\($0))" } ?? "Default microphone").tag("")
                ForEach(sources.microphones) { microphone in
                    Text(microphone.name).tag(microphone.id)
                }
            }
            .labelsHidden()
            .pickerStyle(.menu)
            .frame(maxWidth: .infinity)

            if AudioSources.canRecordApps {
                Text("Also record sound from")
                    .font(.system(size: 13))
                    .foregroundColor(Theme.muted)
                    .padding(.top, 4)
                Picker("Also record sound from", selection: $sources.application) {
                    Text("Nothing else (only the microphone)").tag("")
                    Text("All sound from this Mac").tag(AudioSources.allSound)
                    ForEach(sources.apps) { app in
                        Text(app.name).tag(app.id)
                    }
                }
                .labelsHidden()
                .pickerStyle(.menu)
                .frame(maxWidth: .infinity)
                .help("The app the meeting is in, so the other people in it are recorded too")

                Text(caption)
                    .font(.system(size: 12))
                    .foregroundColor(Theme.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .disabled(recorder.state != .idle)
        .padding(.vertical, 4)
    }

    private var caption: String {
        if recorder.state != .idle, let app = recorder.application, app != AudioSources.allSound {
            return recorder.hearingApp
                ? "Recording the sound from \(sources.applicationName)."
                : "\(sources.applicationName) isn't playing sound. It's recorded as soon as it does."
        }
        if sources.application.isEmpty {
            return "To record a call, choose the app it's in. Apps show up here once they play sound."
        }
        return "Use headphones, so the microphone doesn't pick up the other people a second time."
    }
}
