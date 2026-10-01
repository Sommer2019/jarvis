package de.jarvis.app

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import org.json.JSONArray
import org.json.JSONObject

/** Zugriff auf den Android-Kalender – genutzt von der App und vom Hintergrund-Dienst. */
object CalendarOps {
    fun allowed(ctx: Context) = Api.prefs(ctx).getBoolean("share_calendar", false) &&
        ctx.checkSelfPermission(Manifest.permission.READ_CALENDAR) == PackageManager.PERMISSION_GRANTED

    /** Kalender + Termine an Jarvis schicken (blockierend – nicht im UI-Thread aufrufen). */
    fun sync(ctx: Context): Int {
        val n = Api.post(ctx, "/api/phone/calendar", read(ctx)).optInt("saved")
        Api.prefs(ctx).edit().putLong("calendar_synced", System.currentTimeMillis()).apply()
        return n
    }

    fun read(ctx: Context): JSONObject {
        val cr = ctx.contentResolver
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
    fun run(ctx: Context, type: String, p: JSONObject): Boolean {
        if (ctx.checkSelfPermission(Manifest.permission.WRITE_CALENDAR) != PackageManager.PERMISSION_GRANTED) return false
        val cr = ctx.contentResolver
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
            true
        } catch (e: Exception) {
            android.util.Log.w("Jarvis", "Kalender-Aktion $type fehlgeschlagen", e)
            false
        }
    }
}
