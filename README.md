# AstrBot Plugin Portrait (人物形象)

## 📸 简介

**astrbot_plugin_portrait** 是一款 **AI 绘图增强 + 文生图服务** 插件。

它在检测到用户有"画图"、"拍照"或"查看形象"的需求时，**自动注入**一套预设的高质量视觉描述符（Visual Descriptors），并通过内置的 **Gitee AI / Gemini AI** 文生图服务生成图片。

## ✨ 核心特性

- 🎨 **三提供商支持**：Gitee AI + Gemini AI + Grok AI，主备自动切换
- 🎬 **视频生成**：Grok AI 图生视频，支持动作预设
- 🖼️ **WebUI 管理界面**：环境场景/摄影模式可视化配置，图片/视频画廊管理
- 📷 **人像参考功能**：上传参考照片，Gemini 生图时自动保持角色形象一致
- 🤖 **无缝集成**：平时隐身，仅在检测到绘图意图时介入
- ⚡️ **多轮注入**：触发后连续多轮对话都会携带 Visual Context
- 🎯 **精准控制**：「人物」「环境」「镜头」三大模块独立配置

## 🖼️ WebUI 界面预览

### 配置设置
![配置设置](https://raw.githubusercontent.com/i-kirito/astrbot_plugin_portrait/main/assets/webui_settings.png)

### 人像参考
![人像参考](https://raw.githubusercontent.com/i-kirito/astrbot_plugin_portrait/main/assets/webui_selfie.png)

### 图片画廊
![图片画廊](https://raw.githubusercontent.com/i-kirito/astrbot_plugin_portrait/main/assets/webui_gallery.png)

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
| `/视频 <提示词>` | 图生视频（需附带图片） |
| `/视频预设列表` | 查看可用的视频预设 |
| `/后台管理` | 查看 WebUI 状态 |
| `/后台管理 开` | 手动启动 WebUI |
| `/后台管理 关` | 手动关闭 WebUI |

## ⚙️ 配置说明

### 基础配置

| 配置项 | 说明 |
| :--- | :--- |
| `char_identity` | 人物身份特征描述 |
| `injection_rounds` | 注入轮次（默认 1） |
| `enable_env_injection` | 启用环境场景注入 |
| `enable_camera_injection` | 启用摄影模式注入 |
| `draw_provider` | 主图片生成提供商（gitee/gemini/grok） |
| `enable_fallback` | 启用备用提供商自动切换 |
| `fallback_models` | 备用模型顺序（默认 gemini, grok） |
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

### Grok AI 配置 (grok_config)

| 配置项 | 说明 |
| :--- | :--- |
| `api_key` | API 密钥 |
| `base_url` | API 地址（默认 https://api.x.ai） |
| `image_model` | 图片模型（默认 grok-imagine-1.0） |
| `video_model` | 视频模型（默认 grok-imagine-1.0-video） |
| `video_enabled` | 启用视频生成功能 |
| `video_presets` | 视频预设词（格式：预设名:提示词） |
| `video_send_mode` | 视频发送模式（auto/url/file） |
| `max_cached_videos` | 最大缓存视频数量 |

### 人像参考配置 (selfie_config)

| 配置项 | 说明 |
| :--- | :--- |
| `enabled` | 启用人像参考功能 |

> 人像参考图片请在 WebUI 中上传管理，存放于 `data/selfie_refs/` 目录

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

### v3.3.0 (2026-02-08)
- 独立改图提供商，动态模型切换
- 前后端性能优化，网络连接池优化
- API Key 掩码化，Token 时序安全

### v3.2.0 (2026-02-07)
- 视频预设词管理页面，画廊列数可调
- 备用模型顺序可配置
- 后端 O(n) 去重优化

### v3.1.0 (2026-02-06)
- 视频画廊页面，缓存清理功能
- WebUI 配置完整同步

### v3.0.0 (2026-02-05)
- Grok AI 图生视频，视频预设系统

### v2.9.x (2026-02-05)
- 智能参考图加载，修复无限循环生图

### v2.8.x (2026-02-04)
- SSRF 防护增强，WebUI 安全加固

### v2.7.0 (2026-02-04)
- `/后台管理` 命令，修复未授权访问

### v2.6.0 (2026-02-03)
- 人像参考功能，WebUI 人像管理页

### v2.5.1 (2026-02-03)
- Gitee/Gemini 双提供商，WebUI 管理界面

### v1.x (2026-01)
- 多轮注入、One-Shot 架构、生命周期管理

---
*Generated with ❤️ by ikirito*
