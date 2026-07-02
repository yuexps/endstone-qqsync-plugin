# QQSync 群服互通插件技术报告与架构设计

本文档介绍了 QQSync 群服互通插件的技术架构、核心模块设计、数据流向以及与 Endstone/OneBot API 的交互细节。

## 系统架构

本插件采用异步正向 WebSocket 客户端模式，与 OneBot V11 协议的 QQ 机器人服务端（如 NapCat）建立长连接。

### 关键技术栈
- Python 3.11+：开发语言。
- Endstone API 0.11.0+：与 Minecraft 服务器进行底层交互。
- sqlite3：用于持久化玩家绑定数据与在线时长。
- asyncio：使用独立的子线程事件循环运行 WebSocket 通信，规避对游戏主线程的阻塞。

## 目录结构说明

重构后的插件核心源码结构如下：

| 目录/文件 | 说明 |
| :--- | :--- |
| `qqsync_plugin.py` | 插件主入口。负责生命周期管理、业务管理器初始化与全局调度。 |
| `core/` | 核心逻辑模块。 |
| ├── `config.py` | 配置管理，处理 `config.toml` 配置文件的读写、自愈迁移、热重载以及注释保留。 |
| ├── `data.py` | 持久化数据管理，使用本地 SQLite3 数据库（`data.db`），管理绑定及游玩时长数据。 |
| ├── `events.py` | 游戏事件监听器（处理 Chat, Join, Quit, Death 等核心游戏事件）。 |
| ├── `permissions.py` | 权限控制管理，处理访客限制逻辑与权限挂载附件生命周期。 |
| ├── `ui.py` | 用户界面。主要用于构建游戏内的 QQ 绑定表单和确认对话框。 |
| └── `verification.py` | 绑定流程管理，包括验证码生命周期、发送队列防抖缓冲与风控校验。 |
| `qq/` | QQ 通信模块。 |
| ├── `client.py` | WebSocket 客户端实现，维护独立子线程事件循环、断线退避自愈与心跳监测。 |
| └── `commands.py` | 群消息命令处理器，解析并分发响应在 QQ 群内发送的各类指令。 |
| `utils/` | 辅助工具模块。 |
| ├── `helpers.py` | 系统通用助手函数。 |
| ├── `imports.py` | 动态三方依赖包加载器。 |
| ├── `messages.py` | CQ 码与消息段解析器、表情文本翻译映射以及责任链过滤管道。 |
| └── `timing.py` | 高精度时间戳获取工具。 |

## 核心业务设计

### 1. 配置管理器 (`core/config.py`)
- **TOML 支持**：升级配置文件为 `config.toml`，并利用表数组对各个群组的 `enable_chat`（聊天转发）与 `enable_command`（指令响应）进行细粒度隔离控制。
- **自动迁移与备份**：如检测到旧版 `config.json`，会自动读取并转换为 TOML 格式保存，同时分别将原有 json 文件与可能被覆盖的旧 toml 文件备份为 `.bak` 状态。
- **热重载与注释保留**：重构了序列化保存逻辑，每次回写均在字段旁附加详细中文注释说明，并支持通过群内 `/reload` 实时重载。

### 2. SQLite3 持久化与时长结算 (`core/data.py`)
- **高并发保障**：废弃了易损损坏的 `data.json` 文件读写，迁移至 SQLite3。通过底层线程锁确保多线程异步写事务绝对安全。
- **增量时长结算**：注册服务器定时任务，每隔 60 秒为所有在线玩家增量统计游玩时长写盘。解决了传统崩服机制引发的时长数据丢失痛点。

### 3. 长连接自愈机制 (`qq/client.py`)
- 使用 `asyncio.run_coroutine_threadsafe` 将收发任务安全推送到后台事件循环。
- 在网络重连中，使用指数退避加随机扰动算法（Jitter）控制等待时延，规避网络异常时产生网络风暴。

### 4. 权限与风控体系 (`core/permissions.py` & `core/verification.py`)
- **访客权限控制**：基于事件监听与权限拦截，对未绑定 QQ 的访客移除非默认权限组并取消其聊天、放置/破坏方块、使用物品及交互的执行权限。
- **防抖队列**：在验证码发送方面，加入了基于定时任务的防抖队列，对多条并发验证请求进行排队和频率过滤，优化网络表现。

## 数据流向

### 游戏到 QQ
1. **事件捕获**：Endstone 触发 `PlayerChatEvent` 或其他事件，传递至 `core/events.py`。
2. **消息构建与净化**：将聊天内容传过 `utils/messages.py` 中的中间件管道，对敏感词进行过滤编码，组装成同步文本。
3. **推入发送队列**：调用 `websocket_client.send_message` 将数据包推入子线程异步发送缓冲区。
4. **WebSocket 投递**：网络线程将带有 echo 校验的数据段以 JSON 格式发送给 OneBot 客户端转发。

### QQ 到游戏
1. **网络接收与分发**：`qq/client.py` 接收到 OneBot 推送的消息事件，并交由 `qq/commands.py` 识别是否为特殊指令或普通群消息。
2. **表情与 CQ 码解析**：利用 `utils/messages.py` 中的映射字典和正则表达式，对非文本消息段（图片、语音、AT、表情字面量等）进行格式转换。
3. **线程安全广播**：借助 Endstone `server.scheduler.run_task` 定时调度器，将打印/广播行为调度回服务器主线程安全执行。

## OneBot V11 API 使用情况

插件使用了以下 API：
- `send_group_msg`：发送群消息以用于群聊天消息转发、验证码投递及命令响应。
- `delete_msg`：撤回验证码消息以进行安全风控保护。
- `set_group_card`：自动同步玩家游戏名为群内名片昵称。
- `get_group_member_list`：定时拉取群成员列表，进行退群检测过滤。

## 对外消息 API 设计

插件在 `qqsync_plugin.py` 中暴露了公共方法 `api_send_message(self, text: str, group_id: int | None = None) -> bool`：
- 当 `group_id` 为 `None` 时，调用 `broadcast_to_groups` 实现对所有已配置群的消息广播推送。
- 当传入指定的 `group_id` 时，系统先进行配置内合法性校验，通过后调用 `send_group_message` 实现对单个指定 QQ 群的定向单发投递。

## Endstone API 使用情况

插件调用了以下 Endstone 接口：
- `Plugin` 生命周期的生命周期钩子以处理各种加载初始化和资源注销。
- `event_handler` 及核心玩家与方块交互事件监听器，实现互通与权限管理。
- `CommandSenderWrapper` 包装控制台，以在 QQ 群内运行后台指令并捕获日志回复。
- `ModalForm` / `MessageForm` 表单接口，构建游戏内的直观绑定界面。
