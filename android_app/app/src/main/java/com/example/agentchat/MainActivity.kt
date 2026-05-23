package com.example.agentchat

import android.content.Context
import android.content.SharedPreferences
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.Build
import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
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
    @SerializedName("user_id") val userId: String
)
data class ChatResponse(
    val reply: String,
    @SerializedName("history_saved") val historySaved: Boolean = true
)
data class HistoryResponse(
    val userId: String,
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
    private val client = OkHttpClient()
    private val gson = Gson()
    
    private lateinit var prefs: SharedPreferences
    private lateinit var userId: String

    companion object {
        private const val BACKEND_URL = "http://192.168.8.105:8000"
        private const val PREFS_NAME = "chat_prefs"
        private const val KEY_USER_ID = "user_id"
        // private const val BACKEND_URL = "http://10.0.2.2:8000"  // 模拟器访问本机
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        
        // 初始化 SharedPreferences 和用户标识
        prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        userId = getOrCreateUserId()
        
        recyclerView = findViewById(R.id.chatRecyclerView)
        val editText = findViewById<EditText>(R.id.messageEditText)
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
        val url = "$BACKEND_URL/api/history/$userId"
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
                                        "已加载 ${historyResponse.total} 条历史记录",
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
            ChatRequest(previousMessages, lastUserMessage.content, userId)
        )
        val mediaType = "application/json; charset=utf-8".toMediaType()
        val body = requestBodyJson.toRequestBody(mediaType)
        val request = Request.Builder()
            .url("$BACKEND_URL/api/chat")
            .post(body)
            .build()

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
                    val responseBody = response.body?.string()
                    try {
                        val chatResponse = gson.fromJson(responseBody, ChatResponse::class.java)
                        runOnUiThread {
                            addAssistantMessage(chatResponse.reply)
                        }
                    } catch (_: Exception) {
                        runOnUiThread {
                            addAssistantMessage("解析响应失败")
                        }
                    }
                }
            }
        })
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
