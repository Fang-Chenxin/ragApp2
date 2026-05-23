#!/bin/bash
# 测试豆包 API 连接

API_KEY="your_api_key_here"
MODEL="ep-20260514111645-lmgt2"
BASE_URL="https://ark.cn-beijing.volces.com/api/v3"

curl -X POST "${BASE_URL}/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${API_KEY}" \
  -d '{
    "model": "'${MODEL}'",
    "messages": [
      {"role": "user", "content": "你好，请简单介绍一下自己"}
    ]
  }' 2>/dev/null | jq .
