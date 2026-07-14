# 总控模型选型方案（2026-06-11）

根据官网爬取数据+平台现状输出。

## 1. LLM（剧本/角色/分镜/客服）
- **首用**: `deepseek-chat` — DeepSeek官方API ✅ 已验证集成，性价比最高
- **备用1**: `deepseek-reasoner` — DeepSeek官方API，复杂推理场景
- **备用2**: `doubao-pro-128k-240515` — 火山方舟ARK，128K超长上下文，处理超长剧本

## 2. 图像/立绘
- **首用**: `doubao-seedream-5-0-260128` — 火山方舟ARK ✅ 已验证，1920x1920高质量图
- **备用**: `wan2.7-image` — 阿里百炼，东方美学风格补充

## 3. 视频
- **首用**: `doubao-seedance-1-5-pro-251215` — 火山方舟ARK ✅ 已验证，异步task+poll
- **备用1**: Kling 图生视频 — 有AK/SK但API端点需更新（旧接口404）
- **备用2**: 即梦 视频生成 — 有AK/SK但域名不通

## 4. TTS（配音）
- **首用**: `cosyvoice-v1` — 阿里百炼 ⚠️ 接口待修复（上次返回InvalidParameter）
- **备用**: `edge-tts` — 本地系统 ✅ 当前正在用，稳定但无情绪表达

## 5. 人脸锁定
- **首用**: Kling face model — 有AK/SK但API端点需更新
- **备用1**: 即梦 人脸 — 有AK/SK但域名不通
- **备用2**: doubao-seedance-1-5-pro-251215 (Seedance) — 火山方舟ARK 已验证。Seedance输入图后的人脸保持能力很强，输出角色面部一致性高，作为锁脸方案完全够用

## 6. BGM（背景音乐）
- **首用**: 暂无 — 建议接入 Mubert / Soundraw 等AI音乐生成API
- **备用**: 暂无 — 建议接入 Epidemic Sound / Artlist 商用音乐库

## 7. 字幕
- **首用**: 内部生成（ffmpeg/moviepy同步压制）
- **备用**: Whisper-large-v3 — 外部导入视频的语音转文字

## 8. 语音（语音转文字/语音识别）
- **首用**: `cosyvoice-v1` ASR — 阿里百炼
- **备用**: 火山方舟语音识别接口
