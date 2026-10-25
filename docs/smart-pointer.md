# 智能指针独立扩展

智能指针把“这块区域想做什么”变成可选择、可修改的候选操作，最后写入当前聊天的草稿。它不提交聊天、不启动 Agent、不调用 Computer Use，也不修改文档或表格。

本次扩展同时包含区域选择界面、意图会话引擎、原生 REST 后端和可选的本地 Ollama 意图建议。它在 MISAKA AGENT 中独立安装，不依赖判断模型或免费模型扩展。ZIP 内包含自己的 `_impl/pointer_intents.py`，运行时不需要原始代码仓库。客户端安装器同时检查核心包的版本兼容性；安装包记录每个文件的 SHA256，安装、启用和禁用使用原生扩展管理流程。

## 桌面右上角浮框

在原生命令面板打开“智能指针”会隐藏原聊天窗口，屏幕右上角只保留一个宽 360、空态约 180–220 像素高的置顶浮框。候选或确认内容会让它自适应增高，最高 440 像素，更多内容在框内滚动；小屏幕会自动缩小到可用显示区域内；可以拖动标题栏或调整窗口尺寸。浮框直接复用已安装并启用的智能指针扩展及原生 UI SDK，不启动第二个聊天、网关或模型会话。未安装扩展时没有这个功能；安装后仍由原生 Plugins 页分别启用 Agent 和 Desktop 部分。

默认只显示圈选、关键词和候选；选区预览、手动环境与设置折叠，选中候选后才显示内容同意与确认。在浮框里完成圈选、关键词输入、多候选选择、补词换批、草稿预览和最终确认。“开始圈选”会临时隐藏浮框，显示系统屏幕选择器和实际桌面框选层，完成或取消后返回浮框。原聊天继续保持隐藏。默认不附图；用户必须明确选择附上严格裁剪的区域图片，或粘贴选区原文。确认后仍留在浮框，显示原聊天已接收草稿的结果；它不会自动发送聊天。

点“返回聊天 ×”、关闭浮框或按 Escape 会恢复原聊天窗口；浮框崩溃也会恢复。原聊天没有被关闭，其原会话、连接和输入框继续挂载。打开浮框时固定 session、profile、connection 三部分身份，最终草稿和可选图片通过受限原生 IPC 交回这个原窗口，原窗口再次校验身份并等待真正的输入框/附件确认。身份不匹配、窗口重载、失去确认、超时或旧代际都不会报告接收成功。成功交付的相同草稿在该浮框代际内去重，重试不会重复加入；部分附件已接收而文字失败时会提示先返回聊天检查。

浮框本身仅加载已启用的 smart-pointer 桌面入口，不枚举运行其他扩展，也不会复制任何 provider OAuth/API 凭证。主窗口隐藏期间，截图只保留在本机内存；候选建议不包含图片。用户返回聊天并显式发送后，已确认附件才经原生聊天流程交给该会话模型。

## 使用流程

1. 在智能指针界面选择自己明确提供的图像区域。裁剪图仅用于本地界面预览，不上传后端、模型或云端；区域坐标和用户提供的应用信息可进入意图会话。
2. 应用名和窗口标题可以作为线索；原生截图宿主仅提供窗口源名称时，sourceName 可作为最后的类型提示。普通屏幕名称保持未知类型。可以手动指定文字文档或电子表格；手动指定始终优先。应用信息缺失时，选择“未知”，通过关键词说明目标。插件不会把应用名、窗口标题或框选坐标描述成截图识别或 OCR 结果。
3. 输入关键词，查看多个候选的名称、说明和完整提示词预览。空输入或目标不清楚时，界面同时给出候选和具体追问。
4. 追加关键词、拒绝本轮候选或点击换一批。每轮获得新 revision，已展示或拒绝的候选不会重复出现。固定候选用尽时主动提问，允许换区域、类型或重新建会话，不假装产生了无限个新建议。
5. 选择本轮的一个候选并确认。后端只返回草稿；前端把它放入当前聊天输入框。检查内容后，由用户自行决定是否发送。更早一轮的选项无法提交。

默认确定性规则不调用任何模型。文字文档覆盖润色、改写、翻译和摘要；电子表格覆盖格式、清理、排序、公式、统计报表和图表，每类提供不同的可选方向。未知应用仍可用“翻译”“排序”“公式”“图表”等中英文关键词提出具体候选。

草稿会明确要求先获得用户选择的真实内容。模型看不到区域内容时必须提问，不能根据应用名称猜测单元格值、原文、公式或图表。清理、排序和格式操作均先形成预览；不会因选中候选而自动删除、覆盖或修改数据。

## 可选本地意图建议

独立设置 `local_suggestions` 控制本地 Ollama。它只辅助排列固定意图候选，不承担执行器选择、额度判断或模型路由。默认关闭；保存设置本身不调用模型。

启用后，仅接受 loopback HTTP Ollama 地址及已安装本地模型，不接受带 `cloud` 的模型名。请求 `/api/tags` 和 `/api/show` 确认本地模型元数据；不能确认本地权重或发现远端模型元数据时直接回退。生成请求只发送应用名、窗口标题、手动类型、关键词及允许的 action ID，不发送裁剪图、完整屏幕或 OCR 内容。请使用配置为本地推理的 Ollama 服务。

