#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dsh-webviewgtk - 基于 GTK4 + WebKitGTK 6.0 的 dsh web 启动器

依赖（Debian/Ubuntu）:
    sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-webkit-6.0

用法:
    python3 dsh-webviewgtk.py
"""

import base64
import hashlib
import json
import mimetypes
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")
gi.require_version("WebKit", "6.0")

from gi.repository import GLib, Gdk, Gio, Gtk, WebKit  # noqa: E402

HOST = "127.0.0.1"
PORT = 3081
URL = f"http://{HOST}:{PORT}"
APP_NAME = "dsh-webviewgtk"
# 应用 id 同时是 GApplication/D-Bus 身份与通知分组键。
# 只在 dsh 已经打印出带 token 地址时才回退到固定端口，因此该地址的 origin
# 对通知桥是可信的；若自行改用别的端口，请同步修改 PORT。
APP_ID = "com.github.shellexy.DshWebviewGtk"
ICON_NAME = APP_NAME
ICON_FILE = Path(__file__).resolve().parent / f"{APP_NAME}.svg"

# 页面 → 外壳的桌面通知桥。注入脚本与外壳共用下面两个名字。
BRIDGE_CHANNEL = "dshShellNotify"
BRIDGE_GLOBAL = "__DSH_SHELL_NOTIFY__"
# 桥脚本版本：改动 BRIDGE_JS_TEMPLATE 时递增，启动横幅会打印它以便确认跑的是哪一版。
BRIDGE_VERSION = "21-teardown-reverted"

# 外壳通知正文的显示上限（freedesktop 通知本身也有长度限制）。
NOTIFICATION_BODY_MAX_CHARS = 480

# 待处理交互的通知标题：触发它的转发瀑布事件名 → 标题。
PENDING_TITLE = "DeepSeek Harness 在等你"
PENDING_TITLES = {
    "approval/request": "DeepSeek Harness 在等你批准",
    "user-questions/request": "DeepSeek Harness 在等你回答",
}

# 允许交给系统默认应用打开的外部 URL 协议白名单。
# 如果还需要支持其他自定义协议，直接往这个集合里添加即可，例如：
# "obsidian", "vscode", "slack", "zoommtg" 等。
ALLOWED_EXTERNAL_SCHEMES = {
    "http",
    "https",
    "mailto",
    "tel",
    "sms",
    "ftp",
    "ftps",
    "magnet",
    "irc",
    "ircs",
}

# dsh 新版本启动后会在输出里打印带 token 的访问地址，例如：
#   dsh web: http://127.0.0.1:3081/?token=xxxxxxxx
# 去掉 ANSI 转义后再从输出行里提取这个 URL。
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_READY_URL_RE = re.compile(
    r"dsh web:\s*(https?://(?:127\.0\.0\.1|localhost):\d+/(?:\?token=[^\s]+)?)"
)


def _extract_ready_url(line):
    """从 dsh 输出行中提取带 token 的本地访问地址。"""
    cleaned = _ANSI_ESCAPE_RE.sub("", line)
    match = _READY_URL_RE.search(cleaned)
    return match.group(1) if match else None


def _notification_body(payload, kind):
    """把事件载荷整理成一行受长度限制的通知正文。"""
    fallback = "有新的待处理交互" if kind == "pending" else "会话状态发生变化"
    parts = [str(payload.get("title") or "").strip()]
    body = " ".join(str(payload.get("body") or "").split())
    if body:
        parts.append(body)
    text = " — ".join(part for part in parts if part)
    if len(text) > NOTIFICATION_BODY_MAX_CHARS:
        text = text[: NOTIFICATION_BODY_MAX_CHARS - 1] + "…"
    return text or fallback


# 注入页面的桥在文档开始执行，早于页面自身脚本。
# "这轮跑完了"从 /api/remote.mux 上观察：dsh 客户端用 (sessionId, running) 这对
# 转发事件参数驱动它自己的会话状态，桥只按参数形状识别，不绑定事件名。
# "在等你"以页面渲染出的待处理面板为准（data-approval-key / data-question-key /
# data-plan-review-key，dsh 自己的稳定标识），由 MutationObserver 加 1 秒轮询发现，
# 不依赖任何 dsh 事件名：实测某些 dsh 版本根本不把这类等待以转发瀑布送到浏览器。
# 若哪天真收到了 approval/request 或 user-questions/request 瀑布帧，它只用来把
# 标题说得更准，并覆盖"后台会话在等你、面板不在当前视图"这一种情况。
# 页面级开关：把 globalThis.__DSH_SHELL_NOTIFY_DISABLE__ 置为 true 可整体关闭。
BRIDGE_JS_TEMPLATE = """
(() => {
  'use strict'
  const CHANNEL = '__BRIDGE_CHANNEL__'
  const GLOBAL = '__BRIDGE_GLOBAL__'
  const PENDING_MARKERS = '[data-approval-key],[data-question-key],[data-plan-review-key]'
  // 外壳用 DSH_WEBVIEWGTK_DEBUG=1 启动时置为 1：每次页面加载都自带调试输出，
  // 免得手动设的标志被页面重载冲掉。
  const FORCED_DEBUG = __FORCED_DEBUG__
  // 等待用户交互的转发瀑布事件 → 通知标题里的一句话。只影响标题：
  // 未列出的瀑布同样按待处理交互处理，以免 dsh 改名后整类通知消失。
  const PENDING_TRIGGERS = {
    'approval/request': '在等你批准',
    'user-questions/request': '在等你回答',
  }

  const host = window.webkit?.messageHandlers?.[CHANNEL]
  if (host === undefined) return

  const send = (payload) => {
    try { host.postMessage(payload) } catch (error) { /* 外壳已消失，通知本就无处可去 */ }
  }
  const enabled = () => globalThis.__DSH_SHELL_NOTIFY_DISABLE__ !== true

  // 页面级调试：外壳用 DSH_WEBVIEWGTK_DEBUG=1 时是强制的；否则在控制台执行
  // globalThis.__DSH_SHELL_NOTIFY_DEBUG__ = true 打开，之后每条观察到的帧都会打印。
  // 用 console.log 而不是 console.debug：WebKit 检查器默认可能过滤掉 Debug 级别。
  const debugEnabled = () => FORCED_DEBUG || globalThis.__DSH_SHELL_NOTIFY_DEBUG__ === true
  const log = (...parts) => {
    if (!debugEnabled()) return
    try { console.log('[dsh-shell-notify]', ...parts) } catch (error) { /* 控制台不可用 */ }
  }

  // 侧栏当前选中的会话行：外壳据此抑制"已聚焦窗口的当前会话"通知。
  const activeSessionId = () => {
    const row = document.querySelector('[role="treeitem"][aria-selected="true"]')
    const key = row?.getAttribute?.('data-row-key') ?? ''
    return key.startsWith('session:') ? key.slice('session:'.length) : ''
  }

  // 桥的每一步都直接回传外壳（只打印终端），任何中断都必须看得见原因，
  // 否则一个静默异常就会让整类通知消失且毫无线索。
  const fail = (stage, error) => {
    const detail = error instanceof Error ? `${error.name}: ${error.message}` : String(error)
    send({ kind: 'diagnose', message: `error stage=${stage} ${detail}` })
  }
  const guard = (stage, body) => {
    try { return body() } catch (error) { fail(stage, error); return undefined }
  }
  // 未捕获异常（含异步回调）也回传终端：否则一次抛错只会让后续静默消失。
  guard('install-error-hook', () => {
    addEventListener('error', (event) => {
      fail('window-error', event.error ?? event.message ?? 'unknown')
    })
  })
  guard('install-rejection-hook', () => {
    addEventListener('unhandledrejection', (event) => { fail('unhandled-rejection', event.reason ?? 'unknown') })
  })
  send({ kind: 'diagnose', message: 'bridge-start href=' + location.href })

  // 把可能抛错的初始化逐段上报：console 的静默 try/catch 会让整条链无声失效。
  guard('init-log', () => { log('bridge ready', 'channel=' + CHANNEL, 'href=' + location.href) })
  send({ kind: 'diagnose', message: 'log-ready' })
  guard('init-global', () => {
    // 给以后由 dsh 插件主动调用预留同一通道，目前仅外壳自己消费。
    globalThis[GLOBAL] = (payload) => { if (enabled()) send(payload) }
  })
  send({ kind: 'diagnose', message: 'global-ready' })
  guard('init-active-session', () => { send({ kind: 'diagnose', message: 'active=' + (activeSessionId() || '(none)') }) })
  send({ kind: 'diagnose', message: 'active-ready' })

  // MessageEvent.data 的取值器只观察不修改；抛错也绝不影响页面自身的消息。
  guard('install-message-hook', () => {
    const dataGetter = typeof MessageEvent === 'function'
      ? Object.getOwnPropertyDescriptor(MessageEvent.prototype, 'data')?.get
      : undefined
    if (typeof dataGetter !== 'function') return
    Object.defineProperty(MessageEvent.prototype, 'data', {
      configurable: true,
      enumerable: true,
      get() {
        const raw = dataGetter.call(this)
        guard('inspect-frame', () => { inspectFrame(raw) })
        return raw
      },
    })
  })
  send({ kind: 'diagnose', message: 'message-hook-ready' })

  function inspectFrame(raw) {
    const frame = decodeFrame(raw)
    if (frame === undefined) return
    if (frame.type === 'item') inspectItem(frame.value)
    else if (frame.type === 'waterfall') inspectWaterfall(frame)
  }

  // 转发事件：api-session/status 的 running→false 就是本轮结束。
  function inspectItem(value) {
    if (value?.type !== 'emit') return
    // 转发事件是页面会话状态的来源，但事件名可能随 dsh 版本变化：
    // 一轮结束的判定不绑定事件名，只看 (sessionId, running) 这对参数。
    const args = value.args
    if (!Array.isArray(args) || args.length !== 2) return
    if (typeof args[0] !== 'string' || args[0] === '') return
    if (typeof args[1] !== 'boolean') return
    log('status', value.event, args[0], args[1])
    if (args[1]) return
    notify('completed', args[0], '这一轮已经结束')
  }

  // 等待用户交互是转发"瀑布"而不是转发事件，只有它是权威触发点：
  // 权限审批与 tool ask_user 的提问都从这里到达浏览器，事件名已知时直接通知。
  // dsh 若给这类瀑布改名，至少还会在调试日志里现形；未知瀑布不凭空通知，
  // 改由下方 DOM 面板兜底触发。
  function inspectWaterfall(frame) {
    const trigger = PENDING_TRIGGERS[frame.event]
    log('waterfall', frame.event, frame.agentId, frame.eventId)
    if (trigger === undefined) return
    presentPending(frame.event, String(frame.agentId ?? ''), frame.eventId)
  }

  // mux 帧是页面自己 WebSocket 上的文本 JSON；少数部署会对二进制帧压 gzip。
  function decodeFrame(raw) {
    try {
      if (typeof raw === 'string') return JSON.parse(raw)
      if (raw instanceof ArrayBuffer) return JSON.parse(new TextDecoder().decode(maybeGunzip(new Uint8Array(raw))))
      if (ArrayBuffer.isView(raw)) {
        return JSON.parse(new TextDecoder().decode(
          maybeGunzip(new Uint8Array(raw.buffer, raw.byteOffset, raw.byteLength))))
      }
    } catch (error) { /* 明文帧或非 mux 帧，原样放行 */ }
    return undefined
  }

  function maybeGunzip(bytes) {
    if (bytes.length < 2 || bytes[0] !== 0x1f || bytes[1] !== 0x8b) return bytes
    // 解压是异步的，而 MessageEvent.data 的取值器必须同步返回，
    // 因此 gzip 压缩的帧直接放行、不做观察。
    return bytes
  }

  // 已经通知过的待处理交互，避免每次 DOM 变化重复弹出。
  const pendingSeen = new Set()
  // DOM 轮询间隔：观察器之外的第二条发现路径，代价是一次选择器查询。
  const PENDING_POLL_INTERVAL_MS = 1000
  // 等 DOM 真正渲染出待处理面板的时间；仅用于瀑布帧先到、面板还没渲染的情况。
  const PENDING_FALLBACK_DELAY_MS = 1500

  // 主路径：瀑布帧到达即触发。会话 id 优先取当前渲染出来的面板所属会话，
  // 后台会话的面板不在 DOM 里时退回 agentId（本地与 sessionId 同值）。
  // 面板出现说明请求就在当前会话里，此时不必为别的会话重复通知，直接登记去重。
  function presentPending(trigger, agentId, requestId) {
    if (!enabled() || requestId === '') return
    const key = 'request:' + requestId
    if (pendingSeen.has(key)) return
    const marker = document.querySelector(PENDING_MARKERS)
    if (marker !== null) {
      pendingSeen.add(key)
      const sessionId = activeSessionId()
      if (sessionId !== '') pendingSeen.add(sessionId)
      log('pending', trigger, sessionId, markerKey(marker))
      notify('pending', sessionId, '', markerKey(marker) || key, trigger)
      return
    }
    // 面板可能还没渲染完，给它一个渲染窗口再回退。
    setTimeout(() => {
      if (pendingSeen.has(key)) return
      pendingSeen.add(key)
      const late = document.querySelector(PENDING_MARKERS)
      const sessionId = late === null ? agentId : activeSessionId()
      if (late !== null && sessionId !== '') pendingSeen.add(sessionId)
      log('pending-late', trigger, sessionId, late === null ? 'no-panel' : markerKey(late))
      notify('pending', sessionId, '', late === null ? key : (markerKey(late) || key), trigger)
    }, PENDING_FALLBACK_DELAY_MS)
  }

  // 兜底也是主路径：面板出现本身就是"在等你"的证据，不依赖任何 dsh 事件名。
  // 存在即通知一次，面板消失后同一会话可以再次通知（下一个请求）。
  // 调试模式下周期性自证"确实在扫、扫到了什么"，否则"没通知"无法区分是没扫还是没面板。
  // 状态变化同时回传外壳（终端可见），不依赖检查器的日志过滤。
  let lastPanelKey = ''
  let lastMarkerCount = -1
  function scanPending() {
    const element = document.querySelector(PENDING_MARKERS)
    const sessionId = activeSessionId()
    const count = element === null ? 0 : document.querySelectorAll(PENDING_MARKERS).length
    if (count !== lastMarkerCount) {
      lastMarkerCount = count
      diagnose('markers', String(count), sessionId || '(none)', element === null ? 'none' : markerKey(element))
    }
    if (element === null) {
      if (lastPanelKey !== '') {
        lastPanelKey = ''
        log('pending-gone', sessionId)
      }
      return
    }
    const key = markerKey(element)
    if (lastPanelKey !== key) {
      lastPanelKey = key
      log('pending-panel', sessionId, key, element.getAttributeNames().join(','))
    }
    // 同一轮 ask_user 的多道题各有独立面板 key（…:1、…:2、…），
    // 但它们属于同一次请求：按去掉序号的身份去重，只通知一次。
    const identity = pendingIdentity(key, sessionId)
    if (identity === '' || pendingSeen.has(identity)) return
    pendingSeen.add(identity)
    notify('pending', sessionId, '', key, 'ui')
  }

  // 一次 ask_user 调用的身份 = 面板 key 去掉最后一段序号；没有序号时退回会话 id。
  function pendingIdentity(key, sessionId) {
    if (key === '') return sessionId
    const cut = key.lastIndexOf(':')
    return cut > 0 ? key.slice(0, cut) : key
  }

  // 回传一条仅终端可见的诊断（kind=diagnose 不会弹通知），只在调试时发送。
  function diagnose(...parts) {
    if (!debugEnabled()) return
    send({ kind: 'diagnose', message: parts.join(' ') })
  }

  function markerKey(element) {
    return element.getAttribute('data-approval-key')
      ?? element.getAttribute('data-question-key')
      ?? element.getAttribute('data-plan-review-key')
      ?? ''
  }

  // 待处理交互通常出现在主视图，会话 id 取当前选中行即可。
  function notify(kind, sessionId, body, pendingKey, trigger) {
    if (!enabled()) return
    send({
      kind,
      sessionId: sessionId ?? '',
      body: body ?? '',
      pendingKey: pendingKey ?? '',
      trigger: trigger ?? '',
    })
  }

  // 调试自证：周期打印扫描状态，让"没通知"能区分成"没扫到面板"还是"扫到了但没发出去"。
  function heartbeat() {
    if (!debugEnabled()) return
    const element = document.querySelector(PENDING_MARKERS)
    log('heartbeat', location.pathname, 'markers=' + String(document.querySelectorAll(PENDING_MARKERS).length),
      'active=' + (activeSessionId() || '(none)'), 'panel=' + (element === null ? 'none' : markerKey(element)))
  }

  // 扫描器安装：脚本在文档最开始注入，此刻 #root 还不存在，
  // 因此不能以它为安装前提——挂在 documentElement 上，并在 DOM 有序后立即扫一遍。
  let scannerInstalled = false
  function installScanner() {
    if (scannerInstalled) return
    scannerInstalled = true
    const root = document.getElementById('root') ?? document.documentElement
    guard('tail', () => {
      let scheduled = false
      const scheduleScan = () => {
        if (scheduled) return
        scheduled = true
        queueMicrotask(() => { guard('scan-microtask', scanPending) })
      }
      guard('install-observer', () => {
        new MutationObserver(scheduleScan).observe(root, { childList: true, subtree: true })
      })
      // 观察器之外再加轮询：DOM 变化被合并、或面板渲染在观察目标之外时兜住。
      // 轮询回调各自包住，一次抛错不能让整条定时器链失效。
      guard('install-poll', () => { setInterval(() => { guard('scan-poll', scanPending) }, PENDING_POLL_INTERVAL_MS) })
      guard('install-heartbeat', () => { setInterval(heartbeat, PENDING_POLL_INTERVAL_MS * 5) })
      guard('scan-initial', scanPending)
      diagnose('scanner-ready root=' + String(root?.tagName ?? 'none')
        + ' interval=' + String(PENDING_POLL_INTERVAL_MS) + 'ms')
    })
  }
  guard('install-scanner-when-ready', () => {
    if (document.readyState === 'loading') {
      // 先记录意图再挂监听：DOM 已经就绪时 installScanner 会同步执行，
      // 这样日志顺序仍然是"意图 → 安装完成"。
      send({ kind: 'diagnose', message: 'scanner-deferred readyState=loading' })
      document.addEventListener('DOMContentLoaded', installScanner, { once: true })
    } else {
      installScanner()
    }
  })
  send({ kind: 'diagnose', message: 'bridge-done' })
})();
"""


def _bridge_js():
    """把通道名与强制调试标志注入页面桥脚本。"""
    forced = "1" if os.environ.get("DSH_WEBVIEWGTK_DEBUG") == "1" else "0"
    return (
        BRIDGE_JS_TEMPLATE.replace("__BRIDGE_CHANNEL__", BRIDGE_CHANNEL)
        .replace("__BRIDGE_GLOBAL__", BRIDGE_GLOBAL)
        .replace("__FORCED_DEBUG__", forced)
    )


def build_info():
    """一行运行版本指纹：确认跑的到底是哪个文件、哪一版桥。"""
    script = _bridge_js()
    digest = hashlib.sha256(script.encode("utf-8")).hexdigest()[:12]
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(Path(__file__).resolve().stat().st_mtime))
    return f"代码 {Path(__file__).resolve()}（{stamp}）bridge={BRIDGE_VERSION} sha256={digest}"


def _icon_candidates():
    """返回可能存放 dsh-webviewgtk.svg 的位置。"""
    yield ICON_FILE
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    yield data_home / "icons" / "hicolor" / "scalable" / "apps" / f"{APP_NAME}.svg"
    yield Path(sys.prefix) / "share" / "icons" / "hicolor" / "scalable" / "apps" / f"{APP_NAME}.svg"
    yield Path("/usr/local/share/icons/hicolor/scalable/apps") / f"{APP_NAME}.svg"
    yield Path("/usr/share/icons/hicolor/scalable/apps") / f"{APP_NAME}.svg"

LOADING_HTML_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
    :root { color-scheme: light dark; }
    body {
        margin: 0;
        height: 100vh;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        font-family: sans-serif;
        background: #f5f5f5;
    }
    .loading-logo {
        width: 96px;
        height: 96px;
    }
    h1 {
        margin: 0.5em 0 0;
        font-size: 2em;
        background: linear-gradient(
            90deg,
            #4d6bfe,
            #00bfff,
            #1e90ff,
            #4d6bfe
        );
        background-size: 200% auto;
        -webkit-background-clip: text;
        background-clip: text;
        -webkit-text-fill-color: transparent;
        color: transparent;
        animation: dsh-gradient-flow 3s linear infinite;
    }
    @keyframes dsh-gradient-flow {
        0% {
            background-position: 0% center;
        }
        100% {
            background-position: 200% center;
        }
    }
    #log {
        position: fixed;
        left: 50%;
        transform: translateX(-50%);
        bottom: 1em;
        width: min(80%, 900px);
        max-height: 30vh;
        box-sizing: border-box;
        background: #fff;
        padding: 1em;
        border: 1px solid #ccc;
        border-radius: 4px;
        white-space: pre-wrap;
        word-wrap: break-word;
        overflow-y: auto;
    }
    #log:empty {
        display: none;
    }
</style>
</head>
<body>
    {icon_html}
    <h1>Loading...</h1>
    <pre id="log"></pre>
</body>
</html>
"""


