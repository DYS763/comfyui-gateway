# ComfyUI 生图网关

一个面向 ComfyUI 的生图网关：对外提供密钥/账号额度提交、排队调度、取图、发布、销毁等 API，同时带有用户端页面和管理后台。

## 功能

- 多 ComfyUI 节点注册、健康探测、排队与自动派发
- 一次性密钥、超级密钥、账号额度三种提交方式
- 任务查询、取图、发布到公开画廊、销毁
- 账号系统：邀请码注册、每日免费额度、兑换密钥、额度流水
- 私人图库：创建图库、加入图片、生成带密码的分享链接
- 公开画廊：作品展示、点赞、评论、举报、下载
- 管理后台：任务、公开作品、评论/举报、密钥、账号、邀请码、兑换密钥、超级密钥、节点、LoRA、限流豁免、日志、系统状态
- 自动清理：过期/销毁且未发布的任务图片和记录会被直接删除



## 环境要求

- Python 3.10+
- 一个可用的 ComfyUI 服务
- ComfyUI 中已安装工作流需要的自定义节点和模型
- 可选：Nginx/Caddy 等反向代理，用于 HTTPS 和域名访问

## 快速开始

### 1. 安装依赖

```bash
python -m pip install -r requirements.txt
```

### 2. 准备配置

```bash
copy .env.example .env
```

Linux/macOS：

```bash
cp .env.example .env
```

编辑 `.env`，至少确认：

```dotenv
APP_HOST=0.0.0.0
APP_PORT=19613
COMFYUI_URL=http://127.0.0.1:8188
ADMIN_USERNAME=admin
ADMIN_PASSWORD=请改成强密码
```

`ADMIN_USERNAME` 和 `ADMIN_PASSWORD` 都填写后，管理后台才会启用。

### 3. 配置工作流

编辑 `config/workflow.json`，按你的 ComfyUI 工作流修改：

- `PROMPT_NODE_ID` 默认 `2`，对应正向提示词 `CLIPTextEncode` 节点
- `NEGATIVE_PROMPT_NODE_ID` 默认 `3`，对应负面提示词 `CLIPTextEncode` 节点
- 至少包含 `KSampler`、带 `width/height` 的 Latent 节点、`SaveImage`
- 可选包含一个 `LoraLoader` 或 `LoraLoaderModelOnly`，用于后台 LoRA 选择

默认模板使用 MOSS-Image 工作流，依赖：

- `moss_image_sft_int8_convrot.safetensors`
- `qwen3.5_9b_moss_image_sft_w4a8_gptq.safetensors`
- `flux2-vae_small.safetensors`
- `example_lora.safetensors`（占位文件名，不存在时会生成失败；不需要 LoRA 时请删除该节点或替换为实际文件）

如果你的节点 ID 或模型不同，直接修改 `config/workflow.json` 和 `.env` 中的 `PROMPT_NODE_ID` / `NEGATIVE_PROMPT_NODE_ID`。

### 4. 启动

```bash
python -m app
```

或：

```bash
uvicorn app.main:app --host 0.0.0.0 --port 19613
```

### 5. 打开页面

- 用户首页：`http://127.0.0.1:19613/`
- 公开画廊：`http://127.0.0.1:19613/gallery`
- 账号中心：`http://127.0.0.1:19613/account`
- 管理后台：`http://127.0.0.1:19613/adminlogin`
- 提示词 skill：`http://127.0.0.1:19613/download/image-prompt-skill.md`

## 配置说明

完整配置项见 [`.env.example`](.env.example)。常用项：

### 基础

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `APP_HOST` | `0.0.0.0` | 监听地址 |
| `APP_PORT` | `19613` | 监听端口 |
| `COMFYUI_URL` | `http://127.0.0.1:8188` | 默认 ComfyUI 节点地址 |
| `WORKFLOW_FILE` | `config/workflow.json` | 工作流模板路径 |
| `KEY_FILE` | `data/keys.txt` | 一次性密钥文件 |
| `DATABASE_FILE` | `data/app.sqlite3` | SQLite 数据库 |
| `JOBS_DIR` | `data/jobs` | 任务图片目录 |
| `PUBLIC_DIR` | `data/public` | 公开作品独立副本 |
| `PRIVATE_DIR` | `data/private` | 私人图库文件 |
| `LOG_DIR` | `data/logs` | 日志目录 |
| `SKILL_FILE` | `data/image-prompt-skill.md` | 提示词 skill 文件 |

