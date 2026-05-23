from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import chromadb
from chromadb.config import Settings
from openai import AsyncOpenAI
import os
from contextlib import asynccontextmanager


USE_DOUBAO_EMBEDDING = os.getenv("USE_DOUBAO_EMBEDDING", "false").lower() == "true"
DOUBAO_MODEL = os.getenv("DOUBAO_MODEL", "ep-20260514111645-lmgt2")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global chroma_client, collection, llm_client, embedding_client
    chroma_client = chromadb.PersistentClient(
        path="./data/chroma",
        settings=Settings(anonymized_telemetry=False)
    )
    
    if USE_DOUBAO_EMBEDDING:
        from chromadb.utils import embedding_functions
        doubao_ef = embedding_functions.OpenAIEmbeddingFunction(
            api_key=os.getenv("DOUBAO_API_KEY", "your_doubao_key_here"),
            api_base="https://ark.cn-beijing.volces.com/api/v3",
            model_name="doubao-embedding-vision"
        )
        collection = chroma_client.get_or_create_collection(
            name="agent_knowledge",
            embedding_function=doubao_ef
        )
        print("✅ 使用豆包 Doubao-embedding-vision 作为向量模型")
    else:
        collection = chroma_client.get_or_create_collection(name="agent_knowledge")
        print("✅ 使用本地免费 all-MiniLM-L6-v2 Embedding 模型，无需任何API Key")
    
    llm_client = AsyncOpenAI(
        api_key=os.getenv("DOUBAO_API_KEY", "your_doubao_key_here"),
        base_url="https://ark.cn-beijing.volces.com/api/v3"
    )
    
    if USE_DOUBAO_EMBEDDING:
        embedding_client = AsyncOpenAI(
            api_key=os.getenv("DOUBAO_API_KEY", "your_doubao_key_here"),
            base_url="https://ark.cn-beijing.volces.com/api/v3"
        )
    else:
        embedding_client = None
    
    yield
    print("Shutting down...")


app = FastAPI(title="Agent对话应用后端", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    user_query: str


class ChatResponse(BaseModel):
    reply: str


class AddKnowledgeRequest(BaseModel):
    content: str
    metadata: Optional[dict] = None


@app.post("/api/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    api_key = os.getenv("DOUBAO_API_KEY", "")
    
    if not api_key or api_key == "your_doubao_key_here":
        return ChatResponse(reply=f"您好！我收到了您的消息：'{request.user_query}'。\n\n这是模拟回复。要使用真实的AI对话功能，请配置 DOUBAO_API_KEY 环境变量。")
    
    try:
        results = collection.query(
            query_texts=[request.user_query],
            n_results=3
        )
        context_docs = "\n".join([str(doc) for doc in results['documents'][0]]) if results['documents'] else ""
        
        system_prompt = f"""你是一个智能Agent对话助手。
参考知识库内容：
{context_docs}

请基于以上知识库内容和用户进行友好对话，如果知识库中没有相关内容就正常回答用户问题。"""

        messages = [{"role": "system", "content": system_prompt}]
        for msg in request.messages:
            messages.append({"role": msg.role, "content": msg.content})
        messages.append({"role": "user", "content": request.user_query})

        response = await llm_client.chat.completions.create(
            model=DOUBAO_MODEL,
            messages=messages,
            temperature=0.7
        )
        reply_content = response.choices[0].message.content or "抱歉，我现在无法回答您的问题。"
        
        return ChatResponse(reply=reply_content)
    except Exception as e:
        error_msg = str(e).encode('utf-8').decode('utf-8', errors='replace')
        print(f"Error: {error_msg}")
        raise HTTPException(status_code=500, detail=error_msg)


@app.post("/api/add_knowledge")
async def add_knowledge(request: AddKnowledgeRequest):
    try:
        collection.add(
            documents=[request.content],
            metadatas=[request.metadata or {}],
            ids=[f"doc_{collection.count() + 1}"]
        )
        return {"status": "success", "message": "知识添加成功"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/")
async def root():
    return {"message": "Agent对话应用后端服务运行正常！"}


if __name__ == "__main__":
    import uvicorn
    os.makedirs("./data/chroma", exist_ok=True)
    uvicorn.run(app, host="0.0.0.0", port=8000)
