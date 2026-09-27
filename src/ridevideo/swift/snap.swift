import Foundation
import AppKit
import MapKit

// args: lat lon latMeters lonMeters width height scale dark poi out [lon lat ...]
let a = CommandLine.arguments
guard a.count >= 11 else { fputs("bad args\n", stderr); exit(2) }
let lat = Double(a[1])!, lon = Double(a[2])!
let latM = Double(a[3])!, lonM = Double(a[4])!
let w = Double(a[5])!, h = Double(a[6])!
let scale = Double(a[7])!
let dark = a[8] == "1"
let hidePOI = a[9] == "1"
let out = URL(fileURLWithPath: a[10])
let pts = Array(a.dropFirst(11))

if dark { NSApp?.appearance = NSAppearance(named: .darkAqua) }

let o = MKMapSnapshotter.Options()
o.region = MKCoordinateRegion(center: CLLocationCoordinate2D(latitude: lat, longitude: lon),
                              latitudinalMeters: latM, longitudinalMeters: lonM)
o.size = CGSize(width: w, height: h)
_ = scale
o.mapType = .standard
if hidePOI { o.pointOfInterestFilter = .excludingAll }
if dark { o.appearance = NSAppearance(named: .darkAqua) }

var done = false
MKMapSnapshotter(options: o).start(with: DispatchQueue.global(qos: .userInitiated)) { snap, err in
    defer { done = true }
    if let err { fputs("SNAPFAIL \(err.localizedDescription)\n", stderr); return }
    guard let snap else { fputs("SNAPFAIL nil\n", stderr); return }
    let img = snap.image
    guard let tiff = img.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff),
          let png = rep.representation(using: .png, properties: [:]) else {
        fputs("SNAPFAIL encode\n", stderr); return
    }
    try? png.write(to: out)
    fputs("PIXELS \(rep.pixelsWide)x\(rep.pixelsHigh)\n", stderr)
    // snapshot.point(for:) returns AppKit coordinates with the origin at the
    // BOTTOM-left and y growing upward.  The PNG we just wrote is consumed by
    // PIL, whose origin is TOP-left with y growing downward, so flip y here —
    // otherwise every route is mirrored north/south against its own basemap.
    // point(for:) works in image points; the PNG may be written at a different
    // pixel density, so convert before flipping.
    let pixelHeight = Double(rep.pixelsHigh)
    let scaleX = Double(rep.pixelsWide) / Double(o.size.width)
    let scaleY = pixelHeight / Double(o.size.height)
    var i = 0
    while i + 1 < pts.count {
        let p = snap.point(for: CLLocationCoordinate2D(latitude: Double(pts[i+1])!, longitude: Double(pts[i])!))
        print("\(Double(p.x) * scaleX),\(pixelHeight - Double(p.y) * scaleY)")
        i += 2
    }
}
let dl = Date().addingTimeInterval(45)
while !done && Date() < dl { RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.05)) }
if !done { fputs("SNAPFAIL timeout\n", stderr); exit(1) }
