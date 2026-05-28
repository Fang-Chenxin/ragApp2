package com.example.agentchat

import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.Build
import android.os.Bundle
import android.view.Menu
import android.view.MenuItem
import android.widget.Button
import android.widget.EditText
import android.widget.Toast
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
import java.io.IOException
import java.util.UUID


data class ChatMessage(val role: String, val content: String, val thinking: String? = null, val timings: Map<String, Any>? = null)
data class ChatRequest(
    val messages: List<ChatMessage>,
    @SerializedName("user_query") val userQuery: String,
    @SerializedName("user_id") val userId: String,
    @SerializedName("conv_id") val convId: String? = null,
    @SerializedName("include_thinking") val includeThinking: Boolean = false
)
data class ChatResponse(
    val reply: String,
    @SerializedName("history_saved") val historySaved: Boolean = true,
    @SerializedName("conv_id") val convId: String? = null
)
data class StreamResponse(
    val content: String = "",
    val thinking: String = "",
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


class MainActivity : AppCompatActivity() {
    private val messages = mutableListOf<ChatMessage>()
    private lateinit var adapter: ChatAdapter
    private lateinit var recyclerView: RecyclerView
    private lateinit var editText: EditText
    private lateinit var thinkingSwitch: android.widget.Switch
    private lateinit var requestThinkingSwitch: android.widget.Switch
    
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

    companion object {
        private const val PREFS_NAME = "chat_prefs"
        private const val KEY_USER_ID = "user_id"
        private const val KEY_INCLUDE_THINKING = "include_thinking"
        private const val KEY_REQUEST_THINKING = "request_thinking"
        private const val REQUEST_CONVERSATION = 1001
        private const val REQUEST_CONFIG = 1002
    }

    private fun getBackendUrl(): String {
        return ConfigManager.getBackendUrl(this)
    }
    
    private fun getBackendUrlWithPath(path: String): String {
        return "${getBackendUrl()}$path"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        
        // 初始化 Toolbar
        val toolbar = findViewById<Toolbar>(R.id.toolbar)
        setSupportActionBar(toolbar)
        supportActionBar?.title = currentConvTitle
        supportActionBar?.setDisplayHomeAsUpEnabled(true)
        
        // 初始化 SharedPreferences 和用户标识
        prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        userId = getOrCreateUserId()
        
        recyclerView = findViewById(R.id.chatRecyclerView)
        editText = findViewById(R.id.messageEditText)
        thinkingSwitch = findViewById(R.id.thinkingSwitch)
        requestThinkingSwitch = findViewById(R.id.requestThinkingSwitch)
        val sendButton = findViewById<Button>(R.id.sendButton)

        adapter = ChatAdapter(messages)
        recyclerView.layoutManager = LinearLayoutManager(this)
        recyclerView.adapter = adapter
        
        // 读取保存的思考开关状态
        thinkingSwitch.isChecked = prefs.getBoolean(KEY_INCLUDE_THINKING, false)
        thinkingSwitch.setOnCheckedChangeListener { _, isChecked ->
            prefs.edit().putBoolean(KEY_INCLUDE_THINKING, isChecked).apply()
            // 切换开关时，重新刷新历史记录以正确显示/隐藏思考过程
            reloadHistoryWithCurrentThinkingState()
        }

        // 读取保存的“请求思考”开关状态
        requestThinkingSwitch.isChecked = prefs.getBoolean(KEY_REQUEST_THINKING, true)
        requestThinkingSwitch.setOnCheckedChangeListener { _, isChecked ->
            prefs.edit().putBoolean(KEY_REQUEST_THINKING, isChecked).apply()
        }
        
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
        
        AlertDialog.Builder(this)
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
                    Toast.makeText(this, "服务器地址已保存", Toast.LENGTH_SHORT).show()
                }
            }
            .setNegativeButton("取消", null)
            .show()
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
                supportActionBar?.title = currentConvTitle
                loadHistoryFromServer()
            } else {
                // 新建对话 - 清空页面
                currentConvId = null
                currentConvTitle = "智能助手"
                supportActionBar?.title = currentConvTitle
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
                                supportActionBar?.title = currentConvTitle
                                
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
        AlertDialog.Builder(this)
            .setTitle("清除历史")
            .setMessage("确定要清除当前会话的所有消息吗？此操作不可恢复。")
            .setPositiveButton("确定") { _, _ ->
                clearHistoryOnServer()
            }
            .setNegativeButton("取消", null)
            .show()
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
    
    /**
     * 根据当前开关状态重新加载历史并正确渲染
     */
    private fun reloadHistoryWithCurrentThinkingState() {
        loadHistoryFromServer()
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
                                messages.clear()
                                val showThinking = thinkingSwitch.isChecked
                                
                                // 处理历史记录，完全重新构建消息列表
                                for (hMsg in historyResponse.history) {
                                    if (hMsg.role == "user") {
                                        messages.add(ChatMessage("user", hMsg.content))
                                    } else if (hMsg.role == "assistant") {
                                        // 如果有思考过程且开关打开，先显示思考消息
                                        if (showThinking && !hMsg.thinking.isNullOrEmpty()) {
                                            messages.add(ChatMessage("thinking", "🤔 " + hMsg.thinking))
                                        }
                                        // 再添加助手正式回复，把思考内容完整存到本地
                                        messages.add(ChatMessage("assistant", hMsg.content, hMsg.thinking))
                                    }
                                }
                                
                                adapter.notifyDataSetChanged()
                                
                                if (messages.isNotEmpty()) {
                                    recyclerView.scrollToPosition(messages.size - 1)
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
        // 构建给后端请求的历史消息，彻底剥离thinking字段，只保留 role 和 content，绝对不混入思考内容
        val cleanedPreviousMessages = messages.dropLast(1)
            .filter { it.role == "user" || it.role == "assistant" }  // 过滤掉本地临时的 thinking 显示消息
            .map { ChatMessage(it.role, it.content, null) }  // 强制把thinking置空，结构纯净化

        val showThinkingDuringStream = thinkingSwitch.isChecked
        val requestThinking = requestThinkingSwitch.isChecked
        val requestBodyJson = gson.toJson(
            ChatRequest(cleanedPreviousMessages, lastUserMessage.content, userId, currentConvId, requestThinking)
        )
        val mediaType = "application/json; charset=utf-8".toMediaType()
        val body = requestBodyJson.toRequestBody(mediaType)
        val request = Request.Builder()
            .url(getBackendUrlWithPath("/api/chat/stream"))
            .post(body)
            .build()

        var assistantMainMessageIndex = -1
        var thinkingDisplayIndex = -1
        val fullContent = StringBuilder()
        val fullThinking = StringBuilder()
        var hasReceivedData = false
        
        // 先添加助手的空占位消息
        runOnUiThread {
            messages.add(ChatMessage("assistant", "🤔 正在思考...", ""))
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
                            updateAssistantMessage(assistantMainMessageIndex, "🔄 正在重试... (${retryCount + 1}/2)")
                        }
                        Toast.makeText(this@MainActivity, "正在重试... (${retryCount + 1}/2)", Toast.LENGTH_SHORT).show()
                        sendMessageToBackendWithRetry(retryCount + 1)
                    } else {
                        if (assistantMainMessageIndex >= 0) {
                            updateAssistantMessage(assistantMainMessageIndex, "❌ $errorMessage")
                        } else {
                            addAssistantMessage("❌ $errorMessage")
                        }
                    }
                }
            }

            override fun onResponse(call: Call, response: Response) {
                response.use {
                    if (!response.isSuccessful) {
                        runOnUiThread {
                            val errorMsg = when (response.code) {
                                404 -> "服务接口不存在"
                                500 -> "服务器内部错误"
                                503 -> "服务暂时不可用"
                                else -> "服务器错误: ${response.code}"
                            }
                            if (assistantMainMessageIndex >= 0) {
                                updateAssistantMessage(assistantMainMessageIndex, "❌ $errorMsg")
                            } else {
                                addAssistantMessage("❌ $errorMsg")
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
                                                    updateAssistantMessage(assistantMainMessageIndex, "❌ 错误: ${streamResponse.error}")
                                                } else {
                                                    addAssistantMessage("❌ 错误: ${streamResponse.error}")
                                                }
                                                return@runOnUiThread
                                            }
                                            
                                            // 1. 处理思考片段
                                            if (streamResponse.thinking.isNotEmpty()) {
                                                fullThinking.append(streamResponse.thinking)
                                                
                                                // 只有当开关打开时才在界面上显示思考消息气泡
                                                if (showThinkingDuringStream) {
                                                    if (thinkingDisplayIndex == -1) {
                                                        // 在助手主消息前插入思考消息
                                                        messages.add(assistantMainMessageIndex, ChatMessage("thinking", "🤔 " + fullThinking.toString()))
                                                        thinkingDisplayIndex = assistantMainMessageIndex
                                                        // 现在助手主消息的索引往后移了一位
                                                        assistantMainMessageIndex++
                                                        adapter.notifyItemInserted(thinkingDisplayIndex)
                                                    } else {
                                                        messages[thinkingDisplayIndex] = ChatMessage("thinking", "🤔 " + fullThinking.toString())
                                                        adapter.notifyItemChanged(thinkingDisplayIndex)
                                                    }
                                                    recyclerView.scrollToPosition(thinkingDisplayIndex)
                                                }
                                            }
                                            
                                            // 2. 处理正式内容片段
                                            if (streamResponse.content.isNotEmpty()) {
                                                fullContent.append(streamResponse.content)
                                                
                                                // 不再移除思考气泡！思考内容一直完整显示给用户
                                                // 思考气泡永久保留在消息列表中
                                                
                                                // 更新助手主消息内容，同时带上完整的思考过程保存到本地
                                                if (assistantMainMessageIndex >= 0) {
                                                    messages[assistantMainMessageIndex] = ChatMessage("assistant", fullContent.toString(), fullThinking.toString())
                                                    adapter.notifyItemChanged(assistantMainMessageIndex)
                                                }
                                                recyclerView.scrollToPosition(assistantMainMessageIndex)
                                            }
                                            
                                            if (streamResponse.done) {
                                                if (streamResponse.convId != null) {
                                                    currentConvId = streamResponse.convId
                                                }
                                                
                                                // 无论什么情况，最终主消息里都要完整保存思考内容和耗时数据
                                                if (assistantMainMessageIndex >= 0) {
                                                    messages[assistantMainMessageIndex] = ChatMessage("assistant", fullContent.toString(), fullThinking.toString(), streamResponse.timings)
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
        if (index >= 0 && index < messages.size) {
            val originalThinking = messages[index].thinking
            val originalTimings = messages[index].timings
            messages[index] = ChatMessage("assistant", content, originalThinking, originalTimings)
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
