# StockAgent

个人投资辅助系统 / 智能盯盘系统（Personal Investment Assistant / Intelligent Market Monitoring System）

StockAgent 是一个面向个人投资研究与实时监控场景的事件驱动系统。项目围绕 Data Ingestion、Signal Analysis、Decision Intelligence 与 Notification Delivery 构建闭环能力，将 Market Data 与 News Data 统一纳入同一决策链路，实现从数据到信号再到推送的自动化处理。


## 一、项目定位（Project Positioning）

| 维度 | 说明 |
|---|---|
| 项目名称 | StockAgent |
| 项目类型 | 个人投资辅助系统 / 智能盯盘系统 |
| 架构风格 | Event-driven, Decoupled, Scalable |
| 交互方式 | Feishu Bot + Webhook |
| 决策增强 | LLM-based Analysis and Decision Support |

## 二、技术特性（Technical Highlights）

| 特性 | 说明 |
|---|---|
| 事件驱动主链路（Event-driven Pipeline） | Producer -> Analyzer -> Decision -> Notifier |
| 双事件域（Dual Event Domains） | 同时处理 Market Events 与 News Events |
| 大模型增强（LLM Integration） | 用于新闻语义抽取、事件研判与建议增强 |
| 核心解耦（Decoupled Brain） | 对话编排与事件决策拆分，便于独立演进 |
| 推送治理（Push Governance） | 限流、聚合、去重，降低噪声与刷屏 |

## 三、架构总览（Architecture Overview）

### 3.1 事件流（Event Flow）

Producer -> Analyzer -> Decision -> Notifier

| 阶段 | 职责 |
|---|---|
| Producer | 接收 Feishu 消息，采集行情/新闻数据，构建并投递事件 |
| Analyzer | 对 Market Data 与 News Data 进行分析，输出结构化结果 |
| Decision | 执行策略判断（push / ignore / call_skill）与风控治理 |
| Notifier | 格式化并回发消息到 Feishu |

### 3.2 分层设计（Layered Design）

| 层级 | 说明 |
|---|---|
| 交互层（Interaction Layer） | 对外通信、Webhook 接入、消息入口治理 |
| 编排层（Orchestration Layer） | 事件路由、任务触发、流程调度 |
| 智能层（Intelligence Layer） | 规则判断、LLM 推理、Skill 编排 |
| 基础设施层（Infrastructure Layer） | Redis 队列、缓存、配置、日志 |

## 四、核心模块（Core Modules）

| 模块 | 关键能力 | 价值 |
|---|---|---|
| Brain（对话 + 事件决策核心） | 意图识别、记忆管理、事件判定、推送策略 | 统一决策入口，降低系统耦合 |
| Agents（market / news 分析） | 行情信号分析、新闻批处理分析 | 输出结构化事件，支撑决策 |
| Bot（webhook + watcher） | 消息接入、实时监控、事件投递 | 构建实时事件来源 |
| Events（队列系统，基于 Redis） | 队列写入、窗口读取、消费裁剪 | 控制处理节奏与吞吐 |
| Notifier（消息推送） | 消息发送、格式封装、失败回执 | 对外统一通知出口 |
| Skills（可扩展插件机制） | 插件化能力扩展与业务动作执行 | 提升扩展性与可维护性 |

## 五、仓库结构（Repository Structure）

### 5.1 目录树（Tree）

    stockagent/
    ├── agents/
    │   ├── brain/
    │   │   ├── chat_engine.py
    │   │   ├── event_engine.py
    │   │   ├── memory.py
    │   │   └── utils.py
    │   ├── brain_agent.py
    │   ├── market_agent.py
    │   └── news_agent.py
    ├── bot/
    │   ├── server.py
    │   ├── market_bot.py
    │   └── news_bot.py
    ├── events/
    │   └── queue.py
    ├── notifier/
    │   └── feishu.py
    ├── skills/
    ├── tools/
    ├── infra/
    │   └── redis_store.py
    ├── config.py
    └── scripts/
        ├── start_server.sh
        └── stop_server.sh

### 5.2 文件职责（File Responsibilities）

