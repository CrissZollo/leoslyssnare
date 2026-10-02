import AVFoundation
import CoreAudio
import Foundation

struct RecordingError: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}

/// Records a chosen microphone, optionally with the sound of another app
/// mixed in (the meeting app, say), so both sides of a call end up in one
/// AAC `.m4a`. The microphone sets the pace: for every block of microphone
/// audio, the same amount of the app's sound is mixed in. While paused,
/// both are dropped, so the file holds none of the paused time.
final class MixingRecorder: @unchecked Sendable {
    let url: URL
    private let microphoneUID: String?
    private let application: String?
    private let engine = AVAudioEngine()
    private let writer = DispatchQueue(label: "Leos Lyssnare recording")
    private var file: AVAudioFile?
    private var microphoneConverter: MonoConverter?
    private var appBuffer: JitterBuffer?
    private var tap: AnyObject?  // AppAudioTap, macOS 14.2 and later

    private let lock = NSLock()
    private var frames = 0
    private var currentLevel: Float = 0
    private var isPaused = false
    private var writeError: Error?

    init(url: URL, microphoneUID: String?, application: String?) {
        self.url = url
        self.microphoneUID = microphoneUID
        self.application = application
    }

    /// Recorded length, excluding paused time.
    var elapsed: TimeInterval { lock.withLock { Double(frames) / MonoConverter.sampleRate } }
    /// Input level in 0...1 for the meter.
    var level: Float { lock.withLock { currentLevel } }
    var paused: Bool {
        get { lock.withLock { isPaused } }
        set { lock.withLock { isPaused = newValue } }
    }

    /// Whether the chosen app's sound is being recorded right now.
    var isHearingApp: Bool {
        if #available(macOS 14.2, *), let tap = tap as? AppAudioTap { return tap.isCapturing }
        return false
    }

    func start() throws {
        let settings: [String: Any] = [
            AVFormatIDKey: kAudioFormatMPEG4AAC,
            AVSampleRateKey: MonoConverter.sampleRate,
            AVNumberOfChannelsKey: 1,
            AVEncoderAudioQualityKey: AVAudioQuality.high.rawValue,
        ]
        file = try AVAudioFile(forWriting: url, settings: settings, commonFormat: .pcmFormatFloat32, interleaved: false)

        do {
            if let application {
                guard #available(macOS 14.2, *) else {
                    throw RecordingError("Recording another app's sound needs macOS 14.2 or later.")
                }
                // First, so the app's sound is already flowing when the microphone's arrives.
                let buffer = JitterBuffer(rate: Int(MonoConverter.sampleRate))
                let appTap = AppAudioTap(target: application) { buffer.push($0) }
                try appTap.start()
                appBuffer = buffer
                tap = appTap
            }
            try startMicrophone()
        } catch {
            stop()
            try? FileManager.default.removeItem(at: url)  // nothing was recorded
            throw error
        }
    }

    private func startMicrophone() throws {
        let input = engine.inputNode
        if let microphoneUID, var device = AudioSources.device(uid: microphoneUID) {
            // One that isn't connected any more falls back to the default.
            guard let unit = input.audioUnit,
                  AudioUnitSetProperty(unit, kAudioOutputUnitProperty_CurrentDevice, kAudioUnitScope_Global, 0,
                                       &device, UInt32(MemoryLayout<AudioDeviceID>.size)) == noErr
            else { throw RecordingError("Couldn't open the microphone.") }
        }
        let format = input.outputFormat(forBus: 0)
        guard format.sampleRate > 0, format.channelCount > 0, let converter = MonoConverter(from: format) else {
            throw RecordingError("No microphone was found. Check that a microphone is connected.")
        }
        microphoneConverter = converter
        input.installTap(onBus: 0, bufferSize: 4096, format: format) { [weak self] buffer, _ in
            self?.receive(buffer)
        }
        engine.prepare()
        try engine.start()
    }

    /// Finishes the file. Returns an error if writing it failed along the way.
    @discardableResult
    func stop() -> Error? {
        if engine.isRunning {
            engine.inputNode.removeTap(onBus: 0)
            engine.stop()
        }
        if #available(macOS 14.2, *), let tap = tap as? AppAudioTap { tap.stop() }
        tap = nil
        // The file is finished when it's released; first let the writes in flight land.
        writer.sync { file = nil }
        return lock.withLock { writeError }
    }

    // Runs on the microphone's audio thread.
    private func receive(_ buffer: AVAudioPCMBuffer) {
        guard let converter = microphoneConverter else { return }
        var samples = converter.convert(buffer)
        guard !samples.isEmpty else { return }
        let app = appBuffer?.take(samples.count)
        if paused { return }  // the app's sound while paused is left out too
        if let app {
            for i in samples.indices { samples[i] = max(-1, min(1, samples[i] + app[i])) }
        }
        let rms = sqrt(samples.reduce(0) { $0 + $1 * $1 } / Float(samples.count))
        let decibels = rms > 0 ? 20 * log10(rms) : -160
        lock.withLock {
            frames += samples.count
            currentLevel = max(0, min(1, (decibels + 50) / 50))
        }
        writer.async { [weak self] in self?.write(samples) }
    }

    private func write(_ samples: [Float]) {
        guard let file,
              let buffer = AVAudioPCMBuffer(pcmFormat: MonoConverter.format, frameCapacity: AVAudioFrameCount(samples.count))
        else { return }
        buffer.frameLength = AVAudioFrameCount(samples.count)
        samples.withUnsafeBufferPointer { buffer.floatChannelData![0].update(from: $0.baseAddress!, count: samples.count) }
        do {
            try file.write(from: buffer)
        } catch {
            lock.withLock { if writeError == nil { writeError = error } }
        }
    }
}

