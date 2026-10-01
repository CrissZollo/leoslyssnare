// swift-tools-version: 5.10
import PackageDescription

let package = Package(
    name: "LeosLyssnare",
    platforms: [.macOS(.v14)],
    dependencies: [
        // On-device Whisper speech recognition and SpeakerKit speaker diarization
        // (Core ML / Apple Neural Engine).
        .package(url: "https://github.com/argmaxinc/WhisperKit.git", from: "1.1.0"),
    ],
    targets: [
        .executableTarget(
            name: "LeosLyssnare",
            dependencies: [
                .product(name: "WhisperKit", package: "WhisperKit"),
                .product(name: "SpeakerKit", package: "WhisperKit"),
            ],
            path: "Sources/LeosLyssnare"
        ),
    ]
)