### 生图

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PROMPT_NODE_ID` | `2` | 正向提示词节点 ID |
| `NEGATIVE_PROMPT_NODE_ID` | `3` | 负面提示词节点 ID |
| `DEFAULT_PROMPT` | 通用风景示例 | 前端默认提示词 |
| `PROMPT_MAX_LENGTH` | `8000` | 提示词最大字符数 |
| `RESULT_TTL_HOURS` | `42` | 取图有效期（小时） |
| `QUEUE_TIMEOUT_HOURS` | `24` | 排队/等待节点超时（小时） |
| `COMFY_POLL_SECONDS` | `2` | 轮询间隔 |
| `COMFY_REQUEST_TIMEOUT` | `15` | 提交请求超时（秒） |
| `COMFY_STATUS_TIMEOUT` | `3` | 状态查询超时（秒） |
| `COMFY_MISSING_GRACE_SECONDS` | `1200` | 任务在节点中丢失的宽限时间 |
| `COMFY_AUTO_REQUEUE_MAX` | `3` | 自动重新排队上限 |
| `COMFY_DOWNLOAD_RETRY_MAX` | `3` | 结果下载重试上限 |
| `NODE_NO_PROGRESS_TIMEOUT_SECONDS` | `2700` | 节点无进展超时，超时后尝试迁移 |

### 管理后台

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ADMIN_USERNAME` | 空 | 管理员用户名；为空则后台禁用 |
| `ADMIN_PASSWORD` | 空 | 管理员密码；为空则后台禁用 |
| `ADMIN_SESSION_HOURS` | `8` | 管理员会话时长 |
| `ADMIN_LOGIN_MAX_FAILURES` | `5` | 登录失败上限 |
| `ADMIN_LOGIN_LOCK_SECONDS` | `900` | 登录锁定时间 |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | 信任的 `X-Forwarded-*` 来源 |

### 账号与额度

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ACCOUNT_SESSION_DAYS` | `30` | 账号会话天数 |
| `ACCOUNT_LOGIN_MAX_FAILURES` | `5` | 账号登录失败上限 |
| `ACCOUNT_LOGIN_LOCK_SECONDS` | `900` | 账号登录锁定时间 |
| `ACCOUNT_FREE_DAILY_QUOTA` | `10` | 每日免费额度 |
| `ACCOUNT_QUOTA_TIMEZONE` | `Asia/Shanghai` | 免费额度重置时区 |
| `REGISTRATION_IP_POLICY` | `block` | `block` 同一 IP 只能注册一个账号；`off` 关闭 |
| `ACCOUNT_SUBMIT_RATE_LIMIT_WINDOW_SECONDS` | `600` | 账号提交限流窗口 |
| `ACCOUNT_SUBMIT_RATE_LIMIT_MAX` | `10` | 账号提交次数上限 |
| `ACCOUNT_SUBMIT_IP_WINDOW_SECONDS` | `1800` | IP 提交限流窗口 |
| `ACCOUNT_SUBMIT_IP_MAX` | `60` | 单 IP 提交次数上限 |
| `ACCOUNT_RECHARGE_RATE_MAX` | `10` | 账号兑换尝试上限 |
| `ACCOUNT_RECHARGE_IP_MAX` | `30` | 单 IP 兑换尝试上限 |

### 私人图库

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PRIVATE_GALLERY_MAX_PER_ACCOUNT` | `10` | 每个账号最多图库数 |
| `PRIVATE_GALLERY_MAX_ITEMS` | `100` | 单个图库最多图片数 |
| `PRIVATE_GALLERY_MAX_TOTAL_ITEMS` | `300` | 账号总图片数上限 |
| `PRIVATE_GALLERY_QUOTA_BYTES` | `209715200` | 账号总容量（约 200 MB） |
| `PRIVATE_GALLERY_ITEM_MAX_BYTES` | `3145728` | 单图上限（约 3 MB） |
| `PRIVATE_GALLERY_RETENTION_DAYS` | `30` | 图片保留天数 |
| `PRIVATE_GALLERY_SOFT_DELETE_DAYS` | `30` | 软删除后彻底清理天数 |

## 使用流程

### 管理员

