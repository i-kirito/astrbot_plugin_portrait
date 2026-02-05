# AstrBot Plugin Portrait (人物形象)

## 📸 简介

**astrbot_plugin_portrait** 是一款 **AI 绘图增强 + 文生图服务** 插件。

它在检测到用户有"画图"、"拍照"或"查看形象"的需求时，**自动注入**一套预设的高质量视觉描述符（Visual Descriptors），并通过内置的 **Gitee AI / Gemini AI** 文生图服务生成图片。

## ✨ 核心特性

- 🎨 **双提供商支持**：Gitee AI + Gemini AI，主备自动切换
- 🖼️ **WebUI 管理界面**：环境场景/摄影模式可视化配置，图片画廊管理
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

### v2.9.1 (2026-02-05)
- [Fix] 修复非角色请求仍然注入外貌描述的问题
- [Enhance] 扩展排除关键词列表：新增车辆、更多机甲角色等关键词
- [Improve] 统一日志提示：明确显示"跳过参考图和外貌注入"

### v2.9.0 (2026-02-05)
- [Fix] 修复无限循环生图问题：检测工具调用响应并跳过注入
- [Fix] 修复注入计数器重复重置问题：仅在计数耗尽时才初始化
- [Feat] 智能参考图加载：仅角色相关请求使用参考图，避免生成奇怪图片
- [Enhance] 强化模板指令：明确单次调用策略防止重复生图

### v2.8.3 (2026-02-04)
- [Security] 使用 `ipaddress` 模块增强 SSRF 防护，阻断私网/回环/IPv6 地址
- [Security] WebUI 非本地监听时自动生成随机 token
- [Perf] MD5 计算移至线程池避免阻塞事件循环

### v2.8.0 (2026-02-04)
- [Fix] 修复 `_next_key()` 索引越界：运行时删除 API Key 后不再导致 IndexError
- [Fix] 修复 `/后台管理 开` 命令在配置禁用时无法动态启用的问题

### v2.7.0 (2026-02-04)
- [Security] 修复 `/selfie-refs/` 未授权访问漏洞
- [Perf] 全面修复 WebUI 阻塞 I/O 问题
- [Feat] 新增 `/后台管理` 命令

### v2.6.0 (2026-02-03)
- [Feat] 人像参考功能：Gemini 生图时保持角色形象一致
- [Feat] WebUI 新增「人像参考」页面

### v2.5.1 (2026-02-03)
- [Feat] Gitee AI / Gemini AI 双提供商文生图服务
- [Feat] 主备提供商自动切换机制
- [Feat] WebUI 管理界面（Vue 3 + Element Plus）

### v1.9.1 (2026-02-01)
- [Feat] 生命周期管理：`terminate()` 方法自动清理资源

### v1.8.0 (2026-01-27)
- [Feat] 多轮注入支持：`injection_rounds` 配置项

### v1.6.0 (2026-01-25)
- [Feat] One-Shot 单次注入架构

---
*Generated with ❤️ by ikirito*
