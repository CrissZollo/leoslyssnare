import AVFoundation
import CoreAudio
import Foundation

/// Captures what one app plays (from all its processes), or everything the
/// Mac plays, with a Core Audio process tap. The app sounds exactly as it does
/// in the speakers, and nothing about its playback changes.
///
/// A tap is fixed to the processes it was made for, so while recording it's
/// remade whenever the app's processes change: a call that starts later, a
/// browser opening a tab. macOS asks the first time whether Leos Lyssnare may
/// record the sound of other apps.
@available(macOS 14.2, *)
final class AppAudioTap: @unchecked Sendable {
    private let target: String
    private let onAudio: ([Float]) -> Void
    /// Tap changes happen here, one at a time.
    private let control = DispatchQueue(label: "Leos Lyssnare app tap")
    /// Core Audio delivers the sound here.
    private let audio = DispatchQueue(label: "Leos Lyssnare app audio", qos: .userInitiated)
    private var watcher: DispatchSourceTimer?
    private var processes: [AudioObjectID] = []
    private var tapID = AudioSources.unknownObject
    private var aggregateID = AudioSources.unknownObject
    private var procID: AudioDeviceIOProcID?
    private let lock = NSLock()
    private var capturing = false

    /// Calls onAudio with mono samples at MonoConverter.sampleRate, from its own queue.
    init(target: String, onAudio: @escaping ([Float]) -> Void) {
        self.target = target
        self.onAudio = onAudio
    }

    /// Whether the chosen app has processes being recorded right now.
    var isCapturing: Bool { lock.withLock { capturing } }

    func start() throws {
        try control.sync {
            if target == AudioSources.allSound {
                try build(processes: AudioSources.ownProcessObject().map { [$0] } ?? [])
            } else {
                processes = matchingProcesses()
                if !processes.isEmpty { try build(processes: processes) }
            }
        }
        guard target != AudioSources.allSound else { return }
        let timer = DispatchSource.makeTimerSource(queue: control)
        timer.schedule(deadline: .now() + 1, repeating: 1)
        timer.setEventHandler { [weak self] in self?.follow() }
        timer.resume()
        watcher = timer
    }

    func stop() {
        watcher?.cancel()
        watcher = nil
        control.sync { teardown() }
    }

    private func matchingProcesses() -> [AudioObjectID] {
        AudioSources.processes().filter { $0.key == target }.map(\.object).sorted()
    }

    private func follow() {
        let now = matchingProcesses()
        guard now != processes else { return }
        processes = now
        teardown()
        if !now.isEmpty { try? build(processes: now) }
    }

    private func build(processes: [AudioObjectID]) throws {
        let description = target == AudioSources.allSound
            ? CATapDescription(stereoGlobalTapButExcludeProcesses: processes)
            : CATapDescription(stereoMixdownOfProcesses: processes)
        description.uuid = UUID()
        description.isPrivate = true
        description.muteBehavior = .unmuted

        var tap = AudioSources.unknownObject
        try check(AudioHardwareCreateProcessTap(description, &tap), "start listening to the app")
        tapID = tap

        var streamDescription: AudioStreamBasicDescription = CoreAudioProperty.value(
            tapID, kAudioTapPropertyFormat, default: AudioStreamBasicDescription())
        guard let format = AVAudioFormat(streamDescription: &streamDescription),
              let converter = MonoConverter(from: format)
        else {
            teardown()
            throw RecordingError("The app's sound has a format that can't be recorded.")
        }

        let output: AudioObjectID = CoreAudioProperty.value(
            AudioSources.systemObject, kAudioHardwarePropertyDefaultSystemOutputDevice, default: AudioSources.unknownObject)
        let outputUID = CoreAudioProperty.string(output, kAudioDevicePropertyDeviceUID) ?? ""
        let aggregate: [String: Any] = [
            kAudioAggregateDeviceNameKey: "Leos Lyssnare",
            kAudioAggregateDeviceUIDKey: UUID().uuidString,
            kAudioAggregateDeviceMainSubDeviceKey: outputUID,
            kAudioAggregateDeviceIsPrivateKey: true,
            kAudioAggregateDeviceIsStackedKey: false,
            kAudioAggregateDeviceTapAutoStartKey: true,
            kAudioAggregateDeviceSubDeviceListKey: [[kAudioSubDeviceUIDKey: outputUID]],
            kAudioAggregateDeviceTapListKey: [
                [kAudioSubTapDriftCompensationKey: true, kAudioSubTapUIDKey: description.uuid.uuidString],
            ],
        ]
        var device = AudioSources.unknownObject
        do {
            try check(AudioHardwareCreateAggregateDevice(aggregate as CFDictionary, &device), "start listening to the app")
            aggregateID = device
            let deliver = onAudio
            try check(AudioDeviceCreateIOProcIDWithBlock(&procID, aggregateID, audio) { _, input, _, _, _ in
                guard let buffer = AVAudioPCMBuffer(pcmFormat: format, bufferListNoCopy: input, deallocator: nil)
                else { return }
                let samples = converter.convert(buffer)
                if !samples.isEmpty { deliver(samples) }
            }, "start listening to the app")
            try check(AudioDeviceStart(aggregateID, procID), "start listening to the app")
        } catch {
            teardown()
            throw error
        }
        lock.withLock { capturing = true }
    }

    private func teardown() {
        lock.withLock { capturing = false }
        if aggregateID != AudioSources.unknownObject {
            if let procID {
                AudioDeviceStop(aggregateID, procID)
                AudioDeviceDestroyIOProcID(aggregateID, procID)
            }
            AudioHardwareDestroyAggregateDevice(aggregateID)
        }
        if tapID != AudioSources.unknownObject {
            AudioHardwareDestroyProcessTap(tapID)
        }
        procID = nil
        aggregateID = AudioSources.unknownObject
        tapID = AudioSources.unknownObject
    }

    private func check(_ status: OSStatus, _ action: String) throws {
        guard status == noErr else { throw RecordingError("Couldn't \(action) (error \(status)).") }
    }
}
