# QQSync 群服互通插件

一个简易的 Minecraft 服务器与 QQ 群聊双向互通插件，基于 Endstone 插件框架开发。

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.12+-green.svg)
![Endstone](https://img.shields.io/badge/endstone-0.6-orange.svg)

最新构建（未测试）：[Actions](https://github.com/yuexps/endstone-qqsync-plugin/actions "Actions")

## 💡 前置组件：

- **NapCat** （或其他支持OneBot V11 正向WS 协议的QQ框架）
- NapCat：https://napneko.github.io/guide/boot/Shell
- Lagrange：https://lagrangedev.github.io/Lagrange.Doc/v1/Lagrange.OneBot/

## ✨ 核心功能

- 🔄 **双向消息同步**：QQ群聊 ↔ 游戏内聊天
- 🎮 **游戏事件同步**：玩家加入/离开/聊天/死亡消息
- 🛠️ **远程管理**：通过QQ群远程查看玩家、执行命令
- 📱 **智能解析**：支持图片、视频等非文本消息转换显示
- ⚙️ **灵活控制**：支持单向/双向同步切换
- 🔐 **QQ身份验证**：强制QQ绑定，确保玩家身份真实性
- 👮 **访客权限管理**：未绑定QQ的玩家受访客权限限制
- 🚫 **玩家封禁系统**：支持封禁玩家并禁止QQ绑定
- 👥 **群成员监控**：自动检测玩家退群并调整权限
- 🎯 **访客限制保护**：限制访客权限，阻止访客恶意行为

## 🚀 快速开始

### 1. 安装
将插件放到 Endstone 服务器插件目录：
```
bedrock_server/plugins/endstone_qqsync_plugin-0.0.5-py2.py3-none-any.whl
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
  "access_token": "",                     // 访问令牌（可选）
  "target_group": 712523104,             // 目标QQ群号
  "admins": ["2899659758"],              // 管理员QQ号列表
  "enable_qq_to_game": true,             // QQ消息转发到游戏
  "enable_game_to_qq": true,             // 游戏消息转发到QQ
  "force_bind_qq": true,                 // 强制QQ绑定（启用身份验证系统）
  "sync_group_card": true,               // 自动同步群昵称为玩家名
  "check_group_member": true,             // 启用退群检测功能
  //聊天刷屏检测配置
  "chat_count_limit": 20, // 1分钟内最多发送消息数（-1则不限制）
  "chat_ban_time": 300    // 刷屏后禁言时间（秒）
}
```

### 3. 启动
启动Endstone服务器即可自动连接NapCat并开始同步群服消息。

## 🎯 使用说明

### QQ群内命令

#### 🕸️ 查询命令（所有用户可用）
- `/help` - 显示本帮助信息
- `/list` - 查看在线玩家列表
- `/tps` - 查看服务器TPS和MSPT
- `/info` - 查看服务器综合信息
- `/bindqq` - 查看QQ绑定状态
- `/verify <验证码>` - 验证QQ绑定

#### ⚙️ 管理命令（仅管理员可用）
- `/cmd <命令>` - 执行服务器命令
  ```
  示例：/cmd say "欢迎大家！"
  示例：/cmd time set day
  ```
- `/who <玩家名|QQ号>` - 查询玩家详细信息（绑定状态、游戏统计、权限状态等）
- `/unbindqq <玩家名|QQ号>` - 解绑玩家的QQ绑定
- `/ban <玩家名> [原因]` - 封禁玩家，禁止QQ绑定
- `/unban <玩家名>` - 解除玩家封禁
- `/banlist` - 查看封禁列表
- `/tog_qq` - 切换QQ消息→游戏转发开关
- `/tog_game` - 切换游戏消息→QQ转发开关
- `/reload` - 重新加载配置文件

### 游戏内命令

#### 🔐 身份验证命令（所有玩家可用）
- `/bindqq` - 开始QQ绑定流程

### 消息示例

#### 🎮 游戏 → QQ群
```
(过滤敏感内容)
🟢 yuexps 加入了服务器
💬 yuexps: 大家好！
💀 yuexps 被僵尸杀死了
🔴 yuexps 离开了服务器
```

#### 💬 QQ群 → 游戏
```
[QQ群] 群友A: 欢迎新玩家！
[QQ群] 群友B: [图片]
[QQ群] 群友C: @全体成员 服务器要重启了
[QQ群] 群友D: [语音]
```

## 🔐 QQ身份验证系统

### 强制绑定模式
当启用 `force_bind_qq` 时，插件会要求所有玩家绑定QQ账号：

#### 🚪 玩家首次进入
1. 未绑定QQ的玩家进入服务器时会自动弹出绑定表单
2. 玩家输入QQ号后，系统发送验证码到该QQ
3. 玩家在游戏内或QQ群输入验证码完成绑定
4. 绑定成功后获得完整游戏权限

#### 👤 访客权限限制
未绑定QQ的玩家将受到访客权限限制：
- ❌ **无法聊天** - 聊天消息被阻止
- ❌ **无法破坏方块** - 破坏操作被取消
- ❌ **无法放置方块** - 建造操作被阻止  
- ❌ **无法拾取/丢弃** - 拾取/丢弃被拦截
- ❌ **无法与方块交互** - 容器、机械等交互被限制
- ❌ **无法攻击实体** - 攻击玩家、生物、载具等被阻止
- ✅ **可以移动和观察** - 基本游戏体验保留

#### 🔄 绑定流程示例
```
📱 游戏内操作：
玩家进入 → 弹出绑定表单 → 输入QQ号 → 等待验证码

💬 QQ端接收：
收到@消息："验证码: 123456"

✅ 验证完成：
游戏内表单输入验证码 或 QQ群输入 "/verify 123456"
```

## 🚫 玩家管理系统

### 封禁功能
- `/ban <玩家名> [原因]` - 封禁玩家，自动解除QQ绑定并禁止绑定
- `/unban <玩家名>` - 解除封禁
- `/banlist` - 查看所有被封禁的玩家

### 退群检测
- 自动监控群成员变化
- 已绑定玩家退群后自动降级为访客权限
- 重新加群后权限自动恢复

### 玩家信息查询
```bash
# QQ群内查询玩家详细信息
/who yuexps        # 按玩家名查询
/who 2899659758    # 按QQ号查询

# 返回信息包括：
- QQ绑定状态和绑定时间
- 游戏统计（在线时长、登录次数）
- 权限状态（正常用户/访客/被封禁）
- 群成员状态
```

## ⚙️ 配置说明

### 身份验证相关配置
- `force_bind_qq`: 是否强制要求QQ绑定（默认：true）
- `sync_group_card`: 是否自动设置群昵称为玩家名（默认：true）
- `check_group_member`: 是否启用退群检测（默认：true）

### 权限系统
当 `force_bind_qq` 为 false 时：
- 所有玩家享有完整权限，无需绑定QQ
- QQ绑定功能可选使用
- 访客权限系统不生效

## 🔧 消息类型支持

### 📱 QQ消息解析
插件会自动解析各种QQ消息类型并转换为游戏内可读格式：

- **混合消息**：文本+图片会显示为 `文字内容[图片]`
- **纯非文本**：只有图片等会显示为对应标识符
- **空消息**：无内容时显示 `[空消息]`
- **CQ码兼容**：自动解析 NapCat 的 CQ 码格式

### 🔐 QQ绑定问题
**验证码收不到？**
- 确认QQ号输入正确
- 检查QQ隐私设置，允许陌生人私聊
- 确认机器人QQ账号正常在线

**绑定失败？**
- 检查该QQ是否已被其他玩家绑定
- 确认QQ号在目标群内（启用退群检测时）
- 验证码5分钟内有效，请及时输入

**权限问题？**
- 访客权限是正常保护机制
- 完成QQ绑定后权限自动恢复
- 管理员可使用 `/who` 查询玩家状态

### 🚫 封禁和权限问题
**玩家被误封？**
- 管理员使用 `/unban <玩家名>` 解封
- 检查封禁列表：`/banlist`

**退群检测异常？**
- 网络问题可能导致误判，重启插件即可
- 可在配置中关闭 `check_group_member`

**访客权限过严？**
- 这是安全保护机制，确保服务器安全
- 可在配置中关闭 `force_bind_qq` 禁用强制绑定)

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

这是一个在 AI 的帮助下编写的 Endstone 插件，用于实现简单的群服互通！

**⭐ 觉得有用请给个 Star！**
