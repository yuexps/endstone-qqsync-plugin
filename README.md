# QQSync 群服互通插件

基于 Endstone 的 Minecraft 服务器与 QQ 群双向消息互通与绑定插件。

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-green.svg)](https://python.org)
[![Endstone](https://img.shields.io/badge/endstone-0.11+-orange.svg)](https://github.com/EndstoneMC/endstone)

## 前置组件

* **NapCat** 或其他支持 OneBot V11 正向 WebSocket 协议的 QQ 机器人框架。

## 核心功能

* **双向消息同步**：游戏内聊天与 QQ 群消息互相转发。
* **远程命令执行**：在 QQ 群查看玩家状态、执行控制台命令。
* **游戏事件广播**：玩家加入、离开、聊天、死亡事件推送到群。
* **屏蔽词过滤**：命中 `custom_ban_words.txt` 的敏感词替换为 `*`。
* **绑定验证**：强制 QQ 绑定与验证码校验，未绑定玩家降级为访客。
* **退群检测**：定时拉取群成员列表，已退群玩家自动降级权限。

## 快速开始

### 1. 安装

#### 自动安装
安装或更新：
```bash
pip install --upgrade endstone-qqsync-plugin
```

#### 手动安装
在 Releases 页面下载插件对应的 `.whl` 文件，将其放入 Endstone 服务器的 plugins 文件夹中。

### 2. 配置

首次启动服务器后，插件将在以下路径自动生成默认配置文件及自定义屏蔽词过滤规则：
* **主配置文件**：`~/bedrock_server/plugins/qqsync_plugin/config.toml`
* **自定义屏蔽词库**：`~/bedrock_server/plugins/qqsync_plugin/custom_ban_words.txt`

可以在 `custom_ban_words.txt` 中按行写入自定义屏蔽词，双向转发的聊天消息中若包含这些词，将会被自动用星号 `*` 遮蔽过滤。

打开并修改 `config.toml` 配置文件：

```toml
# QQsync 群服互通插件配置文件

napcat_ws = "ws://127.0.0.1:3001"  # NapCat WebSocket 服务器地址（正向WS）
access_token = ""  # 访问令牌（可选，若无则保持空）
admins = ["2899659758"]  # 管理员 QQ 号列表
enable_qq_to_game = true  # QQ 消息转发到游戏
enable_game_to_qq = true  # 游戏消息转发到 QQ
force_bind_qq = true  # 强制 QQ 绑定（启用身份验证系统）
sync_group_card = true  # 自动同步群昵称为玩家名
check_group_member = true  # 启用退群检测功能

# 聊天刷屏检测配置
chat_count_limit = 20  # 1分钟内最多发送消息数（-1则不限制）
chat_ban_time = 300  # 刷屏后禁言时间（秒）
api_qq_enable = false  # QQ 消息 API（默认关闭）

# 群发消息提示词自定义模板
msg_first_join = "[首次加入] 欢迎新玩家 {player} 首次进入服务器！"  # 首次加入提示词（支持 {player}）
msg_join = "[+] {player} 上线了 (第 {sessions} 次登录)"  # 玩家上线提示词（支持 {player}、{sessions}）
msg_quit = "[-] {player} 下线了 ({time})"  # 玩家下线提示词（支持 {player}、{time}、{total_time}）

# 群组配置表数组（支持配置多群，各群拥有独立的事件和指令开关）
[[groups]]
id = 712523104  # 目标 QQ 群号
name = "默认群组"  # 群组名称映射（用于区分消息来源）
enable_chat = true  # 是否开启该群聊天同步
enable_command = true  # 是否开启该群指令响应

[[groups]]
id = 987654321  # 目标 QQ 群号（示例副群）
name = "二群"  # 群组名称映射
enable_chat = true
enable_command = false  # 可针对不同群配置独立的指令或聊天开关
```

### 3. 启动
重启 Endstone 服务器。插件会生成缺失的默认配置、加载 `data.db` 并自动连接 NapCat。

## 使用说明

### QQ 群内指令

#### 查询指令（所有群成员可用）
* `/help` - 显示群服互通命令帮助信息
* `/list` - 查看当前游戏在线玩家列表
* `/tps` - 查看服务器 TPS 和 MSPT 性能指标
* `/info` - 查看系统及硬件负载信息
* `/bind` - 查看当前 QQ 绑定状态
* `/verify <6位验证码>` - 验证 QQ 绑定

#### 管理指令（仅 admins 列表中管理员可用）
* `/cmd <命令>` - 执行后台服务器控制台命令（如：`/cmd say hello`）
* `/bindqq <游戏名> <QQ>` - 强制绑定玩家
* `/check <玩家名|QQ>` - 查询玩家绑定状态、时长统计与权限信息
* `/unbindqq <玩家名|QQ>` - 解除指定玩家的 QQ 绑定
* `/ban <玩家名|QQ> [原因]` - 封禁玩家并解除绑定
* `/unban <玩家名|QQ>` - 解封玩家
* `/banlist` - 查询当前封禁黑名单
* `/tog_qq` - 切换 QQ 消息到游戏的转发开关
* `/tog_game` - 切换游戏消息到 QQ 的转发开关
* `/reload` - 重新载入 TOML 配置文件

### 游戏内指令
* `/bindqq`（别名 `/qq`）- 未绑定的玩家用于启动 QQ 绑定表单

## 强制绑定模式与访客权限

当启用 `force_bind_qq` 时，未绑定 QQ 的玩家会被降级为访客，受限行为：
* 无法发送公屏聊天消息
* 无法破坏或放置方块
* 无法使用任何工具与物品
* 无法从地面拾取或丢弃物品
* 无法与容器或设备交互
* 无法攻击游戏内的任何实体

## 消息 API

当在 `config.toml` 中开启了 `api_qq_enable = true` 时，其他 Endstone 插件可以通过获取本插件实例，调用相关 API 向配置的指定或所有 QQ 群组投递消息。

### 调用示例
```python
# 获取 QQSync 插件实例
qqsync = self.server.plugin_manager.get_plugin("qqsync_plugin")
if qqsync:
    # 场景 1：向所有配置并启用了的群组进行消息广播
    success = qqsync.api_send_message("这里是发给所有群组的推送消息")
    
    # 场景 2：向指定且已在配置中的群组投递消息（需传入群号整型 id）
    target_group_id = 712523104
    success_single = qqsync.api_send_message("这里是定向推送到指定群的消息", target_group_id)
```

## Docker 集成部署示例

在 Docker Compose 中配置 Endstone 与 NapCat 同步互通的极简示例：

```yaml
services:
  endstone:
    container_name: endstone-qqsync
    image: ghcr.io/yuexps/endstone-qqsync-plugin:latest
    init: true
    restart: unless-stopped
    ports:
      - "19132:19132/udp"
    volumes:
      - ./bedrock_server:/app/endstone/bedrock_server:rw
    stdin_open: true
    tty: true
    depends_on:
      - napcat
    networks:
      - qqsync-net

  napcat:
    image: mlikiowa/napcat-docker:latest
    container_name: napcat
    restart: always
    ports:
      - "6099:6099" # 映射 WebUI 网页端口，用于扫码登录 QQ
    environment:
      - TZ=Asia/Shanghai
    volumes:
      - ./qq-data:/app/.config/QQ
      - ./napcat-config:/app/napcat/config
    networks:
      - qqsync-net

networks:
  qqsync-net:
    driver: bridge
```

在该部署模式下，请先登录 NapCat 并配置正向 WebSocket 服务器，端口与 `config.toml` 的 `napcat_ws` 保持一致。

注意：容器间通信不能使用 `127.0.0.1`，需改为 NapCat 的服务名，例如 `napcat_ws = "ws://napcat:3001"`。

## 故障排除

* **无法加载插件**：请确认服务器环境为 Python 3.11+ 且安装了 Endstone 0.11+。
* **WebSocket 连接失败**：检查 NapCat 服务端是否正常启动、WebSocket 地址端口是否被防火墙阻拦。
* **消息无法同步**：检查 `config.toml` 中的群号是否填写正确，并确保对应群下的 `enable_chat` 开关已打开。
