import AppKit
import CoreAudio
import Foundation

/// What can be recorded: the microphones, and the apps playing sound (the
/// meeting app, say), read from Core Audio.
enum AudioSources {
    /// Instead of one app: everything the Mac plays.
    static let allSound = "@all@"

    static let unknownObject = AudioObjectID(kAudioObjectUnknown)

    struct Microphone: Identifiable, Hashable {
        let id: String  // Core Audio's UID for the device
        let name: String
    }

    struct App: Identifiable, Hashable {
        let id: String  // the app's bundle identifier, which its helper processes share
        let name: String
    }

    /// Recording an app's sound uses Core Audio process taps, new in macOS 14.2.
    static var canRecordApps: Bool {
        if #available(macOS 14.2, *) { return true }
        return false
    }

    // MARK: - Microphones

    static func microphones() -> [Microphone] {
        CoreAudioProperty.objects(systemObject, kAudioHardwarePropertyDevices).compactMap { device in
            guard CoreAudioProperty.inputChannels(device) > 0,
                  let uid = CoreAudioProperty.string(device, kAudioDevicePropertyDeviceUID)
            else { return nil }
            return Microphone(id: uid, name: CoreAudioProperty.string(device, kAudioObjectPropertyName) ?? uid)
        }
    }

    static func defaultMicrophoneName() -> String? {
        let device: AudioObjectID = CoreAudioProperty.value(systemObject, kAudioHardwarePropertyDefaultInputDevice,
                                                            default: unknownObject)
        return device == unknownObject ? nil : CoreAudioProperty.string(device, kAudioObjectPropertyName)
    }

    /// The device with this UID, or nil when it isn't connected.
    static func device(uid: String) -> AudioDeviceID? {
        CoreAudioProperty.objects(systemObject, kAudioHardwarePropertyDevices).first {
            CoreAudioProperty.string($0, kAudioDevicePropertyDeviceUID) == uid
        }
    }

    // MARK: - Apps

    /// The apps playing sound right now. Browsers and meeting apps only show
    /// up once they play something, for example once a call has started.
    @available(macOS 14.2, *)
    static func apps() -> [App] {
        var found: [String: App] = [:]
        for process in processes() where process.isPlaying && found[process.key] == nil {
            found[process.key] = App(id: process.key, name: appName(key: process.key, pid: process.pid))
        }
        return found.values.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
    }

    struct AudioProcess {
        let object: AudioObjectID
        let pid: pid_t
        let key: String
        let isPlaying: Bool
    }

    /// Every process Core Audio knows about, except this app.
    @available(macOS 14.2, *)
    static func processes() -> [AudioProcess] {
        let own = getpid()
        return CoreAudioProperty.objects(systemObject, kAudioHardwarePropertyProcessObjectList).compactMap { object in
            let pid: pid_t = CoreAudioProperty.value(object, kAudioProcessPropertyPID, default: -1)
            guard pid > 0, pid != own,
                  let bundleID = CoreAudioProperty.string(object, kAudioProcessPropertyBundleID), !bundleID.isEmpty
            else { return nil }
            let playing: UInt32 = CoreAudioProperty.value(object, kAudioProcessPropertyIsRunningOutput, default: 0)
            return AudioProcess(object: object, pid: pid, key: appKey(bundleID: bundleID), isPlaying: playing != 0)
        }
    }

    /// Core Audio's object for this app's own process, if it has one.
    @available(macOS 14.2, *)
    static func ownProcessObject() -> AudioObjectID? {
        var address = CoreAudioProperty.address(kAudioHardwarePropertyTranslatePIDToProcessObject)
        var pid = getpid()
        var object = unknownObject
        var size = UInt32(MemoryLayout<AudioObjectID>.size)
        let status = AudioObjectGetPropertyData(systemObject, &address, UInt32(MemoryLayout<pid_t>.size), &pid,
                                                &size, &object)
        return status == noErr && object != unknownObject ? object : nil
    }

    /// Browsers and Electron apps (Teams, Slack…) play sound from helper
    /// processes, such as "com.google.Chrome.helper.renderer": those count
    /// as their app.
    static func appKey(bundleID: String) -> String {
        if let helper = bundleID.range(of: ".helper", options: .caseInsensitive) {
            return String(bundleID[..<helper.lowerBound])
        }
        return bundleID
    }

    private static func appName(key: String, pid: pid_t) -> String {
        // Safari and other WebKit apps play sound from WebKit's own process.
        if key.hasPrefix("com.apple.WebKit") { return "Safari" }
        return NSRunningApplication.runningApplications(withBundleIdentifier: key).first?.localizedName
            ?? NSRunningApplication(processIdentifier: pid)?.localizedName
            ?? key
    }

    static var systemObject: AudioObjectID { AudioObjectID(kAudioObjectSystemObject) }
}

/// Reading Core Audio properties.
enum CoreAudioProperty {
    static func address(_ selector: AudioObjectPropertySelector,
                        scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal) -> AudioObjectPropertyAddress {
        AudioObjectPropertyAddress(mSelector: selector, mScope: scope, mElement: kAudioObjectPropertyElementMain)
    }

    static func value<T>(_ object: AudioObjectID, _ selector: AudioObjectPropertySelector, default fallback: T) -> T {
        var address = address(selector)
        var result = fallback
        var size = UInt32(MemoryLayout<T>.size)
        let status = AudioObjectGetPropertyData(object, &address, 0, nil, &size, &result)
        return status == noErr ? result : fallback
    }

    static func objects(_ object: AudioObjectID, _ selector: AudioObjectPropertySelector) -> [AudioObjectID] {
        var address = address(selector)
        var size: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(object, &address, 0, nil, &size) == noErr, size > 0 else { return [] }
        var ids = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
        guard AudioObjectGetPropertyData(object, &address, 0, nil, &size, &ids) == noErr else { return [] }
        return Array(ids.prefix(Int(size) / MemoryLayout<AudioObjectID>.size))
    }

    static func string(_ object: AudioObjectID, _ selector: AudioObjectPropertySelector) -> String? {
        var address = address(selector)
        var value: Unmanaged<CFString>?
        var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
        guard AudioObjectGetPropertyData(object, &address, 0, nil, &size, &value) == noErr, let value else {
            return nil
        }
        return value.takeRetainedValue() as String
    }

    static func inputChannels(_ device: AudioObjectID) -> Int {
        var address = address(kAudioDevicePropertyStreamConfiguration, scope: kAudioObjectPropertyScopeInput)
        var size: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(device, &address, 0, nil, &size) == noErr, size > 0 else { return 0 }
        let raw = UnsafeMutableRawPointer.allocate(byteCount: Int(size),
                                                   alignment: MemoryLayout<AudioBufferList>.alignment)
        defer { raw.deallocate() }
        guard AudioObjectGetPropertyData(device, &address, 0, nil, &size, raw) == noErr else { return 0 }
        let buffers = UnsafeMutableAudioBufferListPointer(raw.assumingMemoryBound(to: AudioBufferList.self))
        return buffers.reduce(0) { $0 + Int($1.mNumberChannels) }
    }
}
