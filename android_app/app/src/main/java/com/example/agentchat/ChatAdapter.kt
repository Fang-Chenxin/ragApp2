package com.example.agentchat

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import android.content.ActivityNotFoundException
import android.content.Context
import android.graphics.Color
import android.graphics.BitmapFactory
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.net.Uri
import android.text.SpannableString
import android.text.Spanned
import android.text.style.ClickableSpan
import android.text.style.URLSpan
import android.util.TypedValue
import android.widget.Toast
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.HorizontalScrollView
import android.widget.FrameLayout
import androidx.recyclerview.widget.RecyclerView
import io.noties.markwon.Markwon
import io.noties.markwon.ext.tables.TablePlugin
import okhttp3.OkHttpClient
import okhttp3.Request
import java.util.concurrent.Executors


private fun TextView.keepSelectable() {
    setTextIsSelectable(true)
    isLongClickable = true
    linksClickable = false
    setHorizontallyScrolling(false)
}

private data class MarkdownTable(
    val headers: List<String>,
    val rows: List<List<String>>,
    val before: String,
    val after: String,
)

private data class ProductComparison(
    val index: Int,
    val name: String,
    val fields: List<Pair<String, String>>,
    val conclusion: String,
    val rankScore: Int,
)

class ChatAdapter(private val messages: MutableList<ChatMessage>) : RecyclerView.Adapter<RecyclerView.ViewHolder>() {

    private var markwon: Markwon? = null
    private val imageLoadExecutor = Executors.newFixedThreadPool(2)

    fun attachMarkwon(markwon: Markwon) {
        this.markwon = markwon
    }

