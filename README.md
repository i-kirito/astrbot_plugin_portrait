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
- [Feat] 独立改图提供商：改图功能可独立选择 Gemini/Gitee/Grok，不再绑定生图提供商
- [Feat] 动态改图模型：根据选择的提供商显示对应模型配置
- [Perf] 前端性能优化：Vue/ElementPlus 脚本 defer 加载，图片卡片 GPU 加速
- [Perf] 后端性能优化：元数据批量读写、缩略图并发生成、缓存清理单次落盘
- [Perf] 网络稳定性：Grok/Gemini 连接池优化，重试添加指数退避
- [Perf] 下载链路优化：添加代理支持、大小保护、指数退避重试
- [Security] API Key 掩码化：WebUI 不再返回明文密钥
- [Security] Token 比较使用 secrets.compare_digest 防时序攻击
- [Fix] Grok 模型默认值更新为 grok-imagine-1.0
- [Fix] 移除废弃的 grok_config.model 兼容字段

### v3.2.0 (2026-02-07)
- [Perf] 后端性能优化：O(n²)→O(n) 图片去重、异步文件 I/O、后台清理任务去重
- [Feat] 视频预设词页面：WebUI 新增独立导航，支持添加/删除/编辑预设
- [Feat] 视频画廊列数滑块：支持拖动调整每行显示数量（2-8个）
- [Feat] 备用模型选择器：支持自定义第一/第二备用模型顺序
- [Feat] 新增触发关键词：查岗、在干嘛、在干什么、干嘛呢
- [UI] CSS 变量统一主题色、增强卡片悬浮效果和玻璃拟态
- [Fix] v-for key 优化：使用唯一 ID 替代 index，修复拖拽状态丢失
- [Fix] SortableJS 初始化代码重构，减少冗余
- [Refactor] 移除废弃配置项 vision_model、DEF_CHAR_IDENTITY
- [Refactor] 人格外貌提示词默认为空，不填写则使用 AstrBot 默认人格

### v3.1.0 (2026-02-06)
- [Feat] 视频画廊：独立导航页面，支持在线播放
- [Feat] WebUI 配置完整同步：新增 Grok AI 配置、缓存清理配置
- [Feat] 缓存清理功能：支持按空间/数量限制清理，收藏保护
- [Feat] 浏览器刷新保持当前导航页面
- [Feat] 切换导航自动刷新对应页面数据
- [Fix] 修复收藏数量只显示当前页的问题，改为显示总收藏数
- [UI] 优化缓存清理卡片布局和说明

### v3.0.0 (2026-02-05)
- [Feat] Grok AI 视频生成：支持图生视频功能
- [Feat] 视频预设系统：可以设置动作预设
- [Feat] /视频 命令：参考图生视频

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
