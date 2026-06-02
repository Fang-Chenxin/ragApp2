package com.example.agentchat

import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.Build
import android.os.Bundle
import android.text.InputType
import android.util.Log
import android.graphics.Typeface
import android.view.Menu
import android.view.MenuItem
import android.view.MotionEvent
import android.widget.Button
import android.widget.EditText
import android.widget.ImageButton
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Toast
import android.widget.TextView
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.widget.Toolbar
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.google.gson.Gson
import com.google.gson.annotations.SerializedName
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import io.noties.markwon.Markwon
import java.io.IOException
import java.util.UUID


data class ChatMessage(
    val role: String,
    val content: String,
    val thinking: String? = null,
    val timings: Map<String, Any>? = null,
    val analysisExpanded: Boolean = true
)
data class ChatRequest(
    val messages: List<ChatMessage>,
    @SerializedName("user_query") val userQuery: String,
    @SerializedName("user_id") val userId: String,
    @SerializedName("conv_id") val convId: String? = null,
    val model: String? = null,
    @SerializedName("model_config") val modelConfig: ModelOption? = null
)
data class ChatResponse(
    val reply: String,
    @SerializedName("history_saved") val historySaved: Boolean = true,
    @SerializedName("conv_id") val convId: String? = null
)
data class StreamResponse(
    val content: String = "",
    val thinking: String = "",
    val status: String = "",
    val phase: String? = null,
    val agent: String? = null,
    @SerializedName("analysis") val analysis: String = "",
    @SerializedName("summary") val summary: String = "",
    @SerializedName("selected_product_ids") val selectedProductIds: List<String> = emptyList(),
    @SerializedName("conv_id") val convId: String? = null,
    @SerializedName("history_saved") val historySaved: Boolean = true,
    val done: Boolean = false,
    val error: String? = null,
    val timings: Map<String, Any>? = null
)
data class HistoryResponse(
    val userId: String,
    @SerializedName("conv_id") val convId: String?,
    val total: Int,
    val history: List<HistoryMessage>
)
data class HistoryMessage(
    val role: String,
    val content: String,
    val timestamp: String? = null,
    val thinking: String? = null
)
data class ModelOption(
    val id: String,
    val name: String? = null,
    val source: String? = "server",
    @SerializedName("base_url") val baseUrl: String? = null,
    @SerializedName("api_key") val apiKey: String? = null
)
data class ModelsResponse(
    @SerializedName("default_model") val defaultModel: String,
    val models: List<ModelOption> = emptyList()
)


class MainActivity : AppCompatActivity() {
    private val messages = mutableListOf<ChatMessage>()
    private lateinit var adapter: ChatAdapter
    private lateinit var recyclerView: RecyclerView
    private lateinit var editText: EditText
    private lateinit var toolbarTitleText: TextView
    private lateinit var modelSelectorContainer: LinearLayout
    private lateinit var modelSelectorText: TextView
    
    private val client = OkHttpClient.Builder()
        .connectTimeout(30, java.util.concurrent.TimeUnit.SECONDS)
        .readTimeout(120, java.util.concurrent.TimeUnit.SECONDS)
        .writeTimeout(30, java.util.concurrent.TimeUnit.SECONDS)
        .build()
    
    private val gson = Gson()
    
    private lateinit var prefs: SharedPreferences
    private lateinit var userId: String
    private var currentConvId: String? = null
    private var currentConvTitle: String = "智能助手"
    private var serverModels: List<ModelOption> = emptyList()

    companion object {
        private const val TOUCH_LOG_TAG = "SelectionTouch"
        private const val PREFS_NAME = "chat_prefs"
        private const val KEY_USER_ID = "user_id"
        private const val REQUEST_CONVERSATION = 1001
        private const val REQUEST_CONFIG = 1002
    }

    private fun getBackendUrl(): String {
        return ConfigManager.getBackendUrl(this)
    }
    
    private fun getBackendUrlWithPath(path: String): String {
        return "${getBackendUrl()}$path"
    }

    private fun updateToolbarTitle() {
        if (::toolbarTitleText.isInitialized) {
            toolbarTitleText.text = currentConvTitle
        }
        supportActionBar?.title = currentConvTitle
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        
        // 初始化 Toolbar
        val toolbar = findViewById<Toolbar>(R.id.toolbar)
        setSupportActionBar(toolbar)
        supportActionBar?.setDisplayShowTitleEnabled(false)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)
        
        // 初始化 SharedPreferences 和用户标识
        prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        userId = getOrCreateUserId()
        
        recyclerView = findViewById(R.id.chatRecyclerView)
        editText = findViewById(R.id.messageEditText)
        toolbarTitleText = findViewById(R.id.toolbarTitleText)
        modelSelectorContainer = findViewById(R.id.modelSelectorContainer)
        modelSelectorText = findViewById(R.id.modelSelectorText)
        updateToolbarTitle()
        val sendButton = findViewById<Button>(R.id.sendButton)

