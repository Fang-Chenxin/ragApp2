package com.example.agentchat

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
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


data class ChatMessage(val role: String, val content: String)
data class ChatRequest(
    val messages: List<ChatMessage>,
    @SerializedName("user_query") val userQuery: String
)
data class ChatResponse(val reply: String)


class MainActivity : AppCompatActivity() {
    private val messages = mutableListOf<ChatMessage>()
    private lateinit var adapter: ChatAdapter
    private lateinit var recyclerView: RecyclerView
    private val client = OkHttpClient()
    private val gson = Gson()

    companion object {
        // private const val BACKEND_URL = "http://10.0.2.2:8000/api/chat"
        private const val BACKEND_URL = "http://192.168.8.105:8000/api/chat"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        recyclerView = findViewById(R.id.chatRecyclerView)
        val editText = findViewById<EditText>(R.id.messageEditText)
        val sendButton = findViewById<Button>(R.id.sendButton)

        adapter = ChatAdapter(messages)
        recyclerView.layoutManager = LinearLayoutManager(this)
        recyclerView.adapter = adapter

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

    private fun sendMessageToBackend() {
        val lastUserMessage = messages.lastOrNull { it.role == "user" } ?: return
        val previousMessages = messages.dropLast(1).filter { it.role != "system" }

        val requestBodyJson = gson.toJson(ChatRequest(previousMessages, lastUserMessage.content))
        val mediaType = "application/json; charset=utf-8".toMediaType()
        val body = requestBodyJson.toRequestBody(mediaType)
        val request = Request.Builder().url(BACKEND_URL).post(body).build()

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
