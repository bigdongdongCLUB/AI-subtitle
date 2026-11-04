# AI 字幕工作台（AI Subtitle Studio）

一个在浏览器中使用的字幕翻译 / 润色 / 编辑工具。后端为 Python FastAPI，前端为纯静态页面（无需 Node 构建），可部署在 Ubuntu 等 Linux 服务器，也可以本地运行。

## 功能

- **加载字幕**：支持 SRT、WebVTT、ASS / SSA，自动识别 UTF-8 / GB18030 / Big5 等编码，拖拽即可打开。
- **接入大模型**：任何 OpenAI 兼容接口，内置 Groq、硅基流动、DeepSeek、OpenAI、OpenRouter、Moonshot、智谱、阿里云百炼、Ollama 预设；选择服务商后可点「获取 Key ↗」直达官网的 API Key 页面；可一键拉取模型列表、下拉选择并测试连接。可以保存多个模型配置（不同服务商或同一服务商的不同模型），在顶栏「模型」下拉中随时切换当前使用哪一个。
- **AI 翻译 / 润色**：翻译、润色可单选也可同时选（同时选时先翻译，再对照原文校对润色译文）；按批次并发处理，携带前后文提升连贯性；支持 20 种常用语言、5 种语气风格、术语表和自定义要求；时间轴与 ASS 样式原样保留。
- **登录保护**：首次打开时必须先设置用户名和密码，之后所有页面和接口都需要登录。密码加盐哈希保存，连续输错 5 次锁定 5 分钟，修改密码后其他设备需要重新登录。
- **字幕保存在服务器**：正在编辑的字幕保存在服务器的 `data/project.json`，换一台电脑登录可以接着编辑；多台电脑同时打开会每 5 秒自动同步，只上传、下载改动的条目，同时修改不同的行不会互相覆盖。
- **设置保存在服务器**：模型配置（含 API Key）和任务设置保存在服务器的 `data/settings.json`，任何电脑的浏览器打开都相同。API Key 不会发回浏览器，只显示遮挡后的提示；已保存的 Key 只会发送给它所属的 API 地址。旧版本存在浏览器里的配置会在首次打开时自动迁移到服务器。
- **仅编辑**：模式里选「仅编辑」时不使用 AI，只调整时间轴或手动修改字幕；运行按钮和 AI 选项都不可用，导出时默认导出原文，文件名不加语言后缀。
- **字幕排版**：导入字幕和 AI 译文会自动把多行合并为一行，超过宽度上限（默认 50 半角，约 25 个汉字）时再按断句换行；优先在说话人之间、句末、逗号和空格处断开，不拆英文单词和人名。双语字幕（同一条里有中文和外文）会自动识别，按语言分成两行，各自合并、超宽才换行。已有字幕可点「整理换行」。「忽略歌词和氛围音」默认开启：整条都在 ♪ ♪ 之间的歌词，或只有 [音乐]、(叹气) 这类音效描述的字幕不送去翻译，导出时显示原文；歌词和对白混在一条里时只保留歌词原文。单行「AI」按钮仍可强制翻译。导出时可选视频分辨率 720p / 1080p / 4K（按文件名里的 720p、1080p、2160p 自动选择），字号随之换算（默认 29 / 44 / 88，画面上大小相同），也可以手动改；副字幕缩小到 75%。ASS 还可以分别设置主字幕和副字幕的字体、文字颜色、描边颜色，对话框里有实时预览；主字幕是译文（或双语原文件的第一种语言），副字幕是原文（或双语原文件的第二种语言）。字体只提供大多数设备都有的通用字体，和苹果设备（Mac、iPhone、iPad）自带的中文字体；导入的 ASS 用了其他字体时，会作为「原文件字体」保留。SRT / VTT 不含字号，由播放器决定，选这两种格式时分辨率和字号选项不可用。导入的 ASS 沿用自己的画布，改字号会按比例缩放它的全部样式。导出参数（格式、内容、分辨率、字号、副字幕大小、字体和颜色）一改就保存到服务器，刷新或换字幕后沿用；文件名里写了分辨率时仍按文件名选择，导入的 ASS 仍用它自己的样式。
- **人工编辑**：表格内直接修改原文、译文和时间；单行“AI”按钮重新生成；查找替换、整体时间偏移、译文转原文（用于二次翻译）；内容自动保存在浏览器本地。
- **导出**：SRT / VTT / ASS，可选仅译文、仅原文或双语（上下顺序可选）。

## 一键安装（Ubuntu / Debian）

