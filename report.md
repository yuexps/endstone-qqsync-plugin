### 核心功能
- **双向消息同步**: 实现 QQ 群 <-> Minecraft 游戏内的聊天消息实时互通。
- **游戏事件同步**: 同步玩家加入、离开、死亡等关键游戏事件到 QQ 群。
- **远程管理**: 管理员可通过 QQ 群发送指令（如 `/cmd`）远程控制服务器。
- **身份验证体系**: 强制或可选的 QQ 绑定机制，用于通过 QQ 验证玩家身份。
- **权限与封禁**: 基于 QQ 绑定的访客权限控制（未绑定受限）及封禁系统。

## 1. 系统架构

本插件采用 **异步 WebSocket 客户端** 模式，连接到实现了 OneBot V11 协议的 QQ 机器人框架（推荐 NapCat）。

### 关键技术栈
- **Python 3.11+**: 插件开发语言。
- **Endstone API**: 0.9.4+，用于与 Minecraft 服务器交互。
- **websockets**: 用于建立与 OneBot 实现端的长连接。
- **asyncio**: 处理 WebSocket 异步 IO，运行在独立线程中避免阻塞游戏主线程。

## 2. 目录结构说明

源码位于 `src/endstone_qqsync_plugin`，主要结构如下：

| 目录/文件 | 说明 |
| :--- | :--- |
| `qqsync_plugin.py` | **插件入口**。继承自 `endstone.plugin.Plugin`，负责生命周期管理、管理器初始化及全局调度。 |
| `core/` | **核心逻辑模块**。 |
| ├── `config_manager.py` | 配置管理，处理 `config.json` 和 `custom_ban_words.txt` 的读写与热重载。 |
| ├── `data_manager.py` | 数据持久化，管理玩家绑定数据、统计信息等。 |
| ├── `event_handlers.py` | 游戏事件监听（Chat, Join, Quit, Death 等）。 |
| ├── `permission_manager.py` | 权限管理，处理访客限制逻辑。 |
| └── `verification_manager.py` | 验证流程管理，生成验证码、处理绑定逻辑。 |
| `websocket/` | **通信模块**。 |
| ├── `client.py` | WebSocket 客户端实现，包含断线重连、心跳维持机制。 |
| └── `handlers.py` | 消息处理器，解析 OneBot 事件（Message, Notice, Request）。 |
| `ui/` | **用户界面**。主要包含游戏内表单（ServerForm）构建逻辑。 |
| `utils/` | **通用工具**。 |
| ├── `message_utils.py` | CQ 码解析、消息格式化工具。 |
| ├── `time_utils.py` | 时间处理工具。 |
| └── `imports.py` | 动态导入工具（如 websockets 库的加载）。 |

## 3. 核心模块详解

### 3.1 主插件类 (`qqsync_plugin.py`)
- **生命周期**:
    - `on_load`: 初始化时间系统。
    - `on_enable`: 初始化所有管理器 (`_init_managers`)，启动 WebSocket 独立线程 (`_init_websocket`)，注册事件监听，启动定时任务（清理、成员检查）。
    - `on_disable`: 发送停服消息，保存数据，优雅关闭 WebSocket 连接和事件循环。
- **定时任务**:
    - 数据清理（每 5 分钟）。
    - 群成员缓存更新（每 1 小时）。
    - 验证码队列处理（每 3 秒）。
    - 在线时长统计（每 1 分钟）。
- **API**: 提供 `api_send_message` 供其他插件调用发送 QQ 消息。

### 3.2 配置管理 (`core/config_manager.py`)
- 加载 `config.json`，支持缺省值填充和旧版本配置自动迁移。
- **动态帮助**: 根据配置（如是否开启强制绑定）动态生成 `/help` 命令的帮助文本。
- **热重载**: 支持通过 `/reload` 命令重新加载配置而无需重启服务器。

### 3.3 通信模块 (`websocket/client.py`)
- **连接机制**: 使用 `asyncio.new_event_loop()` 在独立线程 (`threading.Thread`) 中运行 WebSocket 客户端，确保不阻塞 Minecraft 主线程 (Tick Loop)。
- **重连策略**: 指数退避算法，连续失败次数越多等待越久（最大 30 秒）。
- **心跳维持**: 只有当 `ws.state == 1` (OPEN) 时才发送心跳和处理消息。

### 3.4 身份验证与权限 (`core/verification_manager.py` & `core/permission_manager.py`)
- **流程**: 未绑定玩家加入 -> 触发绑定提示 -> 玩家输入 QQ -> 插件发送验证码到 QQ -> 玩家输入验证码 -> 绑定成功。
- **访客模式**: 若开启 `force_bind_qq`，未绑定玩家会被移除非默认权限组，甚至限制方块交互、聊天等（具体实现依赖 `PermissionManager` 对 Endstone 权限 API 的调用）。

## 4. 数据流向

### 4.1 游戏 -> QQ
1. **事件触发**:  Endstone 触发 `PlayerChatEvent` 或其他事件。
2. **事件处理**: `core/event_handlers.py` 捕获事件。
3. **消息构建**: 格式化消息字符串（如 `[服务器] 玩家: 消息内容`）。
4. **异步发送**: 调用 `websocket.handlers.send_group_msg_to_all_groups`，通过 asyncio 线程安全地将其推送到 WebSocket 发送队列。
5. **NapCat 转发**: NapCat 收到请求，调用 QQ API 发送消息。

