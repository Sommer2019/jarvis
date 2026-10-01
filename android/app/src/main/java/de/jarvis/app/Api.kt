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

    /** Datei von Jarvis über den Android-Download-Manager nach Downloads/Jarvis laden (auch im Hintergrund). */
    fun downloadFile(ctx: Context, fileId: String, name: String) {
        val safe = name.replace(Regex("[\\\\/:*?\"<>|]"), "_").ifBlank { "datei" }
        val req = android.app.DownloadManager.Request(android.net.Uri.parse("${serverUrl(ctx)}/api/files/$fileId"))
            .addRequestHeader("Authorization", "Bearer ${token(ctx)}")
            .setTitle(safe)
            .setDescription("Datei von Jarvis")
            .setNotificationVisibility(android.app.DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
            .setDestinationInExternalPublicDir(android.os.Environment.DIRECTORY_DOWNLOADS, "Jarvis/$safe")
        ctx.getSystemService(android.app.DownloadManager::class.java).enqueue(req)
    }

    /** Geteilte Datei (content://…) als multipart an /api/files hochladen. */
    fun uploadUri(ctx: Context, uri: android.net.Uri): JSONObject {
        var name = "datei"
        ctx.contentResolver.query(uri, arrayOf(android.provider.OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
            if (c.moveToFirst()) name = c.getString(0) ?: name
        }
        val mime = ctx.contentResolver.getType(uri) ?: "application/octet-stream"
        val boundary = "jarvis" + System.currentTimeMillis()
        val conn = open(ctx, "/api/files", 300_000)
        try {
            conn.requestMethod = "POST"
            conn.doOutput = true
            conn.setChunkedStreamingMode(64 * 1024)
            conn.setRequestProperty("Content-Type", "multipart/form-data; boundary=$boundary")
            conn.outputStream.use { out ->
                out.write(("--$boundary\r\nContent-Disposition: form-data; name=\"source\"\r\n\r\nHandy (geteilt)\r\n").toByteArray())
                out.write(("--$boundary\r\nContent-Disposition: form-data; name=\"file\"; filename=\"" +
                    java.net.URLEncoder.encode(name, "UTF-8").replace("+", "%20") + "\"\r\nContent-Type: $mime\r\n\r\n").toByteArray())
                ctx.contentResolver.openInputStream(uri)?.use { it.copyTo(out) } ?: throw IllegalStateException("Datei nicht lesbar")
                out.write("\r\n--$boundary--\r\n".toByteArray())
            }
            return read(conn)
        } finally {
            conn.disconnect()
        }
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
