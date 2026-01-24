# AstrBot Plugin Portrait (摄影师模式)

<div align="center">

<img src="https://raw.githubusercontent.com/Soulter/AstrBot/main/assets/logo_text.svg" width="200" alt="logo" />

</div>

## 📸 简介

**astrbot_plugin_portrait** 是一款基于 System Prompt 动态注入技术的人格插件。它能让您的 AstrBot 瞬间化身为一位专业的摄影师，对画面、光影、构图进行细腻的描述。

**v1.1.0 新特性：**
*   🧠 **自然语言触发**：不再需要死记硬背关键词，只需对 Bot 说“帮我拍张照”、“看看我的穿搭”或“记录这个瞬间”，即可自动识别意图并激活模式。
*   🎨 **Prompt 自动构建**：后台自动根据您的配置拼装高质量的摄影 Prompt，无需手动输入复杂指令。
*   ⚙️ **可视化配置**：支持在 AstrBot 后台直接修改人物外貌、环境逻辑和镜头参数，且提供详细的参考模板。

## 📦 安装

1.  确保您的 AstrBot 版本 >= v3.4.0。
2.  在 AstrBot 管理后台 -> 插件管理 -> 安装插件。
3.  输入仓库地址：`https://github.com/i-kirito/astrbot_plugin_portrait`
4.  安装完成后重启 AstrBot。

## ⚙️ 配置说明

插件提供高度自定义的配置项，留空则使用内置的默认值（18岁粉发少女设定）。

| 配置项 | 说明 |
| :--- | :--- |
| **max_turns** | 摄影师模式激活后持续的对话轮数（默认 3 轮）。 |
| **char_visuals** | 人物视觉核心描述 (Visuals)，如肤色、五官。 |
| **char_identity** | 人物身份卡 (Identity)，如发型、身材、表情。 |
| **env_default** | 默认环境 (Env A)，通常设为卧室或室内自拍场景。 |
| **env_fullbody** | 全身照环境 (Env B)，如更衣室或试衣间。 |
| **env_outdoor** | 户外/通用环境逻辑 (Env C)。 |
| **cam_selfie** | 自拍镜头参数 (Mode A)，如手机对镜、特写。 |
| **cam_fullbody** | 全身镜头参数 (Mode B)，如广角、全身展示。 |

## 🎮 使用方法

无需特定指令，直接用自然语言与 Bot 交互：

*   **拍自拍**：“AstrCam，给我拍张自拍。”
*   **看穿搭**：“我想看看你今天的穿搭。”
*   **记录生活**：“记录一下现在在卧室发呆的样子。”

Bot 会立刻进入摄影师状态，用专业的口吻描述画面。

## 🛠️ 开发日志

### v1.1.0
*   [New] 引入 LLM Tool (`enter_portrait_mode`) 实现自然语言意图识别。
*   [Opt] 优化配置文件结构，拆分为 7 个细粒度项，支持大输入框编辑。
*   [Opt] 精简配置界面的参考提示词显示。
*   [Fix] 修复了长文本配置在前端显示不全的问题。

---
*Generated with ❤️ by AstrBot*
