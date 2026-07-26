import AppKit
import ApplicationServices
import CoreMedia
import CoreVideo
import Foundation
import ScreenCaptureKit

private enum HelperError: Error, CustomStringConvertible {
    case usage(String)
    case permission(String)
    case windowNotFound(String)
    case stream(String)

    var description: String {
        switch self {
        case .usage(let value), .permission(let value),
             .windowNotFound(let value), .stream(let value):
            return value
        }
    }
}

private struct WindowRecord: Codable {
    let windowID: UInt32
    let pid: pid_t
    let bundleIdentifier: String
    let ownerName: String
    let title: String
    let x: Double
    let y: Double
    let width: Double
    let height: Double
    let onScreen: Bool
    let active: Bool
}

private func writeJSON<T: Encodable>(_ value: T) throws {
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys]
    let data = try encoder.encode(value)
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data([0x0A]))
}

@MainActor
private func shareableContent(onScreenOnly: Bool) async throws -> SCShareableContent {
    try await SCShareableContent.excludingDesktopWindows(
        true,
        onScreenWindowsOnly: onScreenOnly
    )
}

@MainActor
private func windowRecords(onScreenOnly: Bool) async throws -> [WindowRecord] {
    guard CGPreflightScreenCaptureAccess() else {
        throw HelperError.permission(
            "Screen Recording permission is required; run `permissions --prompt` first"
        )
    }
    let content = try await shareableContent(onScreenOnly: onScreenOnly)
    let activePID = NSWorkspace.shared.frontmostApplication?.processIdentifier
    return content.windows.map { window in
        let app = window.owningApplication
        return WindowRecord(
            windowID: window.windowID,
            pid: app?.processID ?? 0,
            bundleIdentifier: app?.bundleIdentifier ?? "",
            ownerName: app?.applicationName ?? "",
            title: window.title ?? "",
            x: window.frame.origin.x,
            y: window.frame.origin.y,
            width: window.frame.width,
            height: window.frame.height,
            onScreen: window.isOnScreen,
            active: app?.processID == activePID
        )
    }
}

@MainActor
private func findWindow(windowID: UInt32) async throws -> SCWindow {
    let content = try await shareableContent(onScreenOnly: false)
    guard let window = content.windows.first(where: { $0.windowID == windowID }) else {
        throw HelperError.windowNotFound("No shareable window with id \(windowID)")
    }
    return window
}

@MainActor
private func permissionStatus(prompt: Bool) -> [String: Bool] {
    var screen = CGPreflightScreenCaptureAccess()
    if prompt && !screen {
        screen = CGRequestScreenCaptureAccess()
    }
    let options = [
        kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: prompt
    ] as CFDictionary
    let accessibility = AXIsProcessTrustedWithOptions(options)
    return [
        "screenRecording": screen,
        "accessibility": accessibility,
    ]
}

@MainActor
private func activate(pid: pid_t) throws {
    guard let application = NSRunningApplication(processIdentifier: pid) else {
        throw HelperError.windowNotFound("No running application with pid \(pid)")
    }
    let success = application.activate(options: [.activateAllWindows, .activateIgnoringOtherApps])
    if !success {
        throw HelperError.stream("Failed to activate pid \(pid)")
    }
}

private let keyCodes: [String: CGKeyCode] = [
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7,
    "c": 8, "v": 9, "b": 11, "q": 12, "w": 13, "e": 14, "r": 15,
    "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22,
    "5": 23, "=": 24, "9": 25, "7": 26, "-": 27, "8": 28, "0": 29,
    "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35, "enter": 36,
    "return": 36, "l": 37, "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42,
    ",": 43, "/": 44, "n": 45, "m": 46, ".": 47, "tab": 48, "space": 49,
    "`": 50, "backspace": 51, "esc": 53, "escape": 53, "cmd": 55,
    "command": 55, "shift": 56, "lshift": 56, "capslock": 57, "alt": 58,
    "option": 58, "lalt": 58, "ctrl": 59, "control": 59, "lctrl": 59,
    "rshift": 60, "ralt": 61, "rctrl": 62, "f17": 64, "decimal": 65,
    "multiply": 67, "plus": 69, "clear": 71, "divide": 75, "enterpad": 76,
    "minus": 78, "f18": 79, "f19": 80, "equals": 81, "0pad": 82, "1pad": 83,
    "2pad": 84, "3pad": 85, "4pad": 86, "5pad": 87, "6pad": 88, "7pad": 89,
    "f20": 90, "8pad": 91, "9pad": 92, "f5": 96, "f6": 97, "f7": 98,
    "f3": 99, "f8": 100, "f9": 101, "f11": 103, "f13": 105, "f16": 106,
    "f14": 107, "f10": 109, "f12": 111, "f15": 113, "help": 114,
    "home": 115, "pageup": 116, "delete": 117, "f4": 118, "end": 119,
    "f2": 120, "pagedown": 121, "f1": 122, "left": 123, "right": 124,
    "down": 125, "up": 126,
]

