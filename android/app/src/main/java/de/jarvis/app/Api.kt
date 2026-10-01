package de.jarvis.app

import android.content.Context
import android.content.SharedPreferences
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/** Verbindung zum Jarvis-Server – gemeinsam für App, Hintergrund-Dienst und Anruf-Bildschirm. */
object Api {
    fun prefs(ctx: Context): SharedPreferences = ctx.getSharedPreferences(MainActivity.PREFS, Context.MODE_PRIVATE)
    fun serverUrl(ctx: Context) = prefs(ctx).getString("server_url", "")!!.trimEnd('/')
    fun token(ctx: Context) = prefs(ctx).getString("token", "")!!

    fun version(ctx: Context): String = try {
        ctx.packageManager.getPackageInfo(ctx.packageName, 0).versionName ?: "?"
    } catch (e: Exception) { "?" }

    private fun open(ctx: Context, path: String, readTimeoutMs: Int): HttpURLConnection {
        val conn = URL(serverUrl(ctx) + path).openConnection() as HttpURLConnection
        conn.connectTimeout = 15000
        conn.readTimeout = readTimeoutMs
        conn.setRequestProperty("Authorization", "Bearer ${token(ctx)}")
        conn.setRequestProperty("X-Jarvis-App", version(ctx))
        return conn
    }

    private fun read(conn: HttpURLConnection): JSONObject {
        if (conn.responseCode !in 200..299) throw IllegalStateException("HTTP ${conn.responseCode}")
        return JSONObject(conn.inputStream.bufferedReader().readText())
    }

    fun get(ctx: Context, path: String, readTimeoutMs: Int = 30000): JSONObject {
        val conn = open(ctx, path, readTimeoutMs)
        try { return read(conn) } finally { conn.disconnect() }
    }

    fun post(ctx: Context, path: String, body: JSONObject): JSONObject {
        val conn = open(ctx, path, 30000)
        try {
            conn.requestMethod = "POST"
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
            conn.outputStream.use { it.write(body.toString().toByteArray()) }
            return read(conn)
        } finally {
            conn.disconnect()
        }
    }
}
