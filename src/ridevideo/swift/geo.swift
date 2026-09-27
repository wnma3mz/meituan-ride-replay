import Foundation
import CoreLocation
let lat = Double(CommandLine.arguments[1])!, lon = Double(CommandLine.arguments[2])!
var done = false
CLGeocoder().reverseGeocodeLocation(CLLocation(latitude: lat, longitude: lon), preferredLocale: Locale(identifier: "zh_CN")) { pm, err in
    defer { done = true }
    if let err { print("ERR \(err.localizedDescription)"); return }
    guard let p = pm?.first else { print("ERR none"); return }
    let parts = [p.locality, p.subLocality, p.thoroughfare, p.name].compactMap { $0 }
    print(parts.joined(separator: " | "))
}
let dl = Date().addingTimeInterval(20)
while !done && Date() < dl { RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.05)) }
