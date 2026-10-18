import Cocoa
import WebKit

final class BolsaApp: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKDownloadDelegate {
    var window: NSWindow!
    var web: WKWebView!
    var backend: Process?
    var closing = false
    var ready = false
    var attempts = 0
    let root = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/Freqtrade Bolsa", isDirectory: true)
    var base = URL(string: "http://127.0.0.1:8081")!

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        let menu = NSMenu()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "Salir de Freqtrade Bolsa", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        let item = NSMenuItem()
        item.submenu = appMenu
        menu.addItem(item)
        let editItem = NSMenuItem(title: "Edición", action: nil, keyEquivalent: "")
        let editMenu = NSMenu(title: "Edición")
        for (title, action, key) in [("Copiar", "copy:", "c"), ("Pegar", "paste:", "v"), ("Seleccionar todo", "selectAll:", "a")] {
            editMenu.addItem(withTitle: title, action: Selector(action), keyEquivalent: key)
        }
        editItem.submenu = editMenu
        menu.addItem(editItem)
        NSApp.mainMenu = menu
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1250, height: 850), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "Freqtrade Bolsa · Alpaca Paper"
        window.minSize = NSSize(width: 700, height: 550)
        window.delegate = self
        web = WKWebView(frame: window.contentView!.bounds)
        web.autoresizingMask = [.width, .height]
        web.navigationDelegate = self
        window.contentView!.addSubview(web)
        web.loadHTMLString("<html style='background:#101715;color:#ecf1e9;font:22px Georgia'><body style='padding:60px'><h1>Freqtrade Bolsa</h1><p>Preparando panel y backtester…</p><p>Alpaca Paper · Sin dinero real</p></body></html>", baseURL: nil)
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        configure()
        probe(startIfMissing: true)
    }

    func configure() {
        if let data = try? Data(contentsOf: root.appendingPathComponent("settings.json")),
           let settings = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let port = settings["port"] as? Int, port > 0 && port <= 65535 {
            base = URL(string: "http://127.0.0.1:\(port)")!
        }
    }

    func probe(startIfMissing: Bool) {
        var request = URLRequest(url: base.appendingPathComponent("api/health"))
        request.timeoutInterval = 1
        URLSession.shared.dataTask(with: request) { [weak self] data, response, error in
            DispatchQueue.main.async {
                guard let self = self, !self.closing else { return }
                if let data = data,
                   let info = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                   info["application"] as? String == "freqtrade-equities",
                   info["protocol"] as? Int == 1, info["desktop"] as? Bool == true {
                    self.ready = true
                    self.web.load(URLRequest(url: self.base))
                    return
                }
                if startIfMissing {
                    self.startBackend()
                } else if let process = self.backend, !process.isRunning {
                    self.fail("Motor no pudo arrancar. Consulta ~/Library/Logs/Freqtrade Bolsa/backend.log. Puede existir otro bot en el mismo puerto.")
                } else if self.attempts >= 100 {
                    self.fail("Motor no responde. Consulta registro local antes de volver a abrir aplicación.")
                } else {
                    self.attempts += 1
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) { self.probe(startIfMissing: false) }
                }
            }
        }.resume()
    }

    func startBackend() {
        guard let resources = Bundle.main.resourceURL else { fail("Faltan recursos de aplicación."); return }
        let executable = resources.appendingPathComponent("backend/equities-backend")
        guard FileManager.default.isExecutableFile(atPath: executable.path) else { fail("Falta motor incluido en aplicación."); return }
        let process = Process()
        process.executableURL = executable
        process.currentDirectoryURL = root
        var environment = ProcessInfo.processInfo.environment
        environment["EQUITIES_HOME"] = root.path
        process.environment = environment
        do {
            try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
            backend = process
            try process.run()
            probe(startIfMissing: false)
        } catch {
            fail("No se pudo iniciar motor: \(error.localizedDescription)")
        }
    }

    func fail(_ message: String) {
        let alert = NSAlert()
        alert.messageText = "Freqtrade Bolsa"
        alert.informativeText = message
        alert.alertStyle = .warning
        alert.addButton(withTitle: "Cerrar")
        alert.runModal()
        NSApp.terminate(nil)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if closing { return .terminateLater }
        closing = true
        guard let process = backend, process.isRunning else { return .terminateNow }
        window.title = "Freqtrade Bolsa · Deteniendo Paper…"
        process.terminate()
        DispatchQueue.global().async {
            process.waitUntilExit()
            DispatchQueue.main.async { NSApp.reply(toApplicationShouldTerminate: true) }
        }
        return .terminateLater
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if navigationAction.shouldPerformDownload { decisionHandler(.download); return }
        guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
        if url.scheme == "about" || (url.host == base.host && url.port == base.port && url.scheme == "http") {
            decisionHandler(.allow)
        } else if url.scheme == "blob" {
            decisionHandler(.allow)
        } else {
            decisionHandler(.cancel)
        }
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) { download.delegate = self }
    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) { download.delegate = self }

    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = suggestedFilename
        panel.canCreateDirectories = true
        panel.beginSheetModal(for: window) { reply in completionHandler(reply == .OK ? panel.url : nil) }
    }
}

let app = NSApplication.shared
let delegate = BolsaApp()
app.delegate = delegate
app.run()
