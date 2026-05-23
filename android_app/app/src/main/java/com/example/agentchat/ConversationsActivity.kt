package com.example.agentchat

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.view.View
import android.widget.ImageButton
import android.widget.TextView
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
import java.text.SimpleDateFormat
import java.util.*

data class Conversation(
    @SerializedName("conv_id") val convId: String,
    @SerializedName("title") val title: String,
    @SerializedName("created_at") val createdAt: String,
    @SerializedName("message_count") val messageCount: Int,
    @SerializedName("last_message") val lastMessage: String = ""
)

data class ConversationsResponse(
    @SerializedName("user_id") val userId: String,
    @SerializedName("current_conv") val currentConv: String?,
    @SerializedName("conversations") val conversations: List<Conversation>,
    @SerializedName("count") val count: Int
)

data class ConversationResponse(
    @SerializedName("status") val status: String,
    @SerializedName("conversation") val conversation: Conversation?
)

class ConversationsActivity : AppCompatActivity() {
    private lateinit var recyclerView: RecyclerView
    private lateinit var adapter: ConversationAdapter
    private val conversations = mutableListOf<Conversation>()
    private lateinit var userId: String
    private var currentConvId: String? = null
    private val client = OkHttpClient()
    private val gson = Gson()
    
    companion object {
        private const val BACKEND_URL = "http://192.168.8.105:8000"
        const val EXTRA_USER_ID = "user_id"
        const val EXTRA_CURRENT_CONV = "current_conv"
        
        fun start(context: Context, userId: String, currentConv: String?) {
            val intent = Intent(context, ConversationsActivity::class.java).apply {
                putExtra(EXTRA_USER_ID, userId)
                putExtra(EXTRA_CURRENT_CONV, currentConv)
            }
            context.startActivity(intent)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_conversations)
        
        userId = intent.getStringExtra(EXTRA_USER_ID) ?: "default"
        currentConvId = intent.getStringExtra(EXTRA_CURRENT_CONV)
        
        // 返回按钮
        val backButton = findViewById<ImageButton>(R.id.btn_back)
        backButton.setOnClickListener {
            finish()
        }
        
        // 新建会话按钮
        val newChatButton = findViewById<ImageButton>(R.id.btn_new_chat)
        newChatButton.setOnClickListener {
            createNewConversation()
        }
        
        // 标题
        val titleText = findViewById<TextView>(R.id.title_text)
        titleText.text = "对话列表"
        
        // 初始化 RecyclerView
        recyclerView = findViewById(R.id.conversationsRecyclerView)
        adapter = ConversationAdapter(conversations, currentConvId) { conv ->
            selectConversation(conv)
        }
        recyclerView.layoutManager = LinearLayoutManager(this)
        recyclerView.adapter = adapter
        
        // 加载会话列表
        loadConversations()
    }
    
    private fun loadConversations() {
        val url = "$BACKEND_URL/api/conversations/$userId"
        val request = Request.Builder()
            .url(url)
            .get()
            .build()
        
        client.newCall(request).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                runOnUiThread {
                    Toast.makeText(this@ConversationsActivity, "加载会话列表失败", Toast.LENGTH_SHORT).show()
                }
            }
            
