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
| **char_identity** | 人物身份特征 (Identity)，如发型、身材。 |
| **env_default** | 默认环境 Prompt (通常为卧室)。 |
| **env_fullbody** | 全身环境 Prompt (通常为更衣室/试衣间)。 |
| **env_outdoor** | 户外环境逻辑。 |
| **cam_selfie** | 自拍镜头参数 (手机对镜、POV)。 |
| **cam_fullbody** | 全身镜头参数 (广角、全身展示)。 |

## 🛠️ 开发日志

### v2.5.1
*   [Fix] **代码规范**：修复了 `main.py` 中 Prompt 模版结构与定义变量的不对应问题。将 "User Variable" 部分提取为 `DEF_USER_VAR` 常量，保持了代码逻辑的一致性。

### v2.5.0
*   [Feature] **Prompt系统重构 v4.0 (Strict Mode)**：
    *   引入 **[4步组装法]**：强制使用 `Final_Prompt = [Character] + [User_Var] + [Scenario] + [Camera]` 的公式拼接，杜绝 LLM 自行发挥产生幻觉。
    *   **Logic Branching**：明确了场景（卧室/更衣室/自定义）和镜头（自拍/全身/默认）的 `IF/ELSE` 触发逻辑。
    *   **Token优化**：精简了所有冗余的 Markdown 标题和说明，只保留核心 Prompt 标签。
*   [Update] **视觉标签升级**：更新了默认的人物权重标签 (e.g., `(18 year old Asian girl:1.5)`) 和光影描述。

### v2.4.1
*   [Fix] **自然语言覆盖**：大幅扩充了触发正则库，现在支持“看看你长啥样”、“看看在干嘛”、“爆照”、“给我康康”等更多口语化、间接化的拍照请求。

### v2.4.0
*   [Refactor] **配置项清理**：移除了不再使用的 `keywords` 配置项。现在插件完全依赖自然语言正则匹配来触发，无需手动维护关键词列表。

### v2.3.4
*   [Fix] **触发词增强**：修复了“发一张照片”、“给我一张照片”等常用语无法触发插件的问题。新增了“发”、“给”等动词的正则匹配。

### v2.3.3
*   [Update] **交互准则优化**：更新了 Interaction Guidelines，强调“视觉锚定”与“动态穿搭”的平衡，鼓励 LLM 根据语境灵活创作既适合对话又适合独立分享的高质量摄影作品。

### v2.3.2
*   [Update] **环境逻辑优化**：
    *   明确了 **Scenario B (Outfit Check)** 会强制调用更衣室场景。
    *   增强了 **Scenario C (Dynamic)** 的逻辑，使其能够根据对话中的行程安排（如“去海边”、“在公园”）自动生成相应的户外环境 Prompt。
    *   **Scenario A (Default)** 保持为温馨卧室设定。

### v2.3.1
*   [Refactor] **逻辑与内容分离**：重构了代码结构，将 Prompt 的逻辑框架 (Template) 硬编码在插件中，配置文件仅需填写具体的描述内容 (Content)，避免用户配置时不小心破坏指令结构。
*   [UI] **配置项精简**：配置界面现在更加清爽，只暴露必要的文本输入框。

### v2.3.0
*   [Feature] **核心Prompt升级 v3.7**：
    *   引入 **[Optimized Core System Instructions]**，大幅增强画图指令的逻辑性和层级。
    *   明确了 Visuals -> Identity -> Environment -> Camera 的构建顺序。
    *   新增 **Mode Reset (模式不继承)** 机制，强制每次请求重置摄影模式，防止“自拍”设定意外残留。
    *   优化了光影和材质的默认描述符 (Photorealistic, Volumetric Lighting)。

### v2.2.2
*   [Fix] **Prompt修正**：回滚了尾部提示词，移除了“无需主动提及”的负面提示，解决了导致画图工具无法识别 Context 的问题。

### v2.2.1
*   [Update] **Prompt微调**：优化了 System Injection 的头部指令和尾部提示，更改注入标签为 `<portrait_status>`，进一步降低对日常对话的干扰。

### v2.2.0\n*   [Refactor] **Prompt结构简化**：移除了 `char_visuals` 配置项，将 Prompt 简化为纯文本拼接，去除冗余的标题和 Markdown 符号，降低 Token 消耗并提高 LLM 理解效率。\n*   [UI] **配置优化**：更新了后台配置界面的提示文案，使其更加直观。\n\n### v2.1.2
*   [Fix] **Context污染修复**：修复了注入的 User Message 后缀永久保留在上下文中的问题。现在使用深拷贝 (deepcopy) 确保只影响当前请求。

### v2.1.0
*   [Refactor] **单次注入**：移除了轮数保持逻辑，改为单次触发即销毁，更轻量、更纯净。
*   [Opt] **配置精简**：移除了 `max_turns` 配置项。

### v2.0.0
*   [Refactor] **去人格化**：移除“摄影师”扮演逻辑，转变为纯粹的 Prompt 参数注入器。
*   [Feat] **强力注入**：引入正则预判 + User Message 尾随指令双重机制。

---
*Generated with ❤️ by AstrBot*