    companion object {
        private const val VIEW_TYPE_USER = 1
        private const val VIEW_TYPE_ASSISTANT = 2
        const val PAYLOAD_STREAMING_CONTENT = "streaming_content"
        private var okHttpClient: OkHttpClient? = null
        
        fun getHttpClient(): OkHttpClient {
            if (okHttpClient == null) {
                okHttpClient = OkHttpClient()
            }
            return okHttpClient!!
        }

        fun createMarkwon(context: Context): Markwon {
            return Markwon.builder(context)
                .usePlugin(TablePlugin.create(context))
                .build()
        }
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
            is AssistantMessageViewHolder -> holder.bind(
                message.content,
                message.thinking,
                message.timings,
                message.analysisExpanded,
                message.processingStatus,
                message.streaming,
                message.selectedProducts,
            )
        }
    }

    override fun onBindViewHolder(holder: RecyclerView.ViewHolder, position: Int, payloads: MutableList<Any>) {
        val message = messages[position]
        if (
            holder is AssistantMessageViewHolder &&
            payloads.contains(PAYLOAD_STREAMING_CONTENT)
        ) {
            holder.bindStreamingContent(message.content)
            return
        }
        super.onBindViewHolder(holder, position, payloads)
    }

    override fun getItemCount(): Int = messages.size

    class UserMessageViewHolder(itemView: View) : RecyclerView.ViewHolder(itemView) {
        private val textView: TextView = itemView.findViewById(R.id.userMessageText)
        init {
            textView.keepSelectable()
        }

        fun bind(content: String) {
            if (textView.text.toString() != content) {
                textView.text = content
            }
            textView.keepSelectable()
        }
    }

    inner class AssistantMessageViewHolder(itemView: View) : RecyclerView.ViewHolder(itemView) {
        private val messageScroll: FrameLayout = itemView.findViewById(R.id.assistantMessageScroll)
        private val textView: TextView = itemView.findViewById(R.id.assistantMessageText)
        private val structuredContent: LinearLayout = itemView.findViewById(R.id.assistantStructuredContent)
        private val timingsText: TextView = itemView.findViewById(R.id.assistantTimingsText)
        private val analysisContainer: View = itemView.findViewById(R.id.analysisContainer)
        private val analysisHeaderRow: View = itemView.findViewById(R.id.analysisHeaderRow)
        private val analysisHeaderText: TextView = itemView.findViewById(R.id.analysisHeaderText)
        private val analysisTimeText: TextView = itemView.findViewById(R.id.analysisTimeText)
        private val analysisArrowText: TextView = itemView.findViewById(R.id.analysisArrowText)
        private val analysisMessageText: TextView = itemView.findViewById(R.id.analysisMessageText)
        private val copyReplyButton: View = itemView.findViewById(R.id.copyReplyButton)
        private val productCardsScroll: HorizontalScrollView = itemView.findViewById(R.id.productCardsScroll)
        private val productCardsContainer: LinearLayout = itemView.findViewById(R.id.productCardsContainer)

        init {
            textView.keepSelectable()
            analysisMessageText.keepSelectable()
        }

        fun bind(
            content: String,
            analysis: String?,
            timings: Map<String, Any>?,
            analysisExpanded: Boolean,
            processingStatus: String?,
            streaming: Boolean,
            selectedProducts: List<SelectedProduct> = emptyList(),
        ) {
            if (content.isNotBlank()) {
                if (streaming) {
                    structuredContent.visibility = View.GONE
                    bindStreamingContent(content)
                } else {
                    bindFinalContent(content)
                }
            } else {
                messageScroll.visibility = View.GONE
                structuredContent.visibility = View.GONE
                structuredContent.removeAllViews()
                textView.visibility = View.GONE
                textView.text = ""
                textView.keepSelectable()
            }

            copyReplyButton.setOnClickListener {
                copyToClipboard(itemView, "assistant_reply", content)
            }
            copyReplyButton.visibility = if (content.isNotBlank()) View.VISIBLE else View.GONE

            val analysisBody = analysis?.trim().orEmpty()
            val statusLine = processingStatus?.trim().orEmpty()
            val analysisToShow = listOf(
                analysisBody,
                statusLine.takeIf { it.isNotBlank() }?.let { "· $it" }.orEmpty()
            ).filter { it.isNotBlank() }.joinToString("\n\n")
            val hasAnalysis = analysisToShow.isNotBlank()
            analysisContainer.visibility = if (hasAnalysis) View.VISIBLE else View.GONE
            if (hasAnalysis) {
                analysisHeaderText.text = if (statusLine.isNotBlank()) "思考中" else "已思考"
                analysisTimeText.text = if (statusLine.isNotBlank()) "" else formatAnalysisTime(timings)
                renderMarkdown(analysisMessageText, analysisToShow)
                analysisMessageText.visibility = if (analysisExpanded) View.VISIBLE else View.GONE
                analysisArrowText.text = if (analysisExpanded) "⌄" else "›"
                analysisHeaderRow.setOnClickListener {
                    val currentPosition = bindingAdapterPosition
                    if (currentPosition != RecyclerView.NO_POSITION) {
                        val item = this@ChatAdapter.messages[currentPosition]
                        this@ChatAdapter.messages[currentPosition] = item.copy(analysisExpanded = !item.analysisExpanded)
                        this@ChatAdapter.notifyItemChanged(currentPosition)
                    }
                }
            } else {
                analysisHeaderRow.setOnClickListener(null)
                analysisMessageText.visibility = View.GONE
                analysisMessageText.keepSelectable()
            }

            if (timings != null && timings.isNotEmpty()) {
                timingsText.visibility = View.VISIBLE
                val parts = mutableListOf<String>()
                timings["analysis_calls"]?.let { parts.add("思考 ${it}s") }
                timings["vector_search"]?.let { parts.add("向量检索 ${it}s") }
                timings["rag_rerank"]?.let { parts.add("RAG核验 ${it}s") }
                timings["llm_calls"]?.let { parts.add("LLM推理 ${it}s") }
                timings["tool_calls"]?.let { t ->
                    val rounds = timings["tool_rounds"]
                    if (rounds != null && (rounds as Number).toInt() > 0) parts.add("工具查询 ${t}s")
                }
                timings["parallel_overlap_saved_estimate"]?.let { parts.add("并行节省估算 ${it}s") }
                timings["total"]?.let { parts.add("总计 ${it}s") }
                timingsText.text = parts.joinToString(" | ")
            } else {
                timingsText.visibility = View.GONE
            }

            renderProductCards(selectedProducts)
        }

        fun bindStreamingContent(content: String) {
            if (content.isNotBlank()) {
                structuredContent.visibility = View.GONE
                messageScroll.visibility = View.VISIBLE
                textView.visibility = View.VISIBLE
                if (textView.text.toString() != content) {
                    textView.text = content
                }
            } else {
                messageScroll.visibility = View.GONE
                textView.visibility = View.GONE
                textView.text = ""
            }
            textView.keepSelectable()
            copyReplyButton.setOnClickListener {
                copyToClipboard(itemView, "assistant_reply", content)
            }
            copyReplyButton.visibility = if (content.isNotBlank()) View.VISIBLE else View.GONE
        }

        private fun bindFinalContent(content: String) {
            val table = parseFirstMarkdownTable(content)
            if (table == null || table.headers.size < 3 || table.rows.isEmpty()) {
                structuredContent.visibility = View.GONE
                structuredContent.removeAllViews()
                messageScroll.visibility = View.VISIBLE
                textView.visibility = View.VISIBLE
                renderMarkdown(textView, content, allowWideTables = true)
                return
            }

            messageScroll.visibility = View.GONE
            textView.visibility = View.GONE
            textView.text = ""
            structuredContent.visibility = View.VISIBLE
            renderStructuredComparison(content, table)
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

        private fun renderMarkdown(textView: TextView, markdown: String, allowWideTables: Boolean = false) {
            if (allowWideTables) resetMessageTextWidth()
            val renderer = markwon ?: createMarkwon(itemView.context).also { markwon = it }
            val spanned = renderer.toMarkdown(markdown)
            val stripped = stripUrlSpans(spanned)
            if (shouldReplaceRenderedText(textView, stripped)) {
                textView.text = stripped
            }
            textView.keepSelectable()
        }

        private fun renderStructuredComparison(originalMarkdown: String, table: MarkdownTable) {
            if (structuredContent.tag == originalMarkdown && structuredContent.childCount > 0) return
            structuredContent.tag = originalMarkdown
            structuredContent.removeAllViews()

            addMarkdownBlock(table.before)
            addComparisonBlocks(table)
            addMarkdownBlock(table.after)
        }

        private fun addMarkdownBlock(markdown: String) {
            val trimmed = markdown.trim()
            if (trimmed.isBlank()) return
            val block = TextView(itemView.context).apply {
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                ).apply {
                    bottomMargin = dp(8)
                }
                setTextColor(Color.parseColor("#222222"))
                setTextSize(TypedValue.COMPLEX_UNIT_SP, 17f)
                setLineSpacing(dp(6).toFloat(), 1.0f)
                setPadding(0, dp(2), 0, dp(2))
                keepSelectable()
            }
            renderMarkdown(block, trimmed)
            structuredContent.addView(block)
        }

        private fun addComparisonBlocks(table: MarkdownTable) {
            val productNames = table.headers.drop(1).map { it.ifBlank { "商品" } }
            if (productNames.isEmpty()) return

            val comparisons = productNames.mapIndexed { productIndex, productName ->
                val fields = table.rows.mapNotNull { row ->
                    val dimension = row.firstOrNull()?.trim().orEmpty()
                    if (dimension.isBlank()) return@mapNotNull null
                    val value = row.getOrNull(productIndex + 1)?.trim().orEmpty()
                    dimension to value.ifBlank { "未提及" }
                }
                val conclusion = fields.firstOrNull { isConclusionDimension(it.first) }?.second.orEmpty()
                ProductComparison(
                    index = productIndex,
                    name = productName,
                    fields = fields,
                    conclusion = conclusion,
                    rankScore = rankScore(conclusion),
                )
            }.sortedWith(compareBy<ProductComparison> { it.rankScore }.thenBy { it.index })

            addRankingSummary(comparisons)
            comparisons.forEachIndexed { displayIndex, comparison ->
                addProductComparisonCard(comparison, displayIndex)
            }
        }

        private fun addRankingSummary(comparisons: List<ProductComparison>) {
            val summary = LinearLayout(itemView.context).apply {
                orientation = LinearLayout.VERTICAL
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                ).apply {
                    topMargin = dp(6)
                    bottomMargin = dp(8)
                }
                setPadding(dp(12), dp(10), dp(12), dp(10))
                background = roundedBackground("#EEF6FF", "#BFDBFE", 8)
            }
            summary.addView(TextView(itemView.context).apply {
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                ).apply {
                    bottomMargin = dp(8)
                }
                text = "推荐排序"
                setTextColor(Color.parseColor("#0F172A"))
                setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
                typeface = Typeface.DEFAULT_BOLD
            })
            comparisons.forEachIndexed { index, comparison ->
                summary.addView(TextView(itemView.context).apply {
                    layoutParams = LinearLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT,
                        ViewGroup.LayoutParams.WRAP_CONTENT,
                    ).apply {
                        topMargin = if (index == 0) 0 else dp(5)
                    }
                    text = "${rankLabel(index, comparison.rankScore)}：${comparison.name}" +
                        comparison.conclusion.takeIf { it.isNotBlank() }?.let { "\n$it" }.orEmpty()
                    setTextColor(Color.parseColor("#1E3A8A"))
                    setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
                    setLineSpacing(dp(4).toFloat(), 1.0f)
                })
            }
            structuredContent.addView(summary)
        }

        private fun addProductComparisonCard(comparison: ProductComparison, displayIndex: Int) {
            val card = LinearLayout(itemView.context).apply {
                orientation = LinearLayout.VERTICAL
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                ).apply {
                    topMargin = dp(6)
                    bottomMargin = dp(8)
                }
                setPadding(dp(12), dp(11), dp(12), dp(11))
                background = roundedBackground("#FFFFFF", "#E2E8F0", 8)
            }

            val header = LinearLayout(itemView.context).apply {
                orientation = LinearLayout.VERTICAL
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                ).apply {
                    bottomMargin = dp(8)
                }
            }
            header.addView(TextView(itemView.context).apply {
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                )
                text = rankLabel(displayIndex, comparison.rankScore)
                setTextColor(Color.parseColor("#2563EB"))
                setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f)
                typeface = Typeface.DEFAULT_BOLD
            })
            header.addView(TextView(itemView.context).apply {
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                ).apply {
                    topMargin = dp(2)
                }
                text = comparison.name
                setTextColor(Color.parseColor("#0F172A"))
                setTextSize(TypedValue.COMPLEX_UNIT_SP, 17f)
                typeface = Typeface.DEFAULT_BOLD
                setLineSpacing(dp(3).toFloat(), 1.0f)
            })
            card.addView(header)

            comparison.fields.forEach { (dimension, value) ->
                val row = LinearLayout(itemView.context).apply {
                    orientation = LinearLayout.VERTICAL
                    layoutParams = LinearLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT,
                        ViewGroup.LayoutParams.WRAP_CONTENT,
                    ).apply {
                        topMargin = dp(7)
                    }
                    setPadding(dp(10), dp(8), dp(10), dp(8))
                    background = roundedBackground(
                        if (isConclusionDimension(dimension)) "#F8FAFC" else "#FFFFFF",
                        "#E5E7EB",
                        6,
                    )
                }
                row.addView(TextView(itemView.context).apply {
                    layoutParams = LinearLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT,
                        ViewGroup.LayoutParams.WRAP_CONTENT,
                    )
                    text = dimension
                    setTextColor(Color.parseColor("#64748B"))
                    setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f)
                    typeface = Typeface.DEFAULT_BOLD
                })
                row.addView(TextView(itemView.context).apply {
                        layoutParams = LinearLayout.LayoutParams(
                            ViewGroup.LayoutParams.MATCH_PARENT,
                            ViewGroup.LayoutParams.WRAP_CONTENT,
                        ).apply {
                            topMargin = dp(3)
                        }
                        text = value
                        setTextColor(Color.parseColor(if (isConclusionDimension(dimension)) "#0F172A" else "#1F2937"))
                        setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
                        setLineSpacing(dp(4).toFloat(), 1.0f)
                        keepSelectable()
                })
                card.addView(row)
            }

            structuredContent.addView(card)
        }

        private fun isConclusionDimension(dimension: String): Boolean {
            return dimension.contains("推荐") || dimension.contains("结论")
        }

        private fun rankScore(conclusion: String): Int {
            return when {
                conclusion.contains("首选") || conclusion.contains("优先") || conclusion.contains("最推荐") -> 0
                conclusion.contains("备选") || conclusion.contains("次选") || conclusion.contains("可以选") -> 1
                conclusion.contains("不建议") || conclusion.contains("不太") || conclusion.contains("谨慎") || conclusion.contains("不推荐") -> 3
                else -> 2
            }
        }

        private fun rankLabel(index: Int, rankScore: Int): String {
            return when {
                rankScore == 0 -> "首选"
                rankScore == 1 -> "备选"
                rankScore >= 3 -> "谨慎选择"
                index == 0 -> "推荐 ${index + 1}"
                else -> "参考 ${index + 1}"
            }
        }

        private fun roundedBackground(fillColor: String, strokeColor: String, radiusDp: Int): GradientDrawable {
            return GradientDrawable().apply {
                shape = GradientDrawable.RECTANGLE
                setColor(Color.parseColor(fillColor))
                setStroke(dp(1), Color.parseColor(strokeColor))
                cornerRadius = dp(radiusDp).toFloat()
            }
        }

        private fun resetMessageTextWidth() {
            val params = textView.layoutParams
            if (params.width != ViewGroup.LayoutParams.MATCH_PARENT) {
                params.width = ViewGroup.LayoutParams.MATCH_PARENT
                textView.layoutParams = params
            }
        }

        private fun parseFirstMarkdownTable(markdown: String): MarkdownTable? {
            val lines = markdown.lines()
            for (index in 0 until lines.lastIndex) {
                val header = lines[index].trim()
                val separator = lines[index + 1].trim()
                if (!isMarkdownTableHeader(header, separator)) continue

                val headers = parseMarkdownTableRow(header)
                if (headers.size < 2) continue

                val rows = mutableListOf<List<String>>()
                var endIndex = index + 2
                while (endIndex < lines.size) {
                    val rowLine = lines[endIndex].trim()
                    if (!rowLine.startsWith("|") || !rowLine.endsWith("|")) break
                    val cells = parseMarkdownTableRow(rowLine)
                    if (cells.isEmpty()) break
                    rows.add(cells)
                    endIndex += 1
                }
                if (rows.isEmpty()) return null

                return MarkdownTable(
                    headers = headers,
                    rows = rows.map { normalizeTableCells(it, headers.size) },
                    before = lines.take(index).joinToString("\n"),
                    after = lines.drop(endIndex).joinToString("\n"),
                )
            }
            return null
        }

        private fun isMarkdownTableHeader(header: String, separator: String): Boolean {
            return header.startsWith("|") &&
                header.endsWith("|") &&
                Regex("""^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$""").matches(separator)
        }

        private fun parseMarkdownTableRow(row: String): List<String> {
            return row.trim().trim('|').split('|').map { cell ->
                cell.trim()
                    .replace("<br>", "\n", ignoreCase = true)
                    .replace("<br/>", "\n", ignoreCase = true)
                    .replace("<br />", "\n", ignoreCase = true)
                    .replace(Regex("""\*\*([^*]+)\*\*"""), "$1")
                    .replace(Regex("""`([^`]+)`"""), "$1")
            }
        }

        private fun normalizeTableCells(cells: List<String>, expectedSize: Int): List<String> {
            return when {
                cells.size == expectedSize -> cells
                cells.size > expectedSize -> cells.take(expectedSize)
                else -> cells + List(expectedSize - cells.size) { "" }
            }
        }

        private fun dp(value: Int): Int {
            return TypedValue.applyDimension(
                TypedValue.COMPLEX_UNIT_DIP,
                value.toFloat(),
                itemView.resources.displayMetrics,
            ).toInt()
        }

        private fun shouldReplaceRenderedText(textView: TextView, rendered: Spanned): Boolean {
            val currentText = textView.text
            if (currentText.toString() != rendered.toString()) return true
            if (currentText !is Spanned) return true

            return currentText.getSpans(0, currentText.length, URLSpan::class.java).isNotEmpty() ||
                currentText.getSpans(0, currentText.length, ClickableSpan::class.java).isNotEmpty()
        }

        private fun stripUrlSpans(spanned: Spanned): SpannableString {
            val result = SpannableString(spanned)
            val urlSpans = result.getSpans(0, result.length, URLSpan::class.java)
            for (span in urlSpans) {
                result.removeSpan(span)
            }
            val clickableSpans = result.getSpans(0, result.length, ClickableSpan::class.java)
            for (span in clickableSpans) {
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

        private fun renderProductCards(products: List<SelectedProduct>) {
            productCardsContainer.removeAllViews()
            if (products.isEmpty()) {
                productCardsScroll.visibility = View.GONE
                return
            }

            val maxCards = minOf(5, products.size)
            for (i in 0 until maxCards) {
                val product = products[i]
                val cardView = LayoutInflater.from(itemView.context).inflate(
                    R.layout.item_product_card,
                    productCardsContainer,
                    false
                )
                bindProductCard(cardView, product)
                productCardsContainer.addView(cardView)
            }

            productCardsScroll.visibility = View.VISIBLE
        }

        private fun bindProductCard(cardView: View, product: SelectedProduct) {
            val productImage: ImageView = cardView.findViewById(R.id.productImage)
            val imagePlaceholder: TextView = cardView.findViewById(R.id.imagePlaceholder)
            val productTitle: TextView = cardView.findViewById(R.id.productTitle)
            val productBrand: TextView = cardView.findViewById(R.id.productBrand)
            val productCategory: TextView = cardView.findViewById(R.id.productCategory)
            val productPrice: TextView = cardView.findViewById(R.id.productPrice)
            val matchTypeTag: TextView = cardView.findViewById(R.id.matchTypeTag)

            productTitle.text = product.title
            productBrand.text = product.brand.ifEmpty { "未知品牌" }
            productCategory.text = product.category.ifEmpty { product.subCategory }
            productPrice.text = formatPrice(product.basePrice)

            if (product.matchType.isNotEmpty()) {
                matchTypeTag.text = product.matchType
                matchTypeTag.visibility = View.VISIBLE
            } else {
                matchTypeTag.visibility = View.GONE
            }

            if (product.imageUrl.isNotEmpty()) {
                loadProductImage(resolveBackendUrl(product.imageUrl), productImage, imagePlaceholder)
            } else if (product.imagePath.isNotEmpty()) {
                loadProductImage(resolveProductImageUrl(product.imagePath), productImage, imagePlaceholder)
            } else {
                imagePlaceholder.visibility = View.VISIBLE
                productImage.visibility = View.GONE
            }

            cardView.setOnClickListener {
                openProductPage(product)
            }
        }

        private fun formatPrice(basePrice: Any?): String {
            return when (basePrice) {
                is Number -> "¥${"%.2f".format(basePrice.toDouble())}"
                is String -> if (basePrice.isNotEmpty()) "¥$basePrice" else "价格待定"
                else -> "价格待定"
            }
        }

        private fun loadProductImage(url: String, imageView: ImageView, placeholder: TextView) {
            if (url.isBlank()) {
                showImagePlaceholder(imageView, placeholder)
                return
            }

            imageLoadExecutor.execute {
                try {
                    val request = Request.Builder().url(url).build()
                    val response = getHttpClient().newCall(request).execute()
                    if (response.isSuccessful && response.body != null) {
                        val bitmap = BitmapFactory.decodeStream(response.body!!.byteStream())
                        (itemView.context as? MainActivity)?.runOnUiThread {
                            imageView.setImageBitmap(bitmap)
                            imageView.visibility = View.VISIBLE
                            placeholder.visibility = View.GONE
                        }
                    } else {
                        showImagePlaceholder(imageView, placeholder)
                    }
                } catch (e: Exception) {
                    e.printStackTrace()
                    showImagePlaceholder(imageView, placeholder)
                }
            }
        }

        private fun showImagePlaceholder(imageView: ImageView, placeholder: TextView) {
            (itemView.context as? MainActivity)?.runOnUiThread {
                imageView.visibility = View.GONE
                placeholder.visibility = View.VISIBLE
            }
        }

        private fun openProductPage(product: SelectedProduct) {
            val context = itemView.context
            val url = when {
                product.landingUrl.isNotEmpty() -> resolveBackendUrl(product.landingUrl)
                product.productId.isNotEmpty() && context is MainActivity ->
                    context.getBackendUrlWithPath("/api/product-search/products/${product.productId}/page")
                else -> return
            }

            try {
                val intent = Intent(Intent.ACTION_VIEW, Uri.parse(url))
                context.startActivity(intent)
            } catch (e: ActivityNotFoundException) {
                Toast.makeText(context, "未找到可打开商品页的应用", Toast.LENGTH_SHORT).show()
            } catch (e: Exception) {
                e.printStackTrace()
                Toast.makeText(context, "商品页打开失败", Toast.LENGTH_SHORT).show()
            }
        }

        private fun resolveProductImageUrl(imagePath: String): String {
            val cleanPath = imagePath.trim()
            if (cleanPath.isBlank()) return ""
            if (cleanPath.startsWith("http://") || cleanPath.startsWith("https://") || cleanPath.startsWith("/")) {
                return resolveBackendUrl(cleanPath)
            }

            val encodedPath = Uri.encode(cleanPath, "/")
            return resolveBackendUrl("/api/product-search/images/$encodedPath")
        }

        private fun resolveBackendUrl(rawUrl: String): String {
            val cleanUrl = rawUrl.trim()
            if (cleanUrl.isBlank()) return ""
            if (cleanUrl.startsWith("http://") || cleanUrl.startsWith("https://")) {
                return cleanUrl
            }

            val context = itemView.context
            if (context is MainActivity) {
                val path = if (cleanUrl.startsWith("/")) cleanUrl else "/$cleanUrl"
                val encodedPath = Uri.encode(path, "/:?&=%")
                return context.getBackendUrlWithPath(encodedPath)
            }
            return cleanUrl
        }

    }
}
