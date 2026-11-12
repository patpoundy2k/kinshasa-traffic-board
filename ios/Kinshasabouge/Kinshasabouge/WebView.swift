import Combine
import SwiftUI
import WebKit

/// WKWebView wrapper for the live board. Mirrors the Android app:
/// loads https://kinshasabouge.com/ over HTTPS, JavaScript on (default),
/// localStorage persists between launches (default WKWebsiteDataStore),
/// no geolocation, in-app navigation for http/https, tel:/mailto:/sms:
/// links open in the system apps. Shows the offline screen only when
/// the main-frame load fails (network error or HTTP ≥ 400).
struct WebView: UIViewRepresentable {
    static let siteURL = URL(string: "https://kinshasabouge.com/")!

    /// 0.0 – 1.0, driven by the web view's estimatedProgress.
    @Binding var progress: Double
    /// True while the main-frame load has failed.
    @Binding var isOffline: Bool
    /// Increment to reload the site (retry button).
    @Binding var reloadToken: Int

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        // JavaScript on (default) — the board needs it. localStorage
        // persists via the default (persistent) website data store.
        config.websiteDataStore = .default()
        config.defaultWebpagePreferences.allowsContentJavaScript = true

        let webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = context.coordinator
        webView.allowsBackForwardNavigationGestures = true

        // Progress tracking (Combine publisher over KVO — no manual
        // observer registration/removal to get wrong).
        webView.publisher(for: \.estimatedProgress, options: [.initial, .new])
            .receive(on: DispatchQueue.main)
            .sink { [weak coordinator = context.coordinator] value in
                coordinator?.parent.progress = value
            }
            .store(in: &context.coordinator.cancellables)

        webView.load(URLRequest(url: Self.siteURL))
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        if context.coordinator.lastReloadToken != reloadToken {
            context.coordinator.lastReloadToken = reloadToken
            webView.load(URLRequest(url: Self.siteURL))
        }
    }

    func makeCoordinator() -> Coordinator {
        Coordinator(parent: self)
    }

    final class Coordinator: NSObject, WKNavigationDelegate {
        var parent: WebView
        var lastReloadToken = 0
        var cancellables = Set<AnyCancellable>()
        private var loadFailed = false

        init(parent: WebView) {
            self.parent = parent
        }

        // MARK: - Navigation policy

        func webView(
            _ webView: WKWebView,
            decidePolicyFor navigationAction: WKNavigationAction,
            decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
        ) {
            guard let url = navigationAction.request.url,
                  let scheme = url.scheme else {
                decisionHandler(.cancel)
                return
            }
            // tel:/mailto:/sms: open in the system apps.
            if scheme == "tel" || scheme == "mailto" || scheme == "sms" {
                if UIApplication.shared.canOpenURL(url) {
                    UIApplication.shared.open(url)
                }
                decisionHandler(.cancel)
                return
            }
            decisionHandler(.allow) // http/https stay inside the WebView
        }

        func webView(
            _ webView: WKWebView,
            decidePolicyFor navigationResponse: WKNavigationResponse,
            decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void
        ) {
            // HTTP error on the main frame -> offline screen.
            if navigationResponse.isForMainFrame,
               let http = navigationResponse.response as? HTTPURLResponse,
               http.statusCode >= 400 {
                loadFailed = true
                DispatchQueue.main.async { self.parent.isOffline = true }
                decisionHandler(.cancel)
                return
            }
            decisionHandler(.allow)
        }

        // MARK: - Load lifecycle

        func webView(
            _ webView: WKWebView,
            didStartProvisionalNavigation navigation: WKNavigation!
        ) {
            loadFailed = false
            DispatchQueue.main.async { self.parent.isOffline = false }
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            if !loadFailed {
                DispatchQueue.main.async {
                    self.parent.isOffline = false
                    self.parent.progress = 1.0
                }
            }
        }

        func webView(
            _ webView: WKWebView,
            didFailProvisionalNavigation navigation: WKNavigation!,
            withError error: Error
        ) {
            showOffline()
        }

        func webView(
            _ webView: WKWebView,
            didFail navigation: WKNavigation!,
            withError error: Error
        ) {
            showOffline()
        }

        private func showOffline() {
            loadFailed = true
            DispatchQueue.main.async { self.parent.isOffline = true }
        }
    }
}