            override fun onResponse(call: Call, response: Response) {
                response.use {
                    if (response.isSuccessful) {
                        val responseBody = response.body?.string()
                        try {
                            val convResponse = gson.fromJson(responseBody, ConversationsResponse::class.java)
                            runOnUiThread {
                                conversations.clear()
                                conversations.addAll(convResponse.conversations)
                                currentConvId = convResponse.currentConv
                                adapter.currentConvId = currentConvId
                                adapter.notifyDataSetChanged()
                                
                                if (conversations.isEmpty()) {
                                    findViewById<TextView>(R.id.empty_state).visibility = View.VISIBLE
                                } else {
                                    findViewById<TextView>(R.id.empty_state).visibility = View.GONE
                                }
                            }
                        } catch (e: Exception) {
                            e.printStackTrace()
                            runOnUiThread {
                                Toast.makeText(this@ConversationsActivity, "解析数据失败", Toast.LENGTH_SHORT).show()
                            }
                        }
                    }
                }
            }
        })
    }
    
    /**
     * 新建对话 - 只有当前会话有消息时才创建新会话
     */
    private fun createNewConversation() {
        // 检查当前会话是否有消息
        val currentConv = conversations.find { it.convId == currentConvId }
        if (currentConv != null && currentConv.messageCount == 0) {
            // 当前是空会话，不需要创建新会话
            Toast.makeText(this, "当前已是新对话", Toast.LENGTH_SHORT).show()
            return
        }
        
        val url = "$BACKEND_URL/api/conversations/$userId"
        val requestBody = "{}".toRequestBody("application/json".toMediaType())
        val request = Request.Builder()
            .url(url)
            .post(requestBody)
            .build()
        
        client.newCall(request).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                runOnUiThread {
                    Toast.makeText(this@ConversationsActivity, "创建会话失败", Toast.LENGTH_SHORT).show()
                }
            }
            
            override fun onResponse(call: Call, response: Response) {
                response.use {
                    if (response.isSuccessful) {
                        val responseBody = response.body?.string()
                        try {
                            val convResponse = gson.fromJson(responseBody, ConversationResponse::class.java)
                            runOnUiThread {
                                // 返回主界面，传递新会话ID
                                val intent = Intent().apply {
                                    putExtra("conv_id", convResponse.conversation?.convId)
                                    putExtra("title", convResponse.conversation?.title)
                                }
                                setResult(RESULT_OK, intent)
                                finish()
                            }
                        } catch (e: Exception) {
                            e.printStackTrace()
                        }
                    }
                }
            }
        })
    }
    
    private fun selectConversation(conv: Conversation) {
        // 检查当前会话是否为空，如果为空则先删除
        val currentConv = conversations.find { it.convId == currentConvId }
        if (currentConv != null && currentConv.messageCount == 0) {
            // 当前会话为空，先删除
            deleteEmptyConversation(currentConvId!!, conv)
        } else {
            // 直接切换
            switchToConversation(conv)
        }
    }
    
    private fun deleteEmptyConversation(convId: String, targetConv: Conversation) {
        val url = "$BACKEND_URL/api/conversations/$userId/$convId"
        val request = Request.Builder()
            .url(url)
            .delete()
            .build()
        
        client.newCall(request).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                // 删除失败也继续切换
                runOnUiThread {
                    switchToConversation(targetConv)
                }
            }
            
            override fun onResponse(call: Call, response: Response) {
                runOnUiThread {
                    // 从本地列表移除
                    val index = conversations.indexOfFirst { it.convId == convId }
                    if (index >= 0) {
                        conversations.removeAt(index)
                        adapter.notifyItemRemoved(index)
                    }
                    // 切换到目标会话
                    switchToConversation(targetConv)
                }
            }
        })
    }
    
    private fun switchToConversation(conv: Conversation) {
        // 切换到选中的会话
        val url = "$BACKEND_URL/api/conversations/$userId/switch/${conv.convId}"
        val request = Request.Builder()
            .url(url)
            .post("{}".toRequestBody("application/json".toMediaType()))
            .build()
        
        client.newCall(request).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                runOnUiThread {
                    Toast.makeText(this@ConversationsActivity, "切换会话失败", Toast.LENGTH_SHORT).show()
                }
            }
            
            override fun onResponse(call: Call, response: Response) {
                response.use {
                    if (response.isSuccessful) {
                        runOnUiThread {
                            val intent = Intent().apply {
                                putExtra("conv_id", conv.convId)
                                putExtra("title", conv.title)
                            }
                            setResult(RESULT_OK, intent)
                            finish()
                        }
                    }
                }
            }
        })
    }
    
    fun deleteConversation(convId: String, position: Int) {
        val url = "$BACKEND_URL/api/conversations/$userId/$convId"
        val request = Request.Builder()
            .url(url)
            .delete()
            .build()
        
        client.newCall(request).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                runOnUiThread {
                    Toast.makeText(this@ConversationsActivity, "删除失败", Toast.LENGTH_SHORT).show()
                }
            }
            
            override fun onResponse(call: Call, response: Response) {
                response.use {
                    if (response.isSuccessful) {
                        runOnUiThread {
                            conversations.removeAt(position)
                            adapter.notifyItemRemoved(position)
                            Toast.makeText(this@ConversationsActivity, "已删除", Toast.LENGTH_SHORT).show()
                            
                            if (conversations.isEmpty()) {
                                findViewById<TextView>(R.id.empty_state).visibility = View.VISIBLE
                            }
                        }
                    }
                }
            }
        })
    }
    
    inner class ConversationAdapter(
        private val items: List<Conversation>,
        var currentConvId: String?,
        private val onSelect: (Conversation) -> Unit
    ) : RecyclerView.Adapter<ConversationAdapter.ViewHolder>() {
        
        override fun onCreateViewHolder(parent: android.view.ViewGroup, viewType: Int): ViewHolder {
            val view = layoutInflater.inflate(R.layout.item_conversation, parent, false)
            return ViewHolder(view)
        }
        
        override fun onBindViewHolder(holder: ViewHolder, position: Int) {
            val conv = items[position]
            holder.bind(conv, position)
        }
        
        override fun getItemCount(): Int = items.size
        
        inner class ViewHolder(itemView: View) : RecyclerView.ViewHolder(itemView) {
            fun bind(conv: Conversation, position: Int) {
                // 标题
                itemView.findViewById<TextView>(R.id.tv_title).text = conv.title
                
                // 最后消息预览
                val preview = if (conv.lastMessage.isNotEmpty()) {
                    conv.lastMessage
                } else {
                    "暂无消息"
                }
                itemView.findViewById<TextView>(R.id.tv_preview).text = preview
                
                // 消息数量
                itemView.findViewById<TextView>(R.id.tv_count).text = conv.messageCount.toString()
                
                // 时间
                itemView.findViewById<TextView>(R.id.tv_time).text = formatTime(conv.createdAt)
                
                // 当前会话标记
                val indicator = itemView.findViewById<View>(R.id.current_indicator)
                indicator.visibility = if (conv.convId == currentConvId) View.VISIBLE else View.GONE
                
                // 点击事件
                itemView.setOnClickListener {
                    onSelect(conv)
                }
                
                // 删除按钮
                val deleteBtn = itemView.findViewById<ImageButton>(R.id.btn_delete)
                deleteBtn.setOnClickListener {
                    deleteConversation(conv.convId, position)
                }
            }
            
            private fun formatTime(timeStr: String): String {
                return try {
                    val format = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.getDefault())
                    val date = format.parse(timeStr)
                    val now = Date()
                    val diff = now.time - (date?.time ?: now.time)
                    
                    when {
                        diff < 60000 -> "刚刚"
                        diff < 3600000 -> "${diff / 60000}分钟前"
                        diff < 86400000 -> "${diff / 3600000}小时前"
                        else -> {
                            val dateFormat = SimpleDateFormat("MM-dd", Locale.getDefault())
                            dateFormat.format(date ?: now)
                        }
                    }
                } catch (e: Exception) {
                    timeStr.take(10)
                }
            }
        }
    }
}
