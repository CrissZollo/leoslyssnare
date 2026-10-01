import AVFoundation
import Foundation

/// Records the microphone to a single AAC `.m4a` file.
///
/// Pausing uses `AVAudioRecorder.pause()`, which stops writing audio without
/// closing the file. Resuming appends to the same file, so the finished
/// recording contains every unpaused part back to back and none of the
/// paused time.
@MainActor
final class AudioRecorder: ObservableObject {
    enum State {
        case idle, recording, paused
    }

    @Published private(set) var state: State = .idle
    /// Recorded length, excluding paused time.
    @Published private(set) var elapsed: TimeInterval = 0
    /// Input level in 0...1 for the meter.
    @Published private(set) var level: Float = 0
    @Published var errorMessage: String?

    private var recorder: AVAudioRecorder?
    private var timer: Timer?
    private var activity: NSObjectProtocol?

    func start() async {
        guard state == .idle else { return }
        guard await Self.requestMicrophoneAccess() else {
            errorMessage = "Leos Lyssnare doesn't have microphone access. Allow it in System Settings › Privacy & Security › Microphone."
            return
        }

        let settings: [String: Any] = [
            AVFormatIDKey: kAudioFormatMPEG4AAC,
            AVSampleRateKey: 44_100,
            AVNumberOfChannelsKey: 1,
            AVEncoderAudioQualityKey: AVAudioQuality.high.rawValue,
        ]

        do {
            let recorder = try AVAudioRecorder(url: AppPaths.newRecordingURL(), settings: settings)
            recorder.isMeteringEnabled = true
            guard recorder.prepareToRecord(), recorder.record() else {
                errorMessage = "Couldn't start recording. Check that a microphone is connected."
                return
            }
            self.recorder = recorder
            elapsed = 0
            state = .recording
            // Keep the Mac from idle-sleeping during a long meeting.
            activity = ProcessInfo.processInfo.beginActivity(
                options: [.userInitiated, .idleSystemSleepDisabled],
                reason: "Recording a meeting"
            )
            startTimer()
        } catch {
            errorMessage = "Couldn't start recording: \(error.localizedDescription)"
        }
    }

    func pause() {
        guard state == .recording, let recorder else { return }
        recorder.pause()
        level = 0
        state = .paused
    }

    func resume() {
        guard state == .paused, let recorder else { return }
        if recorder.record() {
            state = .recording
        } else {
            errorMessage = "Couldn't resume recording."
        }
    }

    /// Finishes the file and returns its location.
    func stop() -> URL? {
        guard state != .idle, let recorder else { return nil }
        // Read the length before stop(), which resets currentTime.
        elapsed = recorder.currentTime
        recorder.stop()
        let url = recorder.url
        self.recorder = nil
        timer?.invalidate()
        timer = nil
        if let activity {
            ProcessInfo.processInfo.endActivity(activity)
            self.activity = nil
        }
        level = 0
        state = .idle
        return url
    }

    private func startTimer() {
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.tick() }
        }
    }

    private func tick() {
        guard state == .recording, let recorder else { return }
        elapsed = recorder.currentTime
        recorder.updateMeters()
        let decibels = recorder.averagePower(forChannel: 0) // -160...0
        level = max(0, min(1, (decibels + 50) / 50))
    }

    private static func requestMicrophoneAccess() async -> Bool {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized:
            return true
        case .notDetermined:
            return await AVCaptureDevice.requestAccess(for: .audio)
        default:
            return false
        }
    }
}