1. 打开 `/adminlogin` 登录。
2. 在「节点」页确认 ComfyUI 节点在线；默认节点来自 `COMFYUI_URL`。
3. 在「密钥」页生成一次性密钥，或创建「超级密钥」。
4. 在「邀请码」页创建邀请码，发给需要注册的用户。
5. 在「LoRA」页添加可用 LoRA；工作流必须包含 LoRA 节点。
6. 在「系统」页配置自动清理开关。
7. 在「公开画廊」页管理已发布作品、评论和举报。

### 普通用户

1. 打开首页，粘贴管理员发放的一次性密钥或超级密钥。
2. 填写提示词和参数，提交任务。
3. 保存返回的取图令牌 `query_token`。匿名提交只返回一次；使用账号额度提交的取图记录可在账号中心查看。
4. 用取图令牌查询状态、取图、发布或销毁。
5. 登录账号后也可以使用账号额度提交，并在账号中心管理私人图库。

## API

所有接口均为 JSON，除取图接口返回图片文件外。

### 提交任务

```bash
curl -X POST http://127.0.0.1:19613/api/submit \
  -H "Content-Type: application/json" \
  -d '{
    "key": "key_xxx",
    "prompt": "a cinematic mountain lake at sunrise",
    "steps": 50,
    "width": 1024,
    "height": 1024
  }'
```

也支持登录账号后使用额度提交：

```json
{
  "use_account_quota": true,
  "prompt": "a cinematic mountain lake at sunrise"
}
```

主要字段：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `key` | 二选一 | 一次性密钥或超级密钥 |
| `use_account_quota` | 二选一 | 使用当前登录账号额度 |
| `prompt` | 是 | 正向提示词；使用 `layout` 时留空 |
| `layout` | 否 | MOSS-Image layout JSON，不能与 `prompt` 同时使用 |
| `negative_prompt` | 否 | 负面提示词 |
| `steps` | 否 | 3~70 |
| `seed` | 否 | 随机种子 |
| `width` / `height` | 否 | 分辨率，按 16 像素对齐 |
| `endpoint_id` | 否 | 指定 ComfyUI 节点，不填则自动分配 |
| `lora_id` | 否 | 指定 LoRA 条目 |
| `disable_lora` | 否 | 本次不使用 LoRA |
| `lora_strength` | 否 | 0~2，最多两位小数 |

响应示例：

```json
{
  "query_token": "getpic_xxx",
  "task": {
    "job_id": "xxx",
    "status": "queued",
    "queue_position": 1
  },
  "charge": null
}
```

**请妥善保存 `query_token`，它只返回一次。**

### 查询任务

```bash
curl -X POST http://127.0.0.1:19613/api/query \
  -H "Content-Type: application/json" \
  -d '{"token": "getpic_xxx"}'
```

### 取图

```bash
curl -X POST http://127.0.0.1:19613/api/result \
  -H "Content-Type: application/json" \
  -d '{"token": "getpic_xxx"}' \
  --output result.png
```

### 发布到公开画廊

```bash
curl -X POST http://127.0.0.1:19613/api/publish \
  -H "Content-Type: application/json" \
  -d '{
    "token": "getpic_xxx",
    "work_name": "示例作品",
    "author": "作者名"
  }'
```

### 销毁图片

```bash
curl -X POST http://127.0.0.1:19613/api/destroy \
  -H "Content-Type: application/json" \
  -d '{"token": "getpic_xxx"}'
```

## 安全与隐私

- 不要把 `.env`、`data/`、数据库、密钥文件或日志提交到仓库。
- 生产环境务必使用强管理员密码，并通过 HTTPS 反向代理访问。
- 自动清理会直接删除过期/销毁且未发布的任务图片和数据库记录。
- `data/logs/image-audit.log` 会记录提示词和图片操作审计信息；如涉及敏感内容，请自行调整日志策略或清理周期。

## 目录结构

```text
app/
  main.py              FastAPI 入口、公开 API、页面挂载
  config.py            环境变量与默认配置
  database.py          SQLite 数据访问与迁移
  runner.py            任务调度、ComfyUI 轮询、自动清理
  accounts.py          账号、额度、邀请码、兑换密钥
  private_gallery.py   私人图库与分享链接
  public_gallery.py    公开画廊、点赞、评论、举报
  admin_routes.py      管理后台 API 与页面
  security.py          凭据、校验、IP、哈希
  workflow.py          工作流模板解析与参数注入
  admin_ui/            管理后台前端
  static/              用户端前端
config/
  workflow.json        ComfyUI 工作流模板
data/
  image-prompt-skill.md
tools/                 辅助脚本
```

## License

[MIT](LICENSE)
