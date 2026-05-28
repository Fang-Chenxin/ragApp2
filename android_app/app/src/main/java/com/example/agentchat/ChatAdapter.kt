package com.example.agentchat

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView


class ChatAdapter(private val messages: List<ChatMessage>) : RecyclerView.Adapter<RecyclerView.ViewHolder>() {

    companion object {
        private const val VIEW_TYPE_USER = 1
        private const val VIEW_TYPE_ASSISTANT = 2
        private const val VIEW_TYPE_THINKING = 3
    }

    override fun getItemViewType(position: Int): Int {
        return when (messages[position].role) {
            "user" -> VIEW_TYPE_USER
            "thinking" -> VIEW_TYPE_THINKING
            else -> VIEW_TYPE_ASSISTANT
        }
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): RecyclerView.ViewHolder {
        return when (viewType) {
            VIEW_TYPE_USER -> {
                val view = LayoutInflater.from(parent.context).inflate(R.layout.item_chat_user, parent, false)
                UserMessageViewHolder(view)
            }
            VIEW_TYPE_THINKING -> {
                val view = LayoutInflater.from(parent.context).inflate(R.layout.item_chat_thinking, parent, false)
                ThinkingMessageViewHolder(view)
            }
            else -> {
                val view = LayoutInflater.from(parent.context).inflate(R.layout.item_chat_assistant, parent, false)
                AssistantMessageViewHolder(view)
            }
        }
    }

    override fun onBindViewHolder(holder: RecyclerView.ViewHolder, position: Int) {
        val message = messages[position]
        when (holder) {
            is UserMessageViewHolder -> holder.bind(message.content)
            is AssistantMessageViewHolder -> holder.bind(message.content, message.timings)
            is ThinkingMessageViewHolder -> holder.bind(message.content)
        }
    }

    override fun getItemCount(): Int = messages.size

    class UserMessageViewHolder(itemView: View) : RecyclerView.ViewHolder(itemView) {
        private val textView: TextView = itemView.findViewById(R.id.userMessageText)
        fun bind(content: String) {
            textView.text = content
        }
    }

    class AssistantMessageViewHolder(itemView: View) : RecyclerView.ViewHolder(itemView) {
        private val textView: TextView = itemView.findViewById(R.id.assistantMessageText)
        private val timingsText: TextView = itemView.findViewById(R.id.assistantTimingsText)
        fun bind(content: String, timings: Map<String, Any>?) {
            textView.text = content
            if (timings != null && timings.isNotEmpty()) {
                timingsText.visibility = View.VISIBLE
                val parts = mutableListOf<String>()
                timings["vector_search"]?.let { parts.add("向量检索 ${it}s") }
                timings["llm_calls"]?.let { parts.add("LLM推理 ${it}s") }
                timings["tool_calls"]?.let { t ->
                    val rounds = timings["tool_rounds"]
                    if (rounds != null && (rounds as Number).toInt() > 0) parts.add("工具查询 ${t}s")
                }
                timings["total"]?.let { parts.add("总计 ${it}s") }
                timingsText.text = parts.joinToString(" | ")
            } else {
                timingsText.visibility = View.GONE
            }
        }
    }

    class ThinkingMessageViewHolder(itemView: View) : RecyclerView.ViewHolder(itemView) {
        private val textView: TextView = itemView.findViewById(R.id.thinkingMessageText)
        fun bind(content: String) {
            textView.text = content
        }
    }
}