/// Converts any audio to mono 32-bit float at 48 kHz.
final class MonoConverter {
    static let sampleRate: Double = 48_000
    static let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: sampleRate, channels: 1,
                                      interleaved: false)!
    private let converter: AVAudioConverter

    init?(from input: AVAudioFormat) {
        guard let converter = AVAudioConverter(from: input, to: Self.format) else { return nil }
        converter.downmix = true
        self.converter = converter
    }

    func convert(_ buffer: AVAudioPCMBuffer) -> [Float] {
        let ratio = Self.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 64
        guard buffer.frameLength > 0,
              let output = AVAudioPCMBuffer(pcmFormat: Self.format, frameCapacity: capacity)
        else { return [] }
        var delivered = false
        var error: NSError?
        _ = converter.convert(to: output, error: &error) { _, status in
            if delivered {
                status.pointee = .noDataNow
                return nil
            }
            delivered = true
            status.pointee = .haveData
            return buffer
        }
        guard error == nil, let channel = output.floatChannelData?[0] else { return [] }
        return Array(UnsafeBufferPointer(start: channel, count: Int(output.frameLength)))
    }
}

/// Audio from the app, waiting to be mixed in. Two clocks never run at
/// exactly the same speed, and an app delivers sound in bursts, so it keeps a
/// little in reserve: after running dry it waits until `prebuffer` has
/// collected again, and if too much piles up it skips ahead.
final class JitterBuffer: @unchecked Sendable {
    private let prebuffer: Int
    private let limit: Int
    private var samples: [Float] = []
    private var primed = false
    private let lock = NSLock()

    init(rate: Int) {
        prebuffer = rate / 10  // 100 ms
        limit = rate / 2
    }

    func push(_ new: [Float]) {
        lock.withLock {
            samples.append(contentsOf: new)
            if samples.count > limit { samples.removeFirst(samples.count - prebuffer) }
        }
    }

    /// Exactly `count` samples, padded with silence when there aren't enough.
    func take(_ count: Int) -> [Float] {
        lock.withLock {
            var out = [Float](repeating: 0, count: count)
            if !primed {
                guard samples.count >= prebuffer else { return out }
                primed = true
            }
            let available = min(count, samples.count)
            out.replaceSubrange(0..<available, with: samples[0..<available])
            samples.removeFirst(available)
            if available < count { primed = false }
            return out
        }
    }
}
