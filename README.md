# AstrBot Plugin Portrait (视觉上下文注入器)

## 📸 简介

**astrbot_plugin_portrait** 是一款专为 **AI 绘图增强** 设计的 System Prompt 动态注入插件。

它**不改变** AstrBot 原有的人格设定，而是在检测到用户有“画图”、“拍照”或“查看形象”的需求时，**瞬间注入（单次）**一套预设的高质量视觉描述符（Visual Descriptors）。
这确保了 LLM 在调用绘图工具（如 `gitee_draw_image`）时，能够自动携带统一的人物外貌、环境氛围和摄影参数，从而生成风格高度一致的图片。

## ✨ 核心特性

*   🤖 **无缝集成**：平时隐身，仅在检测到绘图意图时毫秒级介入。
*   ⚡️ **单次注入 (One-Shot)**：Prompt 仅在当前请求中生效，生成完毕后立即销毁，绝不污染后续对话上下文，极大节省 Token。
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
| **env_default** | 默认环境 Prompt (通常为卧室)。 |
| **env_fullbody** | 全身环境 Prompt (通常为更衣室/试衣间)。 |
| **env_outdoor** | 户外环境逻辑。 |
| **enable_custom_camera** | 是否启用自定义镜头参数 (开关)。 |
| **cam_selfie** | 自拍镜头参数 (需开启开关)。 |
| **cam_fullbody** | 全身镜头参数 (需开启开关)。 |
| **proactive_target_id** | 主动拍照推送目标ID (格式: platform:id)。 |
| **proactive_morning** | 启用早安拍照 (开关)。 |
| **proactive_morning_time** | 早安拍照时间 (默认 08:00)。 |
| **proactive_noon** | 启用午间拍照 (开关)。 |
| **proactive_noon_time** | 午间拍照时间 (默认 12:00)。 |
| **proactive_evening** | 启用晚安拍照 (开关)。 |
| **proactive_evening_time** | 晚安拍照时间 (默认 22:00)。 |

## 🛠️ 版本日志

### v1.7.0
*   [Feat] **主动拍照功能**：
    *   **定时问候**：支持早安/午间/晚安三个时段主动发送照片和问候语。
    *   **随机问候语**：每个时段内置多条问候语，随机选择增加互动感。
    *   **镜头模式**：早/晚自拍模式，午间半身照模式。
    *   **依赖说明**：需安装 `apscheduler` (`pip install apscheduler`)。

### v1.6.0
*   [Refactor] **One-Shot 单次注入架构**：
    *   **恢复正则匹配**：重新引入关键词正则检测机制，仅在检测到绘图意图时注入 Visual Context。
    *   **节省 Token**：日常对话不再携带视觉设定，显著减少 Token 消耗。
    *   **移除 Safety Guard**：删除负面提示词注入功能，简化 Prompt 结构，提高 LLM 理解准确率。
    *   **支持丰富触发词**：画/拍/照/自拍/全身/穿搭/看看/康康/爆照/形象/样子/draw/photo/selfie/picture/ootd 等。

### v1.5.1
*   [Fix] **修复变量定义**：修复了 v1.5.0 中遗漏定义 `DEF_NEGATIVE` 和 `TPL_NEGATIVE` 导致插件加载失败的问题。

### v1.5.0
*   [Feat] **环境场景开关**：新增 `enable_custom_env` 开关（默认开启）。允许用户选择是否注入环境场景逻辑。
*   [Refactor] **功能调整**：重新启用了“安全守卫 (Safety Guard)”功能，并修复了其与 Prompt 组装的集成。
*   [Optimize] **Prompt组装**：优化了 Prompt 拼接逻辑，现在会自动处理关闭功能时产生的空行，生成的指令更加紧凑。

### v1.4.2
*   [Fix] **安全守卫修复**：修复了 v1.4.1 版本中因 Prompt 组装逻辑错误导致 Safety Guard (负面提示词) 未被正确注入的问题。

### v1.4.1
*   [Fix] **意图识别增强**：在 System Prompt 的场景逻辑中明确了“看看穿搭”等关键词的触发意图，解决了部分模型将“看看穿搭”误判为纯文本描述请求的问题。

### v1.4.0
*   [Refactor] **架构升级 (Always-On)**：
    *   **移除正则**：彻底移除了关键词正则匹配机制，不再依赖特定的触发词。
    *   **被动注入**：采用 "Always-On Passive Injection" 策略，将视觉设定作为常驻 System Context 注入。
    *   **自然语言支持**：现在完全依赖 LLM (如 GPT-4, Claude 3.5) 的语义理解能力来决定何时调用绘图工具，实现了真正的自然语言意图识别（如“看看现在的你”、“记录这个瞬间”等均可自然触发）。

### v1.3.1
*   [Update] **正则增强**：支持了“再拍一张”、“再来一个”等表示“再次/追加”意图的自然语言指令。

### v1.3.0
*   [Feat] **安全与质量守卫 (Safety Guard)**：新增了 `enable_safety_guard` 开关。
    *   **功能**：开启后（默认），会自动向 System Prompt 注入一段 `Negative Prompt` 指令。
    *   **效果**：强制要求 LLM 在生成参数中携带防崩坏（bad anatomy, extra limbs）和防违规（nsfw）的负面提示词，显著减少多肢体、画面崩坏等问题。
    *   **配置**：支持在后台自定义 `negative_content`。

---
*Generated with ❤️ by AstrBot*
