# MISAKA AGENT 0.2.1 快速开始

0.2.1 交付可复现的原生桌面源码、独立扩展 ZIP 和独立 Agent Skill ZIP。
它基于完整 Hermes 原生客户端，保留聊天、历史、工具、Skills、MCP、模型登录和多种连接方式。
本说明不把源码包称为已签名的 Windows 安装器或 Ubuntu deb，也不代表所有平台、账号和 Office 操作均已验收。

一个客户端可以直接使用本机配置的 OAuth、API 或本地模型；无需先部署云端。
云端协同、判断模型、免费模型发现和智能指针均按需使用，安装包不捆绑模型权重。

## 1. 准备构建环境

本文以 Windows 11 和 Ubuntu 24.04 桌面环境为例。需要图形会话、Git、宿主 Python 3.11+，以及原生依赖编译工具。
示例使用 Python 3.12；底层运行时的 Python 与依赖由官方 PM 引导脚本另行准备。

锁定上游接受的 Node.js 版本为 `^22.22.0 || ^24.11.0 || >=26.0.0`；可选 Node.js 24 LTS，至少 24.11。
npm 须满足 `<11.10.0 || >=11.17.0`。先用 `node --version`、`npm --version` 检查。
首次构建需要联网下载锁定源码、依赖及工具，不能当作离线安装包使用。

- Windows：安装 Git for Windows、Python 3.12、合适版本的 Node/npm；安装 Visual Studio Build Tools 的 C++ 桌面开发组件与 Windows SDK，并确保 PowerShell 可以按本机策略运行项目脚本。建议使用短路径，如 `C:\src\misaka-agent`。
- Ubuntu：准备 C/C++ 编译器、X11 开发库和 Electron 运行库。以下包名适用于 Ubuntu 24.04；其他发行版按对应包名安装。

```bash
sudo apt-get update
sudo apt-get install -y git unzip python3-venv python3-pip build-essential pkg-config libx11-dev libxi-dev libgtk-3-0t64 libnss3 libxss1 libnotify4 libxtst6 libasound2t64 libgbm1
```

## 2. 从源码启动完整桌面

### Ubuntu / Bash

```bash
git clone https://github.com/misakamikotoghr-5205/Cost-aware-claim-based-multi-model-AI-orchestrator.git misaka-agent
cd misaka-agent
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
misaka_home="$HOME/.misaka-agent"
python scripts/prepare_native_client.py --hermes-home "$misaka_home" --launch
```

### Windows / PowerShell

以下命令均为独立单行，不使用 Bash 的反斜杠续行。

```powershell
git clone https://github.com/misakamikotoghr-5205/Cost-aware-claim-based-multi-model-AI-orchestrator.git C:\src\misaka-agent
Set-Location C:\src\misaka-agent
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$misakaHome = Join-Path $HOME '.misaka-agent'
.\.venv\Scripts\python.exe scripts\prepare_native_client.py --cache-dir C:\misaka-cache --hermes-home $misakaHome --launch
```

此命令自动检出锁定的原生源码、依次应用本项目补丁、安装锁定的 JS 依赖、准备并探测原生运行时，最后启动客户端。
`--launch` 调用的原生 `start` 已包含桌面构建。若只想构建、不打开窗口，将 `--launch` 换为 `--build`。
也可使用统一入口 `python -m src.desktop --hermes-home <同一个目录>`；Windows 用上面的虚拟环境 Python。

准备日志会打印 `checkout` 和 `native_runtime.launcher`。保存这两个真实绝对路径：

- `checkout` 是打过补丁的**原生源码目录**，也是后续 `--upstream` / Skill `--root` 的值。
- `native_runtime.launcher` 是底层 CLI 启动器，供重启后台和 Skill 使用；它不是 MISAKA 图形程序的 exe。
- 始终使用同一个 `--hermes-home`，避免将一个安装的扩展装进另一个配置目录。

默认缓存位于 `~/.cache/misaka-agent`；Windows 上面的示例改用短缓存路径。
复用已有原生源码时可传 `--upstream <checkout绝对路径> --skip-deps`，前提是它仍符合锁定提交和精确补丁检查。
不要手工更新这个原生目录；客户端的官方上游自动更新入口已禁用，升级从本项目取得同版源码和扩展。

