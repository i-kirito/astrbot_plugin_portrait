"""默认配置常量 - 仅供参考的示例配置"""

# 默认角色外貌描述（示例）
# 用户应在 WebUI 配置页面或 config.yaml 中自定义
DEF_CHAR_IDENTITY = """> **A young person with distinct features. Customize this in the plugin configuration.**"""

# 默认环境场景配置（示例）
DEFAULT_ENVIRONMENTS = [
    {
        "name": "默认室内",
        "keywords": ["default"],
        "prompt": "(indoors:1.2), (natural lighting:1.2), (realistic:1.2), cozy atmosphere"
    },
    {
        "name": "户外",
        "keywords": ["户外", "outdoor", "outside"],
        "prompt": "(outdoors:1.3), (natural daylight:1.3), (blurred background:1.2), (bokeh:1.1)"
    }
]

# 默认摄影模式配置（示例）
DEFAULT_CAMERAS = [
    {
        "name": "半身/默认",
        "keywords": ["default"],
        "prompt": "upper body shot, medium close-up portrait, looking at camera"
    },
    {
        "name": "全身",
        "keywords": ["全身", "full body"],
        "prompt": "full body shot, head to toe visible, wide angle"
    }
]

# Prompt 模板常量
TPL_HEADER = """# Visual Context Injection (System Override)
## 0. Chain of Thought & Trigger Logic
1.  **Analyze User Intent**: specific keywords like "draw", "photo", "selfie", "show me", "look at you", or implicitly asking for a visual representation.
2.  **If Drawing Intent Detected**: You MUST call the `portrait_draw_image` tool with the Visual Data below.
3.  **Prompt Structure**: `[Character Visuals] + [User Action/Outfit] + [Environment] + [Camera]`
4.  **IMPORTANT**: Always use `portrait_draw_image` tool for image generation.
5.  **CRITICAL**: When calling any tool, do NOT output any text content in the same response. Call the tool ONLY, then wait for the result before responding to the user.
6.  **MANDATORY**: You MUST copy the EXACT prompt blocks from the Environment and Camera sections below verbatim. Do NOT simplify, summarize, or omit any parameters. Include ALL lighting, style, and quality tags exactly as written.
7.  **NO REPEAT**: After the tool returns [SUCCESS], do NOT call portrait_draw_image again with the same or similar prompt. The image has already been sent to the user."""

TPL_CHAR = """## 1. Character Visuals (Fixed Identity)
**Core Appearance (Always Active):**
{content}"""

TPL_MIDDLE = """## 2. 动态内容处理 (Handling User Input)
* **穿搭 (Outfit):** 用户未指定时，默认保持简洁风格或根据场景补全。
* **动作 (Action):** 自然融入用户描述的动作。如果动作/表情与核心设定的冲突，**以用户要求为准**"""

TPL_FOOTER = """---"""
