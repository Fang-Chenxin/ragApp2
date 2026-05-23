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


data class ChatMessage(val role: String, val content: String)
data class ChatRequest(
    val messages: List<ChatMessage>,
    @SerializedName("user_query") val userQuery: String,
    @SerializedName("user_id") val userId: String,
    @SerializedName("conv_id") val convId: String? = null
)
data class ChatResponse(
    val reply: String,
    @SerializedName("history_saved") val historySaved: Boolean = true,
    @SerializedName("conv_id") val convId: String? = null
)
data class StreamResponse(
    val content: String = "",
    @SerializedName("conv_id") val convId: String? = null,
    @SerializedName("history_saved") val historySaved: Boolean = true,
    val done: Boolean = false,
    val error: String? = null
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
    val timestamp: String? = null
)


class MainActivity : AppCompatActivity() {
    private val messages = mutableListOf<ChatMessage>()
    private lateinit var adapter: ChatAdapter
    private lateinit var recyclerView: RecyclerView
    private lateinit var editText: EditText
    private val client = OkHttpClient()
    private val gson = Gson()
    
    private lateinit var prefs: SharedPreferences
    private lateinit var userId: String
    private var currentConvId: String? = null
    private var currentConvTitle: String = "智能助手"

    companion object {
        private const val BACKEND_URL = "http://192.168.8.105:8000"
        private const val PREFS_NAME = "chat_prefs"
        private const val KEY_USER_ID = "user_id"
        private const val REQUEST_CONVERSATION = 1001
        
        // private const val BACKEND_URL = "http://10.0.2.2:8000"  // 模拟器访问本机
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
        val sendButton = findViewById<Button>(R.id.sendButton)

        adapter = ChatAdapter(messages)
        recyclerView.layoutManager = LinearLayoutManager(this)
        recyclerView.adapter = adapter
        
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
            else -> super.onOptionsItemSelected(item)
        }
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
        val url = "$BACKEND_URL/api/conversations/$userId"
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
        var url = "$BACKEND_URL/api/history/$userId"
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
    
    private fun loadHistoryFromServer() {
        var url = "$BACKEND_URL/api/history/$userId"
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
                                
                                // 将历史记录加载到消息列表
                                messages.clear()
                                messages.addAll(historyResponse.history.map {
                                    ChatMessage(it.role, it.content)
                                })
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
        val lastUserMessage = messages.lastOrNull { it.role == "user" } ?: return
        val previousMessages = messages.dropLast(1).filter { it.role != "system" }

        val requestBodyJson = gson.toJson(
            ChatRequest(previousMessages, lastUserMessage.content, userId, currentConvId)
        )
        val mediaType = "application/json; charset=utf-8".toMediaType()
        val body = requestBodyJson.toRequestBody(mediaType)
        val request = Request.Builder()
            .url("$BACKEND_URL/api/chat/stream")
            .post(body)
            .build()

        var assistantMessageIndex = -1
        var fullContent = StringBuilder()

        client.newCall(request).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                runOnUiThread {
                    addAssistantMessage("网络请求失败: ${e.message}")
                }
            }

            override fun onResponse(call: Call, response: Response) {
                response.use {
                    if (!response.isSuccessful) {
                        runOnUiThread {
                            addAssistantMessage("服务器错误: ${response.code}")
                        }
                        return
                    }
                    
                    try {
                        response.body?.byteStream()?.bufferedReader()?.use { reader ->
                            var line: String? = reader.readLine()
                            while (line != null) {
                                if (line.startsWith("data: ")) {
                                    val jsonData = line.substring(6)
                                    try {
                                        val streamResponse = gson.fromJson(jsonData, StreamResponse::class.java)
                                        
                                        runOnUiThread {
                                            if (streamResponse.error != null) {
                                                if (assistantMessageIndex == -1) {
                                                    addAssistantMessage("错误: ${streamResponse.error}")
                                                } else {
                                                    updateAssistantMessage(assistantMessageIndex, "错误: ${streamResponse.error}")
                                                }
                                                return@runOnUiThread
                                            }
                                            
                                            if (streamResponse.content.isNotEmpty()) {
                                                fullContent.append(streamResponse.content)
                                                
                                                if (assistantMessageIndex == -1) {
                                                    messages.add(ChatMessage("assistant", streamResponse.content))
                                                    assistantMessageIndex = messages.size - 1
                                                    adapter.notifyItemInserted(assistantMessageIndex)
                                                } else {
                                                    messages[assistantMessageIndex] = ChatMessage("assistant", fullContent.toString())
                                                    adapter.notifyItemChanged(assistantMessageIndex)
                                                }
                                                recyclerView.scrollToPosition(assistantMessageIndex)
                                            }
                                            
                                            if (streamResponse.done) {
                                                if (streamResponse.convId != null) {
                                                    currentConvId = streamResponse.convId
                                                }
                                                
                                                if (assistantMessageIndex == -1 && fullContent.isEmpty()) {
                                                    addAssistantMessage("收到空响应")
                                                }
                                            }
                                        }
                                    } catch (e: Exception) {
                                        e.printStackTrace()
                                    }
                                }
                                line = reader.readLine()
                            }
                        }
                    } catch (e: Exception) {
                        runOnUiThread {
                            addAssistantMessage("解析响应失败: ${e.message}")
                        }
                    }
                }
            }
        })
    }

    private fun updateAssistantMessage(index: Int, content: String) {
        if (index >= 0 && index < messages.size) {
            messages[index] = ChatMessage("assistant", content)
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
