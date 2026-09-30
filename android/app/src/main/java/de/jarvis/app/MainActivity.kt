package de.jarvis.app

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.app.AlertDialog
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Bundle
import android.text.InputType
import android.view.ViewGroup
import android.webkit.PermissionRequest
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast

/**
 * Jarvis-App: lädt die Jarvis-Weboberfläche deines Servers und ergänzt sie um
 * native Fähigkeiten (Android-Spracherkennung, Sprachausgabe, Kontakte, Aktionen).
 */
class MainActivity : Activity() {

    companion object {
        const val ACTION_TALK = "de.jarvis.app.TALK"
        const val ACTION_SETTINGS = "de.jarvis.app.SETTINGS"
        const val LOAD_TIMEOUT_MS = 20_000L
        const val REQ_AUDIO = 1
        const val REQ_CONTACTS = 2
        const val REQ_TERMUX = 3
        const val REQ_LOCATION = 4
        const val PREFS = "jarvis"
        const val LOCAL_URL = "http://127.0.0.1:8080"
        const val TERMUX = "com.termux"
        const val TERMUX_RUN_COMMAND = "com.termux.permission.RUN_COMMAND"
        const val TERMUX_START_SCRIPT = "/data/data/com.termux/files/home/jarvis-start.sh"
    }

    lateinit var webView: WebView
    lateinit var bridge: JarvisBridge
    private val prefs by lazy { getSharedPreferences(PREFS, Context.MODE_PRIVATE) }
    private var pendingTalk = false
    private var startAttempts = 0
    private var pageLoaded = false
    private var mainFrameError = false
    private val watchdog = Runnable {
        if (!pageLoaded) {
            webView.stopLoading()
            if (localMode) handleLocalServerDown()
            else showError("Keine Antwort von Jarvis",
                "$serverUrl antwortet nicht (Zeitüberschreitung). Läuft der Server? Ist Tailscale auf dem Handy verbunden?")
        }
    }
    private val handler = android.os.Handler(android.os.Looper.getMainLooper())