## 3. 首次连接模型与开启核心

首启可选“本地运行模型”“使用 API Key 或自建端点”或某个提供方的 OAuth 登录，也可稍后配置。
已进入主界面时，使用 **设置 → 提供方 → 账号 / API 密钥 / 自定义端点 / 本地模型**。
选择凭证和模型继续走原生流程，不需要把模型密钥交给云端调度服务。

本地推理需另行安装运行时和权重。聊天主模型必须满足原生运行时的上下文要求：当前至少 64,000 Token，服务实际提供的窗口也须足够；不要仅改一个声明值绕过检查。
用于短任务判断的小模型与聊天主模型是两种配置。

准备脚本只默认安装 `orchestrator-core`。要使用 Dashboard、节点管理和扩展安装页：

1. 打开 **技能与工具 → 插件 → 云端本地协同**。
2. 明确开启 **Agent** 和 **桌面** 两个开关，等待操作完成。
3. 当前无进行中的任务时退出客户端，重启该配置的本机后台，再重新打开客户端。新插件的 HTTP 接口需要重新挂载，不能把“桌面开关已亮”当作后台已加载。

可用日志中打印的真实 launcher 停止后台。下面的路径占位符必须先替换：

```bash
native_root="/填写日志中的checkout绝对路径"
launcher="/填写日志中的native_runtime.launcher绝对路径"
HERMES_HOME="$misaka_home" "$launcher" --profile default gateway stop --yes
python scripts/prepare_native_client.py --hermes-home "$misaka_home" --skip-deps --launch
```

```powershell
$nativeRoot = 'C:\填写日志中的checkout目录'
$launcher = 'C:\填写日志中的真实CLI启动器路径'
$env:HERMES_HOME = $misakaHome
& $launcher --profile default gateway stop --yes
.\.venv\Scripts\python.exe scripts\prepare_native_client.py --cache-dir C:\misaka-cache --hermes-home $misakaHome --skip-deps --launch
```

示例明确选择 `default`。使用具名 profile 时替换为该名称，并确保操作的是对应配置。
`--yes` 会确认停止；请先等待或自行处理当前任务。新安装默认不收集、也不上传统计，已有明确选择保留；隐私设置仍如实标明第三方 Nous 的统计服务。

## 4. 按需安装三个独立扩展

| ZIP | 用途 |
| --- | --- |
| `local-judge-0.2.1.zip` | 本机 Ollama 判断；默认只送最多 512 字符的任务摘要和最多 3 个候选，辅助选择，不接管执行授权。 |
| `free-models-0.2.1.zip` | 显式刷新免费模型目录与本机模型；发现零价格不等于账号有可用额度。 |
| `smart-pointer-0.2.1.zip` | 框选区域、关键词、多候选与草稿确认；可独立使用，不要求另装前两个包。 |

从交付合集取出需要的 ZIP。也可在主仓库生成各自的包；此命令只打包，不自动安装或开启：

```bash
python scripts/build_extensions.py --upstream "$native_root" --ids all --output dist/extensions
python -m src.extension_packages --hermes-home "$misaka_home" install dist/extensions/local-judge-0.2.1.zip
python -m src.extension_packages --hermes-home "$misaka_home" install dist/extensions/free-models-0.2.1.zip
python -m src.extension_packages --hermes-home "$misaka_home" install dist/extensions/smart-pointer-0.2.1.zip
python -m src.extension_packages --hermes-home "$misaka_home" list
```

```powershell
.\.venv\Scripts\python.exe scripts\build_extensions.py --upstream $nativeRoot --ids all --output dist\extensions
.\.venv\Scripts\python.exe -m src.extension_packages --hermes-home $misakaHome install dist\extensions\local-judge-0.2.1.zip
.\.venv\Scripts\python.exe -m src.extension_packages --hermes-home $misakaHome install dist\extensions\free-models-0.2.1.zip
.\.venv\Scripts\python.exe -m src.extension_packages --hermes-home $misakaHome install dist\extensions\smart-pointer-0.2.1.zip
.\.venv\Scripts\python.exe -m src.extension_packages --hermes-home $misakaHome list
```

