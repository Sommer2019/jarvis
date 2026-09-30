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
import java.net.HttpURLConnection
import java.net.URL
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

    /** POST an den Jarvis-Server (mit Token), liefert die JSON-Antwort. */
    private fun postJson(path: String, body: JSONObject): JSONObject {
        val conn = URL("${activity.serverUrl}$path").openConnection() as HttpURLConnection
        try {
            conn.requestMethod = "POST"
            conn.doOutput = true
            conn.connectTimeout = 15000
            conn.readTimeout = 30000
            conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
            conn.setRequestProperty("Authorization", "Bearer ${activity.token}")
            conn.setRequestProperty("X-Jarvis-App", version())
            conn.outputStream.use { it.write(body.toString().toByteArray()) }
            if (conn.responseCode !in 200..299) throw IllegalStateException("HTTP ${conn.responseCode}")
            return JSONObject(conn.inputStream.bufferedReader().readText())
        } finally {
            conn.disconnect()
        }
    }

    // --------------------------------------------------------------- Kalender
    fun calendarAllowed() = activity.shareCalendar &&
        activity.hasPermission(Manifest.permission.READ_CALENDAR)

    /** Kalender + Termine (7 Tage zurück bis 90 Tage voraus) an Jarvis schicken. */
    @JavascriptInterface
    fun syncCalendar() {
        if (!calendarAllowed()) return
        Thread {
            try {
                val n = postJson("/api/phone/calendar", readCalendar()).optInt("saved")
                activity.markCalendarSynced()
                android.util.Log.i("Jarvis", "Kalender synchronisiert: $n Termine")
            } catch (e: Exception) {
                android.util.Log.w("Jarvis", "Kalender-Sync fehlgeschlagen", e)
            }
        }.start()
    }

    private fun readCalendar(): JSONObject {
        val cr = activity.contentResolver
        val calendars = JSONArray()
        cr.query(android.provider.CalendarContract.Calendars.CONTENT_URI,
            arrayOf(android.provider.CalendarContract.Calendars._ID,
                android.provider.CalendarContract.Calendars.CALENDAR_DISPLAY_NAME,
                android.provider.CalendarContract.Calendars.ACCOUNT_NAME,
                android.provider.CalendarContract.Calendars.CALENDAR_ACCESS_LEVEL,
                android.provider.CalendarContract.Calendars.IS_PRIMARY,
                android.provider.CalendarContract.Calendars.VISIBLE), null, null, null)?.use { c ->
            while (c.moveToNext()) {
                if (c.getInt(5) == 0) continue  // ausgeblendete Kalender überspringen
                calendars.put(JSONObject()
                    .put("id", c.getLong(0))
                    .put("name", c.getString(1) ?: "")
                    .put("account", c.getString(2) ?: "")
                    .put("writable", c.getInt(3) >= android.provider.CalendarContract.Calendars.CAL_ACCESS_CONTRIBUTOR)
                    .put("primary", c.getInt(4) == 1))
            }
        }
        val now = System.currentTimeMillis()
        val uri = android.provider.CalendarContract.Instances.CONTENT_URI.buildUpon().let {
            android.content.ContentUris.appendId(it, now - 7 * 86_400_000L)
            android.content.ContentUris.appendId(it, now + 90 * 86_400_000L)
            it.build()
        }
        val events = JSONArray()
        cr.query(uri, arrayOf(android.provider.CalendarContract.Instances.EVENT_ID,
            android.provider.CalendarContract.Instances.CALENDAR_ID,
            android.provider.CalendarContract.Instances.TITLE,
            android.provider.CalendarContract.Instances.BEGIN,
            android.provider.CalendarContract.Instances.END,
            android.provider.CalendarContract.Instances.ALL_DAY,
            android.provider.CalendarContract.Instances.EVENT_LOCATION,
            android.provider.CalendarContract.Instances.DESCRIPTION), null, null,
            android.provider.CalendarContract.Instances.BEGIN + " ASC")?.use { c ->
            while (c.moveToNext() && events.length() < 3000) {
                events.put(JSONObject()
                    .put("event_id", c.getLong(0))
                    .put("calendar_id", c.getLong(1))
                    .put("title", c.getString(2) ?: "")
                    .put("start", c.getLong(3))
                    .put("end", c.getLong(4))
                    .put("all_day", c.getInt(5) == 1)
                    .put("location", c.getString(6) ?: "")
                    .put("description", (c.getString(7) ?: "").take(500)))
            }
        }
        return JSONObject().put("calendars", calendars).put("events", events)
    }

    /** Termin anlegen/ändern/löschen direkt im Android-Kalender (synchronisiert mit Google & Co.). */
    private fun runCalendarAction(type: String, p: JSONObject): Boolean {
        if (!activity.hasPermission(Manifest.permission.WRITE_CALENDAR)) {
            activity.runOnUiThread { activity.requestCalendarPermission() }
            return false
        }
        val cr = activity.contentResolver
        return try {
            when (type) {
                "calendar_add" -> {
                    val v = android.content.ContentValues().apply {
                        put(android.provider.CalendarContract.Events.CALENDAR_ID, p.getLong("calendar_id"))
                        put(android.provider.CalendarContract.Events.TITLE, p.optString("title"))
                        put(android.provider.CalendarContract.Events.DTSTART, p.getLong("start"))
                        put(android.provider.CalendarContract.Events.DTEND, p.getLong("end"))
                        put(android.provider.CalendarContract.Events.ALL_DAY, if (p.optBoolean("all_day")) 1 else 0)
                        put(android.provider.CalendarContract.Events.EVENT_TIMEZONE, p.optString("timezone", "UTC"))
                        if (p.optString("location").isNotEmpty()) put(android.provider.CalendarContract.Events.EVENT_LOCATION, p.optString("location"))
                        if (p.optString("description").isNotEmpty()) put(android.provider.CalendarContract.Events.DESCRIPTION, p.optString("description"))
                    }
                    val uri = cr.insert(android.provider.CalendarContract.Events.CONTENT_URI, v) ?: return false
                    if (p.has("reminder_minutes") && !p.isNull("reminder_minutes")) {
                        cr.insert(android.provider.CalendarContract.Reminders.CONTENT_URI, android.content.ContentValues().apply {
                            put(android.provider.CalendarContract.Reminders.EVENT_ID, android.content.ContentUris.parseId(uri))
                            put(android.provider.CalendarContract.Reminders.MINUTES, p.getInt("reminder_minutes"))
                            put(android.provider.CalendarContract.Reminders.METHOD, android.provider.CalendarContract.Reminders.METHOD_ALERT)
                        })
                    }
                }
                "calendar_update" -> {
                    val v = android.content.ContentValues()
                    if (p.has("title")) v.put(android.provider.CalendarContract.Events.TITLE, p.getString("title"))
                    if (p.has("start")) v.put(android.provider.CalendarContract.Events.DTSTART, p.getLong("start"))
                    if (p.has("end")) v.put(android.provider.CalendarContract.Events.DTEND, p.getLong("end"))
                    if (p.has("location")) v.put(android.provider.CalendarContract.Events.EVENT_LOCATION, p.getString("location"))
                    if (p.has("description")) v.put(android.provider.CalendarContract.Events.DESCRIPTION, p.getString("description"))
                    val uri = android.content.ContentUris.withAppendedId(android.provider.CalendarContract.Events.CONTENT_URI, p.getLong("event_id"))
                    if (cr.update(uri, v, null, null) == 0) return false
                }
                "calendar_delete" -> {
                    val uri = android.content.ContentUris.withAppendedId(android.provider.CalendarContract.Events.CONTENT_URI, p.getLong("event_id"))
                    if (cr.delete(uri, null, null) == 0) return false
                }
                else -> return false
            }
            // danach den neuen Stand an Jarvis schicken
            activity.webView.postDelayed({ syncCalendar() }, 1500)
            true
        } catch (e: Exception) {
            android.util.Log.w("Jarvis", "Kalender-Aktion $type fehlgeschlagen", e)
            false
        }
    }
}
