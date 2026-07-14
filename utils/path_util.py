"""
path_util — 统一的文件路径 ↔ 公网URL 转换工具

取代项目中散落的 6 个重复实现（_to_public_url / _path_to_url / download_to_local
的URL拼接 / download_to_storage的URL拼接 / i2i内联本地化 / 各处硬编码域名拼接）。

设计原则：
- 域名一律从 app_config.BASE_URL 读取，移除所有 https://ai.mzsh.top 字面量
- 统一返回完整 URL（https://ai.mzsh.top/storage/...），下游模型 API 可直接消费
- 纯转换，无 IO 副作用（不像旧 _to_public_url 会 copy 文件到 dist 目录）
- 同时兼容 /www/wwwroot/storage/... 和 /storage/... 两种本地路径输入
"""
import os

from app_config import BASE_URL

# 存储根目录（本地绝对路径）
STORAGE_ROOT = "/www/wwwroot/storage"
# 本地路径前缀（/www/wwwroot 对应公网根）
WWW_ROOT = "/www/wwwroot"


def local_path_to_url(path: str) -> str:
    """本地路径 → 公网URL。

    接受：
      /www/wwwroot/storage/figures/char_xxx.jpg  → https://ai.mzsh.top/storage/figures/char_xxx.jpg
      /storage/figures/char_xxx.jpg             → https://ai.mzsh.top/storage/figures/char_xxx.jpg
      /www/wwwroot/ai.mzsh.top/dist/x.jpg       → https://ai.mzsh.top/ai.mzsh.top/dist/x.jpg
      已是 http(s):// 开头                       → 原样返回
      空串                                       → 空串
    """
    if not path:
        return ""
    if path.startswith("http://") or path.startswith("https://"):
        return path
    # /www/wwwroot/xxx → {BASE_URL}/xxx
    if path.startswith(WWW_ROOT + "/"):
        rel = path[len(WWW_ROOT):]  # 保留前导 /，如 /storage/figures/x.jpg
        return f"{BASE_URL}{rel}"
    if path.startswith(WWW_ROOT):  # 不带尾斜杠的兜底
        rel = path[len(WWW_ROOT):]
        if not rel.startswith("/"):
            rel = "/" + rel
        return f"{BASE_URL}{rel}"
    # /storage/xxx → {BASE_URL}/storage/xxx
    if path.startswith("/storage"):
        return f"{BASE_URL}{path}"
    # 其它相对路径原样拼接（不常见，保持兼容）
    return f"{BASE_URL}/{path.lstrip('/')}"


def url_to_local_path(url: str) -> str:
    """公网URL → 本地路径。反向转换。

    接受：
      https://ai.mzsh.top/storage/figures/x.jpg → /www/wwwroot/storage/figures/x.jpg
      已是 /www/wwwroot/ 或 /storage/ 开头      → 原样返回
      空串                                       → 空串
    """
    if not url:
        return ""
    if url.startswith("http://") or url.startswith("https://"):
        # 提取 path 部分，替换域名
        # https://ai.mzsh.top/storage/x.jpg → /www/wwwroot/storage/x.jpg
        if BASE_URL and url.startswith(BASE_URL):
            rel = url[len(BASE_URL):]  # /storage/x.jpg
            return f"{WWW_ROOT}{rel}"
        # 兼容其它域名：取 path 部分
        from urllib.parse import urlparse
        parsed = urlparse(url)
        return f"{WWW_ROOT}{parsed.path}"
    if url.startswith(WWW_ROOT) or url.startswith("/storage"):
        return url if url.startswith(WWW_ROOT) else f"{WWW_ROOT}{url}"
    return url


def normalize_url(url_or_path: str) -> str:
    """归一化为公网URL。接受任意输入格式，统一输出完整 URL。

    用于消费方不确定拿到的是 URL 还是本地路径的场景（如 i2i 参考图）。
    """
    if not url_or_path:
        return ""
    s = str(url_or_path).strip()
    if s.startswith("http://") or s.startswith("https://"):
        return s
    return local_path_to_url(s)


def is_local_path(s: str) -> bool:
    """判断是否本地路径（非 http 开头且非空）。"""
    if not s:
        return False
    return not (s.startswith("http://") or s.startswith("https://"))


def storage_subdir(pipeline_id: str = "", project_id: str = "", fallback: str = "_shared") -> str:
    """统一计算存储子目录（隔离键）。

    优先级：pipeline_id > project_id > fallback(_shared)。
    用于 media_registry.save() 和所有手写落盘点，确保隔离键一致。
    """
    if pipeline_id:
        return str(pipeline_id)
    if project_id:
        return str(project_id)
    return fallback


def build_storage_path(media_type: str, filename: str,
                       pipeline_id: str = "", project_id: str = "") -> str:
    """构建统一的存储绝对路径。

    返回：/www/wwwroot/storage/{type}/{pipeline_id or project_id or _shared}/{filename}
    用显式 / 拼接（存储路径是 Linux 绝对路径，不依赖运行平台的 os.path.sep）。
    """
    subdir = storage_subdir(pipeline_id, project_id)
    return f"{STORAGE_ROOT}/{media_type}/{subdir}/{filename}"
