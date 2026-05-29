package com.example.agentchat

import android.content.ClipData
import android.content.ClipboardManager
import android.text.SpannableString
import android.text.Spanned
import android.text.style.URLSpan
import android.widget.Toast
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView
import io.noties.markwon.Markwon


class ChatAdapter(private val messages: MutableList<ChatMessage>) : RecyclerView.Adapter<RecyclerView.ViewHolder>() {

    private var markwon: Markwon? = null

    fun attachMarkwon(markwon: Markwon) {
        this.markwon = markwon
    }

    companion object {
        private const val VIEW_TYPE_USER = 1
        private const val VIEW_TYPE_ASSISTANT = 2
    }

    override fun getItemViewType(position: Int): Int {
        return when (messages[position].role) {
            "user" -> VIEW_TYPE_USER
            else -> VIEW_TYPE_ASSISTANT
        }
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): RecyclerView.ViewHolder {
        return when (viewType) {
            VIEW_TYPE_USER -> {
                val view = LayoutInflater.from(parent.context).inflate(R.layout.item_chat_user, parent, false)
                UserMessageViewHolder(view)
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
            is AssistantMessageViewHolder -> holder.bind(message.content, message.thinking, message.timings, message.analysisExpanded)
        }
    }

    override fun getItemCount(): Int = messages.size

    class UserMessageViewHolder(itemView: View) : RecyclerView.ViewHolder(itemView) {
        private val textView: TextView = itemView.findViewById(R.id.userMessageText)
        init {
            textView.setTextIsSelectable(true)
        }

        fun bind(content: String) {
            textView.text = content
        }
    }

    inner class AssistantMessageViewHolder(itemView: View) : RecyclerView.ViewHolder(itemView) {
        private val textView: TextView = itemView.findViewById(R.id.assistantMessageText)
        private val timingsText: TextView = itemView.findViewById(R.id.assistantTimingsText)
        private val analysisContainer: View = itemView.findViewById(R.id.analysisContainer)
        private val analysisHeaderText: TextView = itemView.findViewById(R.id.analysisHeaderText)
        private val analysisTimeText: TextView = itemView.findViewById(R.id.analysisTimeText)
        private val analysisArrowText: TextView = itemView.findViewById(R.id.analysisArrowText)
        private val analysisMessageText: TextView = itemView.findViewById(R.id.analysisMessageText)
        private val copyReplyButton: View = itemView.findViewById(R.id.copyReplyButton)

        fun bind(content: String, analysis: String?, timings: Map<String, Any>?, analysisExpanded: Boolean) {
            val trimmedContent = content.trim()
            val statusAsAnalysis = isProcessingStatus(trimmedContent) && analysis.isNullOrBlank()
            val displayContent = if (statusAsAnalysis) "" else content

            if (displayContent.isNotBlank()) {
                textView.visibility = View.VISIBLE
                renderMarkdown(textView, displayContent)
            } else {
                textView.visibility = View.GONE
                textView.text = ""
            }

            copyReplyButton.setOnClickListener {
                copyToClipboard(itemView, "assistant_reply", displayContent)
            }
            copyReplyButton.visibility = if (displayContent.isNotBlank()) View.VISIBLE else View.GONE

            val analysisToShow = if (statusAsAnalysis) trimmedContent else analysis?.trim().orEmpty()
            val hasAnalysis = analysisToShow.isNotBlank()
            analysisContainer.visibility = if (hasAnalysis) View.VISIBLE else View.GONE
            if (hasAnalysis) {
                analysisHeaderText.text = if (statusAsAnalysis) "处理中" else "已思考"
                analysisTimeText.text = if (statusAsAnalysis) "" else formatAnalysisTime(timings)
                renderMarkdown(analysisMessageText, analysisToShow)
                analysisMessageText.visibility = if (analysisExpanded) View.VISIBLE else View.GONE
                analysisArrowText.text = if (analysisExpanded) "⌄" else "›"
                analysisContainer.setOnClickListener {
                    val currentPosition = bindingAdapterPosition
                    if (currentPosition != RecyclerView.NO_POSITION) {
                        val item = this@ChatAdapter.messages[currentPosition]
                        this@ChatAdapter.messages[currentPosition] = item.copy(analysisExpanded = !item.analysisExpanded)
                        this@ChatAdapter.notifyItemChanged(currentPosition)
                    }
                }
            } else {
                analysisContainer.setOnClickListener(null)
                analysisMessageText.visibility = View.GONE
            }

            if (timings != null && timings.isNotEmpty()) {
                timingsText.visibility = View.VISIBLE
                val parts = mutableListOf<String>()
                timings["analysis_calls"]?.let { parts.add("思考 ${it}s") }
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

        private fun formatAnalysisTime(timings: Map<String, Any>?): String {
            val seconds = when (val value = timings?.get("analysis_calls")) {
                is Number -> value.toDouble()
                is String -> value.toDoubleOrNull()
                else -> null
            } ?: return ""
            return "（用时 ${formatSeconds(seconds)}）"
        }

        private fun formatSeconds(seconds: Double): String {
            return if (seconds >= 10 || seconds == seconds.toInt().toDouble()) {
                seconds.toInt().toString()
            } else {
                "%.1f".format(seconds)
            }
        }

        private fun renderMarkdown(textView: TextView, markdown: String) {
            val renderer = markwon ?: Markwon.create(itemView.context).also { markwon = it }
            val spanned = renderer.toMarkdown(markdown)
            val stripped = stripUrlSpans(spanned)
            // 直接设置文本，确保 TextView 在 ViewHolder 初始化时已启用可选中状态
            textView.text = stripped
        }

        private fun stripUrlSpans(spanned: Spanned): SpannableString {
            val result = SpannableString(spanned)
            val urlSpans = result.getSpans(0, result.length, URLSpan::class.java)
            for (span in urlSpans) {
                result.removeSpan(span)
            }
            return result
        }

        private fun copyToClipboard(itemView: View, label: String, text: String) {
            if (text.isBlank()) return
            val clipboard = itemView.context.getSystemService(ClipboardManager::class.java)
            clipboard?.setPrimaryClip(ClipData.newPlainText(label, text))
            Toast.makeText(itemView.context, "已复制回复", Toast.LENGTH_SHORT).show()
        }

        private fun isProcessingStatus(content: String): Boolean {
            if (content.isBlank()) return false
            val keywords = listOf("正在分析", "正在查询", "正在整理", "正在重试")
            if (keywords.any { content.contains(it) }) return true
            return content.startsWith("🧭") || content.startsWith("🔎") || content.startsWith("🧠") ||
                content.startsWith("⏳") || content.startsWith("🔄")
        }
    }
}
