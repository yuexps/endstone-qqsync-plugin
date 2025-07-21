# QQSync 群服互通插件

一个简易的 Minecraft 服务器与 QQ 群聊双向互通插件，基于 Endstone 插件框架开发。

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.12+-green.svg)
![Endstone](https://img.shields.io/badge/endstone-0.6-orange.svg)

## ✨ 核心功能

- 🔄 **双向消息同步**：QQ群聊 ↔ 游戏内聊天
- 🎮 **游戏事件同步**：玩家加入/离开/聊天/死亡消息
- 🛠️ **远程管理**：通过QQ群远程查看玩家、执行命令
- 📱 **智能解析**：支持图片、视频等非文本消息转换显示
- ⚙️ **灵活控制**：支持单向/双向同步切换

## 🚀 快速开始

### 1. 安装
将插件放到 Endstone 服务器插件目录：
```
bedrock_server/plugins/endstone_qqsync_plugin-0.0.1-py2.py3-none-any.whl
```

### 2. 配置
首次运行自动生成 `config.json`
```
bedrock_server/plugins/qqsync_plugin/config.json
```
修改以下配置：
```json
{
  "napcat_ws": "ws://localhost:3001",     // NapCat WebSocket服务器 地址（正向WS）
  "access_token": "",     // 访问令牌（可选）
  "target_group": 712523104,             // 目标QQ群号
  "admins": ["2899659758"],              // 管理员QQ号列表
  "enable_qq_to_game": true,             // QQ消息转发到游戏
  "enable_game_to_qq": true,             // 游戏消息转发到QQ
}
```

### 3. 启动
启动Endstone服务器即可自动连接NapCat并开始同步群服消息。

## 🎯 使用说明

### QQ群内命令

#### � 查询命令（所有用户可用）
- `/help` - 显示本帮助信息
- `/list` - 查看在线玩家列表
- `/tps` - 查看服务器TPS和MSPT
- `/info` - 查看服务器综合信息

#### ⚙️ 管理命令（仅管理员可用）
- `/cmd <命令>` - 执行服务器命令
  ```
  示例：/cmd say "欢迎大家！"
  示例：/cmd time set day
  ```
- `/tog_qq` - 切换QQ消息→游戏转发开关
- `/tog_game` - 切换游戏消息→QQ转发开关
- `/reload` - 重新加载配置文件

### 消息示例

#### 🎮 游戏 → QQ群
```
🟢 Steve 加入了服务器
💬 Steve: 大家好！
💀 Steve 被僵尸杀死了
🔴 Steve 离开了服务器
```

#### 💬 QQ群 → 游戏
```
[QQ群] 群友A: 欢迎新玩家！
[QQ群] 群友B: [图片]
[QQ群] 群友C: @全体成员 服务器要重启了
[QQ群] 群友D: [语音]
```

## 🔧 消息类型支持

### 📱 QQ消息解析
插件会自动解析各种QQ消息类型并转换为游戏内可读格式：

- **混合消息**：文本+图片会显示为 `文字内容[图片]`
- **纯非文本**：只有图片等会显示为对应标识符
- **空消息**：无内容时显示 `[空消息]`
- **CQ码兼容**：自动解析 NapCat 的 CQ 码格式

## �️ 故障排除

**无法加载？**
- ~检查 websockets 是否安装 `pip install websockets`~ (已内置）
- 检查Python 3.12+ 和 Endstone 0.9.4+ 版本

**无法连接？**
- 检查 NapCat 是否正常运行
- NapCat安装：https://napneko.github.io/guide/boot/Shell
- 确认 WebSocket地址 和 token 是否填写正确

**消息不同步？**
- 检查群号配置
- 确认同步开关状态

---

**⭐ 觉得有用请给个 Star！**
