"""兼容层：保留历史导入路径 agents.brain_agent。

内部实现已拆分到 agents/brain 目录：
- chat_engine: 对话路由、skill 编排、记忆摘要
- event_engine: 事件决策、聚合推送、风控限流
"""

from agents.brain import handle_event, handle_user_message

__all__ = ["handle_user_message", "handle_event"]