| 路径 | 作用 |
|---|---|
| agents/brain/chat_engine.py | 对话主流程：意图路由、Skill 协调、会话响应生成 |
| agents/brain/event_engine.py | 事件主流程：事件判定、策略过滤、聚合推送 |
| agents/brain/memory.py | 会话记忆与摘要管理（上下文持久化与压缩） |
| agents/brain/utils.py | Brain 共享工具函数（如 JSON 提取） |
| agents/brain_agent.py | 兼容层入口，保持历史导入路径稳定 |
| agents/market_agent.py | 行情数据分析与市场事件构建 |
| agents/news_agent.py | 新闻窗口批处理分析与 news_signal 生成 |
| bot/server.py | Flask Webhook 服务入口，统一接收并分发请求 |
| bot/market_bot.py | 行情 watcher 启动与轮询调度 |
| bot/news_bot.py | 新闻抓取、入队与触发处理 |
| events/queue.py | Redis 队列封装：写入、读取、裁剪等操作 |
| notifier/feishu.py | 飞书消息发送/回调封装 |
| infra/redis_store.py | Redis 基础读写封装 |
| config.py | 系统配置与运行参数定义 |
| scripts/start_server.sh | 服务启动脚本 |
| scripts/stop_server.sh | 服务停止脚本 |
| skills/ | 可插拔技能模块目录（策略、规则、查询等） |
| tools/ | 数据获取与指标计算工具目录 |

## 六、核心能力（Core Capabilities）

| 能力 | 说明 |
|---|---|
| 实时盯盘（Real-time Monitoring） | 基于规则持续捕捉价格、波动、量能等异动 |
| 新闻理解（News Intelligence） | 对快讯进行批量语义分析与情绪抽取 |
| 决策引擎（Decision Engine） | 支持 push / ignore / call_skill 三态决策 |
| 推送治理（Push Governance） | Per-symbol Rate Limiting + Sentiment Aggregation |
| 对话控制（Conversational Control） | 通过 Feishu 指令执行查询、配置和分析任务 |

## 七、快速启动（Quick Start）

### 7.1 环境要求（Prerequisites）

| 组件 | 要求 |
|---|---|
| Python | 3.10+ |
| Redis | 可用实例 |
| LLM | 可用 API Key |
| Feishu | 已配置机器人与回调能力 |

### 7.2 安装依赖（Install Dependencies）

    pip install -r requirements.txt

### 7.3 配置项（Configuration）

在配置文件 config.py 中准备以下关键参数：

| 配置项 | 说明 |
|---|---|
| OPENAI_API_KEY | LLM 访问密钥 |
| TUSHARE_TOKEN | 行情数据接口 Token |
| FEISHU_WEBHOOK | 飞书机器人 webhook 地址 |
| REDIS_HOST / REDIS_PORT | Redis 连接参数 |
| BASE_URL | LLM 服务地址 |
| MODEL | 使用的模型名称 |
| NEWS_API_KEY | 新闻数据接口密钥 |

建议使用环境变量管理敏感信息，避免密钥进入版本库。

### 7.4 启停服务（Run Service）

| 动作 | 命令 |
|---|---|
| 启动 | bash scripts/start_server.sh |
| 停止 | bash scripts/stop_server.sh |

## 八、运行流程（Runtime Workflow）

| 步骤 | 流程说明 |
|---|---|
| 1 | 用户发送 Feishu 指令（分析 / 配置 / 查询） |
| 2 | Webhook Server 接收请求并转发到 Brain |
| 3 | Brain 完成意图识别与 Skill 调度 |
| 4 | Watcher 产生 Market Events / News Events |
| 5 | Agents 分析并生成结构化事件 |
| 6 | Brain Event Engine 执行限流、聚合与决策 |
| 7 | Notifier 输出最终消息到 Feishu |

## 九、扩展方向（Scalability Roadmap）

| 方向 | 说明 |
|---|---|
| 回测与评估（Backtesting & Evaluation） | 增加信号效果评估与策略闭环验证 |
| 多通道通知（Multi-channel Notification） | 扩展至 WeCom、Telegram 等渠道 |
| 配置中心化（Configuration Management） | 支持多环境配置与密钥托管 |
| 测试体系（Testing System） | 完善 Unit Test / Integration Test |
| 可观测性（Observability） | 增加 Metrics / Tracing / Alerting |

## 十、免责声明（Disclaimer）

本项目仅用于技术研究与工程实践，不构成任何投资建议。金融市场存在不确定性，请独立评估风险并谨慎决策。

这是我因为春招找工作做的练手agent项目，吸收了很多大佬们的经验。我本人也炒股，虽然不是什么很厉害的量化机器人，不过帮忙看盘和看新闻我觉得对我这种懒人来说挺好的。大家可以交流下经验，提提意见，祝在找实习和找工作的大家可以找到自己喜欢的工作。我会一边春招一边优化。祝各位顺顺利利，大家加油！！！！

觉得对你有帮助的话麻烦点个小星星吧