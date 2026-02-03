# AstrBot Plugin Portrait (人物形象)

## 📸 简介

**astrbot_plugin_portrait** 是一款 **AI 绘图增强 + 文生图服务** 插件。

它在检测到用户有"画图"、"拍照"或"查看形象"的需求时，**自动注入**一套预设的高质量视觉描述符（Visual Descriptors），并通过内置的 **Gitee AI / Gemini AI** 文生图服务生成图片。

## ✨ 核心特性

- 🎨 **双提供商支持**：Gitee AI + Gemini AI，主备自动切换
- 🖼️ **WebUI 管理界面**：环境场景/摄影模式可视化配置，图片画廊管理
- 🤖 **无缝集成**：平时隐身，仅在检测到绘图意图时介入
- ⚡️ **多轮注入**：触发后连续多轮对话都会携带 Visual Context
- 🎯 **精准控制**：「人物」「环境」「镜头」三大模块独立配置
- 🔒 **安全加固**：API Key 保护、SSRF 防护、Token 认证

## 📦 安装

1. 在 AstrBot 管理后台 -> 插件管理 -> 安装插件
2. 输入仓库地址：`https://github.com/i-kirito/astrbot_plugin_portrait`
3. 安装完成后重启 AstrBot

## 🎮 使用方式

### 自然语言触发

无需特定指令，自然语言即可触发：

- "帮我画一张在卧室的照片"
- "拍张自拍给我看看"
- "看看现在在干嘛"、"爆照"、"给我康康"

### 命令

| 命令 | 说明 |
| :--- | :--- |
| `/画图帮助` | 查看画图功能帮助 |

## ⚙️ 配置说明

### 基础配置

| 配置项 | 说明 |
| :--- | :--- |
| `char_identity` | 人物身份特征描述 |
| `injection_rounds` | 注入轮次（默认 1） |
| `enable_env_injection` | 启用环境场景注入 |
| `enable_camera_injection` | 启用摄影模式注入 |
| `draw_provider` | 主图片生成提供商（gitee/gemini） |
| `enable_fallback` | 启用备用提供商自动切换 |
| `proxy` | HTTP 代理地址 |

### Gitee AI 配置 (gitee_config)

| 配置项 | 说明 |
| :--- | :--- |
| `api_keys` | API 密钥池（支持多个轮询） |
| `base_url` | API 地址 |
| `model` | 模型名称（z-image-turbo 等） |
| `size` | 输出尺寸（1024x1024 等） |
| `num_inference_steps` | 推理步数 |
| `negative_prompt` | 负面提示词 |

### Gemini AI 配置 (gemini_config)

| 配置项 | 说明 |
| :--- | :--- |
| `api_key` | API 密钥 |
| `base_url` | API 地址 |
| `model` | 模型名称 |
| `image_size` | 分辨率（1K/2K/4K） |

### WebUI 配置 (webui_config)

| 配置项 | 说明 |
| :--- | :--- |
| `enabled` | 启用 WebUI |
| `host` | 监听地址（默认 127.0.0.1） |
| `port` | 端口（默认 8088） |
| `token` | 访问令牌（建议设置） |

### 缓存配置 (cache_config)

| 配置项 | 说明 |
| :--- | :--- |
| `max_storage_mb` | 最大存储空间 MB（0 不限制） |
| `max_count` | 最大图片数量（0 不限制） |

## 🛠️ 版本历史

### v2.5.0 (2026-02-03)
- [Feat] 新增 Gitee AI 文生图服务（API Key 轮询、多模型支持）
- [Feat] 新增 Gemini AI 文生图服务（原生接口优先，OpenAI 兼容回退）
- [Feat] 主备提供商自动切换机制
- [Feat] WebUI 管理界面（Vue 3 + Element Plus）
  - 环境场景/摄影模式动态配置
  - 图片画廊（收藏、删除、缩略图）
  - Token 认证保护
- [Feat] `/画图帮助` 命令
- [Security] Gemini base_url SSRF 防护
- [Security] API Key 占位符保护

### v1.9.1 (2026-02-01)
- [Feat] 生命周期管理：添加 `_is_terminated` 标志防止重载时旧实例复活
- [Feat] 后台任务追踪：添加 `_bg_tasks` 集合用于清理
- [Feat] `terminate()` 方法：插件卸载时自动清理资源

### v1.8.9 (2026-01-30)
- [Refactor] 移除主动拍照定时推送功能
- [Security] 新增会话过期清理机制，防止内存泄漏

### v1.8.0 (2026-01-27)
- [Feat] 多轮注入支持：新增 `injection_rounds` 配置项

### v1.6.0 (2026-01-25)
- [Refactor] One-Shot 单次注入架构
- [Optimize] 支持丰富触发词

---
*Generated with ❤️ by AstrBot*
