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
import android.os.Build
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
        const val ACTION_ANSWER = "de.jarvis.app.ANSWER"
        const val ACTION_MESSAGE = "de.jarvis.app.MESSAGE"
        const val REQ_NOTIFY = 6
        @Volatile var visible = false
        const val LOAD_TIMEOUT_MS = 20_000L
        const val REQ_AUDIO = 1
        const val REQ_CONTACTS = 2
        const val REQ_TERMUX = 3
        const val REQ_LOCATION = 4
        const val REQ_CALENDAR = 5
        const val REQ_CALLS = 7
        const val PREFS = "jarvis"
        const val LOCAL_URL = "http://127.0.0.1:8080"
        const val TERMUX = "com.termux"
        const val TERMUX_RUN_COMMAND = "com.termux.permission.RUN_COMMAND"
        const val TERMUX_START_SCRIPT = "/data/data/com.termux/files/home/jarvis-start.sh"
    }

    lateinit var webView: WebView
    lateinit var bridge: JarvisBridge
    val prefs: android.content.SharedPreferences by lazy { getSharedPreferences(PREFS, Context.MODE_PRIVATE) }
    private var pendingTalk = false
    private var startAttempts = 0
    private var pageLoaded = false
    /** Angenommener Jarvis-Anruf / angetippte Nachricht, die an die Oberfläche geht, sobald sie bereit ist. */
    private var pendingJs: String? = null
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
    val shareCalendar: Boolean get() = prefs.getBoolean("share_calendar", false)

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
            override fun onConsoleMessage(msg: android.webkit.ConsoleMessage): Boolean {
                if (msg.messageLevel() == android.webkit.ConsoleMessage.MessageLevel.ERROR) {
                    consoleErrors.addLast("${msg.message()} (${msg.sourceId().substringAfterLast('/')}:${msg.lineNumber()})")
                    while (consoleErrors.size > 5) consoleErrors.removeFirst()
                }
                return false
            }

            override fun onPermissionRequest(request: PermissionRequest) {
                if (hasPermission(Manifest.permission.RECORD_AUDIO)) request.grant(request.resources)
                else request.deny()
            }
        }

        JarvisService.channels(this)
        JarvisService.start(this)
        takeCallIntent(intent)
        takeShareIntent(intent)
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

    /** Anruf angenommen / Nachricht angetippt → Text an die Weboberfläche übergeben. */
    private fun takeCallIntent(i: Intent?): Boolean {
        val text = i?.getStringExtra("text") ?: return false
        val fn = when (i.action) {
            ACTION_ANSWER -> "incomingCall"
            ACTION_MESSAGE -> "showMessage"
            else -> return false
        }
        getSystemService(android.app.NotificationManager::class.java).cancel(IncomingCallActivity.NOTIFICATION_ID)
        pendingJs = "window.JarvisNative && window.JarvisNative.$fn && window.JarvisNative.$fn(${org.json.JSONObject.quote(text)})"
        return true
    }

    /** „Teilen → Jarvis“: Dateien hochladen bzw. geteilten Text ins Eingabefeld übernehmen. */
    private fun takeShareIntent(i: Intent?): Boolean {
        if (i?.action != Intent.ACTION_SEND && i?.action != Intent.ACTION_SEND_MULTIPLE) return false
        @Suppress("DEPRECATION")
        val uris: List<Uri> = if (i.action == Intent.ACTION_SEND_MULTIPLE)
            i.getParcelableArrayListExtra<Uri>(Intent.EXTRA_STREAM) ?: emptyList()
        else listOfNotNull(i.getParcelableExtra<Uri>(Intent.EXTRA_STREAM))
        if (uris.isEmpty()) {
            val text = i.getStringExtra(Intent.EXTRA_TEXT) ?: return true
            pendingJs = "window.JarvisNative && window.JarvisNative.prefill && window.JarvisNative.prefill(${org.json.JSONObject.quote(text)})"
            deliverPendingJs()
            return true
        }
        if (serverUrl.isEmpty()) {
            Toast.makeText(this, "Bitte Jarvis zuerst einrichten.", Toast.LENGTH_LONG).show()
            return true
        }
        Toast.makeText(this, "Wird an Jarvis geschickt …", Toast.LENGTH_SHORT).show()
        Thread {
            val metas = org.json.JSONArray()
            for (u in uris.take(10)) {
                try { metas.put(Api.uploadUri(this, u)) } catch (e: Exception) {
                    runOnUiThread { Toast.makeText(this, "Hochladen fehlgeschlagen: ${e.message}", Toast.LENGTH_LONG).show() }
                }
            }
            if (metas.length() > 0) runOnUiThread {
                pendingJs = "window.JarvisNative && window.JarvisNative.filesShared && window.JarvisNative.filesShared(${metas})"
                deliverPendingJs()
            }
        }.start()
        return true
    }

    private fun deliverPendingJs() {
        val js = pendingJs ?: return
        if (!pageLoaded) return  // kommt nach dem Laden (checkUiReady)
        pendingJs = null
        webView.evaluateJavascript(js, null)
    }

    override fun onResume() {
        super.onResume()
        visible = true
        if (serverUrl.isNotEmpty()) maybeSyncCalendar()
    }

    override fun onPause() {
        visible = false
        super.onPause()
    }

    private fun isTalkIntent(i: Intent?): Boolean =
        i?.action in setOf(ACTION_TALK, Intent.ACTION_ASSIST, Intent.ACTION_VOICE_COMMAND)

    fun loadApp() {
        startAttempts = 0
        if (localMode && !hasPermission(TERMUX_RUN_COMMAND) && isTermuxInstalled()) {
            requestPermissions(arrayOf(TERMUX_RUN_COMMAND), REQ_TERMUX)
        }
        showStatus("Verbinde mit Jarvis …", esc(serverUrl))
        val voice = pendingTalk
        pendingTalk = false
        checkServer { loadServer(withVoice = voice) }
        maybeSyncContacts()
        maybeSyncCalendar()
    }

    /**
     * Prüft vor dem Laden direkt, ob der Server erreichbar ist und der Token stimmt –
     * so gibt es klare Fehlermeldungen statt einer leeren Seite.
     */
    private fun checkServer(onOk: () -> Unit) {
        val url = serverUrl
        Thread {
            val serverVersion = try {
                val h = java.net.URL("$url/api/health").openConnection() as java.net.HttpURLConnection
                h.connectTimeout = 5000
                h.readTimeout = 10000
                val body = h.inputStream.bufferedReader().readText()
                h.disconnect()
                org.json.JSONObject(body).optString("version", "")
            } catch (e: Exception) {
                null
            }
            val code = try {
                val c = java.net.URL("$url/api/phone/actions").openConnection() as java.net.HttpURLConnection
                c.connectTimeout = 5000
                c.readTimeout = 10000
                c.setRequestProperty("Authorization", "Bearer $token")
                c.setRequestProperty("X-Jarvis-App", bridge.version())
                val rc = c.responseCode
                c.disconnect()
                rc
            } catch (e: Exception) {
                -1
            }
            runOnUiThread {
                when {
                    code in 200..299 -> {
                        if (serverVersion == "") {
                            Toast.makeText(this, "Jarvis auf dem Server ist veraltet – bitte aktualisieren " +
                                "(Handy: ~/jarvis-update.sh)", Toast.LENGTH_LONG).show()
                        }
                        onOk()
                    }
                    code == 401 -> showError("Token passt nicht",
                        "Der Token in der App stimmt nicht mit dem Jarvis-Server überein. Den richtigen Token " +
                        "zeigt Termux mit ~/jarvis-token.sh (bzw. auf dem Server: jarvis token). " +
                        "Dann hier „Einstellungen“ → Token neu einfügen.")
                    code == -1 && localMode -> handleLocalServerDown()
                    code == -1 -> showError("Jarvis nicht erreichbar", "Keine Verbindung zu $url. " +
                        "Läuft der Server? Ist Tailscale auf dem Handy verbunden?")
                    else -> showError("Fehler $code", "Der Server unter $url antwortet unerwartet. " +
                        "Ist das wirklich die Jarvis-Adresse?")
                }
            }
        }.start()
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
        if (takeCallIntent(intent)) {
            deliverPendingJs()
            return
        }
        if (takeShareIntent(intent)) return
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
        val calendarBox = CheckBox(this).apply {
            text = "Handy-Kalender mit Jarvis teilen (lesen + Termine eintragen)"
            isChecked = shareCalendar
        }
        layout.addView(calendarBox)
        val backgroundBox = CheckBox(this).apply {
            text = "Im Hintergrund verbunden bleiben (Nachrichten & Anrufe von Jarvis, auch wenn die App zu ist)"
            isChecked = JarvisService.enabled(this@MainActivity)
        }
        layout.addView(backgroundBox)
        layout.addView(android.widget.Button(this).apply {
            text = "🔊 Stimme & Tempo einstellen"
            setOnClickListener { showVoiceSettings() }
        })
        layout.addView(android.widget.Button(this).apply {
            text = "🤖📞 Jarvis telefoniert selbst (Zweit-SIM)"
            setOnClickListener { showCallSettings() }
        })

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
                    .putBoolean("share_calendar", calendarBox.isChecked)
                    .putBoolean("background", backgroundBox.isChecked)
                    .putLong("calendar_synced", 0)
                    .putLong("contacts_synced", 0)
                    .apply()
                if (locationBox.isChecked && !hasPermission(Manifest.permission.ACCESS_FINE_LOCATION)) {
                    requestPermissions(arrayOf(Manifest.permission.ACCESS_FINE_LOCATION,
                        Manifest.permission.ACCESS_COARSE_LOCATION), REQ_LOCATION)
                }
                if (calendarBox.isChecked) requestCalendarPermission()
                if (backgroundBox.isChecked) enableBackground() else JarvisService.stop(this)
                if (url.isEmpty()) showSetup("Bitte eine Server-Adresse eingeben.") else loadApp()
            }
            .show()
    }

    /** Letzte JavaScript-Fehler (für die Fehlerseite). */
    val consoleErrors = ArrayDeque<String>()

    private fun checkUiReady(view: WebView) {
        // __jarvisReady gibt es ab Server 0.1.2; ältere Server erkennt man an JarvisNative.listen (aus app.js)
        val js = "JSON.stringify({ready: !!window.__jarvisReady || !!(window.JarvisNative && window.JarvisNative.listen), " +
            "css: document.styleSheets.length, errors: (window.__jarvisErrors || []).slice(0, 3), title: document.title})"
        view.evaluateJavascript(js) { raw ->
            val info = try {
                org.json.JSONObject(org.json.JSONTokener(raw).nextValue() as String)
            } catch (e: Exception) { null }
            if (info?.optBoolean("ready") == true) {
                pageLoaded = true
                deliverPendingJs()
                return@evaluateJavascript
            }
            val details = buildString {
                append("Seite: ").append(info?.optString("title").takeUnless { it.isNullOrEmpty() } ?: "?")
                append(" · Stylesheets geladen: ").append(info?.optInt("css") ?: "?")
                val errs = mutableListOf<String>()
                info?.optJSONArray("errors")?.let { a -> for (i in 0 until a.length()) errs.add(a.getString(i)) }
                errs.addAll(consoleErrors)
                append(" · Fehler: ").append(if (errs.isEmpty()) "keine gemeldet" else errs.distinct().take(4).joinToString(" | "))
            }
            showError("Oberfläche startet nicht", "Die Seite von $serverUrl kam an, aber die App-Oberfläche " +
                "(JavaScript/CSS) lief nicht. Bitte schick diese Meldung an den Entwickler. $details")
        }
    }

    // ------------------------------------------------- Jarvis telefoniert
    /** Berechtigungen + SIM-Auswahl für Telefonate, die Jarvis selbst führt. */
    fun showCallSettings() {
        if (!AgentCall.hasPermissions(this)) {
            requestPermissions(AgentCall.PERMISSIONS, REQ_CALLS)
            return
        }
        val pad = (16 * resources.displayMetrics.density).toInt()
        val layout = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(pad, pad / 2, pad, 0) }
        layout.addView(TextView(this).apply {
            text = "Über welche SIM soll Jarvis anrufen? Der Gesprächston muss zum PC laufen, auf dem Jarvis " +
                "läuft (Bluetooth mit Windows-Smartphone-Link oder Kabel) – Anleitung: „jarvis call-setup“."
            setPadding(0, 0, 0, pad / 2)
        })
        val accounts = AgentCall.accounts(this)
        val current = AgentCall.pickAccount(this)
        val group = android.widget.RadioGroup(this)
        accounts.forEachIndexed { i, (handle, label) ->
            group.addView(android.widget.RadioButton(this).apply {
                id = 2000 + i
                text = label
                isChecked = handle == current
            })
        }
        if (accounts.isEmpty()) layout.addView(TextView(this).apply { text = "Keine SIM gefunden." })
        layout.addView(group)
        AlertDialog.Builder(this)
            .setTitle("Jarvis-Anrufe")
            .setView(layout)
            .setNegativeButton("Abbrechen", null)
            .setPositiveButton("Speichern") { _, _ ->
                accounts.getOrNull(group.checkedRadioButtonId - 2000)?.let {
                    prefs.edit().putString("call_sim", it.first.id).apply()
                    Toast.makeText(this, "Jarvis ruft über „${it.second}“ an.", Toast.LENGTH_SHORT).show()
                }
            }
            .show()
    }

    // ------------------------------------------------------------ Stimme
    /** Auswahl der Vorlese-Stimme (mit Probe), Tempo und Tonhöhe. */
    fun showVoiceSettings() {
        val voices = bridge.germanVoices()
        val pad = (16 * resources.displayMetrics.density).toInt()
        val layout = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(pad, pad / 2, pad, 0) }
        val scroll = android.widget.ScrollView(this).apply { addView(layout) }

        if (!bridge.hasGoogleTts()) {
            layout.addView(TextView(this).apply {
                text = "Tipp: Die „Sprachausgabe von Google“ klingt deutlich natürlicher als die Standardstimme vieler Handys."
                setPadding(0, 0, 0, pad / 2)
            })
            layout.addView(android.widget.Button(this).apply {
                text = "Google-Sprachausgabe installieren"
                setOnClickListener { openUrl("market://details?id=${JarvisBridge.GOOGLE_TTS}") }
            })
        }

        var selected: android.speech.tts.Voice? = voices.firstOrNull { it.name == prefs.getString("tts_voice", null) }
            ?: voices.firstOrNull()
        val group = android.widget.RadioGroup(this)
        val serverId = 999
        var useServer = prefs.getBoolean("tts_server", true) && bridge.serverVoiceAvailable
        group.addView(android.widget.RadioButton(this).apply {
            id = serverId
            text = if (bridge.serverVoiceAvailable) "★ Natürliche Stimme „Thorsten“ (Jarvis-Server) · beste Qualität"
                else "★ Natürliche Stimme „Thorsten“ (Jarvis-Server) · noch nicht eingerichtet"
            isChecked = useServer
        })
        if (!bridge.serverVoiceAvailable) {
            layout.addView(TextView(this).apply {
                text = "Die schönste Stimme läuft auf deinem Jarvis-Server (kostenlos, offline): dort einmal " +
                    "„jarvis voice-setup“ ausführen (Handy: ~/jarvis-shell.sh) und Jarvis neu starten."
                setPadding(0, 0, 0, pad / 2)
            })
        }
        if (voices.isEmpty()) {
            layout.addView(TextView(this).apply { text = "Keine deutsche Stimme gefunden. Installiere in den " +
                "Android-Einstellungen unter „Sprachausgabe“ die deutschen Sprachdaten." })
        }
        voices.forEachIndexed { i, v ->
            val quality = when {
                v.quality >= android.speech.tts.Voice.QUALITY_VERY_HIGH -> "sehr gut"
                v.quality >= android.speech.tts.Voice.QUALITY_HIGH -> "gut"
                else -> "einfach"
            }
            group.addView(android.widget.RadioButton(this).apply {
                id = 1000 + i
                text = "Stimme ${('A' + i)} · $quality · ${if (v.isNetworkConnectionRequired) "online" else "offline"}"
                isChecked = !useServer && v == selected
            })
        }
        layout.addView(group)

        fun seek(label: String, value: Float): android.widget.SeekBar {
            layout.addView(TextView(this).apply { text = label; setPadding(0, pad / 2, 0, 0) })
            return android.widget.SeekBar(this).apply {
                max = 100
                progress = (((value - 0.5f) / 1.5f) * 100).toInt().coerceIn(0, 100)  // 0.5 … 2.0
                layout.addView(this)
            }
        }
        fun value(sb: android.widget.SeekBar) = 0.5f + sb.progress / 100f * 1.5f
        val oldRate = prefs.getFloat("tts_rate", 1.0f)
        val rate = seek("Tempo", oldRate)
        val pitch = seek("Tonhöhe (links = tiefer)", prefs.getFloat("tts_pitch", 1.0f))

        fun playPreview() {
            if (useServer) {
                prefs.edit().putFloat("tts_rate", value(rate)).apply()  // Tempo gilt auch für die Server-Stimme
                bridge.previewServerVoice()
            } else bridge.preview(selected, value(rate), value(pitch))
        }
        group.setOnCheckedChangeListener { _, checkedId ->
            useServer = checkedId == serverId
            if (!useServer) selected = voices.getOrNull(checkedId - 1000)
            playPreview()
        }
        layout.addView(android.widget.Button(this).apply {
            text = "▶ Probe hören"
            setOnClickListener { playPreview() }
        })

        AlertDialog.Builder(this)
            .setTitle("Stimme von Jarvis")
            .setView(scroll)
            .setNeutralButton("System-Einstellungen") { _, _ ->
                try { startActivity(Intent("com.android.settings.TTS_SETTINGS")) } catch (e: Exception) { }
            }
            .setNegativeButton("Abbrechen") { _, _ ->
                prefs.edit().putFloat("tts_rate", oldRate).apply()
                bridge.applyVoicePrefs()
            }
            .setPositiveButton("Übernehmen") { _, _ ->
                prefs.edit()
                    .putBoolean("tts_server", useServer)
                    .putString("tts_voice", selected?.name)
                    .putFloat("tts_rate", value(rate))
                    .putFloat("tts_pitch", value(pitch))
                    .apply()
                bridge.applyVoicePrefs()
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
        handler.postDelayed({ checkServer { loadServer() } }, 3000)
    }

    private fun openUrl(url: String) = try {
        startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
    } catch (e: ActivityNotFoundException) { }

    // ---------------------------------------------------------- Hintergrund
    /** Hintergrund-Verbindung: Benachrichtigungen, Akku-Ausnahme und Vollbild-Anrufe erlauben, Dienst starten. */
    private fun enableBackground() {
        if (Build.VERSION.SDK_INT >= 33 && !hasPermission(Manifest.permission.POST_NOTIFICATIONS)) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), REQ_NOTIFY)
        }
        val pm = getSystemService(android.os.PowerManager::class.java)
        if (!pm.isIgnoringBatteryOptimizations(packageName)) {
            try {
                @SuppressLint("BatteryLife")
                val i = Intent(android.provider.Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName"))
                startActivity(i)
            } catch (e: Exception) { }
        }
        if (Build.VERSION.SDK_INT >= 34) {
            val nm = getSystemService(android.app.NotificationManager::class.java)
            if (!nm.canUseFullScreenIntent()) {
                Toast.makeText(this, "Bitte „Vollbild-Benachrichtigungen“ erlauben, damit Jarvis anrufen kann.", Toast.LENGTH_LONG).show()
                try {
                    startActivity(Intent(android.provider.Settings.ACTION_MANAGE_APP_USE_FULL_SCREEN_INTENT,
                        Uri.parse("package:$packageName")))
                } catch (e: Exception) { }
            }
        }
        JarvisService.start(this)
    }

    // --------------------------------------------------------------- Kalender
    fun requestCalendarPermission() {
        if (!hasPermission(Manifest.permission.READ_CALENDAR) || !hasPermission(Manifest.permission.WRITE_CALENDAR)) {
            requestPermissions(arrayOf(Manifest.permission.READ_CALENDAR, Manifest.permission.WRITE_CALENDAR), REQ_CALENDAR)
        }
    }

    /** Kalender höchstens alle 5 Minuten an Jarvis schicken (beim Öffnen/Zurückkehren). */
    private fun maybeSyncCalendar() {
        if (!shareCalendar) return
        if (System.currentTimeMillis() - prefs.getLong("calendar_synced", 0) < 5 * 60 * 1000L) return
        bridge.syncCalendar()
    }

    fun markCalendarSynced() {
        prefs.edit().putLong("calendar_synced", System.currentTimeMillis()).apply()
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
            REQ_TERMUX -> if (granted && localMode) loadApp()
            REQ_CALENDAR -> if (granted) { prefs.edit().putLong("calendar_synced", 0).apply(); maybeSyncCalendar() }
            else Toast.makeText(this, "Ohne Kalender-Zugriff kann Jarvis deinen Handy-Kalender nicht nutzen.", Toast.LENGTH_LONG).show()
            REQ_LOCATION -> if (!granted && !hasPermission(Manifest.permission.ACCESS_COARSE_LOCATION))
                Toast.makeText(this, "Ohne Standort-Freigabe kann Jarvis deinen Ort nicht nutzen.", Toast.LENGTH_LONG).show()
            REQ_CALLS -> if (AgentCall.hasPermissions(this)) showCallSettings()
            else Toast.makeText(this, "Ohne Telefon-Berechtigung kann Jarvis nicht selbst anrufen.", Toast.LENGTH_LONG).show()
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
            handler.removeCallbacks(watchdog)
            // Kurz warten, dann prüfen, ob die Oberfläche wirklich läuft (CSS + JavaScript)
            handler.postDelayed({ checkUiReady(view) }, 2500)
        }
    }
}
