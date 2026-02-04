# AstrBot Plugin Portrait (人物形象)

## 简介

**astrbot_plugin_portrait** 是一款 AI 绘图增强 + 文生图服务插件。

检测到用户有"画图"、"拍照"或"查看形象"需求时，自动注入预设的视觉描述符，并通过 **Gitee AI / Gemini AI** 生成图片。

## 核心特性

- 双提供商支持：Gitee AI + Gemini AI，主备自动切换
- WebUI 管理界面：环境场景/摄影模式可视化配置，图片画廊管理
- 人像参考功能：上传参考照片，Gemini 生图时保持角色形象一致
- 无缝集成：仅在检测到绘图意图时介入
- 多轮注入：触发后连续多轮对话都会携带 Visual Context

## 安装

1. AstrBot 管理后台 -> 插件管理 -> 安装插件
2. 输入仓库地址：`https://github.com/i-kirito/astrbot_plugin_portrait`
3. 重启 AstrBot

## 命令

| 命令 | 说明 |
| :--- | :--- |
| `/画图帮助` | 查看画图功能帮助 |
| `/后台管理` | 查看 WebUI 状态 |
| `/后台管理 开` | 启动 WebUI |
| `/后台管理 关` | 关闭 WebUI |

## 版本历史

### v2.8.3
- [Refactor] 清理死代码，删除废弃的 `core/utils.py` 模块
- [Security] 使用 `ipaddress` 模块增强 SSRF 防护，阻断私网/回环/IPv6 地址
- [Security] WebUI 非本地监听时自动生成随机 token
- [Perf] MD5 计算移至线程池避免阻塞事件循环
- [Refactor] 重构 `__init__` 调用 `rebuild_full_prompt()` 避免重复代码

### v2.8.0
- [Fix] 修复 `_next_key()` 索引越界：运行时删除 API Key 后不再导致 IndexError
- [Fix] 修复 `/后台管理 开` 命令在配置禁用时无法动态启用的问题

### v2.7.0
- [Security] 修复 `/selfie-refs/` 未授权访问漏洞，所有图片资源需要 token 认证
- [Security] 改进文件名生成算法，使用 `secrets.token_hex(16)` 防止时间侧信道攻击
- [Perf] 全面修复 WebUI 阻塞 I/O，所有文件操作改用 `asyncio.to_thread`
- [Feat] 新增 `/后台管理` 命令，支持手动启动/关闭 WebUI
- [Fix] 修复 WebUI 图片/缩略图无法显示的问题

### v2.6.0
- [Feat] 人像参考功能：上传参考照片，Gemini 生图时自动传入保持形象一致
- [Feat] WebUI 新增「人像参考」页面

### v2.5.1
- [Feat] Gitee AI 文生图服务（API Key 轮询、多模型支持）
- [Feat] Gemini AI 文生图服务（原生接口优先，OpenAI 兼容回退）
- [Feat] 主备提供商自动切换机制
- [Feat] WebUI 管理界面（Vue 3 + Element Plus）
- [Security] Gemini base_url SSRF 防护

### v1.9.1
- [Feat] 生命周期管理：添加 `terminate()` 方法，插件卸载时自动清理资源
- [Fix] 防止重载时旧实例复活

### v1.8.9
- [Security] 新增会话过期清理机制，防止内存泄漏

### v1.8.0
- [Feat] 多轮注入支持：新增 `injection_rounds` 配置项

### v1.6.0
- [Refactor] One-Shot 单次注入架构

---
*by ikirito*