private func keyEvent(name: String, down: Bool) throws {
    guard let code = keyCodes[name.lowercased()] else {
        throw HelperError.usage("Unsupported key: \(name)")
    }
    guard let event = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: down) else {
        throw HelperError.stream("Failed to create keyboard event")
    }
    event.post(tap: .cghidEventTap)
}

private func modifierFlag(name: String) throws -> CGEventFlags {
    switch name.lowercased() {
    case "alt", "option", "lalt", "ralt":
        return .maskAlternate
    case "ctrl", "control", "lctrl", "rctrl":
        return .maskControl
    case "shift", "lshift", "rshift":
        return .maskShift
    case "cmd", "command":
        return .maskCommand
    default:
        throw HelperError.usage("Unsupported modifier key: \(name)")
    }
}

private func textEvent(_ text: String) throws {
    let units = Array(text.utf16)
    guard !units.isEmpty else {
        return
    }
    guard let event = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: true) else {
        throw HelperError.stream("Failed to create text event")
    }
    units.withUnsafeBufferPointer { pointer in
        event.keyboardSetUnicodeString(stringLength: units.count, unicodeString: pointer.baseAddress!)
    }
    event.post(tap: .cghidEventTap)
}

private func mouseButton(_ name: String) throws -> CGMouseButton {
    switch name.lowercased() {
    case "left": return .left
    case "right": return .right
    case "middle": return .center
    default: throw HelperError.usage("Unsupported mouse button: \(name)")
    }
}

private func mouseType(button: CGMouseButton, down: Bool, dragged: Bool = false) -> CGEventType {
    switch button {
    case .left:
        return dragged ? .leftMouseDragged : (down ? .leftMouseDown : .leftMouseUp)
    case .right:
        return dragged ? .rightMouseDragged : (down ? .rightMouseDown : .rightMouseUp)
    default:
        return dragged ? .otherMouseDragged : (down ? .otherMouseDown : .otherMouseUp)
    }
}

private func postMouse(
    x: Double,
    y: Double,
    button: CGMouseButton = .left,
    down: Bool? = nil,
    dragged: Bool = false,
    flags: CGEventFlags = []
) throws {
    let type = down.map { mouseType(button: button, down: $0, dragged: dragged) } ?? .mouseMoved
    guard let event = CGEvent(
        mouseEventSource: nil,
        mouseType: type,
        mouseCursorPosition: CGPoint(x: x, y: y),
        mouseButton: button
    ) else {
        throw HelperError.stream("Failed to create mouse event")
    }
    event.flags = flags
    event.post(tap: .cghidEventTap)
}

private func postModifiedClick(
    x: Double,
    y: Double,
    button: CGMouseButton,
    milliseconds: Int,
    modifiers: [String]
) throws {
    guard !modifiers.isEmpty else {
        throw HelperError.usage("modified-click requires at least one modifier key")
    }

    var flags: CGEventFlags = []
    for modifier in modifiers {
        flags.formUnion(try modifierFlag(name: modifier))
    }

    for modifier in modifiers {
        try keyEvent(name: modifier, down: true)
    }
    do {
        try postMouse(x: x, y: y, flags: flags)
        try postMouse(x: x, y: y, button: button, down: true, flags: flags)
        usleep(useconds_t(max(0, milliseconds) * 1_000))
        try postMouse(x: x, y: y, button: button, down: false, flags: flags)
    } catch {
        for modifier in modifiers.reversed() {
            try? keyEvent(name: modifier, down: false)
        }
        throw error
    }
    for modifier in modifiers.reversed() {
        try keyEvent(name: modifier, down: false)
    }
}

private final class StreamOutput: NSObject, SCStreamOutput, SCStreamDelegate {
    private let output = FileHandle.standardOutput
    private let lock = NSLock()
    private var stopped = false

