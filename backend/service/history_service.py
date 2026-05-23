"""对话历史服务层 - 管理用户对话历史和多会话"""
import json
import os
import uuid
from typing import List, Dict, Optional
from datetime import datetime
from config.settings import settings


class ConversationInfo:
    """会话信息"""
    def __init__(self, conv_id: str, title: str, created_at: str, message_count: int, last_message: str = ""):
        self.conv_id = conv_id
        self.title = title
        self.created_at = created_at
        self.message_count = message_count
        self.last_message = last_message


class HistoryService:
    """对话历史服务 - 支持多会话管理"""

    def __init__(self):
        self.storage_path = "./data/history"
        self._ensure_storage_dir()

    def _ensure_storage_dir(self):
        """确保存储目录存在"""
        os.makedirs(self.storage_path, exist_ok=True)

    def _get_user_dir(self, user_id: str) -> str:
        """获取用户目录路径"""
        import hashlib
        user_hash = hashlib.sha256(user_id.encode()).hexdigest()[:16]
        user_dir = os.path.join(self.storage_path, f"user_{user_hash}")
        os.makedirs(user_dir, exist_ok=True)
        return user_dir

    def _get_conv_file(self, user_id: str, conv_id: str) -> str:
        """获取会话文件路径"""
        user_dir = self._get_user_dir(user_id)
        return os.path.join(user_dir, f"conv_{conv_id}.json")

    def _get_user_meta_file(self, user_id: str) -> str:
        """获取用户元数据文件路径（存储当前会话、会话列表等）"""
        user_dir = self._get_user_dir(user_id)
        return os.path.join(user_dir, "meta.json")

    def _load_user_meta(self, user_id: str) -> Dict:
        """加载用户元数据"""
        meta_file = self._get_user_meta_file(user_id)
        if os.path.exists(meta_file):
            try:
                with open(meta_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return {"current_conv": None, "conversations": []}

    def _save_user_meta(self, user_id: str, meta: Dict):
        """保存用户元数据"""
        meta_file = self._get_user_meta_file(user_id)
        with open(meta_file, 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

    def create_conversation(self, user_id: str, title: Optional[str] = None) -> str:
        """创建新会话

        Args:
            user_id: 用户标识
            title: 会话标题（可选）

        Returns:
            新会话ID
        """
        conv_id = str(uuid.uuid4())[:8]
        created_at = datetime.now().isoformat()
        
        # 检查当前会话是否为空，如果为空则删除
        meta = self._load_user_meta(user_id)
        current_conv_id = meta.get("current_conv")
        if current_conv_id:
            # 检查当前会话的消息数量
            current_conv_file = self._get_conv_file(user_id, current_conv_id)
            if os.path.exists(current_conv_file):
                try:
                    with open(current_conv_file, 'r', encoding='utf-8') as f:
                        messages = json.load(f)
                        if len(messages) == 0:
                            # 当前会话为空，删除它
                            os.remove(current_conv_file)
                            # 从会话列表中移除
                            meta["conversations"] = [
                                c for c in meta.get("conversations", [])
                                if c.get("conv_id") != current_conv_id
                            ]
                except (json.JSONDecodeError, IOError):
                    pass
        
        # 设置默认标题
        if not title:
            title = f"对话 {len(meta.get('conversations', [])) + 1}"
        
        # 保存空会话文件
        conv_file = self._get_conv_file(user_id, conv_id)
        with open(conv_file, 'w', encoding='utf-8') as f:
            json.dump([], f, ensure_ascii=False, indent=2)
        
        # 更新用户元数据
        meta["current_conv"] = conv_id
        meta["conversations"].append({
            "conv_id": conv_id,
            "title": title,
            "created_at": created_at,
            "message_count": 0,
            "last_message": ""
        })
        self._save_user_meta(user_id, meta)
        
        return conv_id

    def get_conversations(self, user_id: str) -> List[Dict]:
        """获取用户的所有会话列表

        Args:
            user_id: 用户标识

        Returns:
            会话列表 [{"conv_id": "...", "title": "...", "created_at": "...", "message_count": 0}]
        """
        meta = self._load_user_meta(user_id)
        return meta.get("conversations", [])

    def get_current_conversation(self, user_id: str) -> Optional[str]:
        """获取当前会话ID

        Args:
            user_id: 用户标识

        Returns:
            当前会话ID，无会话时返回None
        """
        meta = self._load_user_meta(user_id)
        return meta.get("current_conv")

    def switch_conversation(self, user_id: str, conv_id: str) -> bool:
        """切换到指定会话

        Args:
            user_id: 用户标识
            conv_id: 会话ID

        Returns:
            是否切换成功
        """
        meta = self._load_user_meta(user_id)
        conv_ids = [conv["conv_id"] for conv in meta.get("conversations", [])]
        
        if conv_id in conv_ids:
            meta["current_conv"] = conv_id
            self._save_user_meta(user_id, meta)
            return True
        return False

    def delete_conversation(self, user_id: str, conv_id: str) -> bool:
        """删除指定会话

        Args:
            user_id: 用户标识
            conv_id: 会话ID

        Returns:
            是否删除成功
        """
        meta = self._load_user_meta(user_id)
        conv_file = self._get_conv_file(user_id, conv_id)
        
        # 检查会话是否存在
        conv_ids = [conv["conv_id"] for conv in meta.get("conversations", [])]
        if conv_id not in conv_ids:
            return False
        
        # 删除会话文件
        if os.path.exists(conv_file):
            os.remove(conv_file)
        
        # 更新元数据
        meta["conversations"] = [conv for conv in meta["conversations"] if conv["conv_id"] != conv_id]
        
        # 如果删除的是当前会话，切换到最后一个会话
        if meta.get("current_conv") == conv_id:
            if meta["conversations"]:
                meta["current_conv"] = meta["conversations"][-1]["conv_id"]
            else:
                meta["current_conv"] = None
        
        self._save_user_meta(user_id, meta)
        return True

    def update_conversation_title(self, user_id: str, conv_id: str, title: str) -> bool:
        """更新会话标题

        Args:
            user_id: 用户标识
            conv_id: 会话ID
            title: 新标题

        Returns:
            是否更新成功
        """
        meta = self._load_user_meta(user_id)
        
        for conv in meta.get("conversations", []):
            if conv["conv_id"] == conv_id:
                conv["title"] = title
                self._save_user_meta(user_id, meta)
                return True
        return False

    def save_message(self, user_id: str, conv_id: str, role: str, content: str, thinking: Optional[str] = None):
        """保存单条消息

        Args:
            user_id: 用户标识
            conv_id: 会话ID
            role: 消息角色 (user/assistant)
            content: 消息内容
            thinking: 思考过程内容（可选）
        """
        file_path = self._get_conv_file(user_id, conv_id)

        # 读取现有历史
        history = self.load_history(user_id, conv_id)

        # 添加新消息
        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat()
        }
        if thinking:
            # 双重清洗：去除思考过程前后的空白换行，避免无意义的 \n 残留
            cleaned = thinking.strip()
            if cleaned:
                message["thinking"] = cleaned
        history.append(message)

        # 写入文件
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        
        # 更新元数据中的消息计数和最后消息
        self._update_conv_meta(user_id, conv_id, len(history), content)

    def _update_conv_meta(self, user_id: str, conv_id: str, message_count: int, last_message: str):
        """更新会话元数据"""
        meta = self._load_user_meta(user_id)
        
        for conv in meta.get("conversations", []):
            if conv["conv_id"] == conv_id:
                conv["message_count"] = message_count
                # 限制最后消息长度
                conv["last_message"] = last_message[:100] if len(last_message) > 100 else last_message
                self._save_user_meta(user_id, meta)
                break

    def load_history(self, user_id: str, conv_id: Optional[str] = None, limit: Optional[int] = None) -> List[Dict]:
        """加载对话历史

        Args:
            user_id: 用户标识
            conv_id: 会话ID（可选，默认使用当前会话）
            limit: 限制返回的消息数量（最近 N 条）

        Returns:
            消息列表
        """
        # 如果没有指定会话ID，使用当前会话
        if not conv_id:
            conv_id = self.get_current_conversation(user_id)
        
        if not conv_id:
            return []
        
        file_path = self._get_conv_file(user_id, conv_id)

        if not os.path.exists(file_path):
            return []

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                history = json.load(f)

            # 如果指定了 limit，返回最近的消息
            if limit and limit > 0:
                history = history[-limit:]

            return history

        except (json.JSONDecodeError, IOError) as e:
            print(f"加载历史记录失败: {e}")
            return []

    def clear_history(self, user_id: str, conv_id: Optional[str] = None) -> bool:
        """清空指定会话的历史（保留会话）

        Args:
            user_id: 用户标识
            conv_id: 会话ID（可选，默认使用当前会话）

        Returns:
            是否成功清空
        """
        if not conv_id:
            conv_id = self.get_current_conversation(user_id)
        
        if not conv_id:
            return False
        
        file_path = self._get_conv_file(user_id, conv_id)

        if os.path.exists(file_path):
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump([], f, ensure_ascii=False, indent=2)
                self._update_conv_meta(user_id, conv_id, 0, "")
                return True
            except OSError as e:
                print(f"清空历史记录失败: {e}")
                return False
        return True

    def get_history_count(self, user_id: str, conv_id: Optional[str] = None) -> int:
        """获取消息数量

        Args:
            user_id: 用户标识
            conv_id: 会话ID（可选，默认使用当前会话）

        Returns:
            消息数量
        """
        return len(self.load_history(user_id, conv_id))

    def ensure_default_conversation(self, user_id: str) -> str:
        """确保用户有至少一个会话，如果没有则创建

        Args:
            user_id: 用户标识

        Returns:
            当前会话ID
        """
        current_conv = self.get_current_conversation(user_id)
        
        if not current_conv:
            # 创建第一个会话
            current_conv = self.create_conversation(user_id, "对话 1")
        
        return current_conv


# 创建全局历史服务实例
history_service = HistoryService()
