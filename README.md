# AstrBot Plugin Portrait (视觉上下文注入器)

<div align="center">

<img src="https://raw.githubusercontent.com/Soulter/AstrBot/main/assets/logo_text.svg" width="200" alt="logo" />

</div>

## 📸 简介

**astrbot_plugin_portrait** 是一款专为 **AI 绘图增强** 设计的 System Prompt 动态注入插件。

它**不改变** AstrBot 原有的人格设定，而是在检测到用户有“画图”、“拍照”或“查看形象”的需求时，**瞬间注入（单次）**一套预设的高质量视觉描述符（Visual Descriptors）。这确保了 LLM 在调用绘图工具（如 `gitee_draw_image`）时，能够自动携带统一的人物外貌、环境氛围和摄影参数，从而生成风格高度一致的图片。

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
*   **OOTD**：“看看今天的穿搭。”
    *   *Result*: 生成一张全身照，展示全身服饰搭配。
*   **日常询问**：“看看现在在干嘛”、“爆照”、“给我康康”。
    *   *Result*: 智能识别意图并生成相应的场景照片。

## ⚙️ 配置说明

插件默认内置了一套 **18岁粉发少女** 的视觉设定。您可以在后台随意修改为自己的设定。

| 配置项 | 说明 |
| :--- | :--- |
| **char_identity** | 人物身份特征 (Identity)，如发型、身材。 |
| **env_default** | 默认环境 Prompt (通常为卧室)。 |
| **env_fullbody** | 全身环境 Prompt (通常为更衣室/试衣间)。 |
| **env_outdoor** | 户外环境逻辑。 |
| **cam_selfie** | 自拍镜头参数 (手机对镜、POV)。 |
| **cam_fullbody** | 全身镜头参数 (广角、全身展示)。 |

## 🛠️ 版本日志

### v1.1.4
*   [Refactor] **输出格式定制**：根据用户需求调整了 System Prompt 的最终输出结构。
    *   恢复了默认的 "18-year-old Asian girl" 人物设定作为兜底。
    *   调整了段落编号：Section 2 为动态内容处理，Section 4 为环境逻辑。
    *   精简了结尾，移除了额外的交互准则，确保 Prompt 更加紧凑。

### v1.1.3
*   [Refactor] **Prompt结构重构**：完全按照用户指定的 v3.7 格式重写了 System Prompt 模板，包含精准的 `Logic Branching` 和 `Format Switching` 指令，移除冗余的尾部指令，确保注入内容与预期完全一致。

### v1.1.2
*   [Improvement] **Prompt合并指令**：在 System Prompt 尾部新增了明确的 "Final Output Instruction"，强制要求 LLM 将所有视觉设定合并为完整的 Prompt 传递给画图工具，提高工具调用的精准度。

### v1.1.1
*   [Update] **配置分离**：移除了代码中硬编码的默认人物形象（18岁粉发少女），现在人物设定完全由后台配置决定，更加纯净灵活。

### v1.1.0 (Stable)
*   **正式发布**：集成了自然语言正则触发、单次 Prompt 注入、环境与镜头逻辑自动切换等核心功能。
*   **智能识别**：支持识别“在干嘛”、“爆照”等口语化指令。
*   **配置简化**：移除了冗余配置，仅保留核心 Prompt 修改项。

---
*Generated with ❤️ by AstrBot*