    val serverUrl: String get() = prefs.getString("server_url", "")!!.trimEnd('/')
    val token: String get() = prefs.getString("token", "")!!
    val shareContacts: Boolean get() = prefs.getBoolean("share_contacts", false)
    val localMode: Boolean get() = prefs.getBoolean("local_mode", false)
    val shareLocation: Boolean get() = prefs.getBoolean("share_location", false)

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        webView = WebView(this)
        webView.layoutParams = ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
        webView.setBackgroundColor(0xFF0B1220.toInt())
        setContentView(webView)

        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            mediaPlaybackRequiresUserGesture = false
        }
        bridge = JarvisBridge(this)
        webView.addJavascriptInterface(bridge, "JarvisAndroid")
        webView.webViewClient = JarvisWebClient()
        webView.webChromeClient = object : WebChromeClient() {
            // Fallback-Aufnahme im Web (MediaRecorder) braucht Mikrofon-Freigabe
            override fun onPermissionRequest(request: PermissionRequest) {
                if (hasPermission(Manifest.permission.RECORD_AUDIO)) request.grant(request.resources)
                else request.deny()
            }
        }

        pendingTalk = isTalkIntent(intent)
        if (serverUrl.isEmpty()) showSetup() else {
            loadApp()
            if (isSettingsIntent(intent)) showSetup()
        }
        if (!hasPermission(Manifest.permission.RECORD_AUDIO)) {
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), REQ_AUDIO)
        }
    }

    private fun isSettingsIntent(i: Intent?): Boolean = i?.action == ACTION_SETTINGS

    private fun isTalkIntent(i: Intent?): Boolean =
        i?.action in setOf(ACTION_TALK, Intent.ACTION_ASSIST, Intent.ACTION_VOICE_COMMAND)

    fun loadApp() {
        startAttempts = 0
        if (localMode && !hasPermission(TERMUX_RUN_COMMAND) && isTermuxInstalled()) {
            requestPermissions(arrayOf(TERMUX_RUN_COMMAND), REQ_TERMUX)
        }
        showStatus("Verbinde mit Jarvis …", esc(serverUrl))
        loadServer(withVoice = pendingTalk)
        pendingTalk = false
        maybeSyncContacts()
    }

    /** Lädt die Jarvis-Oberfläche und überwacht, ob sie wirklich ankommt. */
    fun loadServer(withVoice: Boolean = false) {
        val url = Uri.parse("$serverUrl/").buildUpon()
            .appendQueryParameter("token", token)
            .apply { if (withVoice) appendQueryParameter("voice", "1") }
            .build().toString()
        pageLoaded = false
        mainFrameError = false
        handler.removeCallbacks(watchdog)
        handler.postDelayed(watchdog, LOAD_TIMEOUT_MS)
        // loadDataWithBaseURL (Statusseite) kurz rendern lassen, dann laden
        handler.post { webView.loadUrl(url) }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        if (isSettingsIntent(intent)) {
            showSetup()
            return
        }
        if (isTalkIntent(intent)) {
            webView.evaluateJavascript("window.JarvisNative && window.JarvisNative.listen && window.JarvisNative.listen()", null)
        }
    }

    @Deprecated("Deprecated in Java")
    @Suppress("DEPRECATION")
    override fun onBackPressed() {
        if (webView.canGoBack()) webView.goBack() else super.onBackPressed()
    }

    override fun onDestroy() {
        handler.removeCallbacksAndMessages(null)
        bridge.shutdown()
        webView.destroy()
        super.onDestroy()
    }

    // ------------------------------------------------------------ Einrichtung
    fun showSetup(message: String? = null) {
        val pad = (16 * resources.displayMetrics.density).toInt()
        val layout = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad / 2, pad, 0)
        }
        if (message != null) layout.addView(TextView(this).apply { text = message; setPadding(0, 0, 0, pad / 2) })
        val urlField = EditText(this).apply {
            hint = "https://jarvis.dein-tailnet.ts.net"
            inputType = InputType.TYPE_TEXT_VARIATION_URI
            setText(serverUrl)
        }
        val tokenField = EditText(this).apply {
            hint = "Token (jarvis token)"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
            setText(token)
        }
        val localBox = CheckBox(this).apply {
            text = "Jarvis läuft auf diesem Handy (Termux)"
            isChecked = localMode
        }
        localBox.setOnCheckedChangeListener { _, checked ->
            urlField.isEnabled = !checked
            if (checked) urlField.setText(LOCAL_URL)
        }
        urlField.isEnabled = !localMode
        val contactsBox = CheckBox(this).apply {
            text = "Handy-Kontakte mit Jarvis teilen"
            isChecked = shareContacts
        }
        layout.addView(localBox)
        layout.addView(TextView(this).apply { text = "Server-Adresse" })
        layout.addView(urlField)
        layout.addView(TextView(this).apply { text = "Token" })
        layout.addView(tokenField)
        layout.addView(contactsBox)
        val locationBox = CheckBox(this).apply {
            text = "Standort mit Jarvis teilen (bei jeder Anfrage aus der App)"
            isChecked = shareLocation
        }
        layout.addView(locationBox)

        AlertDialog.Builder(this)
            .setTitle("Jarvis verbinden")
            .setView(layout)
            .setCancelable(serverUrl.isNotEmpty())
            .setNeutralButton("Anleitung") { _, _ ->
                openUrl("https://github.com/Sommer2019/jarvis#jarvis-komplett-auf-dem-handy")
            }
            .setPositiveButton("Speichern") { _, _ ->
                var url = urlField.text.toString().trim().trimEnd('/')
                if (localBox.isChecked) url = LOCAL_URL
                if (url.isNotEmpty() && !url.startsWith("http")) url = "https://$url"
                if (localBox.isChecked && !isTermuxInstalled()) {
                    AlertDialog.Builder(this)
                        .setTitle("Termux fehlt")
                        .setMessage("Für Jarvis auf dem Handy brauchst du die App Termux (aus F-Droid) und " +
                            "einmalig das Installationsskript. Anleitung öffnen?")
                        .setPositiveButton("Öffnen") { _, _ ->
                            openUrl("https://github.com/Sommer2019/jarvis#jarvis-komplett-auf-dem-handy")
                        }
                        .setNegativeButton("Später", null)
                        .show()
                }
                prefs.edit()
                    .putBoolean("local_mode", localBox.isChecked)
                    .putString("server_url", url)
                    .putString("token", tokenField.text.toString().trim())
                    .putBoolean("share_contacts", contactsBox.isChecked)
                    .putBoolean("share_location", locationBox.isChecked)
                    .putLong("contacts_synced", 0)
                    .apply()
                if (locationBox.isChecked && !hasPermission(Manifest.permission.ACCESS_FINE_LOCATION)) {
                    requestPermissions(arrayOf(Manifest.permission.ACCESS_FINE_LOCATION,
                        Manifest.permission.ACCESS_COARSE_LOCATION), REQ_LOCATION)
                }
                if (url.isEmpty()) showSetup("Bitte eine Server-Adresse eingeben.") else loadApp()
            }
            .show()
    }

    // ------------------------------------------------ Jarvis auf dem Handy
    fun isTermuxInstalled(): Boolean = try {
        packageManager.getPackageInfo(TERMUX, 0); true
    } catch (e: PackageManager.NameNotFoundException) { false }

    /** Startet ~/jarvis-start.sh in Termux (RUN_COMMAND-Schnittstelle von Termux). */
    fun startLocalServer(): Boolean {
        if (!isTermuxInstalled()) return false
        if (!hasPermission(TERMUX_RUN_COMMAND)) {
            requestPermissions(arrayOf(TERMUX_RUN_COMMAND), REQ_TERMUX)
            return false
        }
        val i = Intent().setClassName(TERMUX, "com.termux.app.RunCommandService")
            .setAction("com.termux.RUN_COMMAND")
            .putExtra("com.termux.RUN_COMMAND_PATH", TERMUX_START_SCRIPT)
            .putExtra("com.termux.RUN_COMMAND_BACKGROUND", true)
        return try {
            startForegroundService(i)
            true
        } catch (e: Exception) {
            false
        }
    }

    private fun esc(t: String) = t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    /** Einfache Seite mit Titel, Text und Knöpfen (Knöpfe rufen die JS-Brücke auf). */
    private fun showPage(title: String, text: String, busy: Boolean, termuxButton: Boolean) {
        val btn = "display:block;width:100%;max-width:320px;margin:8px auto;padding:14px;border-radius:12px;" +
            "border:1px solid #22304a;background:#121a2b;color:#e6edf7;font-size:16px"
        val buttons = buildString {
            append("""<button style="$btn;background:#1f7cff;border:0" onclick="JarvisAndroid.retry()">Erneut versuchen</button>""")
            if (termuxButton) append("""<button style="$btn" onclick="JarvisAndroid.startTermux()">Jarvis in Termux starten</button>""")
            append("""<button style="$btn" onclick="JarvisAndroid.openSettings()">Einstellungen</button>""")
        }
        val dot = if (busy) """<div style="width:64px;height:64px;border-radius:50%;background:#36c2ff;opacity:.8;margin:0 auto 24px;animation:p 1.2s infinite"></div>"""
            else """<div style="font-size:48px;margin-bottom:12px">⚠️</div>"""
        val html = """<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head>
            <body style="background:#0b1220;color:#e6edf7;font-family:sans-serif;margin:0;padding:32px 20px;
            min-height:90vh;display:flex;flex-direction:column;justify-content:center;text-align:center">
            $dot<h2 style="margin:0 0 12px">$title</h2><p style="color:#8a97ad;line-height:1.5">$text</p>
            <div style="margin-top:24px">$buttons</div>
            <style>@keyframes p{50%{opacity:.3}}</style></body></html>"""
        webView.loadDataWithBaseURL(null, html, "text/html", "utf-8", null)
    }

    private fun showStatus(title: String, text: String) = showPage(title, text, busy = true, termuxButton = false)

    fun showError(title: String, text: String) {
        handler.removeCallbacks(watchdog)
        showPage(esc(title), esc(text), busy = false, termuxButton = localMode)
    }

    /** Lokaler Server nicht erreichbar → per Termux starten und bis ~90 s neu versuchen. */
    private fun handleLocalServerDown() {
        if (startAttempts == 0) {
            val started = startLocalServer()
            if (!started && !isTermuxInstalled()) {
                showError("Termux fehlt", "Für „Jarvis läuft auf diesem Handy“ brauchst du Termux (F-Droid) und " +
                    "einmalig termux/install.sh. Oder in den Einstellungen die Adresse deines Servers eintragen.")
                return
            }
            if (!started && !hasPermission(TERMUX_RUN_COMMAND)) {
                showError("Jarvis läuft nicht", "Die App darf Jarvis noch nicht selbst starten. Entweder in Termux " +
                    "~/jarvis-start.sh ausführen und dann „Erneut versuchen“ – oder in den Android-Einstellungen der " +
                    "Jarvis-App die Berechtigung „Befehle in Termux ausführen“ erlauben.")
                return
            }
        }
        startAttempts++
        if (startAttempts > 20) {
            showError("Jarvis startet nicht", "Öffne Termux und führe ~/jarvis-start.sh aus. Fehler stehen in ~/jarvis.log " +
                "(anzeigen: tail -30 ~/jarvis.log).")
            return
        }
        showStatus("Jarvis startet …", "Der Assistent wird auf deinem Handy hochgefahren (Versuch $startAttempts von 20).")
        handler.postDelayed({ loadServer() }, 3000)
    }

    private fun openUrl(url: String) = try {
        startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
    } catch (e: ActivityNotFoundException) { }

    // --------------------------------------------------------------- Kontakte
    private fun maybeSyncContacts() {
        if (!shareContacts) return
        val last = prefs.getLong("contacts_synced", 0)
        if (System.currentTimeMillis() - last < 12 * 3600 * 1000L) return
        if (!hasPermission(Manifest.permission.READ_CONTACTS)) {
            requestPermissions(arrayOf(Manifest.permission.READ_CONTACTS), REQ_CONTACTS)
            return
        }
        bridge.syncContacts()
    }

    fun markContactsSynced() {
        prefs.edit().putLong("contacts_synced", System.currentTimeMillis()).apply()
    }

    fun hasPermission(p: String) = checkSelfPermission(p) == PackageManager.PERMISSION_GRANTED

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        val granted = grantResults.isNotEmpty() && grantResults[0] == PackageManager.PERMISSION_GRANTED
        when (requestCode) {
            REQ_CONTACTS -> if (granted) bridge.syncContacts()
            else Toast.makeText(this, "Ohne Kontakt-Zugriff kann Jarvis deine Handy-Kontakte nicht nutzen.", Toast.LENGTH_LONG).show()
            REQ_TERMUX -> if (granted && localMode) loadApp()
            REQ_LOCATION -> if (!granted && !hasPermission(Manifest.permission.ACCESS_COARSE_LOCATION))
                Toast.makeText(this, "Ohne Standort-Freigabe kann Jarvis deinen Ort nicht nutzen.", Toast.LENGTH_LONG).show()
            REQ_AUDIO -> if (!granted) Toast.makeText(this, "Ohne Mikrofon keine Sprachsteuerung.", Toast.LENGTH_LONG).show()
        }
    }

    // ------------------------------------------------------------- WebView
    inner class JarvisWebClient : WebViewClient() {
        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
            val uri = request.url
            val sameServer = serverUrl.isNotEmpty() && uri.toString().startsWith(serverUrl)
            if (sameServer) return false
            // tel:, sms:, wa.me, Maps & externe Links → passende App öffnen
            return try {
                startActivity(Intent(Intent.ACTION_VIEW, uri))
                true
            } catch (e: ActivityNotFoundException) {
                true
            }
        }

        override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
            if (!request.isForMainFrame) return
            mainFrameError = true
            handler.removeCallbacks(watchdog)
            if (localMode) handleLocalServerDown()
            else showError("Jarvis nicht erreichbar", "${error.description} – Adresse: $serverUrl. " +
                "Läuft der Server? Ist Tailscale auf dem Handy verbunden?")
        }

        override fun onReceivedHttpError(view: WebView, request: WebResourceRequest, response: WebResourceResponse) {
            if (!request.isForMainFrame) return
            mainFrameError = true
            showError("Fehler ${response.statusCode}", "Der Server unter $serverUrl antwortet mit " +
                "„${response.reasonPhrase}“. Ist das wirklich die Jarvis-Adresse?")
        }

        override fun onPageFinished(view: WebView, url: String?) {
            if (url == null || !url.startsWith(serverUrl) || mainFrameError) return
            // Prüfen, ob wirklich die Jarvis-Oberfläche angekommen ist
            view.evaluateJavascript("(function(){return !!document.getElementById('mic')})()") { ok ->
                if (ok == "true") {
                    pageLoaded = true
                    handler.removeCallbacks(watchdog)
                } else {
                    view.evaluateJavascript("(document.title||'')+' | '+(document.body?document.body.innerText.slice(0,200):'')") { t ->
                        showError("Unerwartete Seite", "Unter $serverUrl kam nicht die Jarvis-Oberfläche an: $t")
                    }
                }
            }
        }
    }
}
