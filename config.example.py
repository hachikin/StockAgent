"""示例配置文件（可提交到 GitHub）。

使用方法：
1) 复制为 config.py
2) 填入你自己的真实密钥
3) 不要把真实 config.py 提交到远程仓库
"""

# LLM
OPENAI_API_KEY = "your_openai_api_key"
BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL = "kimi-k2.5"

# Data Providers
TUSHARE_TOKEN = "your_tushare_token"
NEWS_API_KEY = "your_news_api_key"

# Feishu
FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/your_webhook"
# 可选：用于按 message_id 回帖
FEISHU_APP_ID = "your_feishu_app_id"
FEISHU_APP_SECRET = "your_feishu_app_secret"

# Redis
REDIS_HOST = "localhost"
REDIS_PORT = 6379
REDIS_DB = 0
REDIS_SOCKET_TIMEOUT = 2.0

# 可选：股票别名映射
# STOCK_ALIASES = {
#     "特斯拉": "TSLA",
#     "苹果": "AAPL",
# }
