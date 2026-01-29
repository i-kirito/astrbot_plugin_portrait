# AstrBot Plugin Portrait (视觉上下文注入器)

## 📸 简介

**astrbot_plugin_portrait** 是一款专为 **AI 绘图增强** 设计的 System Prompt 动态注入插件。

它**不改变** AstrBot 原有的人格设定，而是在检测到用户有“画图”、“拍照”或“查看形象”的需求时，**瞬间注入（单次）**一套预设的高质量视觉描述符（Visual Descriptors）。
这确保了 LLM 在调用绘图工具（如 `gitee_draw_image`）时，能够自动携带统一的人物外貌、环境氛围和摄影参数，从而生成风格高度一致的图片。

## ✨ 核心特性

*   🤖 **无缝集成**：平时隐身，仅在检测到绘图意图时毫秒级介入。
*   ⚡️ **多轮注入 (Multi-Shot)**：支持配置注入轮次，触发后连续多轮对话都会携带 Visual Context，确保连续绘图时风格一致。
*   🎯 **精准控制**：将视觉设定拆分为「人物 Visuals」、「身份 Identity」、「环境 Environment」、「镜头 Camera」四大模块。
*   🧠 **智能预判**：内置强力正则，支持“看看在干嘛”、“爆照”、“发张自拍”等自然语言指令，在 LLM 思考调用工具之前抢先注入 Context。
*   ⚙️ **可视化配置**：AstrBot 后台提供精细配置项，支持自定义 Prompt。

## 📦 安装

1.  在 AstrBot 管理后台 -> 插件管理 -> 安装插件。
2.  输入仓库地址：`https://github.com/i-kirito/astrbot_plugin_portrait`
3.  安装完成后重启 AstrBot。

## 🎮 使用场景

无需特定指令，自然语言即可触发：

*   **画图**：“帮我画一张在卧室的照片。”
    *   *Result*: 生成的图片会自动包含配置中的“粉发少女”、“温馨卧室”、“自然光”等元素。
*   **拍照**：“AstrBot，给自己拍张自拍。”
    *   *Result*: 生成一张对镜自拍视角的图片，包含手机遮挡、面部特写等细节。
*   **日常询问**：“看看现在在干嘛”、“爆照”、“给我康康”。
    *   *Result*: 智能识别意图并生成相应的场景照片。

## ⚙️ 配置说明

| 配置项 | 说明 |
| :--- | :--- |
| **char_identity** | 人物身份特征 (Identity)，如发型、身材。 |
| **injection_rounds** | 注入轮次，触发后连续多少轮注入 Visual Context（默认 1）。 |
| **env_default** | 默认环境 Prompt (通常为卧室)。 |
| **env_fullbody** | 全身环境 Prompt (通常为更衣室/试衣间)。 |
| **env_outdoor** | 户外环境逻辑。 |
| **enable_custom_camera** | 是否启用自定义镜头参数 (开关)。 |
| **cam_selfie** | 自拍镜头参数 (需开启开关)。 |
| **cam_fullbody** | 全身镜头参数 (需开启开关)。 |
| **proactive_enabled** | 是否启用定时推送 (开关)。 |
| **proactive_time** | 定时推送时间，支持多个时间逗号分隔 (如 `08:00,22:00`)。 |
| **proactive_target_list** | 定时推送目标群组列表。 |

## 🛠️ 版本历史

### v1.8.6 (2026-1-29)
- [Security] 新增会话过期清理机制，防止内存泄漏
- [Optimize] 全面优化 `_conf_schema.json` 描述和提示

### v1.8.2 (2026-1-28)
- [Refactor] 定时推送使用 `tool_loop_agent` API 调用 LLM 生成图片
- [Optimize] 问候语改为简短自然的问候

### v1.8.0 ~ v1.8.1 (2026-1-27)
- [Feat] 多轮注入支持：新增 `injection_rounds` 配置项
- [Feat] 手动推送指令：新增 `/拍照推送` 指令
- [Feat] 智能时段识别：自动判断早安/午安/晚安
- [Fix] 修复 `vv1.x.x` 双重前缀问题

### v1.7.0 (2026-1-26)
- [Feat] 主动拍照功能：支持定时发送照片和问候语

### v1.6.0 (2026-1-25)
- [Refactor] One-Shot 单次注入架构
- [Optimize] 移除负面提示词注入，简化 Prompt 结构
- [Optimize] 支持丰富触发词：画/拍/照/自拍/穿搭/看看/康康/爆照/ootd 等

### v1.4.0 ~ v1.5.1 (2026-1-24)
- [Feat] 环境场景开关：新增 `enable_custom_env` 开关
- [Feat] 安全守卫 (Safety Guard)：自动注入负面提示词
- [Refactor] Always-On 架构：采用被动注入策略

### v1.3.0 ~ v1.3.1 (2026-1-23)
- [Feat] 新增 `enable_safety_guard` 开关
- [Update] 正则增强：支持"再拍一张"、"再来一个"等追加意图

---
*Generated with ❤️ by AstrBot*
