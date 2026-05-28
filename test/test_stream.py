#!/usr/bin/env python3
"""测试流式聊天API"""
import requests
import json

url = "http://localhost:8000/api/chat/stream"

payload = {
    "messages": [],
    "user_query": "你好，请介绍一下自己",
    "user_id": "test_user",
    "conv_id": None,
    "include_thinking": True
}

print("发送流式聊天请求...")
print(f"URL: {url}")
print(f"Payload: {json.dumps(payload, ensure_ascii=False, indent=2)}")
print("-" * 50)

try:
    response = requests.post(url, json=payload, stream=True)
    
    if response.status_code != 200:
        print(f"错误: HTTP {response.status_code}")
        print(response.text)
        exit(1)
    
    print("开始接收流式数据:")
    full_reply = ""
    
    for line in response.iter_lines():
        if line:
            line_str = line.decode('utf-8')
            if line_str.startswith("data: "):
                data_str = line_str[6:]
                try:
                    data = json.loads(data_str)
                    
                    if "error" in data and data["error"]:
                        print(f"\n错误: {data['error']}")
                        break
                    
                    if data.get("content"):
                        print(data["content"], end="", flush=True)
                        full_reply += data["content"]
                    
                    if data.get("done"):
                        print("\n" + "-" * 50)
                        print(f"完成! 会话ID: {data.get('conv_id')}")
                        print(f"历史保存: {data.get('history_saved')}")
                        print(f"完整回复长度: {len(full_reply)} 字符")
                        
                except json.JSONDecodeError as e:
                    print(f"\nJSON解析错误: {e}")
                    print(f"原始数据: {data_str}")
    
except Exception as e:
    print(f"\n请求失败: {e}")
    import traceback
    traceback.print_exc()
