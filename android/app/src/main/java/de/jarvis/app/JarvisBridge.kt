package de.jarvis.app

import android.Manifest
import android.annotation.SuppressLint
import android.content.ActivityNotFoundException
import android.content.Intent
import android.location.Geocoder
import android.location.Location
import android.location.LocationManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.AlarmClock
import android.provider.ContactsContract
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.speech.tts.Voice
import android.webkit.JavascriptInterface
import org.json.JSONArray
import org.json.JSONObject
import java.util.Locale

/**
 * Wird der Weboberfläche als `window.JarvisAndroid` bereitgestellt.
 * Rückmeldungen gehen an `window.JarvisNative.on…` (siehe jarvis/static/app.js).
 */
class JarvisBridge(private val activity: MainActivity) {

    private var recognizer: SpeechRecognizer? = null
    private var tts: TextToSpeech? = null
    private var ttsReady = false

    init {
        initTts()
    }

    companion object {
        const val GOOGLE_TTS = "com.google.android.tts"
        const val SAMPLE = "Hallo, ich bin Jarvis. Wie kann ich dir helfen?"
    }

    /** Engine: gespeicherte Wahl → Google-Sprachausgabe (klingt am natürlichsten) → Systemstandard. */
    fun initTts(engine: String? = null) {
        tts?.shutdown()
        ttsReady = false
        val wanted = engine ?: activity.prefs.getString("tts_engine", null)
            ?: GOOGLE_TTS.takeIf { isInstalled(it) }
        tts = TextToSpeech(activity, { status ->
            ttsReady = status == TextToSpeech.SUCCESS
            if (ttsReady) {
                tts?.setLanguage(Locale.GERMANY)
                applyVoicePrefs()
                tts?.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                    override fun onStart(id: String?) {}
                    override fun onDone(id: String?) { if (id?.startsWith("jarvis-") == true) js("onSpeakDone") }
                    @Deprecated("Deprecated in Java")
                    override fun onError(id: String?) { if (id?.startsWith("jarvis-") == true) js("onSpeakDone") }
                })
            }
        }, wanted)
    }

    private fun isInstalled(pkg: String) = try {
        activity.packageManager.getPackageInfo(pkg, 0); true
    } catch (e: Exception) { false }

    fun hasGoogleTts() = isInstalled(GOOGLE_TTS)

    /** Deutsche Stimmen, beste zuerst (Qualität, dann offline vor online). */
    fun germanVoices(): List<Voice> = try {
        (tts?.voices ?: emptySet())
            .filter { it.locale.language == "de" &&
                !it.features.contains(TextToSpeech.Engine.KEY_FEATURE_NOT_INSTALLED) }
            .sortedWith(compareByDescending<Voice> { it.quality }
                .thenBy { it.isNetworkConnectionRequired }
                .thenBy { it.name })
    } catch (e: Exception) { emptyList() }

    fun applyVoicePrefs() {
        val t = tts ?: return
        val name = activity.prefs.getString("tts_voice", null)
        val voices = germanVoices()
        (voices.firstOrNull { it.name == name } ?: voices.firstOrNull())?.let { runCatching { t.voice = it } }
        t.setSpeechRate(activity.prefs.getFloat("tts_rate", 1.0f))
        t.setPitch(activity.prefs.getFloat("tts_pitch", 1.0f))
    }

    /** Probe mit einer bestimmten Stimme/Einstellung, ohne sie zu speichern. */
    fun preview(voice: Voice?, rate: Float, pitch: Float) {
        val t = tts ?: return
        voice?.let { runCatching { t.voice = it } }
        t.setSpeechRate(rate)
        t.setPitch(pitch)
        t.speak(SAMPLE, TextToSpeech.QUEUE_FLUSH, null, "preview")
    }

    fun shutdown() {
        recognizer?.destroy()
        tts?.shutdown()
    }

    /** Ruft window.JarvisNative[name](arg) im WebView auf. */
    private fun js(name: String, arg: String? = null) {
        val a = if (arg == null) "" else JSONObject.quote(arg)
        activity.webView.post {
            activity.webView.evaluateJavascript(
                "window.JarvisNative && window.JarvisNative.$name && window.JarvisNative.$name($a)", null)
        }
    }

    @JavascriptInterface
    fun version(): String = try {
        activity.packageManager.getPackageInfo(activity.packageName, 0).versionName ?: "?"
    } catch (e: Exception) {
        "?"
    }

    @JavascriptInterface
    fun openSettings() = activity.runOnUiThread { activity.showSetup() }

    /** Läuft die Hintergrund-Verbindung? Dann zeigt sie Nachrichten/Anrufe selbst an. */
    @JavascriptInterface
    fun backgroundActive(): Boolean = JarvisService.running

    @JavascriptInterface
    fun retry() = activity.runOnUiThread { activity.loadApp() }

    @JavascriptInterface
    fun startTermux() = activity.runOnUiThread {
        if (activity.startLocalServer()) activity.loadApp()
        else activity.showError("Start nicht möglich", "Bitte in Termux ~/jarvis-start.sh ausführen oder der " +
            "Jarvis-App die Berechtigung „Befehle in Termux ausführen“ geben.")
    }

    // --------------------------------------------------------- Spracherkennung
    @JavascriptInterface
    fun startListening() = activity.runOnUiThread {
        if (!activity.hasPermission(Manifest.permission.RECORD_AUDIO)) {
            activity.requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), MainActivity.REQ_AUDIO)
            js("onError", "Bitte Mikrofon-Zugriff erlauben")
            return@runOnUiThread
        }
        if (!SpeechRecognizer.isRecognitionAvailable(activity)) {
            js("onError", "Keine Spracherkennung auf dem Gerät (Google-App installieren)")
            return@runOnUiThread
        }
        recognizer?.destroy()
        recognizer = SpeechRecognizer.createSpeechRecognizer(activity).apply {
            setRecognitionListener(object : RecognitionListener {
                override fun onReadyForSpeech(params: Bundle?) {}
                override fun onBeginningOfSpeech() {}
                override fun onRmsChanged(rmsdB: Float) {}
                override fun onBufferReceived(buffer: ByteArray?) {}
                override fun onEndOfSpeech() {}
                override fun onEvent(eventType: Int, params: Bundle?) {}
                override fun onPartialResults(partial: Bundle?) {
                    partial?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull()?.let { js("onPartial", it) }
                }
                override fun onResults(results: Bundle?) {
                    js("onResult", results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull() ?: "")
                }
                override fun onError(error: Int) {
                    if (error == SpeechRecognizer.ERROR_NO_MATCH || error == SpeechRecognizer.ERROR_SPEECH_TIMEOUT) {
                        js("onResult", "")
                    } else {
                        js("onError", "Spracherkennung fehlgeschlagen (Code $error)")
                    }
                }
            })
        }
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, "de-DE")
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, 1500L)
        }
        recognizer?.startListening(intent)
    }

    @JavascriptInterface
    fun stopListening() = activity.runOnUiThread { recognizer?.stopListening() }

    // ---------------------------------------------------------- Sprachausgabe
    @JavascriptInterface
    fun speak(text: String) {
        if (!ttsReady) {
            js("onSpeakDone")
            return
        }
        tts?.speak(text, TextToSpeech.QUEUE_FLUSH, null, "jarvis-${System.currentTimeMillis()}")
    }

    @JavascriptInterface
    fun stopSpeaking() {
        tts?.stop()
    }

    // --------------------------------------------------------------- Aktionen
    /** Führt eine Aktion aus der Jarvis-Warteschlange aus. true = gestartet. */
    @JavascriptInterface
    fun runAction(json: String): Boolean {
        val action = JSONObject(json)
        val p = action.optJSONObject("params") ?: JSONObject()
        if (action.optString("type").startsWith("calendar_")) return runCalendarAction(action.optString("type"), p)
        if (action.optString("type") == "file") {
            return try { Api.downloadFile(activity, p.optString("file_id"), p.optString("name")); true } catch (e: Exception) { false }
        }
        val number = p.optString("number").filter { it.isDigit() || it == '+' }
        val intent = when (action.optString("type")) {
            // DIAL öffnet nur die Telefon-App mit der Nummer – anrufen tippst du selbst
            "call" -> Intent(Intent.ACTION_DIAL, Uri.parse("tel:$number"))
            "sms" -> Intent(Intent.ACTION_SENDTO, Uri.parse("smsto:$number")).putExtra("sms_body", p.optString("text"))
            "whatsapp" -> Intent(Intent.ACTION_VIEW, Uri.parse(
                "https://wa.me/${number.removePrefix("+")}?text=${Uri.encode(p.optString("text"))}"))
            "navigate" -> Intent(Intent.ACTION_VIEW, Uri.parse("google.navigation:q=${Uri.encode(p.optString("destination"))}"))
            "alarm" -> Intent(AlarmClock.ACTION_SET_ALARM)
                .putExtra(AlarmClock.EXTRA_HOUR, p.optInt("hour"))
                .putExtra(AlarmClock.EXTRA_MINUTES, p.optInt("minute"))
                .putExtra(AlarmClock.EXTRA_MESSAGE, p.optString("label", "Jarvis"))
                .putExtra(AlarmClock.EXTRA_SKIP_UI, true)
            "timer" -> Intent(AlarmClock.ACTION_SET_TIMER)
                .putExtra(AlarmClock.EXTRA_LENGTH, p.optInt("seconds"))
                .putExtra(AlarmClock.EXTRA_MESSAGE, p.optString("label", "Jarvis"))
                .putExtra(AlarmClock.EXTRA_SKIP_UI, true)
            "open_url" -> Intent(Intent.ACTION_VIEW, Uri.parse(p.optString("url")))
            else -> return false
        }
        return try {
            activity.startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            true
        } catch (e: ActivityNotFoundException) {
            if (action.optString("type") == "navigate") {
                // ohne Google Maps: beliebige Karten-App
                return try {
                    activity.startActivity(Intent(Intent.ACTION_VIEW,
                        Uri.parse("geo:0,0?q=${Uri.encode(p.optString("destination"))}")))
                    true
                } catch (e2: ActivityNotFoundException) { false }
            }
            false
        }
    }

    // --------------------------------------------------------------- Standort
    @JavascriptInterface
    fun locationEnabled(): Boolean = activity.shareLocation &&
        (activity.hasPermission(Manifest.permission.ACCESS_FINE_LOCATION) ||
            activity.hasPermission(Manifest.permission.ACCESS_COARSE_LOCATION))

    /**
     * Ermittelt den aktuellen Standort (ohne Google-Play-Dienste) und löst ihn auf dem
     * Gerät in eine Adresse auf. Ergebnis → window.JarvisNative.onLocation(json | "").
     */
    @SuppressLint("MissingPermission") // geprüft in locationEnabled()
    @JavascriptInterface
    fun requestLocation() {
        if (!locationEnabled()) {
            js("onLocation", "")
            return
        }
        val lm = activity.getSystemService(LocationManager::class.java)
        val providers = listOf(LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER, LocationManager.PASSIVE_PROVIDER)
            .filter { runCatching { lm.isProviderEnabled(it) }.getOrDefault(false) }
        val last = providers.mapNotNull { p -> runCatching { lm.getLastKnownLocation(p) }.getOrNull() }
            .maxByOrNull { it.time }
        // frisch genug (< 2 Min.)? → sofort verwenden
        if (last != null && System.currentTimeMillis() - last.time < 120_000) {
            deliverLocation(last)
            return
        }
        val provider = providers.firstOrNull { it != LocationManager.PASSIVE_PROVIDER }
        if (provider == null || Build.VERSION.SDK_INT < 30) {
            if (last != null) deliverLocation(last) else js("onLocation", "")
            return
        }
        try {
            lm.getCurrentLocation(provider, null, activity.mainExecutor) { loc ->
                val best = loc ?: last
                if (best != null) deliverLocation(best) else js("onLocation", "")
            }
        } catch (e: SecurityException) {
            js("onLocation", "")
        }
    }

    private fun deliverLocation(loc: Location) {
        Thread {
            val address = try {
                @Suppress("DEPRECATION")
                Geocoder(activity, Locale.GERMANY).getFromLocation(loc.latitude, loc.longitude, 1)
                    ?.firstOrNull()?.let { a ->
                        listOfNotNull(
                            listOfNotNull(a.thoroughfare, a.subThoroughfare).joinToString(" ").ifBlank { null },
                            listOfNotNull(a.postalCode, a.locality).joinToString(" ").ifBlank { null },
                        ).joinToString(", ")
                    } ?: ""
            } catch (e: Exception) { "" }
            js("onLocation", JSONObject()
                .put("lat", loc.latitude)
                .put("lon", loc.longitude)
                .put("accuracy", loc.accuracy.toDouble())
                .put("address", address)
                .put("time", loc.time / 1000)
                .toString())
        }.start()
    }

    // --------------------------------------------------------------- Kontakte
    @JavascriptInterface
    fun syncContacts() {
        if (!activity.hasPermission(Manifest.permission.READ_CONTACTS)) {
            activity.runOnUiThread {
                activity.requestPermissions(arrayOf(Manifest.permission.READ_CONTACTS), MainActivity.REQ_CONTACTS)
            }
            return
        }
        Thread {
            try {
                val count = uploadContacts(readContacts())
                activity.markContactsSynced()
                js("onContactsSynced", count.toString())
            } catch (e: Exception) {
                js("onError", "Kontakte-Sync fehlgeschlagen: ${e.message}")
            }
        }.start()
    }

    private fun readContacts(): JSONArray {
        data class C(val name: String, val phones: MutableSet<String> = linkedSetOf(), val emails: MutableSet<String> = linkedSetOf())
        val byId = LinkedHashMap<Long, C>()
        val resolver = activity.contentResolver

        resolver.query(ContactsContract.CommonDataKinds.Phone.CONTENT_URI,
            arrayOf(ContactsContract.CommonDataKinds.Phone.CONTACT_ID,
                ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME,
                ContactsContract.CommonDataKinds.Phone.NUMBER), null, null, null)?.use { c ->
            while (c.moveToNext()) {
                val name = c.getString(1) ?: continue
                byId.getOrPut(c.getLong(0)) { C(name) }.phones.add(c.getString(2) ?: continue)
            }
        }
        resolver.query(ContactsContract.CommonDataKinds.Email.CONTENT_URI,
            arrayOf(ContactsContract.CommonDataKinds.Email.CONTACT_ID,
                ContactsContract.CommonDataKinds.Email.DISPLAY_NAME,
                ContactsContract.CommonDataKinds.Email.ADDRESS), null, null, null)?.use { c ->
            while (c.moveToNext()) {
                val name = c.getString(1) ?: continue
                byId.getOrPut(c.getLong(0)) { C(name) }.emails.add(c.getString(2) ?: continue)
            }
        }
        val out = JSONArray()
        for (c in byId.values) {
            out.put(JSONObject()
                .put("name", c.name)
                .put("phones", JSONArray(c.phones.toList()))
                .put("emails", JSONArray(c.emails.toList())))
        }
        return out
    }

    private fun uploadContacts(contacts: JSONArray): Int =
        postJson("/api/phone/contacts", JSONObject().put("contacts", contacts)).optInt("saved")

    private fun postJson(path: String, body: JSONObject): JSONObject = Api.post(activity, path, body)

    // --------------------------------------------------------------- Kalender
    fun calendarAllowed() = CalendarOps.allowed(activity)

    /** Kalender + Termine (7 Tage zurück bis 90 Tage voraus) an Jarvis schicken. */
    @JavascriptInterface
    fun syncCalendar() {
        if (!calendarAllowed()) return
        Thread {
            try {
                val n = CalendarOps.sync(activity)
                android.util.Log.i("Jarvis", "Kalender synchronisiert: $n Termine")
            } catch (e: Exception) {
                android.util.Log.w("Jarvis", "Kalender-Sync fehlgeschlagen", e)
            }
        }.start()
    }

    private fun runCalendarAction(type: String, p: JSONObject): Boolean {
        if (!activity.hasPermission(Manifest.permission.WRITE_CALENDAR)) {
            activity.runOnUiThread { activity.requestCalendarPermission() }
            return false
        }
        val ok = CalendarOps.run(activity, type, p)
        // danach den neuen Stand an Jarvis schicken
        if (ok) activity.webView.postDelayed({ syncCalendar() }, 1500)
        return ok
    }
}