只运行所需扩展的安装行；也可用客户端“云端本地协同 → 从扩展包安装”选择 ZIP。
随后在原生插件页分别开启该包的 Agent / 桌面部分，并按上一节重启本机后台。
安装 ZIP 不等于启用，启用扩展也不等于已在路由中使用它；判断和免费模型参与路由还需在协同页明确打开相应开关。

判断模型设置中可检测本机 Ollama、测试连接，并由用户点击安装所选权重；没有自动下载或付费兜底。
不安装判断扩展仍可使用确定性评分，不安装免费发现扩展仍可在原生聊天手工配置模型。
升级旧版本前先停止相关工作、禁用扩展、重启后台卸载旧模块；旧可选包需先移除，再安装同为 0.2.1 的 core 与所需可选包，详见[扩展安装说明](native-client.md#optional-packages-are-separate-zips)。

## 5. Dashboard 与智能指针

**Dashboard** 随核心提供，点击“读取最新额度”才发起刷新。未登录、服务没有公开用量、记录过期或获取失败会显示原因，未知额度不当作零余额。
订阅窗口、货币余额和共享云端工作配额分别展示；免费目录、拥有 API Key、CLI 显示已连接都不等于已经查到了余额。
页面的本机 profile 决定账号范围，“账号与模型设置”打开该范围的原生设置。

安装并启用智能指针后，在原生命令面板搜索 **打开智能指针**：

1. 主聊天暂时隐藏，右上角出现宽约 360、空态高约 180 像素的小浮框；内容增多时自适应增高。
2. 点击“开始圈选”，在真实桌面选择区域，然后输入关键词，例如“润色”“按月统计”。
3. 查看多个候选；不合适就补充关键词、拒绝或换一批，必要时换区域或指定文档 / 表格类型。
4. 选择候选，审阅提示词；粘贴选区原文，或明确同意附上严格裁剪的区域图片，再确认加入草稿。
5. 点击“返回聊天 ×”或按 Escape，恢复原来的聊天窗口。检查草稿后再自行发送。

确认只交回原会话草稿，不直接改 Word/Excel，也不自动发消息。应用名和标题只是线索，不等于 OCR；模型没有真实内容时应继续询问。
详见[智能指针](smart-pointer.md)。

## 6. 可选：一云连接多台本地电脑

只用本机聊天时可跳过本节。协同时，一台调度服务管理配对、定向任务、租约与共享工作配额；每台本地机保留自己的文件、工具和模型登录，通过出站 HTTPS 领取任务，不需要向云端开放本机模型或控制端口。

服务端先按[云端多节点协议](fleet-protocol.md)配置独立 owner 调度凭据，将 token JSON 安全注入环境变量 `MISAKA_FLEET_TOKENS`。在装好本仓库依赖的云端宿主机上启动，例如：

```bash
mkdir -p "$HOME/.misaka-fleet"
python -m src.runtime_cli server --db "$HOME/.misaka-fleet/tasks.sqlite" --quota-db "$HOME/.misaka-fleet/quota.sqlite" --tokens-env MISAKA_FLEET_TOKENS --host 127.0.0.1 --port 8765
```

```powershell
New-Item -ItemType Directory -Force -Path (Join-Path $HOME '.misaka-fleet') | Out-Null
.\.venv\Scripts\python.exe -m src.runtime_cli server --db "$HOME\.misaka-fleet\tasks.sqlite" --quota-db "$HOME\.misaka-fleet\quota.sqlite" --tokens-env MISAKA_FLEET_TOKENS --host 127.0.0.1 --port 8765
```

示例仅监听云端宿主机回环地址；跨机访问须配置 HTTPS / 受控私网入口，再指向该服务。
在管理端客户端的“云端本地协同”填写服务地址和调度令牌环境变量名（默认 `ORCHESTRATOR_TOKEN`），创建一次性配对码。
其他电脑各自装好客户端和 core，输入配对码、配置自己的工作区别名与工具授权，再明确启动本机任务接收。可对多台电脑重复配对，管理端选择目标节点后下发任务。

调度凭据与模型 OAuth/API 凭据是不同用途；模型凭据留在执行节点。共享配额需有实际观测数据，不会因启动服务自动生成余额。
远端任务使用工作区别名和相对路径，真实本地路径由各节点映射；离线或结果不明时需先核对，不能把未知结果当作可安全重跑。

## 7. 将独立 Skill 接入 Claude Code、Codex 或 Hermes

独立包是 `misaka-agent-0.2.1.zip`，不是上述三个原生扩展之一。也可在主仓库运行：

```bash
python scripts/build_agent_skill.py
```

输出 `dist/skills/misaka-agent-0.2.1.zip`，ZIP 顶层已含 `misaka-agent/`。将其解压到宿主配置的技能目录，保留 `SKILL.md`、`scripts/`、`references/` 等文件在一起：

| 宿主 | 常用目录 |
| --- | --- |
| Claude Code | `~/.claude/skills/`，或项目的 `.claude/skills/` |
| Codex | `~/.agents/skills/`；宿主版本有不同配置时使用其实际技能目录 |
| Hermes | 选定 `HERMES_HOME` 下的 `skills/` |

以下以 Codex 为例，安装到其他宿主时只替换技能目录。

```bash
skills_dir="$HOME/.agents/skills"
mkdir -p "$skills_dir"
unzip dist/skills/misaka-agent-0.2.1.zip -d "$skills_dir"
skill_cli="$skills_dir/misaka-agent/scripts/misaka.py"
python3 "$skill_cli" --connection local --launcher "$launcher" --root "$native_root" --home "$misaka_home" setup
python3 "$skill_cli" --connection local --profile default status
python3 "$skill_cli" --connection local --profile default quota
python3 "$skill_cli" --connection local --profile default extensions
```

```powershell
$skillsDir = Join-Path $HOME '.agents\skills'
New-Item -ItemType Directory -Path $skillsDir -Force | Out-Null
Expand-Archive -LiteralPath 'dist\skills\misaka-agent-0.2.1.zip' -DestinationPath $skillsDir
$skillCli = Join-Path $skillsDir 'misaka-agent\scripts\misaka.py'
.\.venv\Scripts\python.exe $skillCli --connection local --launcher $launcher --root $nativeRoot --home $misakaHome setup
.\.venv\Scripts\python.exe $skillCli --connection local --profile default status
.\.venv\Scripts\python.exe $skillCli --connection local --profile default quota
.\.venv\Scripts\python.exe $skillCli --connection local --profile default extensions
```

Skill CLI 只需 Python 3.10+ 标准库，但必须已有匹配的 MISAKA 原生运行时及已启用的 core。
`--root` 填原生源码目录，不是本说明所在的主仓库目录；`--home` 填包含默认配置和 `profiles/` 的根，具名配置通过 `--profile NAME` 选择。
`setup` 只保存具名连接的三条非秘密绝对路径，不登录或启动 Agent；后续命令可启动选定的原生 gateway。
临时认证票据由随包的桥接程序获取，不要复制 provider token、手工运行 `runtime_bridge.py` 或安装 ChatGPT 插件来代替这个步骤。

让宿主按其方式重新扫描技能或新开会话，然后明确调用 `misaka-agent`。
`quota` 只读缓存，显式刷新使用 `quota-refresh`；节点与指针命令见 [Skill 命令](../skills/misaka-agent/references/commands.md)。
Skill 的指针接口只从给定线索生成候选 / 草稿；真实桌面框选仍由客户端手势完成。未安装的可选能力不会被 Skill 自动安装。

## 交付与验收边界

构建、界面、API、某个模型或某个平台的测试通过，只证明对应检查。
请以本次交付的实际报告区分已验收、未验收和环境受限项；这里不预先宣布 Windows 最终构建、真实 OAuth 登录或 Word/Excel 实操全部通过。
签名安装器、完整离线模型权重及各提供方可查询余额也不是源码和扩展 ZIP 自动附带的内容。