### 4.2 QQ -> 游戏
1. **WebSocket 接收**: `websocket/client.py` 的 `_message_loop` 收到 JSON 数据。
2. **分发处理**: 判断 `post_type`，转交给 `websocket/handlers.py`。
3. **消息解析**: `parse_qq_message` 解析 CQ 码（图片、At 等）为可读文本。
4. **主线程回调**: 使用 `server.scheduler` 或线程安全方法调用 Endstone API（如 `server.broadcast_message`）在游戏内广播消息。

## 5. 部署与环境

- **依赖**:
    - 运行环境: Endstone Server (基岩版服务器)
    - 插件环境: Python 3.11+
    - 外部服务: OneBot V11 实现（如 NapCat, Lagrange）
- **安装**:放置 `.whl` 文件到 `plugins` 目录。
- **Docker**: 项目提供了 `Dockerfile` 和 `docker-compose.yml`，支持容器化部署 Endstone + 插件环境。

## 6. OneBot V11 API 使用情况

### 6.1 已实现的 API
插件当前使用了以下 OneBot V11 标准 API (参考 `src/endstone_qqsync_plugin/websocket/handlers.py`)：

| API 名称 | 用途 | 调用场景 |
| :--- | :--- | :--- |
| `send_group_msg` | 发送群消息 | 游戏消息转发、命令回复、验证码发送 |
| `delete_msg` | 撤回消息 | 验证码消息的自动撤回 |
| `set_group_card` | 设置群名片 | 玩家绑定成功后同步游戏名为群昵称 |
| `get_group_member_list` | 获取群成员列表 | 插件启动或重连时同步群成员信息 |

### 6.2 潜在可用的 API (参考 [OneBot V11 文档](https://github.com/botuniverse/onebot-11/blob/master/api/public.md))
以下 API 尚未在本项目中使用，但可能对未来功能扩展有帮助：

- **群组管理**:
    - `get_group_info`: 获取群名称、人数等信息 (可用于优化 `group_names` 配置的自动获取)。
    - `set_group_kick`: 群组踢人 (可用于与游戏封禁联动，实现"封禁即退群")。
    - `set_group_ban`: 群组禁言 (可用于同步游戏内的禁言惩罚)。

- **媒体处理**:
    - `get_image`: 获取图片文件 (可用于将群图片保存到服务器或转换显示)。
    - `can_send_image` / `can_send_record`: 检查发送能力 (用于功能降级处理)。

- **系统监控**:
    - `get_login_info`: 获取 Bot 登录信息 (用于验证连接身份)。
    - `get_status`: 获取运行状态 (用于 `/info` 命令显示 Bot 端状态)。

## 7. Endstone API 使用情况

### 7.1 已实现的 API
插件深度集成了 Endstone API，主要涉及以下几个模块 (参考 `src/endstone_qqsync_plugin/` 下的源码)：

| 模块 | API 名称 | 用途 |
| :--- | :--- | :--- |
| **Plugin** | `endstone.plugin.Plugin` | 插件基类，生命周期管理 (`on_load`, `on_enable`, `on_disable`) |
| **Event** | `endstone.event.event_handler` | 事件监听器装饰器 |
| **Event** | `PlayerChatEvent`, `PlayerJoinEvent`, `PlayerQuitEvent`, `PlayerDeathEvent` | 监听核心游戏事件以实现消息同步 |
| **Event** | `BlockBreakEvent`, `BlockPlaceEvent`, `PlayerInteractEvent` 等 | 监听交互事件以实现访客权限控制 |
| **Command** | `endstone.command.CommandSenderWrapper` | 包装命令发送者，用于截获并转发命令执行结果 |
| **UI** | `endstone.form.ModalForm`, `MessageForm` | 构建游戏内 GUI，如 QQ 绑定表单和确认对话框 |
| **Scheduler** | `server.scheduler.run_task` | 任务调度，确保异步操作（WebSocket回调）在主线程执行 |
| **Server** | `server.broadcast_message`, `dispatch_command` | 广播消息和执行控制台命令 |

### 7.2 潜在可用的 API (参考 [Endstone 文档](https://endstone.readthedocs.io/en/latest/reference/))
以下 API 可能有助于进一步增强插件功能：

- **Scoreboard API**:
    - 用途：可在游戏侧边栏显示 QQ 群消息预览或服务器统计信息。
    - 涉及类：`Scoreboard`, `Objective`, `DisplaySlot`。

- **BossBar API**:
    - 用途：用于显示全服公告或重要通知（如"正在与 QQ 群同步中..."）。
    - 涉及类：`BossBar`。

- **Level / Dimension API**:
    - 用途：如果插件需要支持跨维度的特定同步逻辑（如只同步主世界的聊天）。
    - 涉及类：`Level`, `Dimension`。

- **Direct NBT API** (New in 0.11.0):
    - 用途：更直接地操作物品或实体的 NBT 数据，无需绕过复杂的方法。
    - 涉及类：`CompoundTag`, `ListTag` 等。

- **Player Chat Format** (New in 0.11.0):
    - 用途：通过 `PlayerChatEvent::setFormat` 直接修改聊天格式，可能简化 `on_player_chat` 中的处理逻辑。
    - 涉及方法：`setFormat(format: str)`。

---
