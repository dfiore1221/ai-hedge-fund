import Foundation

func fail(_ message: String, code: Int32 = 1) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(code)
}

let arguments = Array(CommandLine.arguments.dropFirst())
guard !arguments.isEmpty else {
    fail("AIFundOS Scheduler requires a script path.", code: 64)
}

if arguments[0] == "--probe" {
    guard arguments.count >= 2 else { fail("Probe requires a directory path.", code: 64) }
    do {
        let entries = try FileManager.default.contentsOfDirectory(atPath: arguments[1])
        print("AIFundOS Scheduler can access the project directory (\(entries.count) entries).")
        exit(0)
    } catch {
        fail("AIFundOS Scheduler cannot access the project directory: \(error)", code: 77)
    }
}

let scriptPath = arguments[0]
let scriptArguments = Array(arguments.dropFirst())
let scriptURL = URL(fileURLWithPath: scriptPath)
guard FileManager.default.isExecutableFile(atPath: scriptPath) else {
    fail("AIFundOS Scheduler cannot execute \(scriptPath).", code: 78)
}

let process = Process()
process.executableURL = URL(fileURLWithPath: "/bin/zsh")
process.arguments = [scriptPath] + scriptArguments
process.currentDirectoryURL = scriptURL
    .deletingLastPathComponent()
    .deletingLastPathComponent()

do {
    try process.run()
    process.waitUntilExit()
    exit(process.terminationStatus)
} catch {
    fail("AIFundOS Scheduler failed to start the job: \(error)", code: 70)
}
