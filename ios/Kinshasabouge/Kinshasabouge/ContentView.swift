import SwiftUI

/// Single screen: the live board full-screen, a thin loading bar pinned
/// to the top while it loads, and the bilingual offline screen when the
/// main-frame load fails.
struct ContentView: View {
    @State private var progress: Double = 0
    @State private var isOffline = false
    @State private var reloadToken = 0

    var body: some View {
        ZStack {
            WebView(progress: $progress, isOffline: $isOffline, reloadToken: $reloadToken)
                .ignoresSafeArea()

            if isOffline {
                OfflineView { reloadToken += 1 }
            } else if progress < 1.0 {
                VStack {
                    ProgressView(value: progress)
                        .progressViewStyle(.linear)
                        .tint(Color("AccentColor"))
                        .frame(height: 4)
                    Spacer()
                }
                .ignoresSafeArea(edges: .top)
            }
        }
    }
}

/// Bilingual offline screen — same wording as the Android app
/// (French first).
struct OfflineView: View {
    var onRetry: () -> Void

    var body: some View {
        VStack(spacing: 20) {
            Text("📡 Pas de connexion Internet.\nVérifiez votre connexion, puis réessayez.\n\nNo internet connection.\nCheck your connection, then try again.")
                .multilineTextAlignment(.center)
                .font(.body)
                .foregroundColor(Color(red: 33 / 255, green: 33 / 255, blue: 33 / 255))
            Button("Réessayer / Retry", action: onRetry)
                .buttonStyle(.borderedProminent)
        }
        .padding(32)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(red: 250 / 255, green: 250 / 255, blue: 247 / 255))
    }
}
