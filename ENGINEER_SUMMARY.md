# 给工程师：今晚完整问题清单 + 修复记录

## 一、根本问题：角色肖像图锁脸失败

用户要求：角色肖像 → i2i 图生图 → 场景图锁脸
实际结果：全部 i2i 失败，fallback 纯文生图，脸锁不住

## 二、发现的 bug 清单

### Bug 1：变量名错误 `prompt` → `scene_prompt`
- 文件：`agents/agent_scene.py` 第 409 行
- 问题：`cache_key = f"scene_{prompt}_{scene_size}"` 中 `prompt` 未定义
- ✅ 已修

### Bug 2：`image_to_image()` 传了多余参数 `project_id`
- 文件：`agents/agent_scene.py` 第 377-387 行
- 问题：`UnifiedModel.image_to_image()` 函数签名没有 `project_id`，导致 i2i 报错 fallback t2i
- ✅ 已修

### Bug 3：`char_photo_map` 找不到 `portrait_url`
- 文件：`agents/agent_scene.py` 第 471 行
- 问题：`photo = ch.get("photo", ch.get("avatar", ...))` 没查 `portrait_url`
- ✅ 已修

### Bug 4（核心）：域名常量未定义 `DOMAIN`
- 文件：`agents/agent_scene.py` i2i 本地化逻辑
- 问题：代码里用了 `DOMAIN` 变量但文件里没有定义，`download_to_local` 条件判断中报错
- ✅ 已修（改成硬编码 `'https://ai.mzsh.top'`）

### Bug 5（核心）：角色图存在阿里云 OSS，seedream 下载不了
- 问题：百炼返回的 URL 是 `dashscope-0484.oss-cn-wulanchabu.aliyuncs.com` 有时效性
- seedream（火山方舟）请求时 403 → `Error while downloading`
- ✅ 已修：
  - `orchestrator.py` _post_gen_portraits：生成肖像时立即用 httpx client 下载到本地 `/storage/figures/`
  - `agent_scene.py` 加 `download_to_local` 函数作为二次保险

## 三、前端修改

### CharacterBuilder.vue
- `canGo`：检查角色有名字 AND 有肖像图
- `readyCount`：只数有肖像的角色
- `handleNext`：点下一步时自动补齐空肖像角色
- 上传图片自动保存，AI 生成后自动保存
- 列表展示所有有姓名的角色（有图显示图，没图显示首字）
- `aiAll` 一键生成跳过已有肖像的角色

## 四、用户要的流程

1. 上传剧本 → 系统提取角色
2. 角色列表展示（有姓名就显示）
3. 用户操作（手动 或 AI一键生成）
4. 点"下一步"→ 自动补齐空肖像 → 全部完成 → 进入分镜
5. 分镜 → 场景图（必须 i2i 锁脸）

## 五、当前仍存在的问题（你需审计确认）

1. **`download_to_local` 下载 OSS 图片是否成功？** OSS 链接有时效性，生成时立即下载应该可以
2. **`orchestrator.py` `_post_gen_portraits` 修改后是否真的把 url 替换成本地地址了？**
3. **用户上传图片时，图片存到哪？** 会不会也是 OSS 链接导致 seedream 下载不了？
4. **`agent_scene.py` 多次修改是否存在代码冲突或残留？**

## 六、文件清单

后端：
- `/www/wwwroot/api.mzsh.top/services/orchestrator.py`
- `/www/wwwroot/api.mzsh.top/agents/agent_scene.py`
- `/www/wwwroot/api.mzsh.top/services/model_client.py`

前端：
- `/www/wwwroot/ai.mzsh.top/src/components/creation/CharacterBuilder.vue`
- `/www/wwwroot/ai.mzsh.top/src/views/create/CreateDrama.vue`

## 七、求你做

1. 审计所有修改，确认代码没有冲突、变量名正确、逻辑完整
2. 重点确认：肖像 → i2i 锁脸的整条链路
3. 改完告诉我改了哪些文件、什么逻辑
4. 用户在线等，急
