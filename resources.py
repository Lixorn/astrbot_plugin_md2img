# -*- coding: utf-8 -*-
import io
import ipaddress
import os
import re
import socket
import threading
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from PIL import Image
from astrbot.api import logger
try:
    from astrbot.api.star import StarTools
except Exception:
    StarTools = None

PLUGIN_NAME = "astrbot_plugin_md2img"
FONT_FILE_NAME = "NotoSansSC-VF.ttf"
FONT_URLS = [
    "https://cdn.jsdelivr.net/gh/notofonts/noto-cjk@Sans2.004/Sans/Variable/TTF/Subset/NotoSansSC-VF.ttf",
    "https://raw.githubusercontent.com/notofonts/noto-cjk/Sans2.004/Sans/Variable/TTF/Subset/NotoSansSC-VF.ttf",
]
SYSTEM_FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
]
EMOJI_FONT_FILE_NAME = "NotoColorEmoji.ttf"
EMOJI_FONT_URLS = [
    "https://cdn.jsdelivr.net/gh/googlefonts/noto-emoji@v2.051/fonts/NotoColorEmoji.ttf",
    "https://raw.githubusercontent.com/googlefonts/noto-emoji/v2.051/fonts/NotoColorEmoji.ttf",
]

# ---------------------------------------------------------------- 字体管理

def _data_dir() -> str:
    try:
        if StarTools is not None:
            d = StarTools.get_data_dir(PLUGIN_NAME)
            os.makedirs(d, exist_ok=True)
            return d
    except Exception:
        pass
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    os.makedirs(d, exist_ok=True)
    return d


def _cached_font_path(file_name: str, min_bytes: int) -> str | None:
    local = os.path.join(_data_dir(), file_name)
    if os.path.exists(local) and os.path.getsize(local) > min_bytes:
        return local
    return None


def _system_font_path() -> str | None:
    for p in SYSTEM_FONT_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def _download_file_to_cache(urls, file_name: str, min_bytes: int, label: str) -> str:
    """从固定上游地址下载资源到插件数据目录，使用临时文件 + 原子替换。"""
    local = os.path.join(_data_dir(), file_name)
    part = local + ".part"
    for url in urls:
        try:
            logger.info(f"[md2img] 正在下载{label}: {url}")
            if os.path.exists(part):
                os.remove(part)
            req = urllib.request.Request(url, headers={"User-Agent": "AstrBot-md2img/1.0"})
            with urllib.request.urlopen(req, timeout=45) as resp, open(part, "wb") as f:
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
            if os.path.getsize(part) <= min_bytes:
                raise RuntimeError(f"下载文件异常，大小仅 {os.path.getsize(part)} bytes")
            os.replace(part, local)
            logger.info(f"[md2img] {label}下载完成")
            return local
        except Exception as e:
            logger.warning(f"[md2img] {label}下载失败({url}): {e}")
            try:
                if os.path.exists(part):
                    os.remove(part)
            except OSError:
                pass
    raise RuntimeError(f"{label}自动下载失败")


def find_cached_font() -> str | None:
    return _cached_font_path(FONT_FILE_NAME, 1024 * 1024)


def download_font() -> str:
    return _download_file_to_cache(FONT_URLS, FONT_FILE_NAME, 1024 * 1024, "Noto Sans SC 字体")


def find_cached_emoji_font() -> str | None:
    return _cached_font_path(EMOJI_FONT_FILE_NAME, 512 * 1024)


def download_emoji_font() -> str:
    return _download_file_to_cache(EMOJI_FONT_URLS, EMOJI_FONT_FILE_NAME, 512 * 1024, "Noto Color Emoji 字体")


# ---------------------------------------------------------------- 外链图片下载

_IMG_MD_RE = re.compile(r"!\[[^\]\n]*\]\((https?://[^)\s]+)\)")
_IMG_BARE_RE = re.compile(
    r"(?m)^\s*(https?://\S+?\.(?:png|jpe?g|gif|webp|bmp)(?:\?\S*)?)\s*$", re.I)


def extract_image_urls(text: str):
    """提取文中的图片链接：markdown 图片语法 + 独占一行的裸图片 URL，去重保序。"""
    return list(dict.fromkeys(_IMG_MD_RE.findall(text) + _IMG_BARE_RE.findall(text)))


