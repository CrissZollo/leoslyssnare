import AppKit
import CryptoKit
import Foundation

/// Looks for a newer release on GitHub and replaces the app with it.
///
/// Releases are tagged `desktop-v<version>` and hold the files for all three
/// platforms; the Mac app is the `…-macos-arm64.zip`. Checking is one request
/// to GitHub that sends nothing about the user, and failing (offline) is silent.
@MainActor
final class UpdateChecker: ObservableObject {
    struct Release: Equatable {
        let version: String
        let page: URL
        let zipURL: URL?
        let zipSize: Int
        let sha256: String?
    }

    static let repo = "CrissZollo/leoslyssnare"
    nonisolated static let tagPrefix = "desktop-v"
    private static let skippedKey = "skippedUpdate"

    @Published private(set) var available: Release?
    @Published private(set) var isInstalling = false
    @Published private(set) var progress: Double?
    @Published var errorMessage: String?

    static var currentVersion: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0"
    }

    /// The app can only replace itself when it's a real .app in a folder it may write to.
    var canInstallInPlace: Bool {
        let bundle = Bundle.main.bundleURL
        return available?.zipURL != nil && bundle.pathExtension == "app"
            && FileManager.default.isWritableFile(atPath: bundle.deletingLastPathComponent().path)
    }

    private var downloadTask: URLSessionDownloadTask?

    // MARK: - Checking

    /// Soon after start-up, then every few hours while the app is open.
    func checkPeriodically() async {
        try? await Task.sleep(nanoseconds: 3_000_000_000)
        while !Task.isCancelled {
            if !isInstalling { await check() }
            try? await Task.sleep(nanoseconds: 6 * 60 * 60 * 1_000_000_000)
        }
    }

    func check() async {
        guard let url = URL(string: "https://api.github.com/repos/\(Self.repo)/releases?per_page=10") else { return }
        var request = URLRequest(url: url, timeoutInterval: 15)
        request.setValue("LeosLyssnare", forHTTPHeaderField: "User-Agent")
        request.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        guard let (data, response) = try? await URLSession.shared.data(for: request),
              (response as? HTTPURLResponse)?.statusCode == 200,
              let release = Self.latest(in: data),
              Self.isNewer(release.version, than: Self.currentVersion),
              UserDefaults.standard.string(forKey: Self.skippedKey) != release.version
        else { return }
        available = release
    }

    /// Hides the banner until a still newer version comes out.
    func skip() {
        if let version = available?.version { UserDefaults.standard.set(version, forKey: Self.skippedKey) }
        available = nil
    }

    nonisolated static func isNewer(_ candidate: String, than current: String) -> Bool {
        func parts(_ text: String) -> [Int] {
            text.split(separator: ".").map { Int($0.prefix { $0.isNumber }) ?? 0 }
        }
        let a = parts(candidate), b = parts(current)
        for i in 0..<max(a.count, b.count) {
            let x = i < a.count ? a[i] : 0, y = i < b.count ? b[i] : 0
            if x != y { return x > y }
        }
        return false
    }

    /// The highest published desktop release in GitHub's list.
    nonisolated static func latest(in data: Data) -> Release? {
        struct Asset: Decodable {
            let name: String
            let browser_download_url: URL
            let size: Int?
            let digest: String?
        }
        struct Item: Decodable {
            let tag_name: String
            let html_url: URL
            let draft: Bool
            let prerelease: Bool
            let assets: [Asset]
        }
        guard let items = try? JSONDecoder().decode([Item].self, from: data) else { return nil }
        var best: Release?
        for item in items where !item.draft && !item.prerelease && item.tag_name.hasPrefix(tagPrefix) {
            let version = String(item.tag_name.dropFirst(tagPrefix.count))
            guard version.first?.isNumber == true else { continue }
            if let best, !isNewer(version, than: best.version) { continue }
            let zip = item.assets.first { $0.name.hasSuffix("-macos-arm64.zip") }
            let sha = zip?.digest.flatMap { $0.hasPrefix("sha256:") ? String($0.dropFirst(7)) : nil }
            best = Release(version: version, page: item.html_url, zipURL: zip?.browser_download_url,
                           zipSize: zip?.size ?? 0, sha256: sha)
        }
        return best
    }

    // MARK: - Installing

    func cancelInstall() {
        downloadTask?.cancel()
    }

    /// Downloads the new version, checks it, swaps it in for this app and
    /// starts it. The swap is done by a small script once this app has quit.
    func install() async {
        guard let release = available, let zipURL = release.zipURL, !isInstalling else { return }
        isInstalling = true
        progress = nil
        errorMessage = nil
        defer {
            isInstalling = false
            progress = nil
        }
        let bundle = Bundle.main.bundleURL
        let fileManager = FileManager.default
        // Next to the app, so moving it into place stays on the same disk.
        let staging = bundle.deletingLastPathComponent()
            .appendingPathComponent(".LeosLyssnare-update-\(UUID().uuidString.prefix(8))")
        do {
            let zip = try await download(zipURL, size: release.zipSize)
            defer { try? fileManager.removeItem(at: zip) }
            if let expected = release.sha256, try Self.sha256(of: zip) != expected {
                throw UpdateError("The downloaded file doesn't match its checksum. Try again.")
            }
            try fileManager.createDirectory(at: staging, withIntermediateDirectories: true)
            try Self.run("/usr/bin/ditto", ["-x", "-k", zip.path, staging.path])
            guard let newApp = try fileManager.contentsOfDirectory(at: staging, includingPropertiesForKeys: nil)
                .first(where: { $0.pathExtension == "app" })
            else { throw UpdateError("The update didn't contain an app.") }
            try Self.run("/usr/bin/codesign", ["--verify", "--deep", "--strict", newApp.path])

            // After this app has quit: move the old one aside, put the new one
            // in its place (back again if that fails), clean up and open it.
            let script = """
            while kill -0 "$1" 2>/dev/null; do sleep 0.2; done
            if mv "$2" "$2.old" && mv "$3" "$2"; then rm -rf "$2.old"; else mv "$2.old" "$2"; fi
            rm -rf "$4"
            open "$2"
            """
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/bin/sh")
            process.arguments = ["-c", script, "sh", String(ProcessInfo.processInfo.processIdentifier),
                                 bundle.path, newApp.path, staging.path]
            process.standardOutput = FileHandle.nullDevice
            process.standardError = FileHandle.nullDevice
            try process.run()
            NSApplication.shared.terminate(nil)
        } catch is CancellationError {
            try? fileManager.removeItem(at: staging)
        } catch {
            try? fileManager.removeItem(at: staging)
            errorMessage = "The update failed: \(error.localizedDescription)"
        }
    }

    private struct UpdateError: LocalizedError {
        let errorDescription: String?
        init(_ message: String) { errorDescription = message }
    }

    private static func run(_ tool: String, _ arguments: [String]) throws {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: tool)
        process.arguments = arguments
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        try process.run()
        process.waitUntilExit()
        guard process.terminationStatus == 0 else {
            throw UpdateError("\((tool as NSString).lastPathComponent) failed (\(process.terminationStatus)).")
        }
    }

    private static func sha256(of url: URL) throws -> String {
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }
        var hasher = SHA256()
        while let chunk = try handle.read(upToCount: 1 << 20), !chunk.isEmpty {
            hasher.update(data: chunk)
        }
        return hasher.finalize().map { String(format: "%02x", $0) }.joined()
    }

    /// Downloads to a temporary file, reporting progress. The file is the caller's to delete.
    private func download(_ url: URL, size: Int) async throws -> URL {
        var request = URLRequest(url: url, timeoutInterval: 60)
        request.setValue("LeosLyssnare", forHTTPHeaderField: "User-Agent")
        let delegate = DownloadDelegate { [weak self] fraction in
            Task { @MainActor in self?.progress = fraction }
        }
        let session = URLSession(configuration: .ephemeral, delegate: delegate, delegateQueue: nil)
        defer { session.finishTasksAndInvalidate() }
        return try await withCheckedThrowingContinuation { continuation in
            delegate.continuation = continuation
            let task = session.downloadTask(with: request)
            downloadTask = task
            task.resume()
        }
    }
}

