# Codex 工程师任务：角色肖像锁脸 + 前端交互逻辑

## 一、用户要的完整流程

1. 用户上传剧本 → 系统提取角色（角色名、性格、外貌等）
2. 用户进入角色编辑页
3. **用户手动操作**：
   - 点"AI生成形象" → 后端生成角色肖像 → 出预览图
   - 用户点"保存为头像" → 该角色的 `avatar` 字段写入
   - 每个角色名只认一张脸，不会重复覆盖
4. **角色列表只展示有肖像的角色** — 没有 `avatar` 的角色卡片不显示
5. **全部角色都有肖像后** → "下一步"按钮可点
6. 用户点"下一步" → **后端才开始跑分镜**
7. 分镜完成后 → **场景图生成时用角色肖像做 i2i 参考** → 锁脸

## 二、后端管线顺序（必须有依赖保证）

```
CHARACTER → STORYBOARD → SCENE
```

- CHARACTER 阶段：提取角色 + 生成角色肖像 → 全部完成后才进入下一阶段
- STORYBOARD 阶段：等用户前端点了"下一步"才触发
- SCENE 阶段：拿 characters 里的 portrait_url 做 i2i 参考生场景图

## 三、肖像 → 场景锁脸（必须有）

SCENE 阶段调用 scene agent 时，参数里必须有：
- `characters` 列表（含每个角色的 portrait_url/avatar/photo）
- scene agent `_batch_generate` 构建 `char_photo_map` 时，从 `portrait_url` → `photo` → `avatar` → `image_url` 依次查找
- 找到参考图后调 `UnifiedModel.image_to_image()` 做 i2i，不能 fallback 成 t2i

**严格禁止：** 纯文生图（t2i）生成场景图。角色脸必须锁住。

## 四、一名一张脸（必须有）

- 每个角色名字只对应一张肖像图
- 用户手动生成后点"保存为头像"才写入
- 一键生成全部时，已有肖像的角色跳过
- 不允许同一个角色反复生成导致多张脸混淆

## 五、文件清单（必须审计）

后端：
- `/www/wwwroot/api.mzsh.top/services/orchestrator.py` — 管线编排
- `/www/wwwroot/api.mzsh.top/agents/agent_scene.py` — 场景生成
- `/www/wwwroot/api.mzsh.top/services/model_client.py` — 模型调用
- `/www/wwwroot/api.mzsh.top/routers/pipeline.py` — 管线 API

前端（只展示不修，但要确认逻辑）：
- `/www/wwwroot/ai.mzsh.top/src/components/creation/CharacterBuilder.vue` — 角色编辑组件
- `/www/wwwroot/ai.mzsh.top/src/views/create/CreateDrama.vue` — 创作页

## 六、你需要做的事

1. **审计确认**：角色肖像 → i2i 场景锁脸的完整链路没有断点
2. **改什么改哪里**：告诉我改了哪些文件、改了什么逻辑
3. **注意**：不允许纯文生图替代 i2i 做场景图

## 七、交付要求

改完后告诉我：
1. 确认链路没问题 → 跑一次看看效果
2. 有问题 → 详细说明问题+怎么修的
3. 最终角色肖像→场景锁脸的通了

文件已放在：`/www/wwwroot/api.mzsh.top/CODE_TASK_PORTRAIT_LOCKFACE.md`
