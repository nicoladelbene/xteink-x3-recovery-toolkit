// mount-blocker: refuses automount of any NON-internal disk while running,
// so a possibly-failing source card is never mounted (and never written) by macOS.
// Uses the public DiskArbitration mount-approval callback. No root required.
// Build: swiftc -O scripts/macos/mount-blocker.swift -o mount-blocker
// Run:   ./mount-blocker            (Ctrl-C to stop; mounts resume normally)
import Foundation
import DiskArbitration

guard let session = DASessionCreate(kCFAllocatorDefault) else { fatalError("DASessionCreate failed") }

func ts() -> String { ISO8601DateFormatter().string(from: Date()) }

let approval: DADiskMountApprovalCallback = { disk, _ in
    let bsd = DADiskGetBSDName(disk).map { String(cString: $0) } ?? "?"
    let desc = DADiskCopyDescription(disk) as? [String: Any] ?? [:]
    let isInternal = (desc[kDADiskDescriptionDeviceInternalKey as String] as? Bool) ?? false
    if isInternal { return nil }  // never interfere with the Mac's own disks
    let model = desc[kDADiskDescriptionDeviceModelKey as String] ?? "?"
    let size = desc[kDADiskDescriptionMediaSizeKey as String] ?? "?"
    print("\(ts()) BLOCKED mount of /dev/\(bsd) model=\(model) size=\(size)")
    fflush(stdout)
    return Unmanaged.passRetained(DADissenterCreate(kCFAllocatorDefault, DAReturn(kDAReturnNotPermitted), "blocked by x3toolkit mount-blocker" as CFString))
}

let appeared: DADiskAppearedCallback = { disk, _ in
    let bsd = DADiskGetBSDName(disk).map { String(cString: $0) } ?? "?"
    let desc = DADiskCopyDescription(disk) as? [String: Any] ?? [:]
    let isInternal = (desc[kDADiskDescriptionDeviceInternalKey as String] as? Bool) ?? false
    if !isInternal { print("\(ts()) APPEARED /dev/\(bsd)"); fflush(stdout) }
}

DARegisterDiskMountApprovalCallback(session, nil, approval, nil)
DARegisterDiskAppearedCallback(session, nil, appeared, nil)
DASessionScheduleWithRunLoop(session, CFRunLoopGetMain(), CFRunLoopMode.defaultMode.rawValue)
print("\(ts()) mount-blocker active: external disks will NOT be mounted. Ctrl-C to stop.")
fflush(stdout)
signal(SIGINT) { _ in print("\nmount-blocker stopped"); exit(0) }
CFRunLoopRun()
