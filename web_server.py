"""
Portrait Plugin WebUI Server
基于 aiohttp 的 Web 管理界面后端
"""

import asyncio
import json
import os
import secrets
from pathlib import Path
from datetime import datetime

from aiohttp import web
from astrbot.api import logger

from .core.image_manager import ImageManager


def _mask_key(key: str) -> str:
    """将密钥掩码化，只显示前4位和后4位"""
    if not key or len(key) <= 8:
        return "*" * len(key) if key else ""
    return f"{key[:4]}...{key[-4:]}"


def _is_masked_key(value: str) -> bool:
    """检测是否为脱敏后的密钥格式（如 XXXX...YYYY）"""
    if not value or not isinstance(value, str):
        return False
    # 脱敏格式: 4字符 + ... + 4字符，或全是星号
    if value == "*" * len(value):
        return True
    if "..." in value and len(value) <= 12:
        parts = value.split("...")
        if len(parts) == 2 and len(parts[0]) == 4 and len(parts[1]) == 4:
            return True
    return False


class WebServer:
    """Portrait 插件 Web 管理服务器"""

    def __init__(self, plugin, host: str = "127.0.0.1", port: int = 8088, token: str = ""):
        self.plugin = plugin
        self.host = host
        self.port = port
        # 安全默认：如果未设置 token 且非本地监听，自动生成随机 token
        if not token and host != "127.0.0.1":
            token = secrets.token_urlsafe(32)
            logger.warning(f"[Portrait WebUI] 未设置 token，已自动生成（请在配置中查看）")
        self.token = token  # 访问令牌
        self.app = web.Application(middlewares=[self._auth_middleware])
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None
        self._started = False

        # Web UI 静态文件目录
        self.static_dir = Path(__file__).parent / "web"
        # 图片数据目录
        self.images_dir = self.plugin.data_dir / "generated_images"
        # 缩略图目录
        self.thumbnails_dir = self.plugin.data_dir / "thumbnails"
        # 自拍参考照目录
        self.selfie_refs_dir = self.plugin.data_dir / "selfie_refs"
        # ImageManager 实例（延迟初始化）
        self._imgr: ImageManager | None = None

        # 缓存
        self._index_cache: str | None = None
        self._images_cache: dict | None = None
        self._images_cache_time: float = 0
        self._images_cache_ttl: float = 5.0  # 5秒缓存
        self._thumb_gen_semaphore = asyncio.Semaphore(4)  # 并发缩略图生成限制

        self._setup_routes()

    @web.middleware
    async def _auth_middleware(self, request: web.Request, handler):
        """Token 认证中间件（支持 Cookie / Header / Query）"""
        # 如果未设置 token，跳过认证（任何请求都放行）
        if not self.token:
            return await handler(request)

        # 静态资源不需要认证（index.html、CSS、JS 等）
        if request.path in ["/", "/index.html"]:
            return await handler(request)
        if request.path.startswith(("/static/", "/web/")):
            return await handler(request)
        # 登录接口不需要认证
        if request.path == "/api/auth":
            return await handler(request)

        # 优先从 Cookie 获取 token，其次是 Header 或 Query
        # 确保所有来源都有默认空字符串，避免 None 导致 compare_digest TypeError
        req_token = (
            request.cookies.get("portrait_token", "")
            or request.headers.get("X-Token", "")
            or request.query.get("token", "")
            or ""  # 最终保底，确保不为 None
        )

        if not req_token or not secrets.compare_digest(req_token, self.token):
            return web.json_response(
                {"success": False, "error": "未授权访问，请提供正确的 token"},
                status=401
            )

        return await handler(request)

    def _setup_routes(self):
        """设置路由"""
        # 认证 API
        self.app.router.add_post("/api/auth", self.handle_auth)

        # API 路由
        self.app.router.add_get("/api/config", self.handle_get_config)
        self.app.router.add_post("/api/config", self.handle_save_config)
        self.app.router.add_get("/api/dynamic-config", self.handle_get_dynamic_config)
        self.app.router.add_post("/api/dynamic-config", self.handle_save_dynamic_config)
        self.app.router.add_get("/api/images", self.handle_list_images)
        self.app.router.add_delete("/api/images/{name}", self.handle_delete_image)
        self.app.router.add_post("/api/images/{name}/favorite", self.handle_toggle_favorite)
        self.app.router.add_get("/api/images/{name}/download", self.handle_download_image)
        self.app.router.add_get("/api/health", self.handle_health)

        # 自拍参考照 API
        self.app.router.add_get("/api/selfie-refs", self.handle_list_selfie_refs)
        self.app.router.add_post("/api/selfie-refs", self.handle_upload_selfie_ref)
        self.app.router.add_delete("/api/selfie-refs/{name}", self.handle_delete_selfie_ref)

        # 视频画廊 API
        self.app.router.add_get("/api/videos", self.handle_list_videos)
        self.app.router.add_delete("/api/videos/{name}", self.handle_delete_video)
        self.app.router.add_get("/api/videos/{name}/download", self.handle_download_video)

        # 视频预设词 API
        self.app.router.add_get("/api/video-presets", self.handle_get_video_presets)
        self.app.router.add_post("/api/video-presets", self.handle_save_video_presets)

        # 改图预设词 API
        self.app.router.add_get("/api/edit-presets", self.handle_get_edit_presets)
        self.app.router.add_post("/api/edit-presets", self.handle_save_edit_presets)

        # 缓存清理 API
        self.app.router.add_post("/api/cache/cleanup", self.handle_cache_cleanup)

        # 前端页面
        self.app.router.add_get("/", self.handle_index)
        self.app.router.add_get("/index.html", self.handle_index)

        # 静态资源（CSS/JS 无需认证）
        if self.static_dir.exists():
            self.app.router.add_static("/web", self.static_dir, show_index=False)

        # 图片/缩略图/参考照/视频使用动态路由（需要认证）
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.thumbnails_dir.mkdir(parents=True, exist_ok=True)
        self.selfie_refs_dir.mkdir(parents=True, exist_ok=True)
        self.videos_dir = self.plugin.data_dir / "videos"
        self.videos_dir.mkdir(parents=True, exist_ok=True)

        self.app.router.add_get("/images/{name}", self.handle_serve_image)
        self.app.router.add_get("/thumbnails/{name}", self.handle_serve_thumbnail)
        self.app.router.add_get("/selfie-refs/{name}", self.handle_serve_selfie_ref)
        self.app.router.add_get("/videos/{name}", self.handle_serve_video)

    @property
    def imgr(self) -> ImageManager:
        """获取 ImageManager 实例"""
        if self._imgr is None:
            self._imgr = ImageManager(self.plugin.data_dir)
        return self._imgr

    async def start(self) -> bool:
        """启动 Web 服务器"""
        try:
            # 安全检查：非本地监听时必须设置 token
            if self.host != "127.0.0.1" and not self.token:
                logger.error(
                    f"[Portrait WebUI] 安全错误: 监听地址为 {self.host}，但未设置访问令牌！"
                    "拒绝启动。请设置 token 或改为 127.0.0.1"
                )
                return False

            # 确保图片目录存在
            self.images_dir.mkdir(parents=True, exist_ok=True)

            self.runner = web.AppRunner(self.app, access_log=None)
            await self.runner.setup()

            self.site = web.TCPSite(self.runner, self.host, self.port)
            await self.site.start()

            self._started = True

            if self.host == "0.0.0.0":
                logger.info(f"[Portrait WebUI] 已启动 - 监听所有接口 (0.0.0.0:{self.port})")
                logger.info(f"[Portrait WebUI] 本地访问: http://127.0.0.1:{self.port}")
            else:
                logger.info(f"[Portrait WebUI] 已启动: http://{self.host}:{self.port}")

            return True

        except OSError as e:
            if "Address already in use" in str(e) or e.errno in (98, 10048):
                logger.error(f"[Portrait WebUI] 端口 {self.port} 已被占用")
            else:
                logger.error(f"[Portrait WebUI] 启动失败: {e}")
            return False
        except Exception as e:
            logger.error(f"[Portrait WebUI] 启动失败: {e}", exc_info=True)
            return False

    async def stop(self):
        """停止 Web 服务器"""
        if not self._started:
            return
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()
        self._started = False
        logger.info("[Portrait WebUI] 已停止")

    async def handle_index(self, request: web.Request) -> web.Response:
        """返回首页"""
        if self._index_cache:
            return web.Response(text=self._index_cache, content_type="text/html")

        index_file = self.static_dir / "index.html"
        if not index_file.exists():
            return web.Response(
                text="<h1>Portrait WebUI</h1><p>index.html not found</p>",
                content_type="text/html",
                status=404,
            )
        try:
            content = await asyncio.to_thread(index_file.read_text, encoding="utf-8")
            self._index_cache = content
            return web.Response(text=content, content_type="text/html")
        except Exception as e:
            logger.error(f"[Portrait WebUI] 读取 index.html 失败: {e}")
            return web.Response(text=f"Error: {e}", status=500)

    async def handle_health(self, request: web.Request) -> web.Response:
        """健康检查"""
        return web.json_response({"status": "ok", "service": "portrait-webui"})

    async def handle_auth(self, request: web.Request) -> web.Response:
        """登录认证，验证 token 后设置 Cookie"""
        try:
            data = await request.json()
            raw_token = data.get("token", "")
            # 确保 token 是字符串类型，防止 JSON 传数字/数组导致 TypeError
            if not isinstance(raw_token, str):
                return web.json_response(
                    {"success": False, "error": "Token 必须是字符串"},
                    status=400
                )
            req_token = raw_token or ""

            if not self.token:
                # 未设置 token，直接放行
                return web.json_response({"success": True, "message": "无需认证"})

            if not req_token or not secrets.compare_digest(req_token, self.token):
                return web.json_response(
                    {"success": False, "error": "Token 错误"},
                    status=401
                )

            # 设置 HttpOnly Cookie
            response = web.json_response({"success": True, "message": "认证成功"})
            response.set_cookie(
                "portrait_token",
                self.token,
                httponly=True,
                samesite="Strict",
                max_age=86400 * 7,  # 7 天有效
            )
            return response

        except json.JSONDecodeError:
            return web.json_response(
                {"success": False, "error": "无效的 JSON"},
                status=400
            )

    async def handle_get_config(self, request: web.Request) -> web.Response:
        """获取配置"""
        try:
            # 提取可编辑的配置字段
            editable_fields = [
                "char_identity",
                "injection_rounds",
                "cooldown_seconds",
                "enable_env_injection",
                "enable_camera_injection",
                "proxy",
                "draw_provider",
                "enable_fallback",
                "fallback_models",
            ]

            config = {}
            for field in editable_fields:
                if field in self.plugin.config:
                    config[field] = self.plugin.config[field]
                else:
                    # 使用插件实例变量作为后备（确保返回正确的当前值）
                    if field == "draw_provider":
                        config[field] = getattr(self.plugin, "draw_provider", "gitee")
                    elif field == "enable_fallback":
                        config[field] = getattr(self.plugin, "enable_fallback", True)
                    elif field == "fallback_models":
                        config[field] = getattr(self.plugin, "fallback_models", ["gemini", "grok"])
                    else:
                        config[field] = None

            # gitee_config 单独处理，密钥掩码化
            gitee_conf = self.plugin.config.get("gitee_config", {}) or {}
            api_keys_raw = gitee_conf.get("api_keys", []) or []
            config["gitee_config"] = {
                "api_keys": [_mask_key(k) for k in api_keys_raw],
                "base_url": gitee_conf.get("base_url", "https://ai.gitee.com/v1"),
                "model": gitee_conf.get("model", "z-image-turbo"),
                "size": gitee_conf.get("size", "1024x1024"),
                "num_inference_steps": gitee_conf.get("num_inference_steps", 9),
                "negative_prompt": gitee_conf.get("negative_prompt", ""),
                "timeout": gitee_conf.get("timeout", 300),
                "max_retries": gitee_conf.get("max_retries", 2),
            }

            # gemini_config 单独处理，密钥掩码化
            gemini_conf = self.plugin.config.get("gemini_config", {}) or {}
            config["gemini_config"] = {
                "api_key": _mask_key(gemini_conf.get("api_key", "") or ""),
                "base_url": gemini_conf.get("base_url", "https://generativelanguage.googleapis.com"),
                "model": gemini_conf.get("model", "gemini-2.0-flash-exp-image-generation"),
                "image_size": gemini_conf.get("image_size", "1K"),
                "timeout": gemini_conf.get("timeout", 120),
            }

            # grok_config 单独处理，密钥掩码化
            grok_conf = self.plugin.config.get("grok_config", {}) or {}
            config["grok_config"] = {
                "api_key": _mask_key(grok_conf.get("api_key", "") or ""),
                "base_url": grok_conf.get("base_url", "https://api.x.ai"),
                "image_model": grok_conf.get("image_model", "grok-2-image"),
                "video_model": grok_conf.get("video_model", "grok-imagine-1.0-video"),
                "size": grok_conf.get("size", "1024x1024"),
                "timeout": grok_conf.get("timeout", 180),
                "max_retries": grok_conf.get("max_retries", 2),
                "video_enabled": grok_conf.get("video_enabled", False),
                "video_send_mode": grok_conf.get("video_send_mode", "auto"),
                "max_cached_videos": grok_conf.get("max_cached_videos", 20),
            }

            # cache_config 单独处理
            cache_conf = self.plugin.config.get("cache_config", {}) or {}
            config["cache_config"] = {
                "max_storage_mb": cache_conf.get("max_storage_mb", 500),
                "max_count": cache_conf.get("max_count", 100),
            }

            # 添加动态配置（从独立文件加载）
            dynamic = self.plugin.get_dynamic_config()
            config["environments"] = dynamic.get("environments", [])
            config["cameras"] = dynamic.get("cameras", [])

            # selfie_config 单独处理
            selfie_conf = self.plugin.config.get("selfie_config", {}) or {}
            config["selfie_config"] = {
                "enabled": selfie_conf.get("enabled", False),
                "reference_images": selfie_conf.get("reference_images", []) or [],
            }

            return web.json_response({"success": True, "config": config})
        except Exception as e:
            logger.error(f"[Portrait WebUI] 获取配置失败: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def handle_save_config(self, request: web.Request) -> web.Response:
        """保存配置并热更新"""
        try:
            data = await request.json()
            new_config = data.get("config", {})

            if not new_config:
                return web.json_response(
                    {"success": False, "error": "配置为空"}, status=400
                )

            # 可编辑字段白名单
            editable_fields = {
                "char_identity",
                "injection_rounds",
                "cooldown_seconds",
                "enable_env_injection",
                "enable_camera_injection",
                "proxy",
                "gitee_config",
                "gemini_config",
                "grok_config",
                "cache_config",
                "draw_provider",
                "enable_fallback",
                "fallback_models",
                "environments",
                "cameras",
                "selfie_config",
            }

            # === 处理脱敏 API Key：如果是脱敏格式，保留原值 ===
            def merge_config_with_masked_keys(field: str, new_val: dict, old_val: dict) -> dict:
                """合并配置，脱敏格式的 Key 保留原值"""
                if not isinstance(new_val, dict) or not isinstance(old_val, dict):
                    return new_val

                result = new_val.copy()
                key_fields = ["api_key", "api_keys", "token"]

                for key_field in key_fields:
                    if key_field in result:
                        new_key = result[key_field]
                        old_key = old_val.get(key_field)

                        if key_field == "api_keys" and isinstance(new_key, list):
                            # 处理 api_keys 列表
                            old_keys = old_val.get("api_keys", [])
                            merged_keys = []
                            for i, k in enumerate(new_key):
                                if _is_masked_key(k) and i < len(old_keys):
                                    merged_keys.append(old_keys[i])
                                elif not _is_masked_key(k):
                                    merged_keys.append(k)
                            result["api_keys"] = merged_keys
                        elif isinstance(new_key, str) and _is_masked_key(new_key) and old_key:
                            # 单个 api_key，保留原值
                            result[key_field] = old_key

                return result

            # 更新配置
            updated_fields = []
            for field, value in new_config.items():
                if field in editable_fields:
                    # selfie_config 特殊处理：移除 reference_images 字段（已废弃）
                    if field == "selfie_config" and isinstance(value, dict):
                        value.pop("reference_images", None)

                    # 处理包含 API Key 的配置（gitee_config, gemini_config, grok_config）
                    if field in ("gitee_config", "gemini_config", "grok_config") and isinstance(value, dict):
                        old_value = self.plugin.config.get(field, {}) or {}
                        value = merge_config_with_masked_keys(field, value, old_value)

                    self.plugin.config[field] = value
                    updated_fields.append(field)

            # 同时清理旧配置中的 reference_images 字段
            if "selfie_config" in self.plugin.config:
                if isinstance(self.plugin.config["selfie_config"], dict):
                    self.plugin.config["selfie_config"].pop("reference_images", None)

            # 热更新：重新组装 full_prompt
            self._reload_plugin_resources()

            # 持久化配置到磁盘（异步）
            await asyncio.to_thread(self.plugin.save_config_to_disk)

            logger.info(f"[Portrait WebUI] 配置已更新: {updated_fields}")

            return web.json_response({
                "success": True,
                "message": f"已更新 {len(updated_fields)} 个字段",
                "updated_fields": updated_fields,
            })

        except json.JSONDecodeError:
            return web.json_response(
                {"success": False, "error": "无效的 JSON"}, status=400
            )
        except Exception as e:
            logger.error(f"[Portrait WebUI] 保存配置失败: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def handle_get_dynamic_config(self, request: web.Request) -> web.Response:
        """获取动态配置（环境和摄影模式列表）"""
        try:
            return web.json_response({
                "success": True,
                "config": self.plugin.get_dynamic_config(),
            })
        except Exception as e:
            logger.error(f"[Portrait WebUI] 获取动态配置失败: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def handle_save_dynamic_config(self, request: web.Request) -> web.Response:
        """保存动态配置并热更新"""
        try:
            data = await request.json()
            new_config = data.get("config", {})

            if not isinstance(new_config, dict) or not new_config:
                return web.json_response(
                    {"success": False, "error": "配置为空"}, status=400
                )

            await asyncio.to_thread(self.plugin.update_dynamic_config, new_config)
            logger.info("[Portrait WebUI] 动态配置已更新")

            return web.json_response({
                "success": True,
                "config": self.plugin.get_dynamic_config(),
            })

        except json.JSONDecodeError:
            return web.json_response(
                {"success": False, "error": "无效的 JSON"}, status=400
            )
        except Exception as e:
            logger.error(f"[Portrait WebUI] 保存动态配置失败: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    def _reload_plugin_resources(self):
        """热更新插件资源"""
        try:
            # 清除 index.html 缓存，确保前端更新生效
            self._index_cache = None

            config = self.plugin.config

            # 使用新的动态 Prompt 重建方法
            self.plugin.rebuild_full_prompt()

            # 更新注入轮次
            self.plugin.injection_rounds = max(1, config.get("injection_rounds", 1))

            # 更新 Gitee 配置
            gitee_conf = config.get("gitee_config", {}) or {}
            if gitee_conf and hasattr(self.plugin, "gitee_draw"):
                self.plugin.gitee_draw.api_keys = [
                    k.strip() for k in gitee_conf.get("api_keys", []) if k.strip()
                ]
                self.plugin.gitee_draw.model = gitee_conf.get("model", "z-image-turbo") or "z-image-turbo"
                self.plugin.gitee_draw.default_size = gitee_conf.get("size", "1024x1024") or "1024x1024"
                self.plugin.gitee_draw.num_inference_steps = gitee_conf.get("num_inference_steps", 9) or 9
                self.plugin.gitee_draw.negative_prompt = gitee_conf.get("negative_prompt", "") or ""

            # 更新 Gemini 配置
            gemini_conf = config.get("gemini_config", {}) or {}
            if gemini_conf and hasattr(self.plugin, "gemini_draw"):
                if gemini_conf.get("api_key"):
                    self.plugin.gemini_draw.api_key = gemini_conf.get("api_key", "").strip()
                self.plugin.gemini_draw.model = gemini_conf.get("model", "gemini-2.0-flash-exp-image-generation") or "gemini-2.0-flash-exp-image-generation"
                self.plugin.gemini_draw.image_size = (gemini_conf.get("image_size", "1K") or "1K").upper()
                self.plugin.gemini_draw.timeout = gemini_conf.get("timeout", 120) or 120
                # 处理 base_url
                base_url = (gemini_conf.get("base_url", "https://generativelanguage.googleapis.com") or "https://generativelanguage.googleapis.com").strip().rstrip("/")
                for suffix in ["/v1beta/models", "/v1beta", "/v1/chat/completions", "/v1"]:
                    if base_url.endswith(suffix):
                        base_url = base_url[:-len(suffix)]
                        break
                self.plugin.gemini_draw.base_url = base_url

            # 更新 Grok 配置
            grok_conf = config.get("grok_config", {}) or {}
            if grok_conf and hasattr(self.plugin, "grok_draw"):
                if grok_conf.get("api_key"):
                    self.plugin.grok_draw.api_key = grok_conf.get("api_key", "").strip()
                self.plugin.grok_draw.model = grok_conf.get("image_model", "") or grok_conf.get("model", "grok-2-image") or "grok-2-image"
                self.plugin.grok_draw.default_size = grok_conf.get("size", "1024x1024") or "1024x1024"
                self.plugin.grok_draw.timeout = grok_conf.get("timeout", 180) or 180
                self.plugin.grok_draw.max_retries = grok_conf.get("max_retries", 2) or 2
                # 更新端点
                base_url = (grok_conf.get("base_url", "https://api.x.ai") or "https://api.x.ai").strip().rstrip("/")
                if not base_url.startswith(("http://", "https://")):
                    base_url = "https://" + base_url
                self.plugin.grok_draw.base_url = base_url
                self.plugin.grok_draw._endpoint = f"{base_url}/v1/chat/completions"
                self.plugin.grok_draw._images_endpoint = f"{base_url}/v1/images/generations"
                logger.info(f"[Portrait WebUI] Grok 配置已更新: size={self.plugin.grok_draw.default_size}, model={self.plugin.grok_draw.model}")

            # 更新提供商配置
            if "draw_provider" in config:
                self.plugin.draw_provider = config.get("draw_provider", "gitee") or "gitee"
                logger.info(f"[Portrait WebUI] draw_provider 已更新为: {self.plugin.draw_provider}")
            if "enable_fallback" in config:
                self.plugin.enable_fallback = config.get("enable_fallback", True)
            if "fallback_models" in config:
                self.plugin.fallback_models = config.get("fallback_models", ["gemini", "grok"]) or ["gemini", "grok"]
                logger.info(f"[Portrait WebUI] fallback_models 已更新为: {self.plugin.fallback_models}")

            # 更新人像参考配置
            selfie_conf = config.get("selfie_config", {}) or {}
            if selfie_conf:
                self.plugin.selfie_enabled = selfie_conf.get("enabled", False)
                logger.info(f"[Portrait WebUI] selfie_enabled 已更新为: {self.plugin.selfie_enabled}")

            logger.debug("[Portrait WebUI] 插件资源已热更新")

        except Exception as e:
            logger.error(f"[Portrait WebUI] 热更新失败: {e}")

    async def handle_list_images(self, request: web.Request) -> web.Response:
        """列出生成的图片"""
        try:
            if not self.images_dir.exists():
                return web.json_response({
                    "success": True, "images": [], "total": 0,
                    "filters": {"models": [], "sizes": [], "categories": []}
                })

            # 分页参数解析，格式错误返回 400
            try:
                page = max(1, int(request.query.get("page", 1)))
                page_size = min(200, max(1, int(request.query.get("size", 50))))
            except (ValueError, TypeError):
                return web.json_response(
                    {"success": False, "error": "分页参数格式错误"},
                    status=400
                )
            filter_favorites = request.query.get("favorites", "").lower() == "true"
            filter_model = request.query.get("model", "").strip()
            filter_size = request.query.get("filter_size", "").strip()  # 1K/2K/4K
            filter_category = request.query.get("category", "").strip()

            # 辅助函数：获取图片尺寸分类
            def get_size_class_from_dimensions(width: int, height: int) -> str:
                """根据图片像素尺寸返回分类"""
                max_dim = max(width, height)
                if max_dim >= 3840:
                    return "4K"
                elif max_dim >= 1920:
                    return "2K"
                else:
                    return "1K"

            # Check cache
            import time
            current_time = time.time()
            if self._images_cache and (current_time - self._images_cache_time < self._images_cache_ttl):
                images = self._images_cache
            else:
                allowed_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

                # 将目录扫描移至线程池避免阻塞（不再在循环中打开图片文件）
                def scan_images():
                    results = []
                    for file_path in self.images_dir.iterdir():
                        if file_path.is_file() and file_path.suffix.lower() in allowed_exts:
                            stat = file_path.stat()
                            results.append((file_path, stat))
                    return results

                file_stats = await asyncio.to_thread(scan_images)

                # 一次性获取元数据和收藏快照（避免循环中重复读取文件）
                metadata_map, favorites = await asyncio.gather(
                    self.imgr.get_metadata_snapshot_async(),
                    self.imgr.get_favorites_snapshot_async(),
                )

                images = []
                missing_metadata_records: list[tuple[str, str, str, str, str]] = []

                for file_path, stat in file_stats:
                    filename = file_path.name

                    # 从快照获取元数据
                    metadata = metadata_map.get(filename) or {}
                    is_favorite = filename in favorites

                    # 如果没有元数据，记录下来稍后批量保存
                    if not metadata:
                        missing_metadata_records.append((filename, "", "", "", ""))

                    # 生成缩略图路径
                    thumb_path = self.thumbnails_dir / filename
                    thumb_url = f"/thumbnails/{filename}" if thumb_path.exists() else f"/images/{filename}"

                    # 获取 category，如果没有则根据 prompt 推断
                    category = ""
                    if metadata:
                        category = metadata.get("category", "")
                        if not category and metadata.get("prompt"):
                            # 旧图片没有 category，根据 prompt 内容推断
                            prompt_lower = metadata.get("prompt", "").lower()
                            char_keywords = [
                                "girl", "woman", "lady", "person", "human", "selfie", "portrait",
                                "女孩", "女生", "女性", "人物", "美女", "自拍", "肖像",
                                "face", "body", "eyes", "hair", "dress", "outfit",
                                "脸", "眼睛", "头发", "衣服", "裙子",
                            ]
                            if any(kw in prompt_lower for kw in char_keywords):
                                category = "character"
                            else:
                                category = "other"

                    # 从 metadata 获取图片尺寸（避免 O(N) 磁盘 I/O）
                    img_width, img_height = 0, 0
                    if metadata and metadata.get("size"):
                        try:
                            size_str = metadata.get("size", "")
                            if "x" in size_str.lower():
                                parts = size_str.lower().split("x")
                                if len(parts) == 2:
                                    img_width, img_height = int(parts[0]), int(parts[1])
                        except (ValueError, AttributeError):
                            pass

                    # 计算尺寸分类
                    size_class = ""
                    if img_width > 0 and img_height > 0:
                        size_class = get_size_class_from_dimensions(img_width, img_height)

                    images.append({
                        "name": filename,
                        "url": f"/images/{filename}",
                        "thumbnail": thumb_url,
                        "size": stat.st_size,
                        "ctime": int(stat.st_ctime),
                        "mtime": int(stat.st_mtime),
                        "prompt": metadata.get("prompt", "") if metadata else "",
                        "model": metadata.get("model", "") if metadata else "",
                        "category": category,
                        "image_size": f"{img_width}x{img_height}" if img_width > 0 else "",
                        "size_class": size_class,
                        "favorite": is_favorite,
                    })

                # 如果有缺失元数据的图片，异步批量保存
                if missing_metadata_records:
                    asyncio.create_task(
                        self.imgr.set_metadata_batch_async(missing_metadata_records)
                    )

                # 按修改时间倒序
                images.sort(key=lambda x: x["mtime"], reverse=True)

                # Update cache
                self._images_cache = images
                self._images_cache_time = current_time

            # 收集可用的筛选选项
            all_models = sorted(set(img["model"] for img in images if img["model"]))
            all_categories = sorted(set(img["category"] for img in images if img["category"]))
            all_size_classes = sorted(set(img["size_class"] for img in images if img["size_class"]),
                                       key=lambda x: ["1K", "2K", "4K"].index(x) if x in ["1K", "2K", "4K"] else 99)

            # Filter and paginate from cached full list
            filtered_images = images

            if filter_favorites:
                filtered_images = [img for img in filtered_images if img["favorite"]]

            if filter_model:
                filtered_images = [img for img in filtered_images if img["model"] == filter_model]

            if filter_size:
                filtered_images = [img for img in filtered_images if img["size_class"] == filter_size]

            if filter_category:
                filtered_images = [img for img in filtered_images if img["category"] == filter_category]

            total = len(filtered_images)
            # 计算总收藏数（从全部图片中统计）
            total_favorite_count = sum(1 for img in images if img["favorite"])
            start = (page - 1) * page_size
            end = start + page_size
            paged_images = filtered_images[start:end]

            # Generate thumbnails for current page (concurrent with semaphore)
            async def ensure_thumbnail(img: dict) -> None:
                thumb_path = self.thumbnails_dir / img["name"]
                if thumb_path.exists():
                    img["thumbnail"] = f"/thumbnails/{img['name']}"
                    return
                file_path = self.images_dir / img["name"]
                if not file_path.exists():
                    return
                async with self._thumb_gen_semaphore:
                    await self._generate_thumbnail(file_path, thumb_path)
                if thumb_path.exists():
                    img["thumbnail"] = f"/thumbnails/{img['name']}"

            await asyncio.gather(
                *(ensure_thumbnail(img) for img in paged_images),
                return_exceptions=True
            )

            return web.json_response({
                "success": True,
                "images": paged_images,
                "total": total,
                "favorite_count": total_favorite_count,
                "page": page,
                "page_size": page_size,
                "filters": {
                    "models": all_models,
                    "sizes": all_size_classes if all_size_classes else ["1K", "2K", "4K"],
                    "categories": all_categories if all_categories else ["character", "other"],
                },
            })

        except Exception as e:
            logger.error(f"[Portrait WebUI] 获取图片列表失败: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def handle_delete_image(self, request: web.Request) -> web.Response:
        """删除图片"""
        try:
            name = request.match_info.get("name", "")
            if not name:
                return web.json_response(
                    {"success": False, "error": "文件名为空"}, status=400
                )

            # 安全检查：防止路径遍历
            if "/" in name or "\\" in name or ".." in name:
                return web.json_response(
                    {"success": False, "error": "非法文件名"}, status=400
                )

            file_path = self.images_dir / name
            if not file_path.exists():
                return web.json_response(
                    {"success": False, "error": "文件不存在"}, status=404
                )

            # 确保文件在 images_dir 内
            try:
                file_path.relative_to(self.images_dir)
            except ValueError:
                return web.json_response(
                    {"success": False, "error": "非法路径"}, status=400
                )

            # 使用 asyncio.to_thread 避免阻塞事件循环
            await asyncio.to_thread(os.remove, file_path)
            # 删除缩略图
            thumb_path = self.thumbnails_dir / name
            if thumb_path.exists():
                await asyncio.to_thread(os.remove, thumb_path)
            # 清理元数据
            await self.imgr.remove_metadata_async(name)
            logger.info(f"[Portrait WebUI] 已删除图片: {name}")

            return web.json_response({"success": True, "deleted": name})

        except Exception as e:
            logger.error(f"[Portrait WebUI] 删除图片失败: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def handle_toggle_favorite(self, request: web.Request) -> web.Response:
        """切换图片收藏状态"""
        try:
            name = request.match_info.get("name", "")
            if not name:
                return web.json_response(
                    {"success": False, "error": "文件名为空"}, status=400
                )

            # 安全检查：防止路径遍历
            if "/" in name or "\\" in name or ".." in name:
                return web.json_response(
                    {"success": False, "error": "非法文件名"}, status=400
                )

            file_path = self.images_dir / name
            if not file_path.exists():
                return web.json_response(
                    {"success": False, "error": "文件不存在"}, status=404
                )

            # 切换收藏状态（使用异步版本避免阻塞）
            new_state = await self.imgr.toggle_favorite_async(name)
            logger.info(f"[Portrait WebUI] 图片收藏状态已{'添加' if new_state else '取消'}: {name}")

            return web.json_response({
                "success": True,
                "name": name,
                "favorite": new_state,
            })

        except Exception as e:
            logger.error(f"[Portrait WebUI] 切换收藏状态失败: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def handle_download_image(self, request: web.Request) -> web.Response:
        """下载图片（带 Content-Disposition 头）"""
        try:
            name = request.match_info.get("name", "")
            if not name:
                return web.json_response(
                    {"success": False, "error": "文件名为空"}, status=400
                )

            # 安全检查：防止路径遍历
            if "/" in name or "\\" in name or ".." in name:
                return web.json_response(
                    {"success": False, "error": "非法文件名"}, status=400
                )

            file_path = self.images_dir / name
            if not file_path.exists():
                return web.json_response(
                    {"success": False, "error": "文件不存在"}, status=404
                )

            # 确保文件在 images_dir 内
            try:
                file_path.relative_to(self.images_dir)
            except ValueError:
                return web.json_response(
                    {"success": False, "error": "非法路径"}, status=400
                )

            # 使用 FileResponse 流式传输文件
            return web.FileResponse(
                file_path,
                headers={
                    "Content-Disposition": f'attachment; filename="{name}"',
                },
            )

        except Exception as e:
            logger.error(f"[Portrait WebUI] 下载图片失败: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def _generate_thumbnail(self, src_path: Path, dest_path: Path, max_size: int = 300) -> bool:
        """生成缩略图"""
        try:
            from PIL import Image
            import asyncio

            def _generate():
                with Image.open(src_path) as img:
                    # 计算缩放比例
                    ratio = min(max_size / img.width, max_size / img.height)
                    if ratio >= 1:
                        # 图片已经足够小，直接复制
                        img.save(dest_path, quality=85, optimize=True)
                    else:
                        new_size = (int(img.width * ratio), int(img.height * ratio))
                        # 使用高质量缩放
                        resized = img.resize(new_size, Image.Resampling.LANCZOS)
                        # 转换模式以支持保存
                        if resized.mode in ("RGBA", "P"):
                            resized = resized.convert("RGB")
                        resized.save(dest_path, "JPEG", quality=85, optimize=True)
                return True

            return await asyncio.to_thread(_generate)
        except ImportError:
            logger.debug("[Portrait WebUI] PIL 未安装，跳过缩略图生成")
            return False
        except Exception as e:
            logger.warning(f"[Portrait WebUI] 生成缩略图失败: {e}")
            return False

    # === 自拍参考照管理 API ===

    async def handle_list_selfie_refs(self, request: web.Request) -> web.Response:
        """列出人像参考"""
        try:
            if not self.selfie_refs_dir.exists():
                return web.json_response({
                    "success": True,
                    "refs": [],
                    "enabled": self.plugin.selfie_enabled,
                })

            refs = []
            allowed_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

            for file_path in self.selfie_refs_dir.iterdir():
                if file_path.is_file() and file_path.suffix.lower() in allowed_exts:
                    stat = file_path.stat()
                    refs.append({
                        "name": file_path.name,
                        "url": f"/selfie-refs/{file_path.name}",
                        "rel_path": f"selfie_refs/{file_path.name}",
                        "size": stat.st_size,
                        "mtime": int(stat.st_mtime),
                    })

            # 按修改时间倒序
            refs.sort(key=lambda x: x["mtime"], reverse=True)

            return web.json_response({
                "success": True,
                "refs": refs,
                "enabled": self.plugin.selfie_enabled,
            })

        except Exception as e:
            logger.error(f"[Portrait WebUI] 获取参考照列表失败: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def handle_upload_selfie_ref(self, request: web.Request) -> web.Response:
        """上传自拍参考照"""
        # 文件大小限制 (10MB)
        MAX_FILE_SIZE = 10 * 1024 * 1024

        try:
            self.selfie_refs_dir.mkdir(parents=True, exist_ok=True)

            reader = await request.multipart()
            uploaded = []

            async for field in reader:
                if field.name == "files":
                    filename = field.filename
                    if not filename:
                        continue

                    # 安全检查
                    if "/" in filename or "\\" in filename or ".." in filename:
                        continue

                    # 检查扩展名
                    ext = Path(filename).suffix.lower()
                    if ext not in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
                        continue

                    # 生成不可预测的唯一文件名（时间戳 + 安全随机数）
                    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
                    random_suffix = secrets.token_hex(16)  # 128-bit 随机数
                    safe_name = f"ref_{timestamp}_{random_suffix}{ext}"
                    file_path = self.selfie_refs_dir / safe_name

                    # 先读取全部内容到内存，然后异步写入
                    chunks = []
                    total_size = 0
                    while True:
                        chunk = await field.read_chunk()
                        if not chunk:
                            break
                        total_size += len(chunk)
                        if total_size > MAX_FILE_SIZE:
                            return web.json_response(
                                {"success": False, "error": f"文件过大，限制 {MAX_FILE_SIZE // 1024 // 1024}MB"},
                                status=413
                            )
                        chunks.append(chunk)

                    # 使用 asyncio.to_thread 非阻塞写入
                    content = b''.join(chunks)
                    await asyncio.to_thread(file_path.write_bytes, content)

                    uploaded.append({
                        "name": safe_name,
                        "url": f"/selfie-refs/{safe_name}",
                    })

            if not uploaded:
                return web.json_response(
                    {"success": False, "error": "未上传有效文件"}, status=400
                )

            logger.info(f"[Portrait WebUI] 已上传 {len(uploaded)} 张参考照")
            return web.json_response({
                "success": True,
                "uploaded": uploaded,
                "message": f"已上传 {len(uploaded)} 张参考照",
            })

        except Exception as e:
            logger.error(f"[Portrait WebUI] 上传参考照失败: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def handle_delete_selfie_ref(self, request: web.Request) -> web.Response:
        """删除自拍参考照"""
        try:
            name = request.match_info.get("name", "")
            if not name:
                return web.json_response(
                    {"success": False, "error": "文件名为空"}, status=400
                )

            # 安全检查
            if "/" in name or "\\" in name or ".." in name:
                return web.json_response(
                    {"success": False, "error": "非法文件名"}, status=400
                )

            file_path = self.selfie_refs_dir / name
            if not file_path.exists():
                return web.json_response(
                    {"success": False, "error": "文件不存在"}, status=404
                )

            # 确保文件在目录内
            try:
                file_path.relative_to(self.selfie_refs_dir)
            except ValueError:
                return web.json_response(
                    {"success": False, "error": "非法路径"}, status=400
                )

            await asyncio.to_thread(os.remove, file_path)

            logger.info(f"[Portrait WebUI] 已删除参考照: {name}")
            return web.json_response({"success": True, "deleted": name})

        except Exception as e:
            logger.error(f"[Portrait WebUI] 删除参考照失败: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    # === 安全的静态文件服务（需要 token 认证）===

    async def _serve_file(self, base_dir: Path, name: str) -> web.Response:
        """通用安全文件服务"""
        if not name:
            raise web.HTTPBadRequest(reason="文件名为空")

        # 安全检查：防止路径遍历
        if "/" in name or "\\" in name or ".." in name:
            raise web.HTTPBadRequest(reason="非法文件名")

        file_path = base_dir / name
        if not file_path.exists():
            raise web.HTTPNotFound(reason="文件不存在")

        # 确保文件在目标目录内
        try:
            file_path.relative_to(base_dir)
        except ValueError:
            raise web.HTTPBadRequest(reason="非法路径")

        # 使用 FileResponse 流式传输文件
        return web.FileResponse(file_path)

    async def handle_serve_image(self, request: web.Request) -> web.Response:
        """服务生成的图片（需要认证）"""
        name = request.match_info.get("name", "")
        return await self._serve_file(self.images_dir, name)

    async def handle_serve_thumbnail(self, request: web.Request) -> web.Response:
        """服务缩略图（需要认证）"""
        name = request.match_info.get("name", "")
        return await self._serve_file(self.thumbnails_dir, name)

    async def handle_serve_selfie_ref(self, request: web.Request) -> web.Response:
        """服务参考照（需要认证）"""
        name = request.match_info.get("name", "")
        return await self._serve_file(self.selfie_refs_dir, name)

    # === 视频画廊 API ===

    async def handle_list_videos(self, request: web.Request) -> web.Response:
        """列出生成的视频（在线URL）"""
        try:
            # 分页参数解析，格式错误返回 400
            try:
                page = max(1, int(request.query.get("page", 1)))
                page_size = min(100, max(1, int(request.query.get("page_size", 20))))
            except (ValueError, TypeError):
                return web.json_response(
                    {"success": False, "error": "分页参数格式错误"},
                    status=400
                )

            # 从 VideoManager 获取视频列表
            all_videos = self.plugin.video_manager.get_video_list()

            videos = []
            for v in all_videos:
                videos.append({
                    "id": v["id"],
                    "url": v["url"],  # 在线URL
                    "prompt": v.get("prompt", ""),
                    "created_at": v.get("created_at", 0),
                })

            total = len(videos)
            start = (page - 1) * page_size
            end = start + page_size
            paged_videos = videos[start:end]

            return web.json_response({
                "success": True,
                "videos": paged_videos,
                "total": total,
                "page": page,
                "page_size": page_size,
            })

        except Exception as e:
            logger.error(f"[Portrait WebUI] 获取视频列表失败: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def handle_delete_video(self, request: web.Request) -> web.Response:
        """删除视频（从元数据中删除）"""
        try:
            video_id = request.match_info.get("name", "")
            if not video_id:
                return web.json_response(
                    {"success": False, "error": "视频ID为空"}, status=400
                )

            # 从 VideoManager 删除
            if self.plugin.video_manager.delete_video(video_id):
                logger.info(f"[Portrait WebUI] 已删除视频: {video_id}")
                return web.json_response({"success": True, "deleted": video_id})
            else:
                return web.json_response(
                    {"success": False, "error": "视频不存在"}, status=404
                )

        except Exception as e:
            logger.error(f"[Portrait WebUI] 删除视频失败: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def handle_download_video(self, request: web.Request) -> web.Response:
        """下载视频（带 Content-Disposition 头）"""
        try:
            name = request.match_info.get("name", "")
            if not name:
                return web.json_response(
                    {"success": False, "error": "文件名为空"}, status=400
                )

            # 安全检查：防止路径遍历
            if "/" in name or "\\" in name or ".." in name:
                return web.json_response(
                    {"success": False, "error": "非法文件名"}, status=400
                )

            file_path = self.videos_dir / name
            if not file_path.exists():
                return web.json_response(
                    {"success": False, "error": "文件不存在"}, status=404
                )

            # 确保文件在 videos_dir 内
            try:
                file_path.relative_to(self.videos_dir)
            except ValueError:
                return web.json_response(
                    {"success": False, "error": "非法路径"}, status=400
                )

            # 使用 FileResponse 流式传输文件
            return web.FileResponse(
                file_path,
                headers={
                    "Content-Disposition": f'attachment; filename="{name}"',
                },
            )

        except Exception as e:
            logger.error(f"[Portrait WebUI] 下载视频失败: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def handle_get_video_presets(self, request: web.Request) -> web.Response:
        """获取视频预设词列表"""
        try:
            # 尝试从 AstrBot 配置文件读取最新值（确保同步）
            import json
            astrbot_config_path = self.plugin.data_dir.parent.parent / "config" / "astrbot_plugin_portrait_config.json"
            if astrbot_config_path.exists():
                try:
                    with open(astrbot_config_path, "r", encoding="utf-8-sig") as f:
                        astrbot_config = json.load(f)
                    grok_config = astrbot_config.get("grok_config", {}) or {}
                    presets = grok_config.get("video_presets", []) or []
                    # 同步到内存
                    if "grok_config" not in self.plugin.config:
                        self.plugin.config["grok_config"] = {}
                    self.plugin.config["grok_config"]["video_presets"] = presets
                except Exception as e:
                    logger.warning(f"[Portrait WebUI] 读取 AstrBot 配置失败: {e}")
                    grok_config = self.plugin.config.get("grok_config", {}) or {}
                    presets = grok_config.get("video_presets", []) or []
            else:
                grok_config = self.plugin.config.get("grok_config", {}) or {}
                presets = grok_config.get("video_presets", []) or []

            # 确保是字符串列表
            presets = [p for p in presets if isinstance(p, str)]
            return web.json_response({"success": True, "presets": presets})
        except Exception as e:
            logger.error(f"[Portrait WebUI] 获取视频预设词失败: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def handle_save_video_presets(self, request: web.Request) -> web.Response:
        """保存视频预设词列表"""
        try:
            import json
            data = await request.json()
            presets = data.get("presets", [])
            # 过滤空字符串，保留有效格式
            presets = [p.strip() for p in presets if isinstance(p, str) and p.strip() and ":" in p]

            # 保存到内存中的 grok_config
            if "grok_config" not in self.plugin.config:
                self.plugin.config["grok_config"] = {}
            self.plugin.config["grok_config"]["video_presets"] = presets

            # 同时直接更新 AstrBot 配置文件
            astrbot_config_path = self.plugin.data_dir.parent.parent / "config" / "astrbot_plugin_portrait_config.json"
            if astrbot_config_path.exists():
                try:
                    with open(astrbot_config_path, "r", encoding="utf-8-sig") as f:
                        astrbot_config = json.load(f)
                    if "grok_config" not in astrbot_config:
                        astrbot_config["grok_config"] = {}
                    astrbot_config["grok_config"]["video_presets"] = presets
                    with open(astrbot_config_path, "w", encoding="utf-8") as f:
                        json.dump(astrbot_config, f, ensure_ascii=False, indent=2)
                except Exception as e:
                    logger.warning(f"[Portrait WebUI] 更新 AstrBot 配置失败: {e}")

            # 保存到 webui_config.json
            self.plugin.save_config_to_disk()

            # 更新视频服务的预设词
            self.plugin.video_service.presets = dict(
                item.split(":", 1) for item in presets if ":" in item
            )

            return web.json_response({"success": True})
        except Exception as e:
            logger.error(f"[Portrait WebUI] 保存视频预设词失败: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def handle_get_edit_presets(self, request: web.Request) -> web.Response:
        """获取改图预设词列表"""
        try:
            import json
            astrbot_config_path = self.plugin.data_dir.parent.parent / "config" / "astrbot_plugin_portrait_config.json"
            if astrbot_config_path.exists():
                try:
                    with open(astrbot_config_path, "r", encoding="utf-8-sig") as f:
                        astrbot_config = json.load(f)
                    edit_config = astrbot_config.get("edit_config", {}) or {}
                    presets = edit_config.get("presets", []) or []
                    # 同步到内存
                    if "edit_config" not in self.plugin.config:
                        self.plugin.config["edit_config"] = {}
                    self.plugin.config["edit_config"]["presets"] = presets
                except Exception as e:
                    logger.warning(f"[Portrait WebUI] 读取 AstrBot 配置失败: {e}")
                    edit_config = self.plugin.config.get("edit_config", {}) or {}
                    presets = edit_config.get("presets", []) or []
            else:
                edit_config = self.plugin.config.get("edit_config", {}) or {}
                presets = edit_config.get("presets", []) or []

            # 确保是字符串列表
            presets = [p for p in presets if isinstance(p, str)]
            return web.json_response({"success": True, "presets": presets})
        except Exception as e:
            logger.error(f"[Portrait WebUI] 获取改图预设词失败: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def handle_save_edit_presets(self, request: web.Request) -> web.Response:
        """保存改图预设词列表"""
        try:
            import json
            data = await request.json()
            presets = data.get("presets", [])
            # 过滤空字符串，保留有效格式（预设名:提示词）
            presets = [p.strip() for p in presets if isinstance(p, str) and p.strip() and ":" in p]

            # 保存到内存中的 edit_config
            if "edit_config" not in self.plugin.config:
                self.plugin.config["edit_config"] = {}
            self.plugin.config["edit_config"]["presets"] = presets

            # 同时直接更新 AstrBot 配置文件
            astrbot_config_path = self.plugin.data_dir.parent.parent / "config" / "astrbot_plugin_portrait_config.json"
            if astrbot_config_path.exists():
                try:
                    with open(astrbot_config_path, "r", encoding="utf-8-sig") as f:
                        astrbot_config = json.load(f)
                    if "edit_config" not in astrbot_config:
                        astrbot_config["edit_config"] = {}
                    astrbot_config["edit_config"]["presets"] = presets
                    with open(astrbot_config_path, "w", encoding="utf-8") as f:
                        json.dump(astrbot_config, f, ensure_ascii=False, indent=2)
                except Exception as e:
                    logger.warning(f"[Portrait WebUI] 更新 AstrBot 配置失败: {e}")

            # 保存到 webui_config.json
            self.plugin.save_config_to_disk()

            # 更新插件的改图预设词
            self.plugin.edit_presets = presets

            return web.json_response({"success": True})
        except Exception as e:
            logger.error(f"[Portrait WebUI] 保存改图预设词失败: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def handle_serve_video(self, request: web.Request) -> web.Response:
        """服务视频文件（需要认证）"""
        name = request.match_info.get("name", "")
        if not name:
            raise web.HTTPBadRequest(reason="文件名为空")

        # 安全检查：防止路径遍历
        if "/" in name or "\\" in name or ".." in name:
            raise web.HTTPBadRequest(reason="非法文件名")

        file_path = self.videos_dir / name
        if not file_path.exists():
            raise web.HTTPNotFound(reason="文件不存在")

        # 确保文件在目标目录内
        try:
            file_path.relative_to(self.videos_dir)
        except ValueError:
            raise web.HTTPBadRequest(reason="非法路径")

        # 使用 FileResponse 流式传输文件
        return web.FileResponse(file_path)

    # === 缓存清理 API ===

    async def handle_cache_cleanup(self, request: web.Request) -> web.Response:
        """手动清理缓存"""
        try:
            data = await request.json()
            # 参数边界校验：限制合理范围，格式错误返回 400
            try:
                max_storage_mb = min(10000, max(0, int(data.get("max_storage_mb", 500))))
                max_count = min(10000, max(0, int(data.get("max_count", 100))))
            except (ValueError, TypeError):
                return web.json_response(
                    {"success": False, "error": "参数格式错误"},
                    status=400
                )

            deleted_count = 0
            freed_bytes = 0
            deleted_names: list[str] = []

            # 获取收藏快照（避免扫描时反复读取）
            favorites = await self.imgr.get_favorites_snapshot_async()

            # 获取所有图片文件及其信息
            def scan_images():
                results = []
                for file_path in self.images_dir.iterdir():
                    if file_path.is_file() and file_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
                        stat = file_path.stat()
                        # 使用快照判断收藏状态
                        is_favorite = file_path.name in favorites
                        results.append({
                            "path": file_path,
                            "name": file_path.name,
                            "size": stat.st_size,
                            "mtime": stat.st_mtime,
                            "favorite": is_favorite,
                        })
                return results

            all_images = await asyncio.to_thread(scan_images)

            # 按修改时间排序（旧的在前）
            all_images.sort(key=lambda x: x["mtime"])

            # 筛选出非收藏的图片
            non_favorite = [img for img in all_images if not img["favorite"]]
            total_size = sum(img["size"] for img in all_images)
            total_count = len(all_images)

            to_delete = []

            # 按数量限制
            if max_count > 0 and total_count > max_count:
                excess_count = total_count - max_count
                for img in non_favorite:
                    if excess_count <= 0:
                        break
                    to_delete.append(img)
                    excess_count -= 1

            # 按存储限制
            if max_storage_mb > 0:
                max_bytes = max_storage_mb * 1024 * 1024
                current_size = total_size - sum(img["size"] for img in to_delete)
                for img in non_favorite:
                    if img in to_delete:
                        continue
                    if current_size <= max_bytes:
                        break
                    to_delete.append(img)
                    current_size -= img["size"]

            # 执行删除（只删文件，不在循环里写元数据）
            def do_delete():
                nonlocal deleted_count, freed_bytes, deleted_names
                for img in to_delete:
                    try:
                        img["path"].unlink()
                        # 删除缩略图
                        thumb_path = self.thumbnails_dir / img["name"]
                        if thumb_path.exists():
                            thumb_path.unlink()
                        deleted_names.append(img["name"])
                        deleted_count += 1
                        freed_bytes += img["size"]
                    except Exception:
                        pass

            await asyncio.to_thread(do_delete)

            # 批量删除元数据（单次落盘）
            if deleted_names:
                await self.imgr.remove_metadata_batch_async(deleted_names)
                # 清理缓存
                self._images_cache = None
                self._images_cache_time = 0

            return web.json_response({
                "success": True,
                "deleted_count": deleted_count,
                "freed_bytes": freed_bytes,
            })

        except Exception as e:
            logger.error(f"[Portrait WebUI] 缓存清理失败: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

