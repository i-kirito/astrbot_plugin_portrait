"""默认配置常量 - 仅供参考的示例配置"""

# 默认环境场景配置（示例）
DEFAULT_ENVIRONMENTS = [
    {
        "name": "默认室内",
        "keywords": ["default"],
        "prompt": "indoors, cozy atmosphere with natural lighting, realistic setting"
    },
    {
        "name": "户外",
        "keywords": ["户外", "outdoor", "outside"],
        "prompt": "outdoors, natural daylight, softly blurred background with bokeh effect"
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
6.  **PROMPT FORMAT (MANDATORY)**:
    - Write the ENTIRE prompt as **flowing natural English prose**, like describing a photograph.
    - **DO NOT** use weighted tag format like `(tag:1.3)` or `(keyword:1.2)`. These are for Stable Diffusion only and will degrade output quality.
    - **DO**: "The subject is a young girl with pink hair, standing in a cozy bedroom with warm natural lighting"
    - **DON'T**: "(young girl:1.4), (pink hair:1.3), (indoors:1.2), (natural lighting:1.2)"
    - Environment and camera descriptions below may contain reference tags — **translate them into natural language** when building your prompt.
7.  **NO REPEAT**: After the tool returns [SUCCESS], do NOT call portrait_draw_image again with the same or similar prompt. The image has already been sent to the user. Just respond naturally to acknowledge the task completion.
8.  **SINGLE CALL ONLY**: Only call the tool ONCE per user request. If you already called it, DO NOT call again even if the user message contains drawing keywords."""

TPL_CHAR = """## 1. Character Visuals (Fixed Identity)
**Core Appearance (Always Active):**
{content}"""

TPL_MIDDLE = """## 2. 动态内容处理 (Handling User Input)
* **穿搭 (Outfit):** 用户未指定时，默认保持简洁风格或根据场景补全。
* **表情 (Expression):** 根据场景补全，参考图片时表情尽可能随机性。
* **动作 (Action):** 根据场景补全或自然融入用户描述的动作。如果动作/表情与核心设定的冲突，**以用户要求为准**
"""

TPL_FOOTER = """---"""