private final class DownloadDelegate: NSObject, URLSessionDownloadDelegate {
    var continuation: CheckedContinuation<URL, Error>?
    private let onProgress: (Double) -> Void

    init(onProgress: @escaping (Double) -> Void) {
        self.onProgress = onProgress
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didWriteData bytesWritten: Int64,
                    totalBytesWritten: Int64, totalBytesExpectedToWrite: Int64) {
        if totalBytesExpectedToWrite > 0 { onProgress(Double(totalBytesWritten) / Double(totalBytesExpectedToWrite)) }
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didFinishDownloadingTo location: URL) {
        // The system deletes `location` when this returns, so move it first.
        let kept = FileManager.default.temporaryDirectory.appendingPathComponent("LeosLyssnare-\(UUID().uuidString).zip")
        do {
            if let status = (downloadTask.response as? HTTPURLResponse)?.statusCode, status != 200 {
                throw URLError(.badServerResponse)
            }
            try FileManager.default.moveItem(at: location, to: kept)
            finish(.success(kept))
        } catch {
            finish(.failure(error))
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        guard let error else { return }
        finish(.failure((error as? URLError)?.code == .cancelled ? CancellationError() : error))
    }

    private func finish(_ result: Result<URL, Error>) {
        continuation?.resume(with: result)
        continuation = nil
    }
}
