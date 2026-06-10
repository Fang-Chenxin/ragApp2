#!/bin/bash
# 测试豆包 API 连接

VERBOSE=""
if [ "$1" = "-v" ]; then
    VERBOSE=1
fi

API_KEY="your_api_key_here"
MODEL="ep-20260514111645-lmgt2"
BASE_URL="https://ark.cn-beijing.volces.com/api/v3"

# 优先级：命令行配置 > 环境变量 > backend/.env
_get_api_key() {
    # 1. 检查环境变量
    if [ -n "$LLM_API_KEY" ]; then
        echo "$LLM_API_KEY"
        return 0
    fi

    # 2. 如果使用默认值，则从 backend/.env 读取
    if [ "$API_KEY" = "your_api_key_here" ]; then
        local ENV_FILE="backend/.env"
        if [ -f "$ENV_FILE" ]; then
            local LLM_KEY=$(grep "^LLM_API_KEY=" "$ENV_FILE" | cut -d'=' -f2- | tr -d '"' | tr -d "'" | tr -d ' ')
            if [ -n "$LLM_KEY" ]; then
                echo "$LLM_KEY"
                return 0
            fi
        fi
    fi

    return 1
}

# 获取 API Key
RESOLVED_KEY=$(_get_api_key)
if [ -z "$RESOLVED_KEY" ]; then
    echo "[ERROR] 请先配置 API Key"
    echo "[ERROR] 方式1: 设置环境变量 export LLM_API_KEY=your_actual_key"
    echo "[ERROR] 方式2: 编辑 backend/.env，填入 LLM_API_KEY=your_actual_key"
    exit 1
fi

API_KEY="$RESOLVED_KEY"

if [ -n "$VERBOSE" ]; then
    echo "[INFO] 测试火山方舟 API 连接..."
    echo "[CHECK] API Key: ${API_KEY:0:8}..."
    echo "[CHECK] 端点: $BASE_URL/chat/completions"
fi

# 执行 API 请求
RESPONSE=$(curl -s -X POST "${BASE_URL}/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${API_KEY}" \
  -d '{
    "model": "'${MODEL}'",
    "messages": [
      {"role": "user", "content": "你好，请简单介绍一下自己"}
    ]
  }')

# 检查响应
if [ -z "$RESPONSE" ]; then
    echo "[ERROR] API 请求无响应，请检查网络连接或 API Key"
    exit 1
fi

# 检查是否包含错误
if echo "$RESPONSE" | jq -e '.error' > /dev/null 2>&1; then
    echo "[ERROR] API 返回错误:"
    echo "$RESPONSE" | jq '.error'
    exit 1
fi

if [ -n "$VERBOSE" ]; then
    echo "[RESULT] 连接成功: HTTP 200"
fi

# 输出格式化结果
echo "$RESPONSE" | jq .