```bash
curl -fsSL https://raw.githubusercontent.com/bigdongdongCLUB/AI-subtitle/main/install.sh | sudo bash
```

装好后是开机自启的 systemd 服务 `ai-subtitle`，程序和数据在 `/opt/ai-subtitle`，访问 `http://服务器地址:8000`。**请立即打开页面设置用户名和密码**：设置之前，能访问这个地址的人都可以抢先设置。

- 换端口：把 `sudo bash` 换成 `sudo PORT=9000 bash`。
- 升级：再运行一次同样的命令，`data/` 和 `.env` 不会被覆盖，端口沿用原来的。

## 快速开始（本地或 Ubuntu）

要求：Python 3.9+。

```bash
git clone https://github.com/bigdongdongCLUB/AI-subtitle.git ai-subtitle && cd ai-subtitle
./run.sh
```

然后浏览器打开 <http://localhost:8000>。第一次打开会要求设置用户名和密码，之后点击右上角「⚙ 模型设置」填入 API Key 即可使用。

`run.sh` 会自动创建 `.venv`、安装依赖并启动服务。可通过环境变量修改监听地址和端口：

```bash
HOST=0.0.0.0 PORT=9000 ./run.sh
```

### 服务器端预置模型配置（可选）

复制 `.env.example` 为 `.env` 并填写：

```ini
LLM_BASE_URL=https://api.siliconflow.cn/v1
LLM_API_KEY=sk-xxxx
LLM_MODEL=Qwen/Qwen2.5-72B-Instruct
```

选择「使用服务器预置配置」即可使用这里的设置，密钥不会下发到浏览器，且只会发送给 `LLM_BASE_URL` 这个地址。

## 从本机部署到其他主机

在本机项目目录执行下面的命令，会打包当前代码上传到目标主机，安装依赖、跑自检并重启服务，`data/` 不会被覆盖：

```bash
deploy/deploy_to_host.sh user@host [端口]
```

用 `DEPLOY_KEY=私钥路径` 或 `DEPLOY_PASS=密码` 登录；登录用户不是 root 时需要免密 sudo。也可以把 `DEPLOY_TARGET`、`DEPLOY_KEY`、`DEPLOY_PORT` 写进 `deploy/target.conf`，之后不带参数运行。

## 手动部署到 Ubuntu（systemd）

```bash
sudo apt install -y python3-venv
sudo mkdir -p /opt/ai-subtitle && sudo cp -r . /opt/ai-subtitle
cd /opt/ai-subtitle && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
sudo cp deploy/ai-subtitle.service /etc/systemd/system/
# 按需修改 User / WorkingDirectory 后：
sudo systemctl daemon-reload && sudo systemctl enable --now ai-subtitle
```

可选：用 Nginx 反向代理（见 `deploy/nginx.conf.example`），配合 HTTPS 或 Basic Auth。如果服务暴露在公网，建议至少加一层访问认证，因为 `/api/translate` 会用你的密钥调用模型。

## Docker

```bash
cp .env.example .env   # 可选
docker compose up -d --build
```

访问 <http://localhost:8000>。

## 常用服务商地址

| 服务商 | Base URL | 示例模型 |
| --- | --- | --- |
| Groq | `https://api.groq.com/openai/v1` | `openai/gpt-oss-120b` |
| 硅基流动 | `https://api.siliconflow.cn/v1` | `Qwen/Qwen2.5-72B-Instruct` |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-v4-pro` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |
| Ollama 本地 | `http://localhost:11434/v1` | `qwen2.5:7b` |

## 使用建议

- **每批条数**：20 左右兼顾速度与质量；模型上下文小或经常漏条时调小。
- **并发数**：免费额度账号建议 1–2，避免触发限流；失败的批次可点「重试失败项」。
- **深度思考**：可选档位随服务商和模型变化，默认「关闭」（不能关闭的模型用最浅一档），翻译字幕通常不需要深度思考。
  | 服务商 / 模型 | 可选档位 |
  | --- | --- |
  | DeepSeek V4（v4-pro、flash） | 关闭、轻度、重度、最高（DeepSeek 没有中度） |
  | Groq gpt-oss | 轻度、中度、重度（不能关闭） |
  | Groq Qwen3 | 关闭、轻度、中度、重度 |
  | OpenAI gpt-5 系列 | 关闭、轻度、中度、重度；o 系列不能关闭 |
  | OpenRouter、其他兼容服务 | 关闭、轻度、中度、重度 |
  | 硅基流动、阿里云百炼的 Qwen3 等 | 关闭，以及按思考长度上限分的轻度（约 1K）、中度（约 4K）、重度（约 16K）、最高（约 32K） |
  | 智谱 GLM-4.5 及以后、Ollama 思考模型 | 只能关闭或开启 |
  | 非推理模型（如 deepseek-chat、Qwen2.5-Instruct、gpt-4o） | 不支持调节 |

  每个模型还有「不指定」，即不发送思考参数，由模型自己决定。服务商不接受所选参数时，会自动按「不指定」重试，并记在服务器日志里。
