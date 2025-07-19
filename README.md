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
  "napcat_ws": "ws://localhost:3001", //正向WS (NapCat WebSocket服务器 地址)
  "access_token": "",
  "target_group": 712523104, //监控群聊
  "admins": ["2899659758"] //管理员QQ
}
```

### 3. 启动
启动Endstone服务器即可自动连接NapCat并开始同步群服消息。

## 🎯 使用说明

### QQ群内命令
- `/help` - 显示帮助
- `/list` - 查看在线玩家
- `/cmd <命令>` - 执行服务器命令（管理员）
- `/tog_qq` - 切换QQ→游戏转发（管理员）
- `/tog_game` - 切换游戏→QQ转发（管理员）
- `/reload` - 重新加载配置（管理员）

### 消息示例
**游戏 → QQ群：**
```
🟢 Steve 加入了服务器
💬 Steve: 大家好！
🔴 Steve 离开了服务器
```

**QQ群 → 游戏：**
```
[QQ群] 张三: 欢迎新玩家！
[QQ群] 李四: [图片]
```

## 🔧 消息类型支持

| QQ消息 | 游戏显示 | QQ消息 | 游戏显示 |
|--------|----------|--------|----------|
| 图片 | `[图片]` | 视频 | `[视频]` |
| 语音 | `[语音]` | 文件 | `[文件]` |
| @某人 | `@QQ号` | 表情 | `[表情]` |

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
