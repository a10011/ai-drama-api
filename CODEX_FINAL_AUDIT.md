# Codex 工程师：一次性审计 & 修复所有问题

## 紧急程度 🔴
用户在线等了4个小时，反复修复反复出新bug，今天必须彻底解决。

## 用户流程要求

1. 上传剧本 → 系统提取角色（姓名、性格、外貌）
2. 角色列表展示（有姓名就显示，有图显示图，没图显示首字）
3. 用户操作：
   - 手动：选角色 → 填信息 → 点"AI生成形象"或上传图片
   - 一键生成：点"🤖 一键生成全部" → AI自动补全+生成肖像（已有肖像的跳过）
4. 用户点"下一步" → 自动检查空肖像角色 → 补齐 → 全部完成后 → 进入分镜
5. 分镜 → 场景图 → **必须用角色肖像做 i2i，锁脸**

## 当前代码问题（我今晚反复修出来的）

### 1. 角色肖像 OSS 链接本地化
- 文件：`/www/wwwroot/api.mzsh.top/services/orchestrator.py`
- 位置：`_post_gen_portraits()` 生成肖像拿到 URL 后，用 httpx client 下载到 `/storage/figures/`
- **问题**：下载 OSS 图片时服务器 403，因为百炼 OSS 链接有时效性或防盗链
- **已修**：改为在 `BailianWanxiangChatProvider` 直接传 OSS URL 给万相（不下载中转）

### 2. 万相 i2i 直接传 URL
- 文件：`/www/wwwroot/api.mzsh.top/services/ai_providers.py`
- 位置：`BailianWanxiangChatProvider.generate_image_to_image()`
- **已修**：去掉 `requests.get` 下载中转，直接传 URL 给万相 API

### 3. agent_scene.py 多轮修改可能有残留
- 文件：`/www/wwwroot/api.mzsh.top/agents/agent_scene.py`
- 我改了以下地方：
  - 加 `download_to_local` 函数（文件末尾）
  - i2i 调用前检查 reference_image 并本地化
  - `char_photo_map` 添加 `portrait_url` 查找
  - 去掉 `project_id` 参数
  - `DOMAIN` → 硬编码字符串
- **问题**：反复修改可能代码冲突、重复、或条件判断不对

### 4. 前端 CharacterBuilder.vue
- 文件：`/www/wwwroot/ai.mzsh.top/src/components/creation/CharacterBuilder.vue`
- 改了：
  - `canGo` 检查肖像
  - `handleNext` 自动补齐
  - 展示所有有姓名角色
  - `aiAll` 跳过已有肖像
- **问题**：需要验证逻辑是否正确

## 你需做的事

### 1️⃣ 审计所有文件改动
看以下文件，确认代码没有冲突、变量名正确、逻辑完整：

- `/www/wwwroot/api.mzsh.top/services/orchestrator.py`
- `/www/wwwroot/api.mzsh.top/agents/agent_scene.py`
- `/www/wwwroot/api.mzsh.top/services/ai_providers.py`
- `/www/wwwroot/api.mzsh.top/services/model_client.py`
- `/www/wwwroot/ai.mzsh.top/src/components/creation/CharacterBuilder.vue`
- `/www/wwwroot/ai.mzsh.top/src/views/create/CreateDrama.vue`

### 2️⃣ 重点确认链路
```
角色设计阶段 (CHARACTER)
  → _post_gen_portraits() 生成角色肖像图
  → URL 存为本地 /storage/figures/ 或直接传 OSS URL 给万相
  → portrait_url 写回 characters

分镜阶段 (STORYBOARD) → 等用户点"下一步"

场景阶段 (SCENE)
  → 传入 characters（含 portrait_url）
  → char_photo_map 构建
  → i2i 调用 seedream/wanxiang 用角色图做参考
  → 场景图锁脸 ✅
```

### 3️⃣ 修复发现的问题
- 如果有代码冲突 → 合并
- 如果有变量未定义 → 补上
- 如果条件判断不对 → 修正
- 如果有 import 缺失 → 补充

### 4️⃣ 改完告诉我
- 改了哪些文件、什么逻辑
- 跑了测试没有（可以调管线或手工 curl 测一个阶段）
- 用户在线等

## 服务器信息
- SSH: `ssh root@36.140.145.225` 密码 `homdHZ40@`
- 代码目录: `/www/wwwroot/api.mzsh.top/`
- 重启: `pm2 restart ai-drama-api`
- 前端 build: `cd /www/wwwroot/ai.mzsh.top && npm run build`
- 日志: `pm2 logs ai-drama-api --lines 50 --nostream`
