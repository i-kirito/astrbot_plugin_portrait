# AstrBot Plugin Portrait (视觉上下文注入器)

<div align="center">

<img src="https://raw.githubusercontent.com/Soulter/AstrBot/main/assets/logo_text.svg" width="200" alt="logo" />

</div>

## 📸 简介

**astrbot_plugin_portrait** (v2.1.0+) 是一款专为 **AI 绘图增强** 设计的 System Prompt 动态注入插件。

它**不改变** AstrBot 原有的人格设定，而是在检测到用户有“画图”、“拍照”或“查看形象”的需求时，**瞬间注入（单次）**一套预设的高质量视觉描述符（Visual Descriptors）。这确保了 LLM 在调用绘图工具（如 `gitee_draw_image`）时，能够自动携带统一的人物外貌、环境氛围和摄影参数，从而生成风格高度一致的图片。

## ✨ 核心特性

*   🤖 **无缝集成**：平时隐身，仅在检测到绘图意图时毫秒级介入。
*   ⚡️ **单次注入 (One-Shot)**：Prompt 仅在当前请求中生效，生成完毕后立即销毁，绝不污染后续对话上下文，极大节省 Token。
*   🎯 **精准控制**：将视觉设定拆分为「人物 Visuals」、「身份 Identity」、「环境 Environment」、「镜头 Camera」四大模块。
*   🧠 **智能预判**：内置强力正则，在 LLM 思考调用工具之前抢先注入 Context，确保 Prompt 有效传递。
*   ⚙️ **可视化配置**：AstrBot 后台提供 7 个精细配置项，支持大输入框编辑和完整参考模板。

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

## ⚙️ 配置说明

插件默认内置了一套 **18岁粉发少女** 的视觉设定。您可以在后台随意修改为自己的设定。

| 配置项 | 说明 |
| :--- | :--- |
| **char_visuals** | 人物视觉核心 (Visuals)，如肤色、五官。 |
| **char_identity** | 人物身份卡 (Identity)，如发型、身材。 |
| **env_default** | 默认环境 Prompt (通常为卧室)。 |
| **env_fullbody** | 全身环境 Prompt (通常为更衣室/试衣间)。 |
| **env_outdoor** | 户外环境逻辑。 |
| **cam_selfie** | 自拍镜头参数 (手机对镜、POV)。 |
| **cam_fullbody** | 全身镜头参数 (广角、全身展示)。 |

## 🛠️ 开发日志

### v2.1.2
*   [Fix] **Context污染修复**：修复了注入的 User Message 后缀永久保留在上下文中的问题。现在使用深拷贝 (deepcopy) 确保只影响当前请求。

### v2.1.0
*   [Refactor] **单次注入**：移除了轮数保持逻辑，改为单次触发即销毁，更轻量、更纯净。
*   [Opt] **配置精简**：移除了 `max_turns` 配置项。

### v2.0.0
*   [Refactor] **去人格化**：移除“摄影师”扮演逻辑，转变为纯粹的 Prompt 参数注入器。
*   [Feat] **强力注入**：引入正则预判 + User Message 尾随指令双重机制。

---
*Generated with ❤️ by AstrBot*
