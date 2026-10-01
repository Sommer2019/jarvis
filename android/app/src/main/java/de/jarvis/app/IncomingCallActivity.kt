package de.jarvis.app

import android.app.Activity
import android.app.NotificationManager
import android.content.Intent
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.media.AudioAttributes
import android.media.Ringtone
import android.media.RingtoneManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.VibrationEffect
import android.os.Vibrator
import android.view.Gravity
import android.view.WindowManager
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView

/** Anruf-Bildschirm „Jarvis ruft an“ – auch auf dem Sperrbildschirm. */
class IncomingCallActivity : Activity() {

    companion object {
        const val NOTIFICATION_ID = 4242
        const val RING_MS = 45_000L
    }

    private var ringtone: Ringtone? = null
    private var vibrator: Vibrator? = null
    private val handler = Handler(Looper.getMainLooper())
    private var text = ""

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        text = intent.getStringExtra("text") ?: ""
        if (intent.getBooleanExtra("decline", false)) { decline(); return }

        showOverLockScreen()
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        val d = resources.displayMetrics.density
        fun circle(color: Int) = GradientDrawable().apply { shape = GradientDrawable.OVAL; setColor(color) }
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            setBackgroundColor(0xFF0B1220.toInt())
            setPadding((24 * d).toInt(), (96 * d).toInt(), (24 * d).toInt(), (48 * d).toInt())
        }
        root.addView(TextView(this).apply {
            background = circle(0xFF36C2FF.toInt())
            layoutParams = LinearLayout.LayoutParams((120 * d).toInt(), (120 * d).toInt())
        })
        root.addView(TextView(this).apply {
            text = "Jarvis ruft an"; textSize = 30f; setTextColor(Color.WHITE); gravity = Gravity.CENTER
            setPadding(0, (28 * d).toInt(), 0, (12 * d).toInt())
        })
        root.addView(TextView(this).apply {
            text = this@IncomingCallActivity.text.take(160)
            textSize = 16f; setTextColor(0xFF8A97AD.toInt()); gravity = Gravity.CENTER
            layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f)
        })
        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER }
        fun roundButton(label: String, color: Int, onClick: () -> Unit) = Button(this).apply {
            text = label; textSize = 16f; setTextColor(Color.WHITE); background = circle(color)
            layoutParams = LinearLayout.LayoutParams((84 * d).toInt(), (84 * d).toInt()).apply {
                setMargins((28 * d).toInt(), 0, (28 * d).toInt(), 0) }
            setOnClickListener { onClick() }
        }
        row.addView(roundButton("✕", 0xFFFF5C6C.toInt()) { decline() })
        row.addView(roundButton("✆", 0xFF3DDC84.toInt()) { answer() })
        root.addView(row)
        root.addView(TextView(this).apply {
            text = "Ablehnen                Annehmen"; setTextColor(0xFF8A97AD.toInt()); gravity = Gravity.CENTER
            setPadding(0, (12 * d).toInt(), 0, 0)
        })
        setContentView(root)
        startRinging()
        handler.postDelayed({ missed() }, RING_MS)
    }

    @Suppress("DEPRECATION")
    private fun showOverLockScreen() {
        if (Build.VERSION.SDK_INT >= 27) {
            setShowWhenLocked(true)
            setTurnScreenOn(true)
        } else {
            window.addFlags(WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON)
        }
    }

    private fun startRinging() {
        try {
            ringtone = RingtoneManager.getRingtone(this, RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE))?.apply {
                audioAttributes = AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_NOTIFICATION_RINGTONE).build()
                if (Build.VERSION.SDK_INT >= 28) isLooping = true
                play()
            }
        } catch (e: Exception) { }
        vibrator = getSystemService(Vibrator::class.java)
        vibrator?.vibrate(VibrationEffect.createWaveform(longArrayOf(0, 800, 600), 0))
    }

    private fun stopRinging() {
        handler.removeCallbacksAndMessages(null)
        ringtone?.stop()
        vibrator?.cancel()
        getSystemService(NotificationManager::class.java).cancel(NOTIFICATION_ID)
    }

    private fun answer() {
        stopRinging()
        startActivity(Intent(this, MainActivity::class.java).setAction(MainActivity.ACTION_ANSWER)
            .putExtra("text", text).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP))
        finish()
    }

    private fun decline() {
        stopRinging()
        // Nachricht nicht verlieren: als normale Benachrichtigung ablegen
        if (text.isNotEmpty()) JarvisService.showMessage(this, "Jarvis hat angerufen", text)
        finish()
    }

    private fun missed() {
        stopRinging()
        if (text.isNotEmpty()) JarvisService.showMessage(this, "Verpasster Anruf von Jarvis", text)
        finish()
    }

    override fun onDestroy() {
        stopRinging()
        super.onDestroy()
    }
}