        recyclerView.layoutManager = LinearLayoutManager(this)
        resetChatAdapter()
        updateModelSelectorText()
        loadModelsFromServer()
        modelSelectorContainer.setOnClickListener {
            refreshModelsAndShowSelector()
        }
        modelSelectorText.setOnClickListener {
            refreshModelsAndShowSelector()
        }
        recyclerView.setOnTouchListener { _, event ->
            Log.d(TOUCH_LOG_TAG, "RecyclerView.onTouch action=${motionActionName(event)} x=${event.x} y=${event.y}")
            false
        }
        recyclerView.addOnItemTouchListener(object : RecyclerView.SimpleOnItemTouchListener() {
            override fun onInterceptTouchEvent(rv: RecyclerView, e: MotionEvent): Boolean {
                Log.d(TOUCH_LOG_TAG, "RecyclerView.onInterceptTouch action=${motionActionName(e)} x=${e.x} y=${e.y}")
                return false
            }
        })
        
        // 应用启动时加载历史记录
        loadHistoryFromServer()

        sendButton.setOnClickListener {
            try {
                val userText = editText.text.toString().trim()
                if (userText.isNotEmpty()) {
                    if (!isNetworkAvailable()) {
                        Toast.makeText(this, "网络不可用，请检查网络连接", Toast.LENGTH_SHORT).show()
                        return@setOnClickListener
                    }
                    val incompleteModel = getIncompleteSelectedCustomModel()
                    if (incompleteModel != null) {
                        Toast.makeText(this, "请先补全自定义模型的 Base URL 和 API Key", Toast.LENGTH_SHORT).show()
                        showCustomModelDialog(incompleteModel)
                        return@setOnClickListener
                    }
                    messages.add(ChatMessage("user", userText))
                    val position = messages.size - 1
                    adapter.notifyItemInserted(position)
                    recyclerView.scrollToPosition(position)
                    editText.text.clear()
                    sendMessageToBackend()
                }
            } catch (e: Exception) {
                e.printStackTrace()
                Toast.makeText(this, "发送失败: ${e.message}", Toast.LENGTH_SHORT).show()
            }
        }
    }
    
    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        menuInflater.inflate(R.menu.menu_main, menu)
        return true
    }
    
    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        return when (item.itemId) {
            android.R.id.home -> {
                // 左上角按钮 - 打开会话管理
                openConversationsActivity()
                true
            }
            R.id.action_new_chat -> {
                startNewConversation()
                true
            }
            R.id.action_clear_history -> {
                showClearHistoryDialog()
                true
            }
            R.id.action_settings -> {
                showServerConfigDialog()
                true
            }
            else -> super.onOptionsItemSelected(item)
        }
    }

    /**
     * 显示服务器地址配置对话框
     */
    private fun showServerConfigDialog() {
        val editText = EditText(this).apply {
            hint = "例如: http://192.168.1.106:8000"
            setText(getBackendUrl())
            setPadding(48, 32, 48, 32)
        }
        
        val dialog = AlertDialog.Builder(this)
            .setTitle("服务器地址设置")
            .setMessage("请输入后端服务器的地址")
            .setView(editText)
            .setPositiveButton("保存") { _, _ ->
                val url = editText.text.toString().trim()
                if (url.isNotEmpty()) {
                    if (!url.startsWith("http://") && !url.startsWith("https://")) {
                        Toast.makeText(this, "URL必须以http://或https://开头", Toast.LENGTH_SHORT).show()
                        return@setPositiveButton
                    }
                    ConfigManager.setBackendUrl(this, url)
                    loadModelsFromServer(showError = true)
                    Toast.makeText(this, "服务器地址已保存", Toast.LENGTH_SHORT).show()
                }
            }
            .setNegativeButton("取消", null)
            .create()

        dialog.show()
        dialog.findViewById<TextView>(android.R.id.message)?.setTextIsSelectable(true)
    }

    private fun loadModelsFromServer(showError: Boolean = false, onComplete: (() -> Unit)? = null) {
        val request = Request.Builder()
            .url(getBackendUrlWithPath("/api/models"))
            .get()
            .build()

        client.newCall(request).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                runOnUiThread {
                    updateModelSelectorText()
                    if (showError) {
                        Toast.makeText(this@MainActivity, "刷新模型列表失败: ${e.message}", Toast.LENGTH_SHORT).show()
                    }
                    onComplete?.invoke()
                }
            }

            override fun onResponse(call: Call, response: Response) {
                response.use {
                    if (!response.isSuccessful) {
                        runOnUiThread {
                            if (showError) {
                                Toast.makeText(this@MainActivity, "刷新模型列表失败: ${response.code}", Toast.LENGTH_SHORT).show()
                            }
                            onComplete?.invoke()
                        }
                        return
                    }
                    val responseBody = response.body?.string()
                    try {
                        val modelsResponse = gson.fromJson(responseBody, ModelsResponse::class.java)
                        runOnUiThread {
                            serverModels = modelsResponse.models
                                .filter { it.id.isNotBlank() }
                                .map {
                                    ModelOption(
                                        id = it.id,
                                        name = it.name.orEmpty().ifEmpty { it.id },
                                        source = it.source.orEmpty().ifEmpty { "server" },
                                        baseUrl = it.baseUrl.orEmpty(),
                                        apiKey = it.apiKey.orEmpty()
                                    )
                                }
                            val allOptions = getAllModelOptions()
                            val selectedModel = ConfigManager.getSelectedModel(this@MainActivity)
                            val selectedStillAvailable = allOptions.any { it.id == selectedModel }
                            if (!selectedStillAvailable) {
                                val fallbackModel = modelsResponse.defaultModel
                                    .takeIf { defaultModel -> allOptions.any { it.id == defaultModel } }
                                    ?: allOptions.firstOrNull()?.id
                                if (fallbackModel.isNullOrBlank()) {
                                    ConfigManager.clearSelectedModel(this@MainActivity)
                                } else {
                                    ConfigManager.setSelectedModel(this@MainActivity, fallbackModel)
                                }
                            }
                            updateModelSelectorText()
                            onComplete?.invoke()
                        }
                    } catch (e: Exception) {
                        e.printStackTrace()
                        runOnUiThread {
                            if (showError) {
                                Toast.makeText(this@MainActivity, "模型列表解析失败", Toast.LENGTH_SHORT).show()
                            }
                            onComplete?.invoke()
                        }
                    }
                }
            }
        })
    }

    private fun refreshModelsAndShowSelector() {
        modelSelectorText.text = "正在刷新模型列表..."
        loadModelsFromServer(showError = true) {
            showModelSelectorDialog()
        }
    }

    private fun getAllModelOptions(): List<ModelOption> {
        val customModels = ConfigManager.getCustomModels(this).map {
            ModelOption(
                id = it.id,
                name = it.name.orEmpty().ifEmpty { it.id },
                source = it.source.orEmpty().ifEmpty { "local" },
                baseUrl = it.baseUrl.orEmpty(),
                apiKey = it.apiKey.orEmpty()
            )
        }
        return (serverModels + customModels)
            .filter { it.id.isNotBlank() }
            .distinctBy { it.id }
    }

    private fun getSelectedModelOption(): ModelOption? {
        val selectedModel = getSelectedModel()
        return getAllModelOptions().firstOrNull { it.id == selectedModel }
    }

    private fun getSelectedModel(): String? {
        return ConfigManager.getSelectedModel(this)?.takeIf { it.isNotBlank() }
            ?: getAllModelOptions().firstOrNull()?.id
    }

    private fun updateModelSelectorText() {
        val selectedModel = getSelectedModel()
        val selectedOption = getAllModelOptions().firstOrNull { it.id == selectedModel }
        val label = selectedOption?.name.orEmpty().ifEmpty { selectedModel ?: "选择模型" }
        val sourceLabel = selectedOption?.sourceLabel()?.takeIf { it.isNotBlank() }
        modelSelectorText.text = if (sourceLabel != null) "$label · $sourceLabel ▾" else "$label ▾"
    }

    private fun showModelSelectorDialog() {
        val serverOptions = serverModels
            .filter { it.id.isNotBlank() }
            .distinctBy { it.id }
        val customOptions = ConfigManager.getCustomModels(this)
            .map {
                ModelOption(
                    id = it.id,
                    name = it.name.orEmpty().ifEmpty { it.id },
                    source = it.source.orEmpty().ifEmpty { "local" },
                    baseUrl = it.baseUrl.orEmpty(),
                    apiKey = it.apiKey.orEmpty()
                )
            }
            .filter { it.id.isNotBlank() }
            .filterNot { customOption -> serverOptions.any { it.id == customOption.id } }
            .distinctBy { it.id }

        val container = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(0, 8, 0, 8)
        }
        val scrollView = ScrollView(this).apply {
            addView(container)
        }

        lateinit var dialog: AlertDialog

        fun addHeader(title: String) {
            container.addView(TextView(this).apply {
                text = title
                setTypeface(null, Typeface.BOLD)
                textSize = 13f
                setTextColor(0xFF666666.toInt())
                setPadding(48, 28, 48, 8)
            })
        }

        fun addStatusRow(message: String) {
            container.addView(TextView(this).apply {
                text = message
                textSize = 14f
                setTextColor(0xFF777777.toInt())
                setPadding(48, 16, 48, 24)
            })
        }

        fun addModelRow(option: ModelOption, isCustom: Boolean) {
            val row = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                setPadding(48, 16, 32, 16)
                minimumHeight = 72
                isClickable = true
                isFocusable = true
            }
            val textContainer = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
            }
            val selectedPrefix = if (option.id == getSelectedModel()) "[当前] " else ""
            textContainer.addView(TextView(this).apply {
                text = selectedPrefix + option.name.orEmpty().ifEmpty { option.id }
                textSize = 16f
                setTextColor(0xFF222222.toInt())
            })
            textContainer.addView(TextView(this).apply {
                text = "${option.id} · ${option.sourceLabel()}"
                textSize = 12f
                setTextColor(0xFF777777.toInt())
            })
            row.addView(textContainer)

            row.setOnClickListener {
                if (switchModel(option, requireCompleteConnectionConfig = isCustom)) {
                    dialog.dismiss()
                }
            }

            if (isCustom) {
                row.addView(createModelActionButton(android.R.drawable.ic_menu_edit, "编辑自定义模型") {
                    dialog.dismiss()
                    showCustomModelDialog(option)
                })
                row.addView(createModelActionButton(android.R.drawable.ic_menu_delete, "删除自定义模型") {
                    dialog.dismiss()
                    showDeleteCustomModelDialog(option)
                })
            }
            container.addView(row)
        }

        addHeader("服务器提供的模型")
        if (serverOptions.isNotEmpty()) {
            serverOptions.forEach { addModelRow(it, isCustom = false) }
        } else {
            addStatusRow("服务端未返回可用模型")
        }
        if (customOptions.isNotEmpty()) {
            addHeader("用户自定义模型")
            customOptions.forEach { addModelRow(it, isCustom = true) }
        }

        container.addView(TextView(this).apply {
            text = "+ 添加自定义模型..."
            textSize = 16f
            setTextColor(0xFF222222.toInt())
            setPadding(48, 24, 48, 24)
            isClickable = true
            isFocusable = true
            setOnClickListener {
                dialog.dismiss()
                showAddCustomModelDialog()
            }
        })

        dialog = AlertDialog.Builder(this)
            .setTitle("选择对话模型")
            .setView(scrollView)
            .setNegativeButton("取消", null)
            .create()
        dialog.show()
    }

    private fun createModelActionButton(iconResId: Int, description: String, onClick: () -> Unit): ImageButton {
        return ImageButton(this).apply {
            setImageResource(iconResId)
            contentDescription = description
            background = null
            setPadding(20, 20, 20, 20)
            layoutParams = LinearLayout.LayoutParams(88, 88)
            setOnClickListener { onClick() }
        }
    }

    private fun switchModel(option: ModelOption, requireCompleteConnectionConfig: Boolean = false): Boolean {
        if (requireCompleteConnectionConfig && !option.hasCompleteConnectionConfig()) {
            Toast.makeText(this, "请先补全自定义模型的 Base URL 和 API Key", Toast.LENGTH_SHORT).show()
            showCustomModelDialog(option)
            return false
        }
        ConfigManager.setSelectedModel(this, option.id)
        updateModelSelectorText()
        Toast.makeText(this, "已切换模型：${option.name.orEmpty().ifEmpty { option.id }}", Toast.LENGTH_SHORT).show()
        return true
    }

    private fun ModelOption.isLocalModel(): Boolean {
        return ConfigManager.hasCustomModel(this@MainActivity, id)
    }

    private fun ModelOption.hasCompleteConnectionConfig(): Boolean {
        return baseUrl.orEmpty().isNotBlank() && apiKey.orEmpty().isNotBlank()
    }

    private fun getIncompleteSelectedCustomModel(): ModelOption? {
        val selectedModelOption = getSelectedModelOption() ?: return null
        if (serverModels.any { it.id == selectedModelOption.id }) return null

        return selectedModelOption.takeIf {
            it.isLocalModel() && !it.hasCompleteConnectionConfig()
        }
    }

    private fun ModelOption.sourceLabel(): String {
        val cleanSource = source.orEmpty().trim()
        return when {
            cleanSource.equals("server", ignoreCase = true) -> "服务端"
            cleanSource.equals("local", ignoreCase = true) -> "本机"
            cleanSource.isBlank() -> "未知来源"
            else -> cleanSource
        }
    }

    private fun showDeleteCustomModelDialog(option: ModelOption) {
        val modelName = option.name.orEmpty().ifEmpty { option.id }
        AlertDialog.Builder(this)
            .setTitle("删除自定义模型")
            .setMessage("确定删除“$modelName”吗？删除后不会影响服务器提供的模型列表。")
            .setPositiveButton("删除") { _, _ ->
                ConfigManager.deleteCustomModel(this, option.id)
                loadModelsFromServer {
                    updateModelSelectorText()
                }
                Toast.makeText(this, "已删除自定义模型：$modelName", Toast.LENGTH_SHORT).show()
            }
            .setNegativeButton("取消", null)
            .show()
    }

    private fun showAddCustomModelDialog() {
        showCustomModelDialog()
    }

    private fun showCustomModelDialog(existingModel: ModelOption? = null) {
        val container = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(48, 24, 48, 0)
        }
        fun createModelInput(hintText: String, longText: Boolean = false): EditText {
            return EditText(this).apply {
                hint = hintText
                inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE
                setSingleLine(false)
                minLines = 1
                maxLines = if (longText) 3 else 2
                setHorizontallyScrolling(false)
            }
        }

        val modelNameEditText = EditText(this).apply {
            hint = "显示名称，仅用于列表显示，例如 DeepSeek V3"
            inputType = InputType.TYPE_CLASS_TEXT
            setText(existingModel?.name.orEmpty().ifEmpty { existingModel?.id.orEmpty() })
        }
        val sourceEditText = createModelInput("来源，仅用于显示，例如 OpenAI / DeepSeek / 硅基流动").apply {
            setText(existingModel?.source.orEmpty().takeIf { it != "local" }.orEmpty())
        }
        val modelIdEditText = createModelInput("模型 ID，用于请求，例如 deepseek-chat / gpt-4.1-mini", longText = true).apply {
            setText(existingModel?.id.orEmpty())
        }
        val baseUrlEditText = createModelInput("Base URL，用于请求，例如 https://api.openai.com", longText = true).apply {
            setText(existingModel?.baseUrl.orEmpty())
        }
        val apiKeyEditText = createModelInput("API Key，用于请求，仅保存在本机", longText = true).apply {
            setText(existingModel?.apiKey.orEmpty())
        }
        container.addView(modelNameEditText)
        container.addView(sourceEditText)
        container.addView(modelIdEditText)
        container.addView(baseUrlEditText)
        container.addView(apiKeyEditText)
        val scrollView = ScrollView(this).apply {
            addView(container)
        }

        val isEditing = existingModel != null
        val dialog = AlertDialog.Builder(this)
            .setTitle(if (isEditing) "编辑自定义模型" else "添加自定义模型")
            .setMessage("支持 OpenAI-compatible 接口。名称和来源仅影响显示；模型 ID、Base URL、API Key 会用于实际请求。Base URL 请填写供应商提供的接口地址。")
            .setView(scrollView)
            .setPositiveButton(if (isEditing) "保存" else "添加", null)
            .setNegativeButton("取消", null)
            .create()

        dialog.setOnShowListener {
            dialog.findViewById<TextView>(android.R.id.message)?.setTextIsSelectable(true)
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
                val modelId = modelIdEditText.text.toString().trim()
                val modelName = modelNameEditText.text.toString().trim().ifEmpty { modelId }
                val source = sourceEditText.text.toString().trim().ifEmpty { "local" }
                val baseUrl = baseUrlEditText.text.toString().trim()
                val apiKey = apiKeyEditText.text.toString().trim()
                val originalId = existingModel?.id

                when {
                    modelId.isEmpty() -> {
                        modelIdEditText.error = "模型 ID 不能为空"
                        modelIdEditText.requestFocus()
                        return@setOnClickListener
                    }
                    baseUrl.isEmpty() -> {
                        baseUrlEditText.error = "Base URL 不能为空"
                        baseUrlEditText.requestFocus()
                        return@setOnClickListener
                    }
                    !baseUrl.startsWith("http://") && !baseUrl.startsWith("https://") -> {
                        baseUrlEditText.error = "Base URL 必须以 http:// 或 https:// 开头"
                        baseUrlEditText.requestFocus()
                        return@setOnClickListener
                    }
                    apiKey.isEmpty() -> {
                        apiKeyEditText.error = "API Key 不能为空"
                        apiKeyEditText.requestFocus()
                        return@setOnClickListener
                    }
                    originalId != modelId && ConfigManager.hasCustomModel(this, modelId) -> {
                        modelIdEditText.error = "该模型 ID 已存在"
                        modelIdEditText.requestFocus()
                        return@setOnClickListener
                    }
                }

                ConfigManager.saveCustomModel(
                    this,
                    LocalModelConfig(
                        id = modelId,
                        name = modelName,
                        source = source,
                        baseUrl = baseUrl,
                        apiKey = apiKey
                    ),
                    originalId
                )
                if (!isEditing || ConfigManager.getSelectedModel(this) == originalId) {
                    ConfigManager.setSelectedModel(this, modelId)
                }
                updateModelSelectorText()
                Toast.makeText(
                    this,
                    if (isEditing) "已保存自定义模型：$modelName" else "已添加并切换模型：$modelName",
                    Toast.LENGTH_SHORT
                ).show()
                dialog.dismiss()
            }
        }
        dialog.show()
    }
    
    /**
     * 打开会话管理页面
     */
    private fun openConversationsActivity() {
        val intent = Intent(this, ConversationsActivity::class.java).apply {
            putExtra(ConversationsActivity.EXTRA_USER_ID, userId)
            putExtra(ConversationsActivity.EXTRA_CURRENT_CONV, currentConvId)
        }
        startActivityForResult(intent, REQUEST_CONVERSATION)
    }
    
    /**
     * 从会话管理页面返回后的处理
     */
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        
        if (requestCode == REQUEST_CONVERSATION && resultCode == RESULT_OK) {
            val newConvId = data?.getStringExtra("conv_id")
            val newTitle = data?.getStringExtra("title")
            
            if (newConvId != null) {
                // 切换到指定会话
                currentConvId = newConvId
                currentConvTitle = newTitle ?: "智能助手"
                updateToolbarTitle()
                loadHistoryFromServer()
            } else {
                // 新建对话 - 清空页面
                currentConvId = null
                currentConvTitle = "智能助手"
                updateToolbarTitle()
                messages.clear()
                adapter.notifyDataSetChanged()
            }
        }
    }
    
    /**
     * 新建对话 - 只有当前会话有消息时才创建新会话
     */
    private fun startNewConversation() {
        // 检查当前会话是否有消息
        if (messages.isEmpty()) {
            // 当前是空会话，不需要创建新会话
            Toast.makeText(this, "当前已是新对话", Toast.LENGTH_SHORT).show()
            return
        }
        
        // 当前会话有消息，创建新会话
        val url = getBackendUrlWithPath("/api/conversations/$userId")
        val requestBody = "{}".toRequestBody("application/json".toMediaType())
        val request = Request.Builder()
            .url(url)
            .post(requestBody)
            .build()
        
        client.newCall(request).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                runOnUiThread {
                    Toast.makeText(this@MainActivity, "创建会话失败", Toast.LENGTH_SHORT).show()
                }
            }
            
            override fun onResponse(call: Call, response: Response) {
                response.use {
                    if (response.isSuccessful) {
                        val responseBody = response.body?.string()
                        try {
                            val responseJson = gson.fromJson(responseBody, Map::class.java)
                            val convData = responseJson["conversation"] as? Map<String, Any>
                            
                            runOnUiThread {
                                currentConvId = convData?.get("conv_id") as? String
                                currentConvTitle = convData?.get("title") as? String ?: "智能助手"
                                updateToolbarTitle()
                                
                                messages.clear()
                                adapter.notifyDataSetChanged()
                                Toast.makeText(this@MainActivity, "已开始新对话", Toast.LENGTH_SHORT).show()
                            }
                        } catch (e: Exception) {
                            e.printStackTrace()
                        }
                    }
                }
            }
        })
    }
    
    /**
     * 显示清除历史确认对话框
     */
    private fun showClearHistoryDialog() {
        val dialog = AlertDialog.Builder(this)
            .setTitle("清除历史")
            .setMessage("确定要清除当前会话的所有消息吗？此操作不可恢复。")
            .setPositiveButton("确定") { _, _ ->
                clearHistoryOnServer()
            }
            .setNegativeButton("取消", null)
            .create()

        dialog.show()
        dialog.findViewById<TextView>(android.R.id.message)?.setTextIsSelectable(true)
    }
    
    /**
     * 清除服务器上的当前会话历史
     */
    private fun clearHistoryOnServer() {
        var url = getBackendUrlWithPath("/api/history/$userId")
        if (currentConvId != null) {
            url += "?conv_id=$currentConvId"
        }
        
        val request = Request.Builder()
            .url(url)
            .delete()
            .build()
        
        client.newCall(request).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                runOnUiThread {
                    Toast.makeText(this@MainActivity, "清除失败: ${e.message}", Toast.LENGTH_SHORT).show()
                }
            }
            
            override fun onResponse(call: Call, response: Response) {
                response.use {
                    if (response.isSuccessful) {
                        runOnUiThread {
                            messages.clear()
                            adapter.notifyDataSetChanged()
                            editText.text.clear()
                            Toast.makeText(this@MainActivity, "历史已清除", Toast.LENGTH_SHORT).show()
                        }
                    } else {
                        runOnUiThread {
                            Toast.makeText(this@MainActivity, "清除失败: ${response.code}", Toast.LENGTH_SHORT).show()
                        }
                    }
                }
            }
        })
    }
    
    private fun getOrCreateUserId(): String {
        // 尝试获取已保存的用户ID
        var savedUserId = prefs.getString(KEY_USER_ID, null)
        
        if (savedUserId == null) {
            // 生成新的用户ID（使用设备ID + UUID确保唯一性）
            savedUserId = "android_${getUniqueDeviceId()}_${UUID.randomUUID().toString().take(8)}"
            prefs.edit().putString(KEY_USER_ID, savedUserId).apply()
        }
        
        return savedUserId
    }

    private fun motionActionName(event: MotionEvent): String {
        return when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> "DOWN"
            MotionEvent.ACTION_UP -> "UP"
            MotionEvent.ACTION_MOVE -> "MOVE"
            MotionEvent.ACTION_CANCEL -> "CANCEL"
            MotionEvent.ACTION_POINTER_DOWN -> "POINTER_DOWN"
            MotionEvent.ACTION_POINTER_UP -> "POINTER_UP"
            else -> event.actionMasked.toString()
        }
    }

    private fun getUniqueDeviceId(): String {
        // 获取唯一的设备标识符
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            // Android 10+ 使用随机UUID（设备ID不再可访问）
            UUID.randomUUID().toString().take(8)
        } else {
            @Suppress("DEPRECATION")
            val androidId = android.provider.Settings.Secure.getString(
                contentResolver,
                android.provider.Settings.Secure.ANDROID_ID
            )
            androidId.take(8)
        }
    }

    private fun resetChatAdapter() {
        adapter = ChatAdapter(messages)
        adapter.attachMarkwon(Markwon.create(this))
        recyclerView.adapter = adapter
        recyclerView.itemAnimator = null
    }
    
    private fun loadHistoryFromServer() {
        var url = getBackendUrlWithPath("/api/history/$userId")
        if (currentConvId != null) {
            url += "?conv_id=$currentConvId"
        }
        
        val request = Request.Builder()
            .url(url)
            .get()
            .build()
        
        client.newCall(request).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                runOnUiThread {
                    Toast.makeText(this@MainActivity, "加载历史记录失败", Toast.LENGTH_SHORT).show()
                }
            }
            
            override fun onResponse(call: Call, response: Response) {
                response.use {
                    if (response.isSuccessful) {
                        val responseBody = response.body?.string()
                        try {
                            val historyResponse = gson.fromJson(responseBody, HistoryResponse::class.java)
                            runOnUiThread {
                                // 更新当前会话ID
                                currentConvId = historyResponse.convId
                                
                                // 将历史记录加载到消息列表（完全重构建，不依赖内存旧数据）
                                recyclerView.stopScroll()
                                recyclerView.clearFocus()
                                recyclerView.recycledViewPool.clear()
                                recyclerView.adapter = null
                                messages.clear()
                                
                                // 处理历史记录，完全重新构建消息列表
                                for (hMsg in historyResponse.history) {
                                    if (hMsg.role == "user") {
                                        messages.add(ChatMessage("user", hMsg.content))
                                    } else if (hMsg.role == "assistant") {
                                        messages.add(ChatMessage("assistant", hMsg.content, hMsg.thinking, null, true))
                                    }
                                }

                                resetChatAdapter()
                                
                                if (messages.isNotEmpty()) {
                                    // 延迟滚动，确保布局完成后文本选择状态正确
                                    recyclerView.post {
                                        recyclerView.scrollToPosition(messages.size - 1)
                                    }
                                }
                                
                                if (historyResponse.total > 0) {
                                    Toast.makeText(
                                        this@MainActivity,
                                        "已加载 ${historyResponse.total} 条消息",
                                        Toast.LENGTH_SHORT
                                    ).show()
                                }
                            }
                        } catch (e: Exception) {
                            e.printStackTrace()
                        }
                    }
                }
            }
        })
    }

    private fun sendMessageToBackend() {
        sendMessageToBackendWithRetry(retryCount = 0)
    }
    
    private fun sendMessageToBackendWithRetry(retryCount: Int) {
        val lastUserMessage = messages.lastOrNull { it.role == "user" } ?: return
        // 构建给后端请求的历史消息，只保留 role 和 content，避免把本地状态消息带回后端
        val cleanedPreviousMessages = messages.dropLast(1)
            .filter { it.role == "user" || it.role == "assistant" }
            .map { ChatMessage(it.role, it.content, null) }

        val selectedModelOption = getSelectedModelOption()
        val localModelConfig = selectedModelOption?.takeIf {
            it.baseUrl.orEmpty().isNotBlank() || it.apiKey.orEmpty().isNotBlank()
        }
        val requestBodyJson = gson.toJson(
            ChatRequest(
                cleanedPreviousMessages,
                lastUserMessage.content,
                userId,
                currentConvId,
                getSelectedModel(),
                localModelConfig,
            )
        )
        val mediaType = "application/json; charset=utf-8".toMediaType()
        val body = requestBodyJson.toRequestBody(mediaType)
        val request = Request.Builder()
            .url(getBackendUrlWithPath("/api/chat"))
            .post(body)
            .build()

        var assistantMainMessageIndex = -1
        var selectedProductsDisplayIndex = -1
        val fullContent = StringBuilder()
        val fullAnalysis = StringBuilder()
        var hasReceivedData = false
        
        // 先添加助手的空占位消息
        runOnUiThread {
            messages.add(ChatMessage("assistant", "", "正在分析需求..."))
            assistantMainMessageIndex = messages.size - 1
            adapter.notifyItemInserted(assistantMainMessageIndex)
            recyclerView.scrollToPosition(assistantMainMessageIndex)
        }

        client.newCall(request).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                runOnUiThread {
                    val errorMessage = when {
                        e.message?.contains("timeout", ignoreCase = true) == true -> "请求超时，请稍后重试"
                        e.message?.contains("connection", ignoreCase = true) == true -> "网络连接失败"
                        else -> "网络请求失败: ${e.message}"
                    }
                    
                    if (retryCount < 2 && e.message?.contains("timeout", ignoreCase = true) == true) {
                        if (assistantMainMessageIndex >= 0) {
                            updateAssistantStatus(assistantMainMessageIndex, "正在重试... (${retryCount + 1}/2)")
                        }
                        Toast.makeText(this@MainActivity, "正在重试... (${retryCount + 1}/2)", Toast.LENGTH_SHORT).show()
                        sendMessageToBackendWithRetry(retryCount + 1)
                    } else {
                        if (assistantMainMessageIndex >= 0) {
                            updateAssistantMessage(assistantMainMessageIndex, "❌ $errorMessage", null)
                        } else {
                            addAssistantMessage("❌ $errorMessage")
                        }
                    }
                }
            }

            override fun onResponse(call: Call, response: Response) {
                response.use {
                    if (!response.isSuccessful) {
                        val responseBodyStr = response.body?.string()
                        runOnUiThread {
                            val errorMsg = when (response.code) {
                                404 -> "服务接口不存在"
                                500 -> "服务器内部错误"
                                503 -> "服务暂时不可用"
                                else -> "服务器错误: ${response.code}"
                            }
                            val detailed = if (!responseBodyStr.isNullOrEmpty()) "：${responseBodyStr.take(200)}" else ""
                            if (assistantMainMessageIndex >= 0) {
                                updateAssistantMessage(assistantMainMessageIndex, "❌ $errorMsg$detailed")
                            } else {
                                addAssistantMessage("❌ $errorMsg$detailed")
                            }
                        }
                        return
                    }
                    
                    try {
                        response.body?.byteStream()?.bufferedReader()?.use { reader ->
                            var line: String? = reader.readLine()
                            while (line != null) {
                                if (line.startsWith("data: ")) {
                                    hasReceivedData = true
                                    val jsonData = line.substring(6)
                                    try {
                                        val streamResponse = gson.fromJson(jsonData, StreamResponse::class.java)
                                        
                                        runOnUiThread {
                                            if (streamResponse.error != null) {
                                                if (assistantMainMessageIndex >= 0) {
                                                    updateAssistantMessage(assistantMainMessageIndex, "❌ 错误: ${streamResponse.error}", null)
                                                } else {
                                                    addAssistantMessage("❌ 错误: ${streamResponse.error}")
                                                }
                                                return@runOnUiThread
                                            }

                                            if (streamResponse.selectedProductIds.isNotEmpty()) {
                                                val selectedText = "✅ 已选中商品ID: ${streamResponse.selectedProductIds.joinToString(", ")}"
                                                if (selectedProductsDisplayIndex == -1) {
                                                    messages.add(ChatMessage("assistant", selectedText))
                                                    selectedProductsDisplayIndex = messages.size - 1
                                                    adapter.notifyItemInserted(selectedProductsDisplayIndex)
                                                } else {
                                                    messages[selectedProductsDisplayIndex] = ChatMessage("assistant", selectedText)
                                                    adapter.notifyItemChanged(selectedProductsDisplayIndex)
                                                }
                                                recyclerView.scrollToPosition(selectedProductsDisplayIndex)
                                            }

                                            if (streamResponse.status.isNotEmpty()) {
                                                if (streamResponse.phase == "saving_history") {
                                                    Toast.makeText(
                                                        this@MainActivity,
                                                        streamResponse.status,
                                                        Toast.LENGTH_SHORT
                                                    ).show()
                                                } else if (fullContent.isEmpty() && assistantMainMessageIndex >= 0) {
                                                    updateAssistantStatus(assistantMainMessageIndex, streamResponse.status)
                                                }
                                            }

                                            if (streamResponse.analysis.isNotEmpty()) {
                                                fullAnalysis.append(streamResponse.analysis)
                                                if (assistantMainMessageIndex >= 0) {
                                                    messages[assistantMainMessageIndex] = ChatMessage(
                                                        "assistant",
                                                        messages[assistantMainMessageIndex].content,
                                                        fullAnalysis.toString(),
                                                        messages[assistantMainMessageIndex].timings,
                                                        messages[assistantMainMessageIndex].analysisExpanded,
                                                    )
                                                    adapter.notifyItemChanged(assistantMainMessageIndex)
                                                }
                                            }
                                            
                                            // 处理正式内容片段
                                            if (streamResponse.content.isNotEmpty()) {
                                                fullContent.append(streamResponse.content)

                                                // 更新助手主消息内容
                                                if (assistantMainMessageIndex >= 0) {
                                                    messages[assistantMainMessageIndex] = ChatMessage(
                                                        "assistant",
                                                        fullContent.toString(),
                                                        fullAnalysis.toString().ifEmpty { null },
                                                        messages[assistantMainMessageIndex].timings,
                                                        messages[assistantMainMessageIndex].analysisExpanded,
                                                    )
                                                    adapter.notifyItemChanged(assistantMainMessageIndex)
                                                }
                                                recyclerView.scrollToPosition(assistantMainMessageIndex)
                                            }
                                            
                                            if (streamResponse.done) {
                                                if (streamResponse.convId != null) {
                                                    currentConvId = streamResponse.convId
                                                }
                                                
                                                // 最终主消息保存回复内容和耗时数据
                                                if (assistantMainMessageIndex >= 0) {
                                                    messages[assistantMainMessageIndex] = ChatMessage(
                                                        "assistant",
                                                        fullContent.toString(),
                                                        fullAnalysis.toString().ifEmpty { null },
                                                        streamResponse.timings,
                                                        messages[assistantMainMessageIndex].analysisExpanded,
                                                    )
                                                    adapter.notifyItemChanged(assistantMainMessageIndex)
                                                }
                                            }
                                        }
                                    } catch (e: Exception) {
                                        e.printStackTrace()
                                    }
                                }
                                line = reader.readLine()
                            }
                            
                            if (!hasReceivedData) {
                                runOnUiThread {
                                    addAssistantMessage("未收到有效响应数据")
                                }
                            }
                        }
                    } catch (e: Exception) {
                        runOnUiThread {
                            val errorMessage = when {
                                e.message?.contains("timeout", ignoreCase = true) == true -> "读取响应超时"
                                else -> "解析响应失败: ${e.message}"
                            }
                            addAssistantMessage(errorMessage)
                        }
                    }
                }
            }
        })
    }

    private fun updateAssistantMessage(index: Int, content: String) {
        updateAssistantMessage(index, content, messages.getOrNull(index)?.thinking)
    }

    private fun updateAssistantMessage(index: Int, content: String, thinking: String?) {
        if (index >= 0 && index < messages.size) {
            val originalTimings = messages[index].timings
            val originalAnalysisExpanded = messages[index].analysisExpanded
            messages[index] = ChatMessage("assistant", content, thinking, originalTimings, originalAnalysisExpanded)
            adapter.notifyItemChanged(index)
        }
    }

    private fun updateAssistantStatus(index: Int, status: String) {
        if (index >= 0 && index < messages.size) {
            val originalTimings = messages[index].timings
            val originalAnalysisExpanded = messages[index].analysisExpanded
            messages[index] = ChatMessage("assistant", "", status, originalTimings, originalAnalysisExpanded)
            adapter.notifyItemChanged(index)
        }
    }

    private fun addAssistantMessage(content: String) {
        messages.add(ChatMessage("assistant", content))
        val position = messages.size - 1
        adapter.notifyItemInserted(position)
        recyclerView.scrollToPosition(position)
    }

    private fun isNetworkAvailable(): Boolean {
        val connectivityManager = getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        val network = connectivityManager.activeNetwork ?: return false
        val capabilities = connectivityManager.getNetworkCapabilities(network) ?: return false
        return capabilities.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) ||
                capabilities.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) ||
                capabilities.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)
    }
}
