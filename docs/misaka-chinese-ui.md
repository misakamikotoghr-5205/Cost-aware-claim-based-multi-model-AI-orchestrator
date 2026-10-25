# MISAKA AGENT 中文界面

`0010-misaka-chinese-ui.patch` 在固定上游提交及 0001–0009 补丁之后应用。它只调整客户端界面语言，不修改提供商认证协议、模型参数标识、文件路径或用户对话正文。

新配置在 Windows 和 Ubuntu 上均从简体中文开始，不再随英文操作系统切换为英文。首次读取旧配置时会将界面语言迁移为 `display.language: zh`，并保存 `display.misaka_locale_version: 1`。迁移完成后，用户通过原有语言选择器选择的语言会保留；后台重试、切换配置和迟到的保存结果不会覆盖另一配置的选择。语言写入继续使用读取时捕获的配置来源，保留其他配置字段。

本补丁为中文目录补齐此前缺少的 522 个词条，另外修正 11 个自然语言英文词条。新增测试直接比较中文覆盖对象与英文源对象，防止合并后的英文回退掩盖漏译；参数化词条保留动态值。第三方品牌、OAuth、API、MCP、真实命令与技术协议名称保留原名。

| 原界面字段 | 中文界面 |
| --- | --- |
| Sessions / New session | 会话 / 新建会话 |
| Bots / Capabilities | 机器人 / 技能与工具 |
| Settings / Backend ready | 设置 / MISAKA AGENT 桌面版已就绪 |
| Give MISAKA AGENT a task | 给 MISAKA AGENT 一个任务 |
| Agent / Desktop 插件开关 | 智能体 / 桌面 |
| Low / Med / High 推理强度 | 低 / 中 / 高 |
| Select a screen / Cancel | 选择屏幕 / 取消 |
| Screen 1（截图选择按钮） | 屏幕 1 |

新会话标题按当前语言解析；输入框提示会在语言改变时更新，不会保留启动时选中的英文。用户自己命名为 `New session` 的会话仍显示其原名。设置中的服务商说明、窗口控制、原生菜单、保存和目录选择对话框、截图覆盖层、工具和消息状态、表情控件、媒体预览与通用缩放控件均补齐中文。原生加载失败恢复页显示中文说明和重新加载操作，实际错误详情及修复命令仍原样保留并转义。

## 验证

在准备好的原生源码目录中，从 `apps/desktop` 运行：

```bash
../../node_modules/.bin/vitest run --project ui \
  src/i18n/context.test.tsx src/i18n/profile-locale.test.tsx \
  src/i18n/languages.test.ts src/i18n/runtime.test.ts \
  src/i18n/misaka-chinese.test.tsx src/i18n/catalog-completeness.test.ts \
  src/app/chat/composer/hooks/use-composer-placeholder.test.tsx \
  src/lib/reasoning-effort.test.ts

../../node_modules/.bin/vitest run --project electron \
  electron/misaka-native-menu.test.ts electron/region-capture.test.ts \
  electron/region-capture-controller.test.ts electron/region-capture-geometry.test.ts \
  electron/renderer-load-error-page.test.ts
```

测试覆盖新配置、旧英文配置的一次迁移、重启后保留手动语言、进行中的迁移与手动保存排序、配置来源隔离、语言切换后的输入框提示、默认标题、推理强度标签、中文目录结构与插值、原生菜单动作保留、截图流程与恢复页安全转义。设置保存、服务商说明即时切换、Markdown 媒体和引用数据另有对应回归测试。

上述是源码级验证。最终组合构建与真实 Windows/Ubuntu 截图验收由发布流程另行记录；本文不把单元测试当作 OAuth 登录或 Office 实机验收。
