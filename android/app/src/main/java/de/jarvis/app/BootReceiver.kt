package de.jarvis.app

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** Startet die Hintergrund-Verbindung nach einem Neustart des Handys. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED || intent.action == Intent.ACTION_MY_PACKAGE_REPLACED) {
            JarvisService.start(ctx)
        }
    }
}
