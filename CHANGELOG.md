# 更新日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.2.0] - 2026-10-16

### 新增

- **桌面通知**：窗口不在前台时，一轮回复结束、以及出现待处理交互（权限审批、
  `ask_user` 提问、计划确认）都会弹出系统通知；窗口在前台且通知属于当前会话时不打扰，
  点击通知聚焦窗口并切回该会话，回到应用即视为已读并撤回通知。
  - 完成判定观察转发事件参数 `(sessionId, running)`，**不绑定事件名**；
  - 等待交互以 dsh 自己渲染的 `data-approval-key` / `data-question-key` /
    `data-plan-review-key` 面板为准，`MutationObserver` + 1 秒轮询，**不依赖任何
    dsh 事件名**；同一轮 `ask_user` 的多道题只通知一次；
  - 页面控制台可用 `globalThis.__DSH_SHELL_NOTIFY_DISABLE__ = true` 关闭。
- **开发者工具**：F12 或 Ctrl+Shift+I 打开/收起 WebKit 检查器（加载页阶段也可用）。
- **诊断开关**：`DSH_WEBVIEWGTK_DEBUG=1` 打印实际注入的桥脚本、收到的每条载荷与被丢弃
  的原因；页面侧 `globalThis.__DSH_SHELL_NOTIFY_DEBUG__ = true` 打印逐帧观察与心跳。
- 测试脚本：`tests/bridge_smoke_test.py`（桥逻辑，仅需 node）、
  `tests/notification_probe.py`（真实通知与点击回调）、
  `tests/bridge_webkit_probe.py`（真实 WebKit 注入与通道）。

### 修复

- 接入 `Gtk.Application` 的 `activate` 信号，消除
  `Your application does not implement g_application_activate()` 告警。
- `script-message-received` 回调拿到的载荷可能是 JSON 文本、也可能是已反序列化的对象，
  现在两者都接受。
- 来源校验不再丢弃导航早期（`get_uri()` 为空时）到达的真实通知。

### 变更

- 应用 id 与 desktop 文件名统一为 `com.github.shellexy.DshWebviewGtk`
  （通知的应用名与图标由此解析）。安装后请删除旧的 `dsh-webviewgtk.desktop`。

### 已知问题

- **退出后 glibc 打印 `corrupted double-linked list`**：只有加载过真实 dsh 页面才会出现，
  发生在主循环返回之后，不影响使用。已确认与 shell 逻辑无关（只建窗口的路径干净，
  打开检查器也不复现），`WEBKIT_DISABLE_DMABUF_RENDERER=1` 可消除，因此判定为
  WebKitGTK 2.52.6 退出期的 DMA-BUF 渲染缺陷。**不要**为此在主循环返回后显式
  `webview.destroy()` / `window.destroy()`：实测会把无害提示变成段错误。
