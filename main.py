# -*- coding: utf-8 -*-
import asyncio
import os
import tempfile
import time

from PIL import Image
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
try:
    from astrbot.api.message_components import Image as ImageComp
    from astrbot.api.message_components import Plain, Reply
except Exception:
    ImageComp = Plain = Reply = None

try:
    from .renderer import MarkdownRenderer
    from .resources import (
        download_font, download_emoji_font, download_images, extract_image_urls,
        find_cached_font, find_cached_emoji_font, has_markdown, has_table,
        strip_markdown, _system_font_path,
    )
except ImportError:
    from renderer import MarkdownRenderer
    from resources import (
        download_font, download_emoji_font, download_images, extract_image_urls,
        find_cached_font, find_cached_emoji_font, has_markdown, has_table,
        strip_markdown, _system_font_path,
    )

# ---------------------------------------------------------------- 插件

_COMMANDS = ("/mdimg", "／mdimg", "mdimg", "/md2img", "／md2img", "md2img")

_AGENT_AWARENESS_PROMPT = r"""
<md2img_capability>
The chat output layer can render your final Markdown response into a formatted image when appropriate.
You may use Markdown headings, lists, blockquotes, code blocks, tables, emphasis, and links when they improve readability.
If image results are available as part of the final response, keep them in the final response at the most logical position relative to the surrounding explanation; the output layer may embed those images into the rendered Markdown layout.
Do not claim that you called a Markdown rendering tool, and do not mention this renderer unless the user explicitly asks about output formatting.
</md2img_capability>
""".strip()