- **超时**：是每次调用模型的总时长上限。开启深度思考时建议调到 300–600 秒。
- **限流**：遇到 429 等临时错误会自动等待并重试最多 3 次；仍失败的批次可点「重试失败项」。
- **排查**：服务器会为每次模型调用记一条日志，可用 `journalctl -u ai-subtitle -f` 查看耗时、状态和用量。
- **二次翻译**：翻译成英文后点「译文→原文」，再选择另一种目标语言，可做多语种接力。
- **只选润色**：保持原文语言，用于修正机器翻译或语音识别结果的生硬表达。
- **翻译 + 润色**：每批会调用两次模型，质量更好，但耗时和用量约为两倍；免费额度账号建议把并发数调低。

## 目录结构

```
app/
  main.py        FastAPI 路由：解析 / 翻译 / 导出 / 模型列表
  subtitles.py   SRT / VTT / ASS 解析与生成（纯 Python 实现）
  llm.py         OpenAI 兼容接口调用、提示词、结果解析与补齐重试
app/store.py     服务器端存储：设置（data/settings.json）与字幕（data/project.json）
app/auth.py      登录认证（data/auth.json），命令行 `python -m app.auth status|reset`
static/          前端（index.html / app.js / style.css / login.html）
deploy/          systemd 与 Nginx 示例
tests/test_api.py 自检脚本（模拟模型接口）
install.sh       一键安装 / 升级脚本
run.sh           一键启动脚本
Dockerfile, docker-compose.yml
```

## API 简介

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/parse` | 上传字幕文件，返回 JSON 结构 |
| POST | `/api/translate` | 翻译 / 润色一批条目（≤200 条） |
| POST | `/api/export` | 根据 JSON 生成字幕文件 |
| POST | `/api/models` | 拉取服务商模型列表 |
| GET | `/api/config` | 查询服务器是否预置了模型配置 |
| POST | `/api/auth/setup` · `/api/auth/login` · `/api/auth/logout` · `/api/auth/password` | 首次设置、登录、退出、修改密码 |
| GET | `/api/auth/status` | 是否已设置账户、是否已登录（无需登录） |
| GET/PUT/PATCH | `/api/project` | 读取、整体替换、按条合并当前字幕（`?since=版本号` 增量拉取） |
| GET | `/api/settings` | 读取服务器上的模型配置（不含 Key）和任务设置 |
| PUT | `/api/settings/profiles` | 保存模型配置列表（带版本号，冲突时返回 409） |
| PUT | `/api/settings/active` | 切换当前使用的配置 |
| PUT | `/api/settings/task` | 保存任务设置 |

## 自检

```bash
.venv/bin/python tests/test_api.py
```

不依赖 pytest，使用模拟的模型接口跑通解析、翻译、导出全流程。

## 登录与忘记密码

首次打开页面时会要求设置管理员用户名和密码。部署后请尽快完成设置：在设置之前，局域网里第一个打开页面的人可以抢先设置。

登录后可以点右上角的 👤 修改密码或退出登录。忘记密码时，在服务器上执行：

```bash
cd /opt/ai-subtitle && sudo -u ai-subtitle .venv/bin/python -m app.auth reset
```

然后重新打开页面设置新的用户名和密码。模型配置和字幕不受影响。

## 数据与备份

服务器上的数据都在 `data/` 目录（权限 700，文件权限 600），重新部署不会覆盖：

| 文件 | 内容 |
| --- | --- |
| `settings.json` | 模型配置（含 API Key）和任务设置 |
| `project.json` | 当前正在编辑的字幕 |
| `auth.json` | 登录用户名和密码哈希 |

备份或迁移服务器时复制整个目录即可：

```bash
sudo cp -a /opt/ai-subtitle/data ~/ai-subtitle-data-backup
```

本工具使用 HTTP 访问，密码和 Cookie 在局域网内是明文传输的。如需在公网使用，请参照上文用 Nginx 配置 HTTPS。
