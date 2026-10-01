package de.jarvis.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.AudioAttributes
import android.media.RingtoneManager
import android.os.Build
import android.os.IBinder
import android.util.Log
import org.json.JSONObject

/**
 * Hält im Hintergrund die Verbindung zu Jarvis (Long-Polling) und
 *  - zeigt Nachrichten von Jarvis als Benachrichtigung (notify),
 *  - lässt das Handy klingeln, wenn Jarvis „anruft“ (ring),
 *  - trägt Kalender-Termine direkt ein,
 *  - meldet andere Aufgaben (anrufen, SMS, Navigation …) als Benachrichtigung zum Antippen.
 */
class JarvisService : Service() {

    companion object {
        const val CH_STATUS = "jarvis_status"
        const val CH_MSG = "jarvis_messages"
        const val CH_CALL = "jarvis_calls"
        const val ID_STATUS = 1
        @Volatile var running = false

        fun enabled(ctx: Context) = Api.prefs(ctx).getBoolean("background", false)

        fun start(ctx: Context) {
            if (!enabled(ctx) || Api.serverUrl(ctx).isEmpty()) return
            val i = Intent(ctx, JarvisService::class.java)
            if (Build.VERSION.SDK_INT >= 26) ctx.startForegroundService(i) else ctx.startService(i)
        }

        fun stop(ctx: Context) {
            ctx.stopService(Intent(ctx, JarvisService::class.java))
        }

        fun channels(ctx: Context) {
            val nm = ctx.getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(NotificationChannel(CH_STATUS, "Verbindung zu Jarvis",
                NotificationManager.IMPORTANCE_MIN).apply { setShowBadge(false) })
            nm.createNotificationChannel(NotificationChannel(CH_MSG, "Nachrichten von Jarvis",
                NotificationManager.IMPORTANCE_HIGH))
            nm.createNotificationChannel(NotificationChannel(CH_CALL, "Anrufe von Jarvis",
                NotificationManager.IMPORTANCE_HIGH).apply {
                setSound(RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE),
                    AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_NOTIFICATION_RINGTONE)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build())
                enableVibration(true)
                vibrationPattern = longArrayOf(0, 800, 600, 800, 600, 800)
            })
        }

        /** Nachricht von Jarvis als normale Benachrichtigung. */
        fun showMessage(ctx: Context, title: String, text: String, id: Int = text.hashCode()) {
            val open = PendingIntent.getActivity(ctx, id, Intent(ctx, MainActivity::class.java)
                .setAction(MainActivity.ACTION_MESSAGE).putExtra("text", text)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            val n = Notification.Builder(ctx, CH_MSG)
                .setSmallIcon(R.drawable.ic_mic)
                .setContentTitle(title)
                .setContentText(text)
                .setStyle(Notification.BigTextStyle().bigText(text))
                .setContentIntent(open)
                .setAutoCancel(true)
                .build()
            ctx.getSystemService(NotificationManager::class.java).notify(id, n)
        }

        /** Jarvis „ruft an“: Anruf-Benachrichtigung mit Vollbild-Anrufbildschirm. */
        fun showIncomingCall(ctx: Context, text: String) {
            val id = IncomingCallActivity.NOTIFICATION_ID
            val screen = Intent(ctx, IncomingCallActivity::class.java).putExtra("text", text)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_NO_USER_ACTION)
            val full = PendingIntent.getActivity(ctx, 10, screen, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            val answer = PendingIntent.getActivity(ctx, 11, Intent(ctx, MainActivity::class.java)
                .setAction(MainActivity.ACTION_ANSWER).putExtra("text", text).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            val decline = PendingIntent.getActivity(ctx, 12, Intent(screen).putExtra("decline", true),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            val n = Notification.Builder(ctx, CH_CALL)
                .setSmallIcon(R.drawable.ic_mic)
                .setContentTitle("Jarvis ruft an")
                .setContentText(text.take(120))
                .setCategory(Notification.CATEGORY_CALL)
                .setFullScreenIntent(full, true)
                .setContentIntent(full)
                .setOngoing(true)
                .setTimeoutAfter(IncomingCallActivity.RING_MS)
                .addAction(Notification.Action.Builder(null, "Ablehnen", decline).build())
                .addAction(Notification.Action.Builder(null, "Annehmen", answer).build())
                .build()
            ctx.getSystemService(NotificationManager::class.java).notify(id, n)
        }
    }

    @Volatile private var stop = false
    private val seen = HashSet<String>()

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        channels(this)
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val n = Notification.Builder(this, CH_STATUS)
            .setSmallIcon(R.drawable.ic_mic)
            .setContentTitle("Jarvis ist verbunden")
            .setContentText("Nachrichten und Anrufe von Jarvis kommen an")
            .setContentIntent(open)
            .setOngoing(true)
            .build()
        if (Build.VERSION.SDK_INT >= 34) startForeground(ID_STATUS, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        else startForeground(ID_STATUS, n)
        running = true
        Thread(::loop, "jarvis-poll").start()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = START_STICKY

    override fun onDestroy() {
        stop = true
        running = false
        super.onDestroy()
    }

    private fun loop() {
        var backoff = 5_000L
        while (!stop) {
            try {
                val res = Api.get(this, "/api/phone/actions?wait=25", readTimeoutMs = 40_000)
                val actions = res.optJSONArray("actions")
                for (i in 0 until (actions?.length() ?: 0)) handle(actions!!.getJSONObject(i))
                backoff = 5_000L
            } catch (e: Exception) {
                Log.w("Jarvis", "Verbindung unterbrochen: ${e.message}")
                try { Thread.sleep(backoff) } catch (ie: InterruptedException) { return }
                backoff = (backoff * 2).coerceAtMost(120_000L)
            }
        }
    }

    private fun ack(id: String) {
        try { Api.post(this, "/api/phone/actions/$id/done", JSONObject()) } catch (e: Exception) { }
    }

    private fun handle(a: JSONObject) {
        val id = a.optString("id")
        val type = a.optString("type")
        val p = a.optJSONObject("params") ?: JSONObject()
        when {
            type == "notify" -> { showMessage(this, p.optString("title", "Jarvis"), p.optString("text")); ack(id) }
            type == "ring" -> { showIncomingCall(this, p.optString("text")); ack(id) }
            type == "file" -> {
                try { Api.downloadFile(this, p.optString("file_id"), p.optString("name")) } catch (e: Exception) {
                    showMessage(this, "Jarvis", "Download fehlgeschlagen: ${p.optString("name")} (${e.message})")
                }
                ack(id)
            }
            type.startsWith("calendar_") -> {
                if (CalendarOps.run(this, type, p)) {
                    ack(id)
                    try { CalendarOps.sync(this) } catch (e: Exception) { }
                }
            }
            // Anrufen/SMS/Navigation … darf Android nur aus der App heraus öffnen → antippen lassen
            !MainActivity.visible && seen.add(id) -> showMessage(this, "Jarvis möchte etwas auf dem Handy tun",
                describe(type, p) + " – zum Ausführen antippen", id.hashCode())
        }
    }

    private fun describe(type: String, p: JSONObject) = when (type) {
        "call" -> "📞 ${p.optString("number")} anrufen"
        "sms" -> "💬 SMS an ${p.optString("number")}"
        "whatsapp" -> "🟢 WhatsApp an ${p.optString("number")}"
        "navigate" -> "🧭 Navigation zu ${p.optString("destination")}"
        "alarm" -> "⏰ Wecker ${p.optInt("hour")}:${"%02d".format(p.optInt("minute"))}"
        "timer" -> "⏱️ Timer ${p.optInt("seconds") / 60} Min."
        "open_url" -> "🔗 ${p.optString("url")}"
        else -> type
    }
}
