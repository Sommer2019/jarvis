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
        const val REQ_AUDIO = 1
        const val REQ_CONTACTS = 2
        const val REQ_TERMUX = 3
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
    private val handler = android.os.Handler(android.os.Looper.getMainLooper())

    val serverUrl: String get() = prefs.getString("server_url", "")!!.trimEnd('/')
    val token: String get() = prefs.getString("token", "")!!
    val shareContacts: Boolean get() = prefs.getBoolean("share_contacts", false)
    val localMode: Boolean get() = prefs.getBoolean("local_mode", false)

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
        if (serverUrl.isEmpty()) showSetup() else loadApp()
        if (!hasPermission(Manifest.permission.RECORD_AUDIO)) {
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), REQ_AUDIO)
        }
    }

    private fun isTalkIntent(i: Intent?): Boolean =
        i?.action in setOf(ACTION_TALK, Intent.ACTION_ASSIST, Intent.ACTION_VOICE_COMMAND)

    fun loadApp() {
        startAttempts = 0
        if (localMode && !hasPermission(TERMUX_RUN_COMMAND) && isTermuxInstalled()) {
            requestPermissions(arrayOf(TERMUX_RUN_COMMAND), REQ_TERMUX)
        }
        val url = Uri.parse("$serverUrl/").buildUpon()
            .appendQueryParameter("token", token)
            .apply { if (pendingTalk) appendQueryParameter("voice", "1") }
            .build().toString()
        pendingTalk = false
        webView.loadUrl(url)
        maybeSyncContacts()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
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
                    .putLong("contacts_synced", 0)
                    .apply()
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

    private fun showStatus(title: String, text: String) {
        val html = """<html><body style="background:#0b1220;color:#e6edf7;font-family:sans-serif;
            display:flex;flex-direction:column;justify-content:center;align-items:center;height:90vh;text-align:center;padding:24px">
            <div style="width:64px;height:64px;border-radius:50%;background:#36c2ff;opacity:.8;margin-bottom:24px;
            animation:p 1.2s infinite"></div><h2>$title</h2><p style="color:#8a97ad">$text</p>
            <style>@keyframes p{50%{opacity:.3}}</style></body></html>"""
        webView.loadDataWithBaseURL(null, html, "text/html", "utf-8", null)
    }

    /** Lokaler Server nicht erreichbar → per Termux starten und bis ~90 s neu versuchen. */
    private fun handleLocalServerDown() {
        if (startAttempts == 0) {
            val started = startLocalServer()
            if (!started && !isTermuxInstalled()) {
                showSetup("Termux ist nicht installiert. Siehe „Anleitung“: Jarvis auf dem Handy einrichten.")
                return
            }
            if (!started && !hasPermission(TERMUX_RUN_COMMAND)) {
                showStatus("Berechtigung fehlt", "Bitte erlaube „Befehle in Termux ausführen“ – oder starte " +
                    "in Termux einmal <code>~/jarvis-start.sh</code>.")
            }
        }
        startAttempts++
        if (startAttempts > 30) {
            showSetup("Jarvis startet nicht. Öffne Termux und führe ~/jarvis-start.sh aus (Log: ~/jarvis.log).")
            return
        }
        showStatus("Jarvis startet …", "Der Assistent wird auf deinem Handy hochgefahren (Versuch $startAttempts).")
        handler.postDelayed({
            val url = Uri.parse("$serverUrl/").buildUpon().appendQueryParameter("token", token).build().toString()
            webView.loadUrl(url)
        }, 3000)
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
            if (localMode) handleLocalServerDown()
            else showSetup("Jarvis nicht erreichbar (${error.description}). Läuft der Server, ist Tailscale an?")
        }
    }
}
