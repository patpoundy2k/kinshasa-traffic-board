import SwiftUI

/// Kinshasabouge — thin wrapper around the live Kinshasa Traffic Board
/// at https://kinshasabouge.com/ (French-first bilingual board: live map,
/// roadworks, flights, weather, radio). The site does all the work; the
/// app just shows it full-screen, like the Android app.
@main
struct KinshasabougeApp: App {
    var body: some Scene {
        WindowGroup {
            ContentView()
        }
    }
}
