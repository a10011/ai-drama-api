# AI 模型调用规范 v2.0

## 唯一入口
```
from services.model_client import UnifiedModel
```
**禁止**直接 `from services.ai_providers import ...` 并在代码中 new Provider 实例。
**统一走** `UnifiedModel` 静态方法。

---

## 调用方式

### 图生图（仅 Seedream 支持）
```python
r = UnifiedModel.image_to_image(
    prompt="保持人物主体不变，增强光影，写实风格",
    reference_image="https://ai.mzsh.top/storage/xxx.jpg",  # ⚠️ 必须公网 HTTPS URL
    size="1920x1920",   # ⚠️ 必须 >= 1920x1920（最低 3686400 像素）
    timeout=180,         # 建议 >= 120s
    strength=0.55        # 0.35(最像原图) ~ 0.75(大幅风格化)
)
# r = {"success": True/False, "url": "...", "model": "seedream_i2i", "error": ""}
```

### 图片生成
```python
r = UnifiedModel.image(
    prompt="动漫风格角色立绘，全身像",
    preferred="seedream",    # 首选模型，不传自动路由
    size="1920x1920",        # 不传用模型默认
    timeout=60
)
# r = {"success": True/False, "url": "...", "model": "seedream", "error": ""}
```

### 视频生成
```python
r = UnifiedModel.video(
    prompt="人物向前走",
    image_url="https://...",  # 参考图
    preferred="kling",
    timeout=60
)
# r = {"success": True/False, "url": "...", "model": "kling", "error": ""}
```

### TTS
```python
r = UnifiedModel.tts(
    text="你好世界",
    voice="zh-CN-XiaoxiaoNeural",
    speed=1.0,
    timeout=20
)
# r = {"success": True/False, "audio_path": "/tmp/...", "model": "edge-tts", "error": ""}
```

### LLM
```python
r = UnifiedModel.llm(
    prompt="写一个短剧本",
    system="你是专业编剧",
    model="deepseek-chat",
    timeout=60,
    max_tokens=4096
)
# r = {"success": True/False, "text": "...", "model": "deepseek-chat", "error": ""}
```

### 批量场景图（自动并行）
```python
r = UnifiedModel.scene_images(
    prompts=["第一幕场景", "第二幕场景"],
    size="1920x1920"
)
# r = {"0": {"success": ..., "url": ...}, "1": {"success": ..., "url": ...}}
```

### 角色立绘批量生成（自动并行 + 自动构造prompt）
```python
r = UnifiedModel.character_portraits(
    chars=[{"name": "张三", "gender": "男", "age": "青年", ...}],
    genre="武侠"
)
# r = {"张三": "https://...", "李四": "https://..."}
```

### 下载到本地存储
```python
url = UnifiedModel.download_to_storage("https://...", name_hint="张三")
# url = "/storage/figures/张三_abc12345.jpg"
```

---

## 模型规格表（MANDATORY - 调用前必查）

### 图片模型

| 模型 | Provider | 模型ID | 默认尺寸 | 最小尺寸 | 支持i2i | 超时 | 约束 |
|------|----------|--------|----------|----------|---------|------|------|
| **seedream** | ARKImageProvider | doubao-seedream-5-0-260128 | 1920×1920 | **≥1920×1920 (≥3,686,400px)** | ✅ 支持 | 60s(t2i) / 120s(i2i) | i2i参考图必须**公网HTTPS URL**，不能本地路径 |
| **wanxiang** | TongyiWanxiang | wanx2.1-t2i-plus | 1024×1024 | 1024×768~1440px | ❌ 不支持 | 60s(异步) | 仅支持 1024×1024/768×1024/1024×768，超过→自动降为1024×1024 |
| **hidream** | HiDreamImageProvider | z1-image | 1024×1024 | 1024×1024 | ❌ 不支持 | 60s | - |
| **agnes** | AgnesAIProvider | agnes-image-2.1-flash | 1024×1024 | 1024×1024 | ❌ 不支持 | 30s | - |

### 视频模型