Ollama 必须返回严格 JSON：

```json
{"action_ids":["document.polish.formal","document.polish.clear"]}
```

只允许 2–5 个不同的、当前固定目录内的候选 ID；不接受任意提示词、Shell、工具指令或额外 JSON 字段。错误、服务不可用、超时及越界建议都回退到确定性规则。超时设置范围为 0.05–5 秒；所有请求共享该次建议的 deadline，响应大小最多 64 KiB。没有付费模型请求或自动下载模型的路径。

## 原生 API

后端使用 Hermes 的 `APIRouter`，由已安装且启用的原生插件挂载在 `/api/plugins/smart-pointer`。认证与 profile 隔离由 Hermes 的原生插件宿主处理；它不是另开的匿名 HTTP 服务。源码目录的存在不会自动安装或启用插件，`register(ctx)` 不注册工具、钩子或后台任务。

### 建立会话

`POST /sessions`（`/candidates` 是同义入口）：

```json
{
  "context": {
    "app": "Microsoft Excel",
    "window_title": "Sales.xlsx",
    "kind": "auto",
    "sourceName": "用户选择的屏幕",
    "region_id": "local-selection-1",
    "region": {"x": 120, "y": 80, "width": 700, "height": 400}
  },
  "keywords": "统计 图表"
}
```

`context` 仅接受 `app`、`window_title`、`kind`、`sourceName`、`region_id` 和 `region`。`kind` 为 `auto`、`document`、`spreadsheet` 或 `unknown`；auto 根据已有应用信息推断，unknown 保留未知类型。坐标必须是有限数，宽高必须为正，允许多显示器的负坐标。关键词总长度不超过 4000 字符。不接受图像或 OCR 字段。

直接返回会话对象，无额外 wrapper：

```json
{
  "session_id": "opaque-local-session-id",
  "revision": 1,
  "context": {"derived_kind": "spreadsheet", "kind_source": "app", "evidence_only": true},
  "keywords": "统计 图表",
  "candidates": [
    {
      "action_id": "spreadsheet.statistics.summary",
      "label": "生成统计摘要",
      "description": "给出总数、均值及适用的汇总指标。",
      "preview_prompt": "完整可审阅的草稿文本……"
    }
  ],
  "question": null,
  "mode": "draft-only",
  "executed": false,
  "suggestions": "disabled"
}
```

示例仅缩写了 context 和候选列表。实际通常返回 2–4 个不同候选；匹配类型冲突或候选用尽时允许空列表，并返回具体 `question`。`suggestions` 为 `disabled`、`local` 或 `fallback`。

### 修改与选择

| 方法和相对路径 | 请求 | 返回 |
| --- | --- | --- |
| `GET /sessions/{id}` | 无 | 当前会话与本轮候选。 |
| `POST /sessions/{id}/refine` | `{"revision":1,"keywords":"按月份","reject_action_ids":["spreadsheet.chart.recommend"]}` | 关键词追加，新 revision、新候选；可附 `context` 手动修改类型。 |
| `POST /sessions/{id}/commit` | `{"revision":2,"action_id":"spreadsheet.chart.line"}` | `{session_id,revision,action_id,draft_prompt,executed:false}`。 |

拒绝列表只能包含当前展示的 action ID。未选定 action、选择未展示的 ID 或旧 revision 均被拒绝。相同候选的重复 commit 返回相同草稿；已选定后换选其他候选需要先生成新一轮。客户端仍须防止重复点击把相同草稿多次插入。

会话保存在进程内存，每个 profile 独立，默认上限 128 个、闲置过期一小时。重启、过期或保存建议模型设置会使旧会话失效；重新建立会话即可。区域图像、关键词和草稿不会作为会话文件写入磁盘。

### 独立设置

`GET /settings` 返回默认或已保存配置。`POST /settings` 接受相同结构：

```json
{
  "local_suggestions": {
    "enabled": false,
    "model": "qwen3:4b",
    "base_url": "http://127.0.0.1:11434",
    "timeout": 2
  }
}
```

只有用户显式保存的这些配置写入当前 profile 的 `smart-pointer/settings.json`。设置不含云端密钥，也不会改变路由判断模型开关。API 的 400 表示输入或选择非法，404 表示会话不存在/过期，409 表示 revision 或已选候选冲突，503 表示独立包中的实现缺失，需要重装。

## 验证

运行 `python -m pytest -q tests/test_pointer_intents.py`。测试覆盖全部十类操作、应用/手动类型优先级、未知类型、空输入追问、关键词累计、拒绝与换批去重、旧 revision、并发选择、过期、零默认请求、严格本地建议、错误回退、真实 loopback HTTP 传输，以及没有 core/judge/discovery 的独立包 FastAPI 会话流程。

这些测试验证意图与草稿边界，不声称已在用户真实 Word、Excel 或操作系统的屏幕会话中完成执行。用户确认发送草稿后如何读取文档与执行任务，继续由 Hermes 已授权的工具及本地权限决定。
