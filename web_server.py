"""
Portrait Plugin WebUI Server
基于 aiohttp 的 Web 管理界面后端
"""

import json
import os
from pathlib import Path
from datetime import datetime

from aiohttp import web
from astrbot.api import logger

from .core.image_manager import ImageManager


class WebServer:
    """Portrait 插件 Web 管理服务器"""

    def __init__(self, plugin, host: str = "127.0.0.1", port: int = 8088, token: str = ""):
        self.plugin = plugin
        self.host = host
        self.port = port
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

        self._setup_routes()

    @web.middleware
    async def _auth_middleware(self, request: web.Request, handler):
        """Token 认证中间件"""
        # 如果未设置 token，跳过认证（任何请求都放行）
        if not self.token:
            return await handler(request)

        # 静态资源不需要认证（index.html、CSS、JS、图片等）
        if request.path in ["/", "/index.html"]:
            return await handler(request)
        if request.path.startswith(("/static/", "/web/", "/images/", "/thumbnails/", "/selfie-refs/")):
            return await handler(request)

        # 从 query 参数或 header 获取 token
        req_token = request.query.get("token") or request.headers.get("X-Token", "")

        if req_token != self.token:
            return web.json_response(
                {"success": False, "error": "未授权访问，请提供正确的 token"},
                status=401
            )

        return await handler(request)

    def _setup_routes(self):
        """设置路由"""
        # API 路由
        self.app.router.add_get("/api/config", self.handle_get_config)
        self.app.router.add_post("/api/config", self.handle_save_config)
        self.app.router.add_get("/api/dynamic-config", self.handle_get_dynamic_config)
        self.app.router.add_post("/api/dynamic-config", self.handle_save_dynamic_config)
        self.app.router.add_get("/api/images", self.handle_list_images)
        self.app.router.add_delete("/api/images/{name}", self.handle_delete_image)
        self.app.router.add_post("/api/images/{name}/favorite", self.handle_toggle_favorite)
        self.app.router.add_get("/api/health", self.handle_health)

        # 自拍参考照 API
        self.app.router.add_get("/api/selfie-refs", self.handle_list_selfie_refs)
        self.app.router.add_post("/api/selfie-refs", self.handle_upload_selfie_ref)
        self.app.router.add_delete("/api/selfie-refs/{name}", self.handle_delete_selfie_ref)

        # 前端页面
        self.app.router.add_get("/", self.handle_index)
        self.app.router.add_get("/index.html", self.handle_index)

        # 静态资源
        if self.static_dir.exists():
            self.app.router.add_static("/web", self.static_dir, show_index=False)

        # 图片静态服务（确保目录存在）
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.app.router.add_static("/images", str(self.images_dir), show_index=False)

        # 缩略图静态服务
        self.thumbnails_dir.mkdir(parents=True, exist_ok=True)
        self.app.router.add_static("/thumbnails", str(self.thumbnails_dir), show_index=False)

        # 自拍参考照静态服务
        self.selfie_refs_dir.mkdir(parents=True, exist_ok=True)
        self.app.router.add_static("/selfie-refs", str(self.selfie_refs_dir), show_index=False)

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
                logger.warning(
                    f"[Portrait WebUI] 安全警告: 监听地址为 {self.host}，但未设置访问令牌！"
                    "API 将完全暴露，建议设置 token 或改为 127.0.0.1"
                )

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
        index_file = self.static_dir / "index.html"
        if not index_file.exists():
            return web.Response(
                text="<h1>Portrait WebUI</h1><p>index.html not found</p>",
                content_type="text/html",
                status=404,
            )
        try:
            content = index_file.read_text(encoding="utf-8")
            return web.Response(text=content, content_type="text/html")
        except Exception as e:
            logger.error(f"[Portrait WebUI] 读取 index.html 失败: {e}")
            return web.Response(text=f"Error: {e}", status=500)

    async def handle_health(self, request: web.Request) -> web.Response:
        """健康检查"""
        return web.json_response({"status": "ok", "service": "portrait-webui"})

    async def handle_get_config(self, request: web.Request) -> web.Response:
        """获取配置"""
        try:
            # 提取可编辑的配置字段
            editable_fields = [
                "char_identity",
                "injection_rounds",
                "proxy",
                "draw_provider",
                "enable_fallback",
            ]

            config = {}
            for field in editable_fields:
                if field in self.plugin.config:
                    config[field] = self.plugin.config[field]
                else:
                    config[field] = None

            # gitee_config 单独处理，返回真实密钥
            gitee_conf = self.plugin.config.get("gitee_config", {}) or {}
            config["gitee_config"] = {
                "api_keys": gitee_conf.get("api_keys", []) or [],
                "base_url": gitee_conf.get("base_url", "https://ai.gitee.com/v1"),
                "model": gitee_conf.get("model", "z-image-turbo"),
                "size": gitee_conf.get("size", "1024x1024"),
                "num_inference_steps": gitee_conf.get("num_inference_steps", 9),
                "negative_prompt": gitee_conf.get("negative_prompt", ""),
                "timeout": gitee_conf.get("timeout", 300),
                "max_retries": gitee_conf.get("max_retries", 2),
            }

            # gemini_config 单独处理，返回真实密钥
            gemini_conf = self.plugin.config.get("gemini_config", {}) or {}
            config["gemini_config"] = {
                "api_key": gemini_conf.get("api_key", "") or "",
                "base_url": gemini_conf.get("base_url", "https://generativelanguage.googleapis.com"),
                "model": gemini_conf.get("model", "gemini-2.0-flash-exp-image-generation"),
                "image_size": gemini_conf.get("image_size", "1K"),
                "timeout": gemini_conf.get("timeout", 120),
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
                "proxy",
                "gitee_config",
                "gemini_config",
                "draw_provider",
                "enable_fallback",
                "environments",
                "cameras",
                "selfie_config",
            }

            # 更新配置
            updated_fields = []
            for field, value in new_config.items():
                if field in editable_fields:
                    self.plugin.config[field] = value
                    updated_fields.append(field)

            # 热更新：重新组装 full_prompt
            self._reload_plugin_resources()

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

            self.plugin.update_dynamic_config(new_config)
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

            # 更新提供商配置
            if "draw_provider" in config:
                self.plugin.draw_provider = config.get("draw_provider", "gitee") or "gitee"
            if "enable_fallback" in config:
                self.plugin.enable_fallback = config.get("enable_fallback", True)

            # 更新自拍参考照配置
            selfie_conf = config.get("selfie_config", {}) or {}
            if selfie_conf:
                self.plugin.selfie_enabled = selfie_conf.get("enabled", False)
                self.plugin.selfie_reference_images = selfie_conf.get("reference_images", []) or []

            logger.debug("[Portrait WebUI] 插件资源已热更新")

        except Exception as e:
            logger.error(f"[Portrait WebUI] 热更新失败: {e}")

    async def handle_list_images(self, request: web.Request) -> web.Response:
        """列出生成的图片"""
        try:
            if not self.images_dir.exists():
                return web.json_response({"success": True, "images": [], "total": 0})

            page = int(request.query.get("page", 1))
            page_size = int(request.query.get("size", 50))
            filter_favorites = request.query.get("favorites", "").lower() == "true"

            images = []
            allowed_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

            for file_path in self.images_dir.iterdir():
                if file_path.is_file() and file_path.suffix.lower() in allowed_exts:
                    stat = file_path.stat()
                    filename = file_path.name

                    # 获取元数据
                    metadata = self.imgr.get_metadata(filename)
                    is_favorite = self.imgr.is_favorite(filename)

                    # 如果筛选收藏，跳过非收藏
                    if filter_favorites and not is_favorite:
                        continue

                    # 生成缩略图路径
                    thumb_path = self.thumbnails_dir / filename
                    thumb_url = f"/thumbnails/{filename}" if thumb_path.exists() else f"/images/{filename}"

                    # 如果缩略图不存在，尝试生成
                    if not thumb_path.exists():
                        await self._generate_thumbnail(file_path, thumb_path)
                        if thumb_path.exists():
                            thumb_url = f"/thumbnails/{filename}"

                    images.append({
                        "name": filename,
                        "url": f"/images/{filename}",
                        "thumbnail": thumb_url,
                        "size": stat.st_size,
                        "ctime": int(stat.st_ctime),
                        "mtime": int(stat.st_mtime),
                        "prompt": metadata.get("prompt", "") if metadata else "",
                        "favorite": is_favorite,
                    })

            # 按修改时间倒序
            images.sort(key=lambda x: x["mtime"], reverse=True)

            # 分页
            total = len(images)
            start = (page - 1) * page_size
            end = start + page_size
            paged_images = images[start:end]

            return web.json_response({
                "success": True,
                "images": paged_images,
                "total": total,
                "page": page,
                "page_size": page_size,
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

            os.remove(file_path)
            # 删除缩略图
            thumb_path = self.thumbnails_dir / name
            if thumb_path.exists():
                os.remove(thumb_path)
            # 清理元数据
            self.imgr.remove_metadata(name)
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

            # 切换收藏状态
            new_state = self.imgr.toggle_favorite(name)
            logger.info(f"[Portrait WebUI] 图片收藏状态已{'添加' if new_state else '取消'}: {name}")

            return web.json_response({
                "success": True,
                "name": name,
                "favorite": new_state,
            })

        except Exception as e:
            logger.error(f"[Portrait WebUI] 切换收藏状态失败: {e}")
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

                    # 生成唯一文件名
                    timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
                    safe_name = f"ref_{timestamp}{ext}"
                    file_path = self.selfie_refs_dir / safe_name

                    # 保存文件
                    with open(file_path, "wb") as f:
                        while True:
                            chunk = await field.read_chunk()
                            if not chunk:
                                break
                            f.write(chunk)

                    uploaded.append({
                        "name": safe_name,
                        "url": f"/selfie-refs/{safe_name}",
                    })

            if not uploaded:
                return web.json_response(
                    {"success": False, "error": "未上传有效文件"}, status=400
                )

            # 更新配置中的参考照列表
            self._update_selfie_refs_config()

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

            os.remove(file_path)

            # 更新配置中的参考照列表
            self._update_selfie_refs_config()

            logger.info(f"[Portrait WebUI] 已删除参考照: {name}")
            return web.json_response({"success": True, "deleted": name})

        except Exception as e:
            logger.error(f"[Portrait WebUI] 删除参考照失败: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    def _update_selfie_refs_config(self):
        """更新配置中的参考照列表"""
        try:
            if not self.selfie_refs_dir.exists():
                ref_list = []
            else:
                allowed_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
                ref_list = [
                    f"selfie_refs/{f.name}"
                    for f in self.selfie_refs_dir.iterdir()
                    if f.is_file() and f.suffix.lower() in allowed_exts
                ]

            # 更新插件配置
            if "selfie_config" not in self.plugin.config:
                self.plugin.config["selfie_config"] = {}
            self.plugin.config["selfie_config"]["reference_images"] = ref_list

            # 更新插件实例变量
            self.plugin.selfie_reference_images = ref_list

            logger.debug(f"[Portrait WebUI] 参考照列表已更新: {len(ref_list)} 张")

        except Exception as e:
            logger.error(f"[Portrait WebUI] 更新参考照配置失败: {e}")