| 模型 | 模型ID | 超时 | 约束 |
|------|--------|------|------|
| **seedance** | doubao-seedance-2-0-260128 | 120s | ARK端点 |
| **kling** | kling-v2-6 | 60s | 可灵AK/SK |
| **wan2.7_i2v** | wan2.7-i2v-2026-04-25 | 300s | 阿里百炼 |
| **happyhorse-r2v** | happyhorse-1.1-r2v | 300s | 阿里百炼，6折 |

---

## ⚠️ 图生图关键约束

### 1. 尺寸必须≥1920×1920
Seedream 图生图 API 要求 `size * size >= 3686400` 像素。
- ✅ 1920×1920 = 3,686,400 ✓
- ❌ 1024×1024 = 1,048,576 ✗ → 返回 400: "image size must be at least 3686400 pixels"

### 2. 参考图必须是公网可访问的HTTPS URL
ARK 服务器端下载参考图，必须是外网可达的 HTTPS 地址。
- ✅ `https://ai.mzsh.top/storage/xxx.jpg` — 有效 SSL 证书（Let's Encrypt）
- ❌ `https://api.mzsh.top/storage/xxx.jpg` — SSL 证书只覆盖 ai.mzsh.top，不覆盖 api.mzsh.top
- ❌ `http://36.140.145.225/xxx.jpg` — ARK 强制 HTTPS 且不信任自签证书
- ❌ 本地文件路径 `/www/wwwroot/storage/xxx.jpg` — ARK 无法访问内网

**解决方案**：reference_image 统一使用 `https://ai.mzsh.top/storage/...` 域名

### 3. strength 参数指南
| Strength | 效果 | 适用场景 |
|----------|------|----------|
| 0.35 | 最接近原图，仅轻微增强 | 锁脸特写、保留原图细节 |
| 0.45 | 轻微风格化 | 影棚灯光增强 |
| 0.55 | 默认值，均衡 | 通用角色图（agent_character默认）|
| 0.65 | 明显风格化 | 电影海报风格 |
| 0.75 | 大幅改变 | 暗调剧情风、强风格化 |

### 4. 图生图是同步调用
- 请求→ARK生成→返回URL，通常30-90秒完成
- 返回的 URL 有 24h 有效期（TOS签名），需及时下载到 storage 持久化
- 只用 Seedream，不归入生态链路由（无降级）

---

## 生态链（ROUTING_CHAINS）

| 生态 | LLM | 图片 | 视频 | TTS | BGM |
|------|-----|------|------|-----|-----|
| **volc** (火山纯血) | doubao | seedream | seedance | edge-tts | music_api |
| **aliyun** (阿里纯血) | qwen-max | wanxiang | happyhorse-r2v | cosyvoice | music_api |
| **deepseek** (当前) | deepseek-chat | wanxiang | happyhorse-r2v | edge-tts | music_api |

当前生效：**deepseek**（DeepSeek LLM + 万相图 + 快乐马视频）

---

## 模型注册表
所有参数在 `services/model_client.py` 的 `MODEL_REGISTRY` 字典中统一配置：
- `size` — 默认尺寸
- `timeout` — 超时秒数
- `provider` — Provider 类名
- `model` — 模型标识符

路由链 `ROUTING_CHAINS` 按优先级排列。

## 禁用行为

| ❌ 旧写法 | ✅ 新写法 |
|-----------|-----------|
| `from services.ai_providers import ARKImageProvider; p = ARKImageProvider(); p.generate(...)` | `UnifiedModel.image(...)` |
| `from services.ai_providers import agnes; agnes.generate_image(...)` | `UnifiedModel.image(preferred="agnes")` |
| `run_with_fallback("image", ...)` | 自动路由，无需自己调 |
| 硬编码 `"1920x1920"` 散落在各处 | `MODEL_REGISTRY` 统一配置 |
| 各 agent 自己 new provider 实例 | 实例由内部延迟加载，全局单例 |

## 新增模型
1. 在 `MODEL_REGISTRY` 加一条记录
2. 在 `_get_provider()` 加对应 Provider 的 import
3. 在 `ROUTING_CHAINS` 加路由优先级
4. 在 `UnifiedModel` 类加批量方法（可选）