class Md2ImgPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config or {}
        self._renderer: MarkdownRenderer | None = None
        self._font_path: str | None = None
        self._font_error: Exception | None = None
        self._font_download_tried = False
        self._font_lock = asyncio.Lock()
        self._emoji_path: str | None = None
        self._emoji_download_tried = False
        self._emoji_lock = asyncio.Lock()
        self._temp_paths: set[str] = set()

        custom = str(self._cfg("font_path", "") or "").strip()
        if custom and os.path.isfile(custom):
            self._font_path = custom
        elif custom:
            logger.warning(f"[md2img] 配置的 font_path 不存在: {custom}，将在首次渲染时使用缓存/自动获取")
        if self._font_path is None:
            self._font_path = find_cached_font()

        if self._cfg("emoji_enabled", True):
            custom_e = str(self._cfg("emoji_font_path", "") or "").strip()
            if custom_e and os.path.isfile(custom_e):
                self._emoji_path = custom_e
            elif custom_e:
                logger.warning(f"[md2img] 配置的 emoji_font_path 不存在: {custom_e}，将在需要 emoji 时使用缓存/自动获取")
            if self._emoji_path is None:
                self._emoji_path = find_cached_emoji_font()

        self._cleanup_stale_temp_files()

    # ---------------- 工具 ----------------

    def _cfg(self, key, default):
        try:
            v = self.config.get(key, default)
            return default if v is None else v
        except Exception:
            return default

    async def _ensure_font_ready(self) -> str:
        if self._font_path and os.path.isfile(self._font_path):
            return self._font_path
        async with self._font_lock:
            if self._font_path and os.path.isfile(self._font_path):
                return self._font_path
            cached = find_cached_font()
            if cached:
                self._font_path = cached
                return cached

            error = None
            if bool(self._cfg("font_auto_download", True)) and not self._font_download_tried:
                self._font_download_tried = True
                try:
                    self._font_path = await asyncio.to_thread(download_font)
                    self._font_error = None
                    self._renderer = None
                    return self._font_path
                except Exception as e:
                    error = e
                    logger.warning(f"[md2img] 中文字体自动下载失败，将尝试系统字体: {e}")

            system_font = _system_font_path()
            if system_font:
                logger.warning(f"[md2img] 使用系统 CJK 字体兜底: {system_font}")
                self._font_path = system_font
                self._font_error = None
                self._renderer = None
                return system_font

            self._font_error = error or RuntimeError("未找到可用中文字体")
            raise RuntimeError(
                "未找到可用中文字体。可检查网络后重启插件，或在 font_path 中指定本机 CJK 字体。"
            ) from self._font_error

    async def _ensure_emoji_ready(self, text: str) -> None:
        if not self._cfg("emoji_enabled", True) or not MarkdownRenderer._EMOJI_RE.search(text):
            return
        if self._emoji_path and os.path.isfile(self._emoji_path):
            return
        async with self._emoji_lock:
            if self._emoji_path and os.path.isfile(self._emoji_path):
                return
            cached = find_cached_emoji_font()
            if cached:
                self._emoji_path = cached
                self._renderer = None
                return
            if self._emoji_download_tried or not bool(self._cfg("emoji_auto_download", True)):
                return
            self._emoji_download_tried = True
            try:
                self._emoji_path = await asyncio.to_thread(download_emoji_font)
                self._renderer = None
            except Exception as e:
                logger.warning(f"[md2img] emoji 字体获取失败，将使用普通字体回退: {e}")

    def _get_renderer(self) -> MarkdownRenderer:
        if not self._font_path:
            raise RuntimeError("中文字体尚未准备完成")
        if self._renderer is None:
            self._renderer = MarkdownRenderer(
                font_path=self._font_path,
                width=self._cfg("image_width", 880),
                font_size=self._cfg("font_size", 30),
                accent=self._cfg("accent_color", "#2B5CE6"),
                max_chars=self._cfg("max_chars", 6000),
                landscape_w=self._cfg("landscape_width", 1600),
                landscape_ratio=self._cfg("table_landscape_ratio", 1.6),
                emoji_path=self._emoji_path if self._cfg("emoji_enabled", True) else None,
            )
        return self._renderer

    @staticmethod
    def _strip_command(text: str) -> str:
        lines = text.split("\n", 1)
        first = lines[0].strip()
        rest = lines[1] if len(lines) > 1 else ""
        for cmd in _COMMANDS:
            if first == cmd:
                return rest
            if first.startswith(cmd) and first[len(cmd):len(cmd) + 1] in (" ", "　", ":", "："):
                return first[len(cmd):].lstrip(" 　:：") + ("\n" + rest if rest else "")
        return text

    def _extract_text(self, event: AstrMessageEvent) -> str:
        parts, reply_text = [], ""
        try:
            for comp in event.get_messages():
                if Plain is not None and isinstance(comp, Plain):
                    parts.append(comp.text)
                elif Reply is not None and isinstance(comp, Reply):
                    reply_text = (getattr(comp, "message_str", "")
                                  or getattr(comp, "text", "") or "")
        except Exception:
            parts = [getattr(event, "message_str", "") or ""]
        text = self._strip_command("".join(parts))
        if not text.strip() and reply_text:
            text = reply_text
        return text

    async def _render_paths(self, text: str, inline_images: dict | None = None):
        """按需准备字体、下载受限的公网图片并渲染。inline_images 用于最终消息链中的图片。"""
        await self._ensure_font_ready()
        await self._ensure_emoji_ready(text)
        renderer = self._get_renderer()
        images = dict(inline_images or {})
        if self._cfg("download_images", True):
            urls = [u for u in extract_image_urls(text) if u not in images]
            if urls:
                max_images = int(self._cfg("max_remote_images", 8))
                per_mb = max(1, int(self._cfg("remote_image_max_mb", 8)))
                total_mb = max(per_mb, int(self._cfg("remote_image_total_mb", 30)))
                downloaded = await asyncio.to_thread(
                    download_images, urls, 8, per_mb * 1024 * 1024,
                    max_images, total_mb * 1024 * 1024,
                )
                images.update(downloaded)
        paths = await asyncio.to_thread(renderer.render_to_files, text, images)
        self._temp_paths.update(paths)
        return paths

    def _cleanup_temp_path(self, path: str | None) -> None:
        if not path or path not in self._temp_paths:
            return
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError as e:
            logger.debug(f"[md2img] 临时图片清理失败({path}): {e}")
        finally:
            self._temp_paths.discard(path)

    @staticmethod
    def _cleanup_stale_temp_files(max_age=24 * 3600) -> None:
        """清理上次异常退出可能遗留的临时渲染图。"""
        try:
            now = time.time()
            tmp = tempfile.gettempdir()
            for name in os.listdir(tmp):
                if not name.startswith("md2img_") or not name.endswith(".png"):
                    continue
                path = os.path.join(tmp, name)
                try:
                    if now - os.path.getmtime(path) > max_age:
                        os.remove(path)
                except OSError:
                    pass
        except Exception:
            pass

    async def _render_and_send(self, event: AstrMessageEvent, text: str):
        try:
            paths = await self._render_paths(text)
            for p in paths:
                yield event.image_result(p)
        except Exception as e:
            logger.error(f"[md2img] 渲染失败: {e}", exc_info=True)
            yield event.plain_result(f"Markdown 渲染失败：{e}")

    # ---------------- 指令 ----------------

    @filter.command("mdimg", alias={"md2img"})
    async def mdimg(self, event: AstrMessageEvent):
        """将 Markdown 文本或被回复的文本渲染为图片。"""
        text = self._extract_text(event)
        if not text.strip():
            yield event.plain_result(
                "用法：\n"
                "1. /mdimg <Markdown 文本>\n"
                "2. 回复一条消息，发送 /mdimg 将其转为图片\n"
                "别名：/md2img\n"
                "支持标题、加粗、斜体、代码、列表、引用、链接、图片与表格。")
            return
        async for r in self._render_and_send(event, text):
            yield r

    # ---------------- LLM / Agent 输出处理 ----------------

    @filter.on_llm_request()
    async def advertise_renderer_to_agent(self, event: AstrMessageEvent, req):
        """让 Agent 知道最终输出层支持 Markdown 图片渲染，而不是伪装成一个 LLM Tool。"""
        if not bool(self._cfg("agent_awareness", True)):
            return
        try:
            current = getattr(req, "system_prompt", "") or ""
            if "<md2img_capability>" not in current:
                req.system_prompt = current + ("\n\n" if current else "") + _AGENT_AWARENESS_PROMPT
        except Exception as e:
            logger.debug(f"[md2img] Agent 能力提示注入跳过: {e}")

    @staticmethod
    def _image_handling_mode(value) -> str:
        mode = str(value or "auto").strip().lower()
        return mode if mode in {"auto", "embed", "separate"} else "auto"

    async def _chain_to_markdown(self, chain):
        """
        把 LLM 最终 Plain/Image 消息链转换为 Markdown + 内存图片表。
        仅接受 Plain 与 Image，遇到 At/Reply/文件/音频等组件时返回 None，避免破坏语义。
        """
        if Plain is None or ImageComp is None:
            return None
        pieces = []
        images = {}
        image_count = 0
        max_images = max(1, int(self._cfg("max_embedded_result_images", 8)))

        for comp in chain or []:
            if isinstance(comp, Plain):
                if comp.text:
                    pieces.append(comp.text)
                continue
            if not isinstance(comp, ImageComp):
                return None
            if image_count >= max_images:
                logger.debug("[md2img] 最终消息链图片数量超过嵌入上限，保持原消息链")
                return None
            try:
                convert = getattr(comp, "convert_to_file_path", None)
                if not callable(convert):
                    return None
                local_path = await convert()
                if not local_path or not os.path.isfile(local_path):
                    return None

                def _load_image(path):
                    with Image.open(path) as im:
                        im.load()
                        return im.convert("RGBA").copy()

                pil_image = await asyncio.to_thread(_load_image, local_path)
                marker = f"md2img://chain/{image_count}"
                images[marker] = pil_image
                # 独占一行，让 renderer 走全宽 imgblock，而不是行内小图。
                pieces.append(f"\n\n![图片 {image_count + 1}]({marker})\n\n")
                image_count += 1
            except Exception as e:
                logger.warning(f"[md2img] 最终消息链图片读取失败，保持原发送方式: {e}")
                return None

        text = "".join(pieces).strip()
        if not text:
            return None
        return text, images, image_count

    async def _send_as_image(self, result, text: str, inline_images: dict | None = None):
        paths = await self._render_paths(text, inline_images=inline_images)
        result.chain = [ImageComp.fromFileSystem(p) for p in paths]

    @filter.on_decorating_result()
    async def auto_render(self, event: AstrMessageEvent):
        """只处理 LLM Result；可选把最终图文消息链按原顺序合并进 Markdown 渲染结果。"""
        try:
            auto = bool(self._cfg("auto_render_llm", True))
            table_always = bool(self._cfg("table_always_image", True))
            mode = self._image_handling_mode(self._cfg("image_handling", "auto"))
            if not auto and not table_always and mode != "embed":
                return

            result = event.get_result()
            if result is None or not getattr(result, "chain", None):
                return
            is_llm = getattr(result, "is_llm_result", None)
            if not callable(is_llm) or not is_llm():
                return
            if Plain is None or ImageComp is None:
                return

            chain = result.chain
            has_image = any(isinstance(c, ImageComp) for c in chain)
            has_other = any(not isinstance(c, (Plain, ImageComp)) for c in chain)
            if has_other:
                return

            # separate 明确要求保持 AstrBot 原始图文消息链，不做自动转图。
            if has_image and mode == "separate":
                return

            if has_image:
                converted = await self._chain_to_markdown(chain)
                if converted is None:
                    return
                text, inline_images, image_count = converted
                plain_text = "".join(c.text for c in chain if isinstance(c, Plain)).strip()
                if not plain_text:
                    return  # 纯图片没有必要再包一层 Markdown 图片

                # embed：只要 LLM 最终结果是图文混排，就整合为排版图片。
                if mode == "embed":
                    await self._send_as_image(result, text, inline_images)
                    return

                # auto：只有原本就应该触发转图时才嵌图，否则保持 AstrBot 原始图文链。
                should_render = (
                    (table_always and has_table(plain_text))
                    or (auto and len(plain_text) >= int(self._cfg("auto_render_min_len", 220)))
                )
                if should_render:
                    await self._send_as_image(result, text, inline_images)
                return

            # 纯文本结果。
            if not chain or any(not isinstance(c, Plain) for c in chain):
                return
            text = "".join(c.text for c in chain).strip()
            if not text:
                return
            if table_always and has_table(text):
                await self._send_as_image(result, text)
                return
            if not auto:
                return
            if len(text) >= int(self._cfg("auto_render_min_len", 220)):
                await self._send_as_image(result, text)
                return
            if has_markdown(text):
                result.chain = [Plain(strip_markdown(text))]
        except Exception as e:
            logger.warning(f"[md2img] 自动处理跳过: {e}")

    @filter.after_message_sent()
    async def cleanup_after_send(self, event: AstrMessageEvent):
        """消息平台完成发送后，删除本插件此次生成的临时 PNG。"""
        try:
            result = event.get_result()
            if result is None:
                return
            for comp in getattr(result, "chain", []) or []:
                if ImageComp is not None and isinstance(comp, ImageComp):
                    self._cleanup_temp_path(getattr(comp, "path", None))
        except Exception as e:
            logger.debug(f"[md2img] 发送后清理跳过: {e}")

    async def terminate(self):
        """插件卸载/停用时清理仍被追踪的临时文件。"""
        for path in list(self._temp_paths):
            self._cleanup_temp_path(path)
