package com.example.agentchat

import android.content.Context
import android.content.SharedPreferences

object ConfigManager {
    private const val PREFS_NAME = "chat_config"
    private const val KEY_BACKEND_URL = "backend_url"
    private const val DEFAULT_BACKEND_URL = "http://10.0.2.2:8000"

    private var cachedUrl: String? = null

    fun getBackendUrl(context: Context): String {
        if (cachedUrl != null) {
            return cachedUrl!!
        }
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        cachedUrl = prefs.getString(KEY_BACKEND_URL, DEFAULT_BACKEND_URL) ?: DEFAULT_BACKEND_URL
        return cachedUrl!!
    }

    fun setBackendUrl(context: Context, url: String) {
        cachedUrl = url
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        prefs.edit().putString(KEY_BACKEND_URL, url).apply()
    }

    fun isConfigured(context: Context): Boolean {
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        return prefs.contains(KEY_BACKEND_URL)
    }
}