    func stream(
        _ stream: SCStream,
        didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
        of outputType: SCStreamOutputType
    ) {
        guard outputType == .screen,
              sampleBuffer.isValid,
              let imageBuffer = sampleBuffer.imageBuffer else {
            return
        }
        CVPixelBufferLockBaseAddress(imageBuffer, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(imageBuffer, .readOnly) }
        guard let baseAddress = CVPixelBufferGetBaseAddress(imageBuffer) else { return }
        let width = CVPixelBufferGetWidth(imageBuffer)
        let height = CVPixelBufferGetHeight(imageBuffer)
        let bytesPerRow = CVPixelBufferGetBytesPerRow(imageBuffer)
        let payloadLength = bytesPerRow * height

        var header = Data("OKFR".utf8)
        for rawValue in [UInt32(width), UInt32(height), UInt32(bytesPerRow), UInt32(payloadLength)] {
            var value = rawValue.littleEndian
            withUnsafeBytes(of: &value) { header.append(contentsOf: $0) }
        }
        let payload = Data(bytes: baseAddress, count: payloadLength)
        lock.lock()
        output.write(header)
        output.write(payload)
        lock.unlock()
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        FileHandle.standardError.write(Data("Stream stopped: \(error)\n".utf8))
        markStopped()
    }

    func writePNGFrame(_ image: CGImage) throws {
        let representation = NSBitmapImageRep(cgImage: image)
        guard let png = representation.representation(using: .png, properties: [:]) else {
            throw HelperError.stream("Failed to encode screenshot stream frame as PNG")
        }

        var header = Data("OKPN".utf8)
        for rawValue in [
            UInt32(image.width),
            UInt32(image.height),
            UInt32(0),
            UInt32(png.count),
        ] {
            var value = rawValue.littleEndian
            withUnsafeBytes(of: &value) { header.append(contentsOf: $0) }
        }

        lock.lock()
        output.write(header)
        output.write(png)
        lock.unlock()
    }

    private func markStopped() {
        lock.lock()
        stopped = true
        lock.unlock()
    }

    func isStopped() -> Bool {
        lock.lock()
        let value = stopped
        lock.unlock()
        return value
    }

    func installStopSignalHandlers() -> [DispatchSourceSignal] {
        signal(SIGINT, SIG_IGN)
        signal(SIGTERM, SIG_IGN)
        let signalQueue = DispatchQueue(label: "ok.macos.signal")
        let interrupt = DispatchSource.makeSignalSource(signal: SIGINT, queue: signalQueue)
        let terminate = DispatchSource.makeSignalSource(signal: SIGTERM, queue: signalQueue)
        interrupt.setEventHandler { self.markStopped() }
        terminate.setEventHandler { self.markStopped() }
        interrupt.resume()
        terminate.resume()
        return [interrupt, terminate]
    }
}

@MainActor
private func snapshotStream(windowID: UInt32, fps: Int) async throws {
    guard CGPreflightScreenCaptureAccess() else {
        throw HelperError.permission(
            "Screen Recording permission is required; run `permissions --prompt` first"
        )
    }
    let window = try await findWindow(windowID: windowID)
    let filter = SCContentFilter(desktopIndependentWindow: window)
    let configuration = captureConfiguration(for: window, fps: fps)
    let output = StreamOutput()
    let stopSignalHandlers = output.installStopSignalHandlers()
    let interval = UInt64(1_000_000_000 / max(1, fps))

    while !output.isStopped() {
        let image = try await SCScreenshotManager.captureImage(
            contentFilter: filter,
            configuration: configuration
        )
        try output.writePNGFrame(image)
        try await Task<Never, Never>.sleep(nanoseconds: interval)
    }
    _ = stopSignalHandlers
}

@MainActor
private func captureConfiguration(for window: SCWindow, fps: Int = 30) -> SCStreamConfiguration {
    let configuration = SCStreamConfiguration()
    let scale = NSScreen.screens
        .first(where: { $0.frame.intersects(window.frame) })?
        .backingScaleFactor ?? NSScreen.main?.backingScaleFactor ?? 1.0
    configuration.width = max(1, Int(round(window.frame.width * scale)))
    configuration.height = max(1, Int(round(window.frame.height * scale)))
    configuration.pixelFormat = kCVPixelFormatType_32BGRA
    configuration.queueDepth = 3
    configuration.showsCursor = false
    configuration.minimumFrameInterval = CMTime(
        value: 1,
        timescale: CMTimeScale(max(1, fps))
    )
    return configuration
}

