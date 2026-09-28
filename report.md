# QQSync 群服互通插件技术报告与架构设计

本文档说明 QQSync 群服互通插件的架构、模块设计、数据流向以及与 Endstone/OneBot 的交互方式。

## 系统架构

本插件采用异步正向 WebSocket 客户端模式，与 OneBot V11 协议的 QQ 机器人服务端（如 NapCat）建立长连接。

### 关键技术栈
- Python 3.11+：开发语言。
- Endstone API 0.11.0+：与 Minecraft 服务器进行底层交互。
- sqlite3：用于持久化玩家绑定数据与在线时长。
- asyncio：WebSocket 通信运行在独立子线程事件循环，不阻塞游戏主线程。

## 目录结构说明

插件核心源码结构：

| 目录/文件 | 说明 |
| :--- | :--- |
| `qqsync_plugin.py` | 插件主入口：生命周期管理、管理器初始化与全局调度。 |
| `core/` | 核心逻辑模块 |
| ├── `config.py` | 配置管理：`config.toml` 读写、默认配置生成、热重载与字段注释回写。 |
| ├── `data.py` | 数据存储：本地 SQLite3（`data.db`），以自增 `id` 为主键、`XUID` 为唯一身份键，保存绑定与游玩时长。 |
| ├── `events.py` | 游戏事件监听器（Chat / Join / Quit / Death 等）。 |
| ├── `permissions.py` | 访客限制与权限附件生命周期管理。 |
| ├── `ui.py` | 游戏内绑定表单与确认对话框。 |
| └── `verification.py` | 绑定流程：验证码生命周期、发送队列与频控校验。 |
| `qq/` | QQ 通信模块 |
| ├── `client.py` | WebSocket 客户端：独立子线程事件循环、断线退避重连与心跳。 |
| └── `commands.py` | 群命令处理器：解析并分发 QQ 群指令。 |
| `utils/` | 辅助工具模块 |
| ├── `helpers.py` | 通用辅助函数（时间格式化、QQ 号校验等）。 |
| ├── `imports.py` | 动态三方依赖包加载器。 |
| ├── `messages.py` | CQ 码与消息段解析、表情文本映射、过滤管道。 |
| ├── `timing.py` | 时间戳与运行时长工具。 |
| └── `system.py` | 系统与硬件信息采集（供 `/info` 指令使用）。 |

## 核心业务设计

### 1. 配置管理器 (`core/config.py`)
- **TOML 配置**：`config.toml` 使用表数组，按群组分别控制 `enable_chat`（聊天转发）与 `enable_command`（指令响应）。
- **配置自愈**：文件缺失时生成带注释的默认模板；解析失败回退内存默认值并记录日志。
- **热重载**：保存时在字段旁回写中文注释，群内 `/reload` 可实时重载。

### 2. SQLite3 存储与时长结算 (`core/data.py`)
- **身份模型**：`players` 表以自增 `id` 为主键、`xuid` 唯一索引；玩家名可变，仅作展示与查询。旧版以 `player_name` 为主键的表在启动时自动迁移，并按 XUID 合并改名残留的重复行。
- **并发与连接**：主线程与网络线程的读写共用一把锁串行执行，SQLite 连接在进程内复用，插件卸载时关闭。
- **时长结算**：定时任务每 60 秒为在线玩家累计游玩时长并落盘。

### 3. 长连接与重连 (`qq/client.py`)
- 收发任务通过 `asyncio.run_coroutine_threadsafe` 投递到后台事件循环。
- 断线重连采用指数退避加随机抖动，避免集中重连。

### 4. 权限与频控 (`core/permissions.py` & `core/verification.py`)
- **访客权限**：未绑定 QQ 的玩家移除非默认权限组，并取消聊天、放置/破坏方块、使用物品与交互权限。
- **发送队列**：验证码通过定时任务出队发送，对并发请求排队并限频。

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
- `delete_msg`：撤回验证码消息。
- `set_group_card`：自动同步玩家游戏名为群内名片昵称。
- `get_group_member_list`：定时拉取群成员列表用于退群检测。
- `get_stranger_info`：绑定前查询 QQ 昵称用于二次确认。

## 对外消息 API 设计

插件在 `qqsync_plugin.py` 中暴露了公共方法 `api_send_message(self, text: str, group_id: int | None = None) -> bool`：
- `group_id` 为 `None` 时向所有已配置群广播。
- 传入 `group_id` 时先校验其是否在配置内，通过后向该群单发。

## Endstone API 使用情况

插件调用了以下 Endstone 接口：
- `Plugin` 生命周期钩子，处理加载初始化与资源注销。
- `event_handler` 与玩家、方块交互事件监听器，实现消息互通与权限管理。
- `CommandSenderWrapper` 包装控制台，在 QQ 群内执行后台指令并捕获回显。
- `ModalForm` / `MessageForm`，构建游戏内绑定表单。
