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
        const val PREFS = "jarvis"
    }

    lateinit var webView: WebView
    lateinit var bridge: JarvisBridge
    private val prefs by lazy { getSharedPreferences(PREFS, Context.MODE_PRIVATE) }
    private var pendingTalk = false

    val serverUrl: String get() = prefs.getString("server_url", "")!!.trimEnd('/')
    val token: String get() = prefs.getString("token", "")!!
    val shareContacts: Boolean get() = prefs.getBoolean("share_contacts", false)

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
        val contactsBox = CheckBox(this).apply {
            text = "Handy-Kontakte mit Jarvis teilen"
            isChecked = shareContacts
        }
        layout.addView(TextView(this).apply { text = "Server-Adresse" })
        layout.addView(urlField)
        layout.addView(TextView(this).apply { text = "Token" })
        layout.addView(tokenField)
        layout.addView(contactsBox)

        AlertDialog.Builder(this)
            .setTitle("Jarvis verbinden")
            .setView(layout)
            .setCancelable(serverUrl.isNotEmpty())
            .setPositiveButton("Speichern") { _, _ ->
                var url = urlField.text.toString().trim().trimEnd('/')
                if (url.isNotEmpty() && !url.startsWith("http")) url = "https://$url"
                prefs.edit()
                    .putString("server_url", url)
                    .putString("token", tokenField.text.toString().trim())
                    .putBoolean("share_contacts", contactsBox.isChecked)
                    .putLong("contacts_synced", 0)
                    .apply()
                if (url.isEmpty()) showSetup("Bitte eine Server-Adresse eingeben.") else loadApp()
            }
            .show()
    }

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
            if (request.isForMainFrame) {
                showSetup("Jarvis nicht erreichbar (${error.description}). Läuft der Server, ist Tailscale an?")
            }
        }
    }
}
