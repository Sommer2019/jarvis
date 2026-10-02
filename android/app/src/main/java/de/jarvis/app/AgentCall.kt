package de.jarvis.app

import android.Manifest
import android.app.Notification
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.telecom.PhoneAccountHandle
import android.telecom.TelecomManager
import org.json.JSONObject

/**
 * Jarvis telefoniert selbst: Die App wählt über die gewählte SIM (z.B. Aldi-Talk-Zweit-SIM),
 * der Ton läuft per Bluetooth/Kabel zum PC, wo Jarvis zuhört und spricht.
 * Die App meldet dem Server nur „dialing“ und „ended“ und bietet „Auflegen“ an.
 */
object AgentCall {
    const val ACTION_HANGUP = "de.jarvis.app.AGENT_HANGUP"
    private const val NOTIFICATION_ID = 4711
    val PERMISSIONS = buildList {
        add(Manifest.permission.CALL_PHONE)
        add(Manifest.permission.READ_PHONE_STATE)
        if (Build.VERSION.SDK_INT >= 26) add(Manifest.permission.ANSWER_PHONE_CALLS)
    }.toTypedArray()

    @Volatile private var currentId: String? = null

    private fun telecom(ctx: Context) = ctx.getSystemService(TelecomManager::class.java)

    fun hasPermissions(ctx: Context) =
        PERMISSIONS.all { ctx.checkSelfPermission(it) == PackageManager.PERMISSION_GRANTED }

    /** SIM-Karten bzw. Telefonkonten: (ID, Anzeigename). */
    fun accounts(ctx: Context): List<Pair<PhoneAccountHandle, String>> = try {
        val t = telecom(ctx)
        t.callCapablePhoneAccounts.map { h -> h to (t.getPhoneAccount(h)?.label?.toString() ?: h.id) }
    } catch (e: SecurityException) { emptyList() }

    /** Gespeicherte SIM, sonst eine mit „Aldi“ im Namen, sonst (bei nur einer) die einzige. */
    fun pickAccount(ctx: Context): PhoneAccountHandle? {
        val all = accounts(ctx)
        val saved = ctx.getSharedPreferences(MainActivity.PREFS, Context.MODE_PRIVATE).getString("call_sim", null)
        return all.firstOrNull { it.first.id == saved }?.first
            ?: all.firstOrNull { it.second.contains("aldi", ignoreCase = true) }?.first
            ?: all.singleOrNull()?.first
    }

    fun start(ctx: Context, callId: String, number: String, name: String): Boolean {
        if (!hasPermissions(ctx)) {
            report(ctx, callId, "failed", "Telefon-Berechtigung fehlt (Jarvis-App → ⚙ → Jarvis-Anrufe)")
            return false
        }
        if (currentId != null) {
            report(ctx, callId, "failed", "Es läuft schon ein Anruf")
            return false
        }
        val extras = Bundle()
        pickAccount(ctx)?.let { extras.putParcelable(TelecomManager.EXTRA_PHONE_ACCOUNT_HANDLE, it) }
        return try {
            telecom(ctx).placeCall(Uri.fromParts("tel", number, null), extras)
            currentId = callId
            report(ctx, callId, "dialing")
            showOngoing(ctx, name.ifBlank { number })
            watch(ctx, callId)
            true
        } catch (e: Exception) {
            report(ctx, callId, "failed", "Wählen fehlgeschlagen: ${e.message}")
            false
        }
    }

    /** Beobachtet, wann das Gespräch endet (auch wenn das Gegenüber auflegt). */
    private fun watch(ctx: Context, callId: String) = Thread {
        val t = telecom(ctx)
        val started = System.currentTimeMillis()
        var seen = false
        try {
            while (currentId == callId && System.currentTimeMillis() - started < 20 * 60_000) {
                val inCall = try { t.isInCall } catch (e: SecurityException) { false }
                if (inCall) seen = true
                if (seen && !inCall) break
                if (!seen && System.currentTimeMillis() - started > 15_000) break  // Anruf kam nie zustande
                Thread.sleep(1000)
            }
        } catch (e: InterruptedException) { }
        if (currentId == callId) finish(ctx, callId, if (seen) "ended" else "failed",
            if (seen) "" else "Anruf wurde nicht aufgebaut")
    }.start()

    /** Auflegen (von Jarvis oder über die Benachrichtigung). */
    fun hangup(ctx: Context, callId: String? = currentId, byUser: Boolean = false) {
        if (callId == null || callId != currentId) return
        try {
            if (Build.VERSION.SDK_INT >= 28 &&
                ctx.checkSelfPermission(Manifest.permission.ANSWER_PHONE_CALLS) == PackageManager.PERMISSION_GRANTED) {
                @Suppress("DEPRECATION")
                telecom(ctx).endCall()
            }
        } catch (e: Exception) { }
        finish(ctx, callId, "ended", if (byUser) "vom Nutzer beendet" else "jarvis")
    }

    private fun finish(ctx: Context, callId: String, state: String, detail: String) {
        currentId = null
        ctx.getSystemService(NotificationManager::class.java).cancel(NOTIFICATION_ID)
        report(ctx, callId, state, detail)
    }

    private fun report(ctx: Context, callId: String, state: String, detail: String = "") = Thread {
        try {
            Api.post(ctx, "/api/calls/$callId/state", JSONObject().put("state", state).put("detail", detail))
        } catch (e: Exception) { }
    }.start()

    private fun showOngoing(ctx: Context, who: String) {
        JarvisService.channels(ctx)
        val hang = PendingIntent.getBroadcast(ctx, 0, Intent(ctx, HangupReceiver::class.java).setAction(ACTION_HANGUP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val n = Notification.Builder(ctx, JarvisService.CH_MSG)
            .setSmallIcon(R.drawable.ic_mic)
            .setContentTitle("🤖 Jarvis telefoniert mit $who")
            .setContentText("Über die Zweit-SIM – Ton läuft über den PC. Ergebnis kommt danach.")
            .setOngoing(true)
            .addAction(Notification.Action.Builder(null, "Auflegen", hang).build())
            .build()
        ctx.getSystemService(NotificationManager::class.java).notify(NOTIFICATION_ID, n)
    }

    class HangupReceiver : BroadcastReceiver() {
        override fun onReceive(ctx: Context, intent: Intent) {
            if (intent.action == ACTION_HANGUP) hangup(ctx, byUser = true)
        }
    }
}