@MainActor
private func snapshotWindow(windowID: UInt32, path: String) async throws {
    guard CGPreflightScreenCaptureAccess() else {
        throw HelperError.permission(
            "Screen Recording permission is required; run `permissions --prompt` first"
        )
    }
    let window = try await findWindow(windowID: windowID)
    let filter = SCContentFilter(desktopIndependentWindow: window)
    let configuration = captureConfiguration(for: window)
    let image = try await SCScreenshotManager.captureImage(
        contentFilter: filter,
        configuration: configuration
    )
    let representation = NSBitmapImageRep(cgImage: image)
    guard let png = representation.representation(using: .png, properties: [:]) else {
        throw HelperError.stream("Failed to encode screenshot as PNG")
    }
    try png.write(to: URL(fileURLWithPath: path), options: .atomic)
}

@MainActor
private func streamWindow(windowID: UInt32, fps: Int) async throws {
    guard CGPreflightScreenCaptureAccess() else {
        throw HelperError.permission(
            "Screen Recording permission is required; run `permissions --prompt` first"
        )
    }
    let window = try await findWindow(windowID: windowID)
    let filter = SCContentFilter(desktopIndependentWindow: window)
    let configuration = captureConfiguration(for: window, fps: fps)

    let output = StreamOutput()
    let stream = SCStream(filter: filter, configuration: configuration, delegate: output)
    let queue = DispatchQueue(label: "ok.macos.capture", qos: .userInteractive)
    try stream.addStreamOutput(output, type: .screen, sampleHandlerQueue: queue)
    let stopSignalHandlers = output.installStopSignalHandlers()
    try await stream.startCapture()
    while !output.isStopped() {
        try await Task<Never, Never>.sleep(nanoseconds: 100_000_000)
    }
    _ = stopSignalHandlers
    try await stream.stopCapture()
}

private func doubleArgument(_ arguments: [String], _ index: Int, _ name: String) throws -> Double {
    guard arguments.indices.contains(index), let value = Double(arguments[index]) else {
        throw HelperError.usage("Missing or invalid \(name)")
    }
    return value
}

private func intArgument(_ arguments: [String], _ index: Int, _ name: String) throws -> Int {
    guard arguments.indices.contains(index), let value = Int(arguments[index]) else {
        throw HelperError.usage("Missing or invalid \(name)")
    }
    return value
}