def _validate_public_url(url: str) -> None:
    """拒绝本机、内网、链路本地等地址，降低外链图片造成 SSRF 的风险。"""
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValueError("仅允许 http/https 图片链接")
    if p.username or p.password:
        raise ValueError("不允许带认证信息的图片 URL")
    host = p.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost"):
        raise ValueError("不允许访问 localhost")
    try:
        infos = socket.getaddrinfo(host, p.port or (443 if p.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise ValueError(f"域名解析失败: {e}") from e
    if not infos:
        raise ValueError("域名没有可用地址")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise ValueError(f"拒绝访问非公网地址: {ip}")


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urljoin(req.full_url, newurl)
        _validate_public_url(target)
        return super().redirect_request(req, fp, code, msg, headers, target)


def _safe_urlopen(url: str, timeout: float):
    _validate_public_url(url)
    opener = urllib.request.build_opener(_SafeRedirectHandler())
    req = urllib.request.Request(url, headers={"User-Agent": "AstrBot-md2img/1.0"})
    resp = opener.open(req, timeout=timeout)
    _validate_public_url(resp.geturl())
    return resp


def download_images(urls, timeout=8, max_bytes=8 * 1024 * 1024,
                    max_images=8, total_max_bytes=30 * 1024 * 1024):
    """安全地并发下载少量公网图片，返回 {url: PIL.Image(RGBA)}。"""
    out = {}
    urls = list(urls)[:max(0, int(max_images))]
    total = [0]
    total_lock = threading.Lock()

    def one(u):
        try:
            with _safe_urlopen(u, timeout=timeout) as resp:
                data = resp.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise ValueError(f"单张图片超过 {max_bytes // (1024 * 1024)} MB 限制")
            with total_lock:
                if total[0] + len(data) > total_max_bytes:
                    raise ValueError("本次消息的外链图片总大小超过限制")
                total[0] += len(data)
            im = Image.open(io.BytesIO(data))
            # 在完整解码前先检查尺寸，降低超大图片带来的内存压力。
            if im.width * im.height > 20_000_000:
                raise ValueError("图片像素总量过大")
            im.load()
            if im.mode != "RGBA":
                im = im.convert("RGBA")
            if max(im.size) > 2400:
                sc = 2400 / max(im.size)
                im = im.resize((round(im.size[0] * sc), round(im.size[1] * sc)),
                               Image.LANCZOS)
            return u, im
        except Exception as e:
            logger.warning(f"[md2img] 图片下载跳过({u[:80]}): {e}")
            return u, None

    with ThreadPoolExecutor(max_workers=min(4, max(1, len(urls)))) as ex:
        for u, im in ex.map(one, urls):
            if im is not None:
                out[u] = im
    return out


# ---------------------------------------------------------------- Markdown 检测 / 剥离

_TABLE_DETECT_SEP = re.compile(r"^\|?\s*:?-[\s:|-]*$")


def has_table(text: str) -> bool:
    lines = text.split("\n")
    for i in range(len(lines) - 1):
        if "|" in lines[i] and "-" in lines[i + 1] \
                and _TABLE_DETECT_SEP.match(lines[i + 1].strip()):
            return True
    return False


_MD_MARK_RE = re.compile(
    r"(\*\*[^*\n]+\*\*|__[^_\n]+__|~~[^~\n]+~~|`[^`\n]+`"
    r"|^#{1,6}\s|^>\s?|^\s*[-*+]\s|\[[^\]\n]+\]\([^)\n]+\)"
    r"|(?<![A-Za-z0-9_*])\*[^*\n]+\*(?![A-Za-z0-9_*]))", re.M)


def has_markdown(text: str) -> bool:
    return bool(_MD_MARK_RE.search(text))


def strip_markdown(text: str) -> str:
    """删除 Markdown 标记，保留可读纯文本。"""
    out, in_code = [], False
    for ln in text.split("\n"):
        s = ln.rstrip()
        if s.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            out.append(s)
            continue
        if re.match(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$", s):
            continue
        s = re.sub(r"^(\s*)#{1,6}\s+", r"\1", s)
        s = re.sub(r"^(\s*)>\s?", r"\1", s)
        s = re.sub(r"^(\s*)[-*+]\s+", r"\1· ", s)
        out.append(s)
    text = "\n".join(out)
    text = re.sub(r"\*\*([^*\n]+)\*\*|__([^_\n]+)__",
                  lambda m: m.group(1) or m.group(2), text)
    text = re.sub(r"~~([^~\n]+)~~", r"\1", text)
    text = re.sub(r"`([^`\n]+)`", r"\1", text)
    text = re.sub(r"!\[([^\]\n]*)\]\([^)\n]+\)",
                  lambda m: "[图片" + ((":" + m.group(1)) if m.group(1) else "") + "]",
                  text)
    text = re.sub(r"\[([^\]\n]+)\]\(([^)\n]+)\)", r"\1", text)
    text = re.sub(r"(?<![A-Za-z0-9_*])\*([^*\n]+)\*(?![A-Za-z0-9_*])", r"\1", text)
    return text.strip()