def _find_icon():
    """返回第一个存在的 dsh-webviewgtk.svg 路径，找不到则返回 None。"""
    return next((p for p in _icon_candidates() if p.is_file()), None)


def _loading_html():
    """生成加载页 HTML，并把 dsh-webviewgtk.svg 以 data URI 居中嵌入。"""
    icon_file = _find_icon()
    if icon_file is not None:
        encoded = base64.b64encode(icon_file.read_bytes()).decode("ascii")
        icon_html = (
            f'<img class="loading-logo" '
            f'src="data:image/svg+xml;base64,{encoded}" '
            f'alt="{APP_NAME}">'
        )
    else:
        icon_html = ""
        print("警告：找不到 dsh-webviewgtk.svg，加载页将不显示图标", file=sys.stderr)

    return LOADING_HTML_TEMPLATE.replace("{icon_html}", icon_html)


class DshWebviewGtk:
    """主窗口、WebView、dsh 子进程及下载处理。"""

    def __init__(self):
        # 让 WM_CLASS/StartupWMClass 与启动器名字一致。
        GLib.set_prgname(APP_NAME)

        self.ready = False
        self._ready_url_found = False
        self.process = None
        self._closing = False
        self._download_dialog = None
        self._download_dialog_loop = None
        self._download_dialog_accepted = False
        self._shutdown_pgid = None
        self._shutdown_started = None
        self._shutdown_done = False
        self._allowed_origin = urlparse(URL).netloc
        # 真正加载过 dsh 地址后才置位：导航早期 get_uri() 为空时据此仍可信任桥的消息。
        self._trusted_uri = None
        # 通知：活跃会话、待处理交互去重键，以及保活用的 Gio.Notification 引用。
        self._notifications = {}
        self._pending_keys = set()

        # GApplication 身份用于 D-Bus 通知与点击默认动作；NON_UNIQUE 允许同时开多个窗口。
        # 通知上的应用名与图标由名称与应用 id 一致的 desktop 文件决定。
        self.app = Gtk.Application(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.NON_UNIQUE,
        )
        self._notification_action = Gio.SimpleAction.new("notification", GLib.VariantType.new("s"))
        self._notification_action.connect("activate", self.on_notification_action)
        self.app.add_action(self._notification_action)
        # GApplication 生命周期必须完整：缺少 activate 处理时 GTK 会在注册阶段告警，
        # 且非唯一应用（NON_UNIQUE）退出时的行为不确定。
        self.app.connect("activate", self.on_app_activate)
        self.app.register(None)
        # 窗口销毁后应用不自动退出：dsh 进程树还在收尾，退出由 _finish_shutdown 决定。
        self.app.hold()

        self.window = Gtk.Window(title=APP_NAME)
        self._register_icon()
        self.window.set_default_size(1200, 800)
        self.window.set_icon_name(ICON_NAME)
        self.window.connect("close-request", self.on_close_request)
        self.window.connect("destroy", self.on_destroy)
        self.app.add_window(self.window)

        self.webview = WebKit.WebView()

        # 允许网页使用 JavaScript 访问剪贴板，并自动允许剪贴板权限请求。
        # 这样在 dsh 网页里 Ctrl/Cmd+V 粘贴图片才能正常读取系统剪贴板。
        settings = self.webview.get_settings()
        settings.set_javascript_can_access_clipboard(True)
        settings.set_javascript_can_open_windows_automatically(True)
        # WebKit 的开发者工具默认关闭，F12 就是靠它打开的。
        settings.set_enable_developer_extras(True)
        self.webview.connect("permission-request", self.on_permission_request)

        # 网页通过 target=_blank / window.open 请求新窗口时，
        # 交给系统默认浏览器打开外链。
        self.webview.connect("create", self.on_create)

        # 桌面通知桥：脚本在文档开始注入（早于页面自身脚本），
        # 页面用 window.webkit.messageHandlers.<通道> 把通知意图交回外壳。
        self._install_notification_bridge()

        # 捕获 Ctrl+V：如果剪贴板里有图片/图片文件，就在应用层读取，
        # 再向页面派发一个带图片文件的 paste 事件，弥补 WebKitGTK 默认不暴露
        # 文件管理器图片剪贴板的问题。
        # 同一个控制器也负责 F12 / Ctrl+Shift+I 打开开发者工具。
        self.key_controller = Gtk.EventControllerKey.new()
        self.key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.key_controller.connect("key-pressed", self.on_key_pressed)
        self.window.add_controller(self.key_controller)

        # WebKit 的开发者工具（F12 / Ctrl+Shift+I）由 WebKitWebInspector 提供。
        self.inspector = self.webview.get_inspector()

        self.webview.load_html(_loading_html(), None)
        self.window.set_child(self.webview)
        self.window.present()

        # 回到应用即视为已读，撤回本次运行留下的通知。
        self.window.connect("notify::is-active", self.on_window_active_changed)

        # WebKitGTK 6.0 的下载信号位于 NetworkSession 上。
        network_session = self.webview.get_network_session()
        network_session.connect("download-started", self.on_download_started)

        self.start_dsh()

    def _register_icon(self):
        """找到 dsh-webviewgtk.svg 并加入图标主题搜索路径。"""
        icon_file = _find_icon()
        if icon_file is None:
            print("警告：找不到 dsh-webviewgtk.svg", file=sys.stderr)
            return

        if "hicolor" in icon_file.parts:
            # 例如 .../icons/hicolor/scalable/apps/xxx.svg -> 加入 .../icons
            theme_dir = Path(*icon_file.parts[: icon_file.parts.index("hicolor")])
        else:
            # 仓库内直接放在脚本旁边的 xxx.svg -> 加入脚本所在目录
            theme_dir = icon_file.parent

        display = self._get_display()
        if display is not None:
            theme = Gtk.IconTheme.get_for_display(display)
            theme.add_search_path(str(theme_dir))

    @staticmethod
    def _get_display():
        try:
            from gi.repository import Gdk

            gi.require_version("Gdk", "4.0")
            return Gdk.Display.get_default()
        except Exception:
            return None

    # ---------- 桌面通知 ----------

    def _install_notification_bridge(self):
        """把页面桥注册进 UserContentManager，并接上页面→外壳的消息通道。"""
        manager = self.webview.get_user_content_manager()
        if not manager.register_script_message_handler(BRIDGE_CHANNEL):
            print("警告：无法注册通知桥消息通道，桌面通知不可用", file=sys.stderr)
            return
        # 该信号在 WebKit 的网络/回调线程上发出：这里只取 JSON 字符串并调度，
        # 其余解析、来源校验、发送都在主线程完成。使用 connect_data 是为了拿到
        # 无 GDK 依赖的 JSCValue；旧签名回退到 connect。
        try:
            manager.connect_data(BRIDGE_CHANNEL, "script-message-received", self._on_script_message_data)
        except TypeError:
            manager.connect("script-message-received::" + BRIDGE_CHANNEL, self._on_script_message_value)
        script = _bridge_js()
        manager.add_script(
            WebKit.UserScript.new(
                script,
                WebKit.UserContentInjectedFrames.TOP_FRAME,
                WebKit.UserScriptInjectionTime.START,
                None,
                None,
            )
        )
        # 诊断：把实际下发给页面的桥脚本打到终端，确认跑的是哪一版逻辑。
        self._debug("已注入的通知桥脚本：\n" + script)

    @staticmethod
    def _parse_script_message(raw):
        """把通道回调拿到的东西统一成页面桥发来的那个对象。

        primary 参数可能是 JSON 文本，也可能是已经反序列化好的对象（各版本 WebKit 不同）；
        两种都要接受，否则桥的消息会被当作解析失败静默丢掉。
        """
        if isinstance(raw, dict):
            return raw
        if not isinstance(raw, str):
            return None
        try:
            parsed = json.loads(raw)
        except Exception:
            return None
        if isinstance(parsed, dict):
            return parsed
        return None

    def _on_script_message_data(self, _manager, message):
        """connect_data 形态的通道回调：message 是页面的 JSON 文本或已解析对象。"""
        GLib.idle_add(self.on_page_notification, self._parse_script_message(message))

    def _on_script_message_value(self, _manager, message):
        """旧签名回退：从 JSCValue 里取出 JSON 文本。"""
        try:
            raw = message.to_json(0)
        except Exception as exc:
            print(f"读取页面通知载荷失败：{exc}", file=sys.stderr)
            return
        GLib.idle_add(self.on_page_notification, self._parse_script_message(raw))

    def on_page_notification(self, payload):
        """主线程：解析一条通知意图，并按其动作决定是否信任来源。"""
        if self._closing:
            return False
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception as exc:
                print(f"解析页面通知载荷失败：{exc}", file=sys.stderr)
                return False
        if not isinstance(payload, dict):
            return False
        self._debug(f"收到页面通知载荷：{payload!r}")

        kind = payload.get("kind")
        if kind == "diagnose":
            # 桥只回传终端诊断，不弹通知。这类消息始终打印：
            # 排查时"桥是否执行到某一步"必须看得见。
            print(f"[dsh-webviewgtk] 桥诊断：{payload.get('message', '')}", flush=True)
            return False
        if kind not in ("completed", "pending"):
            return False

        # 只有会驱动外壳动作（弹通知、聚焦、切会话）的载荷才校验来源。
        # 诊断不碰外壳，就不该被导航早期的空 URI 挡掉。
        if not self._page_is_allowed():
            self._debug(f"丢弃通知：页面 URI {self._current_uri()!r} 不是 {self._allowed_origin}")
            return False

        session = payload.get("sessionId")
        session = session if isinstance(session, str) else ""
        pending_key = payload.get("pendingKey")
        pending_key = pending_key if isinstance(pending_key, str) else ""

        if kind == "pending":
            # 同一个待处理交互只通知一次。
            identity = pending_key or session
            if identity != "":
                if identity in self._pending_keys:
                    return False
                self._pending_keys.add(identity)
            trigger = payload.get("trigger")
            title = PENDING_TITLES.get(trigger if isinstance(trigger, str) else "", PENDING_TITLE)
        else:
            title = "DeepSeek Harness 已完成"

        body = _notification_body(payload, kind)
        # 焦点状态与侧栏选中行都要在页面的 DOM 落到本轮结束后再读，
        # 因此推迟一次空闲周期，而不是在通知到达的瞬间判断。
        GLib.idle_add(self._present_if_relevant, session, title, body)
        return False

    def _current_uri(self):
        """当前页面 URI；导航早期或页面未挂载时返回空串。"""
        try:
            return self.webview.get_uri() or ""
        except Exception:
            return ""

    def _page_is_allowed(self):
        """判断当前页面是否是把 dsh 地址加载进来的那一页。

        导航早期（`load_uri` 尚未提交、或还停在 `load_html` 占位页）`get_uri()` 可能是空的，
        而页面里的桥已经在报到了；此时以"是否加载过 dsh 地址"为准，否则会把真实通知
        （连同诊断）一起丢掉。`get_uri()` 一旦有值，就按它自己的来源判定。
        """
        uri = self._current_uri()
        if uri == "":
            return self._trusted_uri is not None
        return urlparse(uri).netloc == self._allowed_origin

    @staticmethod
    def _debug(message):
        """DSH_WEBVIEWGTK_DEBUG=1 时把外壳侧的通知诊断打到终端。"""
        if os.environ.get("DSH_WEBVIEWGTK_DEBUG") == "1":
            print(f"[dsh-webviewgtk] {message}", file=sys.stderr, flush=True)

    def _present_if_relevant(self, session, title, body):
        """主线程（延后一拍）：窗口聚焦且正是当前会话时不打扰。"""
        if self._closing:
            return False
        if self.window.get_property("is-active") and session != "" and session == self._active_session_id():
            return False
        self._send_notification(session, title, body)
        return False

    def _active_session_id(self):
        """读取侧栏当前选中行的会话 id，读不到时返回空串。"""
        try:
            value = self.webview.evaluate_javascript(
                "(() => { const row = document.querySelector('[role=\"treeitem\"][aria-selected=\"true\"]');"
                " const key = row && row.getAttribute('data-row-key') || '';"
                " return key.startsWith('session:') ? key.slice(8) : ''; })()",
                -1,
                None,
                None,
                None,
                None,
                None,
            )
        except Exception as exc:
            print(f"读取当前会话失败：{exc}", file=sys.stderr)
            return ""
        try:
            return value.to_string()
        except Exception:
            return ""

    def _send_notification(self, session, title, body):
        """通过 GApplication 发送原生桌面通知，点击后回到应用。"""
        notification = Gio.Notification.new(title)
        notification.set_body(body)
        notification.set_icon(Gio.ThemedIcon.new(ICON_NAME))
        notification.set_priority(Gio.NotificationPriority.NORMAL)
        notification.set_default_action_and_target("app.notification", GLib.Variant("s", session))
        notification_id = f"{APP_ID}.{session or 'session'}"
        try:
            self.app.send_notification(notification_id, notification)
        except Exception as exc:
            print(f"发送桌面通知失败：{exc}", file=sys.stderr)
            return
        # GApplication 只保留通知的 D-Bus 副本，这里继续持有引用以便撤回。
        self._notifications[notification_id] = notification
        print(f"桌面通知：{title} — {body}", flush=True)

    def on_notification_action(self, action, parameter):
        """通知被点击：聚焦窗口（会话 id 非空时再切到该会话）。"""
        session = parameter.get_string() if parameter is not None else ""
        self._notifications.pop(f"{APP_ID}.{session or 'session'}", None)
        self.focus_window()
        if session != "":
            GLib.timeout_add(250, self.switch_session, session)

    def on_app_activate(self, _application):
        """GApplication 激活：唯一窗口已存在时把它带到前台。"""
        try:
            self.window.present()
        except Exception:
            # 激活先于窗口构建完成时窗口还不存在；此时无需处理。
            pass

    def focus_window(self):
        """把窗口带到前台。"""
        try:
            self.window.present()
        except Exception as exc:
            print(f"聚焦窗口失败：{exc}", file=sys.stderr)

    def on_window_active_changed(self, window, _pspec):
        """窗口重新获得焦点即视为已读，撤回所有已弹出的通知。"""
        if window.get_property("is-active"):
            self.withdraw_notifications()

    def switch_session(self, session):
        """点击侧栏里对应会话行，切换到该会话。"""
        selector = json.dumps('[data-row-key="session:' + session.replace('"', '\\"') + '"]')
        script = (
            "(() => { const row = document.querySelector(" + selector + ");"
            " if (row === null) return false;"
            " if (row.getAttribute('aria-selected') === 'true') return true;"
            " row.click(); return true; })()"
        )
        try:
            self.webview.evaluate_javascript(script, -1, None, None, None, None, None)
        except Exception as exc:
            print(f"切换会话失败：{exc}", file=sys.stderr)
        return False

    def withdraw_notifications(self):
        """退出或回到应用时撤回本次运行弹出的通知。"""
        self._pending_keys.clear()
        for notification_id in list(self._notifications):
            self._notifications.pop(notification_id, None)
            try:
                self.app.withdraw_notification(notification_id)
            except Exception:
                pass

    def start_dsh(self):
        """启动 dsh web 子进程，并开启输出读取线程。"""
        try:
            self.process = subprocess.Popen(
                ["npx", "--loglevel", "verbose", "--yes", "@deepseek-ai/dsh", "web", "--no-open", "--port", str(PORT)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                start_new_session=True,  # 独立进程组，便于关闭窗口时清理整棵进程树
            )
        except FileNotFoundError:
            msg = "错误：找不到 npx 命令，请确认 npx 已安装并在 PATH 中。"
            self.append_log(msg)
            print(msg, file=sys.stderr)
            return

        threading.Thread(target=self._reader_loop, daemon=True).start()

    def _reader_loop(self):
        """读取 dsh 输出：同步写标准输出，同时追加到 WebView 的 Loading 页。

        dsh 新版本会在输出里打印带 token 的访问地址，因此这里直接解析输出行，
        不再轮询端口。这样可以正确加载带 token 的正式页面。
        """
        try:
            assert self.process is not None and self.process.stdout is not None
            for raw_line in self.process.stdout:
                sys.stdout.write(raw_line)
                sys.stdout.flush()
                line = raw_line.rstrip("\n")
                GLib.idle_add(self.append_log, line)

                if not self._ready_url_found:
                    ready_url = _extract_ready_url(line)
                    if ready_url:
                        self._ready_url_found = True
                        GLib.idle_add(self.on_ready, ready_url)
        except Exception as exc:
            print(f"读取 dsh 输出出错：{exc}", file=sys.stderr)
        finally:
            if not self.ready and not self._closing:
                GLib.idle_add(self.on_dsh_exited)

    def append_log(self, line):
        """向 Loading 页追加一行日志；页面切换后不再追加。"""
        if self.ready or self._closing:
            return False

        safe_line = json.dumps(line + "\n")
        script = (
            "var el = document.getElementById('log');"
            f"el.textContent += {safe_line};"
            "el.scrollTop = el.scrollHeight;"
        )
        try:
            self.webview.evaluate_javascript(script, -1, None, None, None, None, None)
        except Exception as exc:
            print(f"写入 Loading 页日志失败：{exc}", file=sys.stderr)
        return False

    def on_dsh_exited(self):
        """dsh 在服务就绪前退出时给出提示。"""
        if self.ready or self._closing:
            return False
        self.append_log("dsh 进程已退出，未能在 127.0.0.1:3081 启动服务。")
        print("dsh 进程已退出", file=sys.stderr, flush=True)
        self._finish_shutdown()
        return False

    def on_ready(self, url=None):
        """服务就绪，加载 dsh 输出中解析出的正式页面。"""
        if self.ready or self._closing:
            return False
        self.ready = True
        if not url:
            url = URL
        self._trusted_uri = url
        print(f"服务已就绪，加载 {url}", flush=True)
        self.webview.load_uri(url)
        return False

    def on_permission_request(self, webview, permission_request):
        """自动允许剪贴板读取权限，用于支持粘贴图片。"""
        if isinstance(permission_request, WebKit.ClipboardPermissionRequest):
            permission_request.allow()
            print("允许网页访问剪贴板", flush=True)
            return True
        return False

    def on_create(self, webview, navigation_action):
        """网页请求新窗口时，用系统默认浏览器打开外链。"""
        try:
            uri = navigation_action.get_request().get_uri()
        except Exception:
            return None

        if uri:
            self.open_external_uri(uri)
        return None

    @staticmethod
    def open_external_uri(uri):
        """按协议白名单调用系统默认应用打开外链。"""
        if not uri:
            return

        scheme = urlparse(uri).scheme.lower()
        if scheme not in ALLOWED_EXTERNAL_SCHEMES:
            print(f"忽略不在白名单中的外部协议：{uri}", file=sys.stderr, flush=True)
            return

        try:
            opened = Gio.AppInfo.launch_default_for_uri(uri, None)
            if opened:
                print(f"用默认应用打开：{uri}", flush=True)
            else:
                print(f"无法用默认应用打开：{uri}", file=sys.stderr, flush=True)
        except Exception as exc:
            print(f"打开外部链接失败：{exc}", file=sys.stderr)

    def toggle_inspector(self):
        """F12 / Ctrl+Shift+I：打开或收起 WebKit 开发者工具。"""
        try:
            if self.inspector.is_attached():
                self.inspector.close()
                print("已关闭开发者工具", flush=True)
            else:
                self.inspector.show()
                print("已打开开发者工具（F12 或 Ctrl+Shift+I 可关闭）", flush=True)
        except Exception as exc:
            print(f"打开开发者工具失败：{exc}", file=sys.stderr)

    def on_key_pressed(self, controller, keyval, keycode, state):
        """捕获 F12 / Ctrl+Shift+I 打开开发者工具，以及 Ctrl+V 粘贴图片。"""
        # 开发者工具必须在 ready 之前也能打开：加载页卡住时正是要它来查。
        if keyval == Gdk.KEY_F12:
            self.toggle_inspector()
            return True
        if keyval in (Gdk.KEY_i, Gdk.KEY_I):
            shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
            control = bool(state & Gdk.ModifierType.CONTROL_MASK)
            if shift and control:
                self.toggle_inspector()
                return True

        if not self.ready:
            return False
        if keyval not in (Gdk.KEY_v, Gdk.KEY_V):
            return False
        if not (state & Gdk.ModifierType.CONTROL_MASK):
            return False

        clipboard = self.window.get_display().get_clipboard()
        formats = clipboard.get_formats()
        if formats is None:
            return False

        image_mime = (
            formats.contain_mime_type("image/png")
            or formats.contain_mime_type("image/jpeg")
            or formats.contain_mime_type("image/gif")
            or formats.contain_mime_type("image/webp")
        )
        uri_list = (
            formats.contain_mime_type("text/uri-list")
            or formats.contain_mime_type("x-special/gnome-copied-files")
        )

        if image_mime:
            self._paste_clipboard_texture(clipboard)
            return True
        if uri_list:
            self._paste_clipboard_uri(clipboard)
            return True
        return False

    def _paste_clipboard_texture(self, clipboard):
        """从剪贴板读取图片纹理（image/png 等），转成 PNG 后注入页面。"""
        clipboard.read_texture_async(None, self._on_clipboard_texture_read, None)

    def _on_clipboard_texture_read(self, clipboard, result, user_data):
        try:
            texture = clipboard.read_texture_finish(result)
        except Exception:
            texture = None
        if texture is None:
            return

        try:
            png_bytes = texture.save_to_png_bytes().get_data()
        except Exception as exc:
            print(f"读取剪贴板图片失败：{exc}", file=sys.stderr)
            return

        self._inject_pasted_image(png_bytes, "image/png")

    def _paste_clipboard_uri(self, clipboard):
        """从剪贴板读取 text/uri-list，如果是本地图片文件则读取并注入页面。"""
        clipboard.read_async(
            ["text/uri-list"],
            GLib.PRIORITY_DEFAULT,
            None,
            self._on_clipboard_uri_read,
            None,
        )

    def _on_clipboard_uri_read(self, clipboard, result, user_data):
        try:
            stream, mime_type = clipboard.read_finish(result)
            if stream is None:
                return
            data_bytes = stream.read_bytes(65536).get_data()
            stream.close()
        except Exception:
            return

        try:
            text = data_bytes.decode("utf-8", "replace")
        except Exception:
            return

        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                path, _host = GLib.filename_from_uri(line)
            except Exception:
                continue
            path_obj = Path(path)
            if not path_obj.is_file():
                continue
            mime, _ = mimetypes.guess_type(str(path_obj))
            if not mime or not mime.startswith("image/"):
                continue
            try:
                image_data = path_obj.read_bytes()
            except Exception:
                continue
            self._inject_pasted_image(image_data, mime)
            return

    def _inject_pasted_image(self, image_data, mime):
        """把图片字节作为 File 注入页面当前输入框，并派发 paste 事件。"""
        encoded = base64.b64encode(image_data).decode("ascii")
        extension = mimetypes.guess_extension(mime) or ".png"
        filename = f"pasted-image{extension}"
        script = f"""
            (function() {{
                const b64 = "{encoded}";
                const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
                const file = new File([bytes], "{filename}", {{ type: "{mime}" }});
                const dt = new DataTransfer();
                dt.items.add(file);
                const ev = new ClipboardEvent("paste", {{
                    bubbles: true,
                    cancelable: true,
                    clipboardData: dt
                }});
                const target = document.activeElement
                    && document.activeElement !== document.body
                    ? document.activeElement
                    : (document.querySelector('textarea, input, [contenteditable="true"]') || document.body);
                target.dispatchEvent(ev);
            }})();
        """
        print("向页面注入剪贴板图片", flush=True)
        try:
            self.webview.evaluate_javascript(script, -1, None, None, None, None, None)
        except Exception as exc:
            print(f"向页面注入粘贴图片失败：{exc}", file=sys.stderr)

    # ---------- 下载处理 ----------

    def on_download_started(self, session, download):
        """网页触发下载时连接 Download 信号。"""
        print(f"开始下载：{download.get_request().get_uri()}")
        download.connect("decide-destination", self.on_decide_destination)
        download.connect("created-destination", self.on_created_destination)
        download.connect("finished", self.on_download_finished)
        download.connect("failed", self.on_download_failed)

    def on_decide_destination(self, download, suggested_filename):
        """弹出保存对话框，用户确认后设置下载目标。"""
        dialog = Gtk.FileDialog.new()
        dialog.set_title("保存文件")
        dialog.set_initial_name(suggested_filename or "download")
        dialog.set_modal(True)

        self._download_dialog = dialog
        self._download_dialog_accepted = False
        self._download_dialog_loop = GLib.MainLoop()

        # GtkFileDialog 是异步 API；在信号里用嵌套 MainLoop 等待用户选择，
        # 这样 decide-destination 可以同步返回 WebKit 需要的布尔值。
        dialog.save(
            self.window,
            None,
            self._on_save_dialog_response,
            download,
        )
        self._download_dialog_loop.run()

        return self._download_dialog_accepted

    def _on_save_dialog_response(self, dialog, result, download):
        accepted = False
        try:
            file = dialog.save_finish(result)
            path = file.get_path()
            uri = GLib.filename_to_uri(path)
            download.set_destination(uri)
            download.set_allow_overwrite(True)
            accepted = True
            print(f"下载将保存到：{path}")
        except GLib.Error:
            print("用户取消了下载保存")
            download.cancel()
        except Exception as exc:
            print(f"保存文件出错：{exc}", file=sys.stderr)
            download.cancel()
        finally:
            self._download_dialog_accepted = accepted
            self._download_dialog = None
            if self._download_dialog_loop is not None:
                self._download_dialog_loop.quit()
                self._download_dialog_loop = None

    @staticmethod
    def on_created_destination(download, destination):
        print(f"下载目标已创建：{destination}")

    @staticmethod
    def on_download_finished(download):
        print("下载完成")

    @staticmethod
    def on_download_failed(download, error):
        print(f"下载失败：{error}")

    # ---------- 退出清理 ----------

    def on_close_request(self, widget):
        self.shutdown_dsh()
        return False  # 允许窗口继续关闭

    def on_destroy(self, widget):
        self.shutdown_dsh()
        print("结束 dsh web")

    def shutdown_dsh(self):
        """结束 dsh 子进程所在的整个进程组（进程树）。"""
        print("停止 dsh web")
        if self._closing:
            return
        self._closing = True

        if self.process is None or self.process.poll() is not None:
            self._finish_shutdown()
            return

        try:
            self._shutdown_pgid = os.getpgid(self.process.pid)
            os.killpg(self._shutdown_pgid, signal.SIGTERM)
        except ProcessLookupError:
            self._finish_shutdown()
            return

        self._shutdown_started = time.monotonic()
        # 窗口关闭后继续保持主循环一小段时间，确保进程树真正退出。
        GLib.timeout_add(2000, self._poll_shutdown)

    def _poll_shutdown(self):
        """等待 dsh 进程树退出；超时后升级为 SIGKILL。"""
        if self.process is None or self.process.poll() is not None:
            # 即使主进程已退出，也确认整个进程组不存在后再退出主循环。
            try:
                os.killpg(self._shutdown_pgid, 0)
            except ProcessLookupError:
                self._finish_shutdown()
                return False
            except PermissionError:
                pass
            except Exception:
                self._finish_shutdown()
                return False

        if time.monotonic() - self._shutdown_started >= 5.0:
            try:
                os.killpg(self._shutdown_pgid, signal.SIGKILL)
            except ProcessLookupError:
                self._finish_shutdown()
                return False
            except Exception:
                pass

        # 仍可能残留，继续轮询直到进程组消失。
        try:
            os.killpg(self._shutdown_pgid, 0)
        except ProcessLookupError:
            self._finish_shutdown()
            return False
        except Exception:
            pass

        return True

    def _finish_shutdown(self):
        self.withdraw_notifications()
        if self._shutdown_done:
            return
        self._shutdown_done = True
        # 悬空引用释放后应用才允许退出；兼容没有 hold()/release() 的旧 GIO。
        try:
            self.app.release()
        except Exception:
            pass
        self.app.quit()


def main():
    # 启动横幅必须无条件打印：排查"通知为什么没来"时首先要确认跑的是哪个文件、哪一版桥。
    print(f"{APP_NAME}: {build_info()}", flush=True)
    app = DshWebviewGtk()
    # 通知身份与点击动作由 GApplication 提供，主循环跑在同一个 GLib 上下文里。
    app.app.run([])


if __name__ == "__main__":
    main()
