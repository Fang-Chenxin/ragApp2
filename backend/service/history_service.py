"""对话历史服务层 - 管理用户对话历史"""
import json
import os
from typing import List, Dict, Optional
from datetime import datetime
from config.settings import settings


class HistoryService:
    """对话历史服务 - 使用文件存储"""

    def __init__(self):
        self.storage_path = "./data/history"
        self._ensure_storage_dir()

    def _ensure_storage_dir(self):
        """确保存储目录存在"""
        os.makedirs(self.storage_path, exist_ok=True)

    def _get_user_file(self, user_id: str) -> str:
        """获取用户历史文件路径"""
        # 使用 SHA256 哈希处理 user_id，避免文件名安全问题
        import hashlib
        user_hash = hashlib.sha256(user_id.encode()).hexdigest()[:16]
        return os.path.join(self.storage_path, f"history_{user_hash}.json")

    def save_message(self, user_id: str, role: str, content: str):
        """保存单条消息

        Args:
            user_id: 用户标识
            role: 消息角色 (user/assistant)
            content: 消息内容
        """
        file_path = self._get_user_file(user_id)

        # 读取现有历史
        history = self.load_history(user_id)

        # 添加新消息
        history.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat()
        })

        # 写入文件
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)

    def save_conversation(self, user_id: str, messages: List[Dict[str, str]]):
        """保存完整对话

        Args:
            user_id: 用户标识
            messages: 消息列表 [{"role": "user"/"assistant", "content": "..."}]
        """
        file_path = self._get_user_file(user_id)

        history = []
        for msg in messages:
            history.append({
                "role": msg["role"],
                "content": msg["content"],
                "timestamp": datetime.now().isoformat()
            })

        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)

    def load_history(self, user_id: str, limit: Optional[int] = None) -> List[Dict]:
        """加载用户对话历史

        Args:
            user_id: 用户标识
            limit: 限制返回的消息数量（最近 N 条）

        Returns:
            消息列表
        """
        file_path = self._get_user_file(user_id)

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

    def clear_history(self, user_id: str) -> bool:
        """清空用户对话历史

        Args:
            user_id: 用户标识

        Returns:
            是否成功删除
        """
        file_path = self._get_user_file(user_id)

        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                return True
            except OSError as e:
                print(f"删除历史记录失败: {e}")
                return False
        return True

    def get_history_count(self, user_id: str) -> int:
        """获取用户消息数量

        Args:
            user_id: 用户标识

        Returns:
            消息数量
        """
        return len(self.load_history(user_id))


# 创建全局历史服务实例
history_service = HistoryService()