@main
private struct OKMacOSHelper {
    @MainActor
    static func main() async {
        do {
            // Establish the process-wide WindowServer connection before any
            // ScreenCaptureKit content filter is constructed.
            _ = NSApplication.shared
            let arguments = Array(CommandLine.arguments.dropFirst())
            guard let command = arguments.first else {
                throw HelperError.usage("Usage: ok-macos-helper <command> [arguments]")
            }
            switch command {
            case "permissions":
                try writeJSON(permissionStatus(prompt: arguments.contains("--prompt")))
            case "list":
                try writeJSON(
                    try await windowRecords(onScreenOnly: !arguments.contains("--all"))
                )
            case "activate":
                try activate(pid: pid_t(intArgument(arguments, 1, "pid")))
            case "stream":
                let windowID = UInt32(try intArgument(arguments, 1, "window id"))
                let fps = arguments.count > 2 ? try intArgument(arguments, 2, "fps") : 30
                try await streamWindow(windowID: windowID, fps: fps)
            case "snapshot-stream":
                let windowID = UInt32(try intArgument(arguments, 1, "window id"))
                let fps = arguments.count > 2 ? try intArgument(arguments, 2, "fps") : 5
                try await snapshotStream(windowID: windowID, fps: fps)
            case "snapshot":
                let windowID = UInt32(try intArgument(arguments, 1, "window id"))
                guard arguments.count > 2 else {
                    throw HelperError.usage("Usage: snapshot <window id> <output.png>")
                }
                try await snapshotWindow(windowID: windowID, path: arguments[2])
            case "key":
                guard arguments.count >= 3 else {
                    throw HelperError.usage("Usage: key <down|up|press> <name> [down-ms]")
                }
                let action = arguments[1]
                let name = arguments[2]
                if action == "down" {
                    try keyEvent(name: name, down: true)
                } else if action == "up" {
                    try keyEvent(name: name, down: false)
                } else if action == "press" {
                    let milliseconds = arguments.count > 3
                        ? try intArgument(arguments, 3, "down milliseconds") : 20
                    try keyEvent(name: name, down: true)
                    usleep(useconds_t(max(0, milliseconds) * 1_000))
                    try keyEvent(name: name, down: false)
                } else {
                    throw HelperError.usage("Unknown key action: \(action)")
                }
            case "text":
                guard arguments.count >= 2 else {
                    throw HelperError.usage("Usage: text <value>")
                }
                try textEvent(arguments[1])
            case "mouse":
                guard arguments.count >= 2 else {
                    throw HelperError.usage(
                        "Usage: mouse <move|click|modified-click|down|up|scroll|swipe> ..."
                    )
                }
                let action = arguments[1]
                if action == "move" {
                    try postMouse(
                        x: doubleArgument(arguments, 2, "x"),
                        y: doubleArgument(arguments, 3, "y")
                    )
                } else if action == "click" {
                    let x = try doubleArgument(arguments, 2, "x")
                    let y = try doubleArgument(arguments, 3, "y")
                    let button = try mouseButton(arguments.count > 4 ? arguments[4] : "left")
                    let milliseconds = arguments.count > 5
                        ? try intArgument(arguments, 5, "down milliseconds") : 20
                    try postMouse(x: x, y: y)
                    try postMouse(x: x, y: y, button: button, down: true)
                    usleep(useconds_t(max(0, milliseconds) * 1_000))
                    try postMouse(x: x, y: y, button: button, down: false)
                } else if action == "modified-click" {
                    let x = try doubleArgument(arguments, 2, "x")
                    let y = try doubleArgument(arguments, 3, "y")
                    let button = try mouseButton(arguments.count > 4 ? arguments[4] : "left")
                    let milliseconds = arguments.count > 5
                        ? try intArgument(arguments, 5, "down milliseconds") : 20
                    let modifiers = arguments.count > 6
                        ? Array(arguments.dropFirst(6)) : []
                    try postModifiedClick(
                        x: x,
                        y: y,
                        button: button,
                        milliseconds: milliseconds,
                        modifiers: modifiers
                    )
                } else if action == "down" || action == "up" {
                    let x = try doubleArgument(arguments, 2, "x")
                    let y = try doubleArgument(arguments, 3, "y")
                    let button = try mouseButton(arguments.count > 4 ? arguments[4] : "left")
                    try postMouse(x: x, y: y, button: button, down: action == "down")
                } else if action == "scroll" {
                    let x = try doubleArgument(arguments, 2, "x")
                    let y = try doubleArgument(arguments, 3, "y")
                    let amount = try intArgument(arguments, 4, "scroll amount")
                    try postMouse(x: x, y: y)
                    guard let event = CGEvent(
                        scrollWheelEvent2Source: nil,
                        units: .line,
                        wheelCount: 1,
                        wheel1: Int32(amount),
                        wheel2: 0,
                        wheel3: 0
                    ) else {
                        throw HelperError.stream("Failed to create scroll event")
                    }
                    event.post(tap: .cghidEventTap)
                } else if action == "swipe" {
                    let x1 = try doubleArgument(arguments, 2, "x1")
                    let y1 = try doubleArgument(arguments, 3, "y1")
                    let x2 = try doubleArgument(arguments, 4, "x2")
                    let y2 = try doubleArgument(arguments, 5, "y2")
                    let duration = try intArgument(arguments, 6, "duration milliseconds")
                    let steps = max(1, duration / 16)
                    try postMouse(x: x1, y: y1)
                    try postMouse(x: x1, y: y1, down: true)
                    for step in 1...steps {
                        let progress = Double(step) / Double(steps)
                        let x = x1 + (x2 - x1) * progress
                        let y = y1 + (y2 - y1) * progress
                        try postMouse(x: x, y: y, down: true, dragged: true)
                        usleep(16_000)
                    }
                    try postMouse(x: x2, y: y2, down: false)
                } else {
                    throw HelperError.usage("Unknown mouse action: \(action)")
                }
            default:
                throw HelperError.usage("Unknown command: \(command)")
            }
        } catch {
            FileHandle.standardError.write(Data("\(error)\n".utf8))
            Foundation.exit(1)
        }
    }
}
