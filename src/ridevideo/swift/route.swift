import Foundation
import MapKit

// Infer a plausible bike path for rides whose detail response carried only the
// two endpoints.  MapKit has no cycling mode, so prefer walking: it follows the
// same corridors a bike uses and ignores one-way restrictions.  Fall back to
// driving with highways and tolls avoided when no walking route exists (long
// intercity legs), because plain driving would route a bicycle onto an
// expressway through toll gates.
guard CommandLine.arguments.count >= 5 else {
    fputs("usage: route START_LAT START_LON END_LAT END_LON [walking|driving]\n", stderr)
    exit(2)
}
let source = CLLocationCoordinate2D(latitude: Double(CommandLine.arguments[1])!,
                                    longitude: Double(CommandLine.arguments[2])!)
let destination = CLLocationCoordinate2D(latitude: Double(CommandLine.arguments[3])!,
                                         longitude: Double(CommandLine.arguments[4])!)
let preferred = CommandLine.arguments.count >= 6 ? CommandLine.arguments[5] : "walking"

func makeRequest(_ mode: String) -> MKDirections.Request {
    let request = MKDirections.Request()
    request.source = MKMapItem(placemark: MKPlacemark(coordinate: source))
    request.destination = MKMapItem(placemark: MKPlacemark(coordinate: destination))
    request.requestsAlternateRoutes = false
    if mode == "walking" {
        request.transportType = .walking
    } else {
        request.transportType = .automobile
        if #available(macOS 14.0, *) {
            request.highwayPreference = .avoid
            request.tollPreference = .avoid
        }
    }
    return request
}

func emit(_ route: MKRoute, mode: String) {
    let points = route.polyline.points()
    var coordinates: [[Double]] = []
    for index in 0..<route.polyline.pointCount {
        let c = points[index].coordinate
        coordinates.append([c.longitude, c.latitude])
    }
    let payload: [String: Any] = [
        "distanceMeters": route.distance,
        "mode": mode == "walking" ? "walking" : "driving-no-highway",
        "points": coordinates,
    ]
    if let data = try? JSONSerialization.data(withJSONObject: payload),
       let text = String(data: data, encoding: .utf8) {
        print(text)
    } else {
        fputs("ERROR\tencode\n", stderr)
    }
}

var finished = false
var lastError = "no route"

func attempt(_ modes: [String]) {
    guard let mode = modes.first else {
        fputs("ERROR\t\(lastError)\n", stderr)
        finished = true
        return
    }
    MKDirections(request: makeRequest(mode)).calculate { response, error in
        if let route = response?.routes.first {
            emit(route, mode: mode)
            finished = true
            return
        }
        lastError = error?.localizedDescription ?? "no route"
        attempt(Array(modes.dropFirst()))
    }
}

attempt(preferred == "walking" ? ["walking", "driving"] : ["driving", "walking"])

let deadline = Date().addingTimeInterval(90)
while !finished && Date() < deadline {
    RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.1))
}
if !finished {
    fputs("ERROR\ttimeout\n", stderr)
    exit(1)
}
