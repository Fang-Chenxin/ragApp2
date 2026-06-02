package com.example.agentchat

import android.content.Context
import com.google.gson.Gson
import com.google.gson.reflect.TypeToken

data class LocalModelConfig(
    val id: String,
    val name: String? = null,
    val source: String? = "local",
    val baseUrl: String? = "",
    val apiKey: String? = ""
)

object ConfigManager {
    private const val PREFS_NAME = "chat_config"
    private const val KEY_BACKEND_URL = "backend_url"
    private const val KEY_SELECTED_MODEL = "selected_model"
    private const val KEY_CUSTOM_MODELS = "custom_models"
    private const val DEFAULT_BACKEND_URL = "http://10.0.2.2:8000"

    private var cachedUrl: String? = null
    private var cachedSelectedModel: String? = null
    private val gson = Gson()

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

    fun getSelectedModel(context: Context): String? {
        if (cachedSelectedModel != null) {
            return cachedSelectedModel
        }
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        cachedSelectedModel = prefs.getString(KEY_SELECTED_MODEL, null)
        return cachedSelectedModel
    }

    fun setSelectedModel(context: Context, model: String) {
        cachedSelectedModel = model
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        prefs.edit().putString(KEY_SELECTED_MODEL, model).apply()
    }

    fun clearSelectedModel(context: Context) {
        cachedSelectedModel = null
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        prefs.edit().remove(KEY_SELECTED_MODEL).apply()
    }

    fun getCustomModels(context: Context): List<LocalModelConfig> {
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        val raw = prefs.getString(KEY_CUSTOM_MODELS, null) ?: return emptyList()
        return try {
            val type = object : TypeToken<List<LocalModelConfig>>() {}.type
            gson.fromJson<List<LocalModelConfig>>(raw, type).orEmpty()
                .filter { it.id.isNotBlank() }
        } catch (e: Exception) {
            emptyList()
        }
    }

    private fun cleanCustomModel(model: LocalModelConfig): LocalModelConfig {
        return LocalModelConfig(
            id = model.id.trim(),
            name = model.name.orEmpty().trim().ifEmpty { model.id.trim() },
            source = model.source.orEmpty().trim().ifEmpty { "local" },
            baseUrl = model.baseUrl.orEmpty().trim(),
            apiKey = model.apiKey.orEmpty().trim()
        )
    }

    fun addCustomModel(context: Context, model: LocalModelConfig) {
        saveCustomModel(context, model)
    }

    fun saveCustomModel(context: Context, model: LocalModelConfig, originalId: String? = null) {
        val cleanModel = cleanCustomModel(model)
        if (cleanModel.id.isEmpty()) return

        val cleanOriginalId = originalId.orEmpty().trim().ifEmpty { cleanModel.id }
        val models = getCustomModels(context)
            .filterNot { it.id == cleanOriginalId || it.id == cleanModel.id }
            .plus(cleanModel)
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        prefs.edit().putString(KEY_CUSTOM_MODELS, gson.toJson(models)).apply()
    }

    fun deleteCustomModel(context: Context, modelId: String) {
        val cleanModelId = modelId.trim()
        if (cleanModelId.isEmpty()) return

        val models = getCustomModels(context)
            .filterNot { it.id == cleanModelId }
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        prefs.edit().putString(KEY_CUSTOM_MODELS, gson.toJson(models)).apply()
        if (getSelectedModel(context) == cleanModelId) {
            clearSelectedModel(context)
        }
    }

    fun hasCustomModel(context: Context, modelId: String): Boolean {
        val cleanModelId = modelId.trim()
        return cleanModelId.isNotEmpty() && getCustomModels(context).any { it.id == cleanModelId }
    }

    fun isConfigured(context: Context): Boolean {
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        return prefs.contains(KEY_BACKEND_URL)
    }
}
