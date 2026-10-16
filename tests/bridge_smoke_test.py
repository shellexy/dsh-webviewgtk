#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通知桥冒烟测试：把桥脚本注入伪造页面环境，验证帧解析与通知意图。

用法：python3 tests/bridge_smoke_test.py
依赖 node（仅用其 vm 模块跑沙箱），不依赖 GTK/WebKit。
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dsh_webviewgtk as shell  # noqa: E402

HARNESS = r"""
import { readFileSync } from 'node:fs'
import vm from 'node:vm'

const source = readFileSync(process.argv[1], 'utf8')

const sent = []
const treeItem = { getAttribute: (name) => (name === 'data-row-key' ? 'session:session-a' : null) }
// 测试用待处理面板：元素带一个 data-* 标记键，按需出现在 DOM 里。
const pending = { shown: false, key: 'pending-1' }
const markerElement = {
  getAttribute: (name) => (name === 'data-question-key' && pending.shown ? pending.key : null),
  getAttributeNames: () => (pending.shown ? ['data-question-key'] : []),
}

// 每个沙箱有自己的 sent/pollers，便于同一份源码跑两个阶段。
function newSandbox() {
  const box = { sent: [], pollers: [] }
  box.globalThis = box
  box.console = console
  box.queueMicrotask = queueMicrotask
  box.setTimeout = (fn) => { fn(); return 0 }
  box.setInterval = (fn) => { box.pollers.push(fn); return box.pollers.length }
  box.clearInterval = () => {}
  box.Set = Set
  box.JSON = JSON
  box.String = String
  box.Object = Object
  box.ArrayBuffer = ArrayBuffer
  box.Uint8Array = Uint8Array
  box.TextDecoder = TextDecoder
  box.MessageEvent = class MessageEvent {}
  Object.defineProperty(box.MessageEvent.prototype, 'data', {
    configurable: true,
    get() { return this._data },
  })
  // 模拟"文档还在加载"：桥会把扫描器挂到 DOMContentLoaded 上，这里立即兑现它，
  // 与真实页面一致（真实页面加载完就会触发）。
  box.document = {
    readyState: 'loading',
    querySelector: (selector) => {
      if (selector.includes('treeitem') && selector.includes('aria-selected')) return treeItem
      return pending.shown ? markerElement : null
    },
    querySelectorAll: () => (pending.shown ? [markerElement] : []),
    getElementById: () => null,
    documentElement: { tagName: 'HTML' },
    addEventListener: (type, handler) => { if (type === 'DOMContentLoaded') box.domReady = handler },
    body: {},
  }
  box.MutationObserver = class { observe() {} }
  box.addEventListener = () => {}
  box.location = { href: 'http://127.0.0.1:3081/', pathname: '/' }
  // 诊断载荷只在调试模式回传；测试断言诊断序列，因此这里打开页面级调试开关。
  box.__DSH_SHELL_NOTIFY_DEBUG__ = true
  box.window = box
  box.webkit = { messageHandlers: { __CHANNEL__: { postMessage: (payload) => box.sent.push(payload) } } }
  vm.createContext(box)
  vm.runInContext(source, box)
  // 真实页面里 DOMContentLoaded 会在挂载之后触发；这里显式兑现，扫描器才会安装。
  box.domReady?.()
  return box
}

const sandbox = newSandbox()

const deliver = (data) => {
  const event = new sandbox.MessageEvent()
  event._data = typeof data === 'string' ? data : JSON.stringify(data)
  return event.data
}
const muxItem = (event, args) => ({ type: 'item', streamId: 1, value: { type: 'emit', event, args } })
const muxWaterfall = (event, eventId, agentId) =>
  ({ type: 'waterfall', streamId: 1, event, eventId, agentId, request: {} })

// 无关转发事件：不通知
deliver({ type: 'item', streamId: 1, value: { type: 'emit', event: 'api-session/added', args: [{}] } })
// running 置位：不通知
deliver(muxItem('api-session/status', ['session-a', true]))
// 本轮结束：通知 completed
deliver(muxItem('api-session/status', ['session-a', false]))
// 事件名换了但参数仍是 (sessionId, running)：照样判定本轮结束
deliver(muxItem('api-session/whatever-status', ['session-renamed', false]))
// 参数不是 (字符串, 布尔)：忽略
deliver(muxItem('api-session/status', ['session-a']))
deliver(muxItem('api-session/status', [42, false]))
// 非 JSON 文本帧：不得抛错
deliver('not json at all')
// 未识别的瀑布且页面上没有待处理面板：不通知（避免误报）
deliver(muxWaterfall('some/other-request', 'req-other', 'session-a'))

// 权限审批：面板已在 DOM，取当前会话
pending.shown = true
deliver(muxWaterfall('approval/request', 'req-approve', 'session-a'))
// 同一请求重复投递：不重复通知
deliver(muxWaterfall('approval/request', 'req-approve', 'session-a'))
// ask_user 提问：面板未渲染，渲染窗口后回退到 agentId
pending.shown = false
deliver(muxWaterfall('user-questions/request', 'req-question', 'session-b'))
// 兜底扫描与瀑布通知去重：面板随后出现且没有新帧时不得重复通知
pending.key = 'pending-late'
const snapshot = sent.length
sandbox.__DSH_SHELL_NOTIFY__({ kind: 'completed', sessionId: 'session-c', body: '这一轮已经结束' })
const deduped = sent.slice(snapshot + 1).length === 0

// 页面侧主动调用预留通道：等价于以后由 dsh 插件直接触发
sandbox.__DSH_SHELL_NOTIFY__({ kind: 'pending', sessionId: 'session-z', body: '', pendingKey: '', trigger: 'plugin' })
// 关闭开关：不再通知
sandbox.__DSH_SHELL_NOTIFY_DISABLE__ = true
deliver(muxItem('api-session/status', ['session-c', false]))
deliver(muxWaterfall('approval/request', 'req-late', 'session-c'))

const expected = [
  { kind: 'completed', sessionId: 'session-a', body: '这一轮已经结束', pendingKey: '', trigger: '' },
  { kind: 'completed', sessionId: 'session-renamed', body: '这一轮已经结束', pendingKey: '', trigger: '' },
  {
    kind: 'pending', sessionId: 'session-a', body: '',
    pendingKey: 'pending-1', trigger: 'approval/request',
  },
  {
    kind: 'pending', sessionId: 'session-b', body: '',
    pendingKey: 'request:req-question', trigger: 'user-questions/request',
  },
  { kind: 'completed', sessionId: 'session-c', body: '这一轮已经结束' },
  { kind: 'pending', sessionId: 'session-z', body: '', pendingKey: '', trigger: 'plugin' },
]

// 第二阶段：只用 DOM 轮询发现待处理面板，完全不依赖任何转发帧。
const b = newSandbox()
pending.shown = false
pending.key = 'question:abc:1'
b.pollers[0]()
pending.shown = true
b.pollers[0]()
b.pollers[0]()                       // 面板仍在：不重复通知
pending.key = 'question:abc:2'       // 同一轮 ask_user 的第 2 题：属于同一次请求，不重复弹
b.pollers[0]()
pending.shown = false
b.pollers[0]()
pending.key = 'question:xyz:1'       // 新的一次 ask_user：再次通知
pending.shown = true
b.pollers[0]()
const pollExpected = [
  { kind: 'pending', sessionId: 'session-a', body: '', pendingKey: 'question:abc:1', trigger: 'ui' },
  { kind: 'pending', sessionId: 'session-a', body: '', pendingKey: 'question:xyz:1', trigger: 'ui' },
]
// 诊断载荷只回传终端，不是通知；单独断言，避免它混进通知序列。
const withoutDiagnose = (list) => list.filter(item => item.kind !== 'diagnose')
const diagnose2 = b.sent.filter(item => item.kind === 'diagnose').map(item => item.message)
const diagnoseExpected = [
  'bridge-start href=http://127.0.0.1:3081/',
  'log-ready',
  'global-ready',
  'active=session-a',
  'active-ready',
  'message-hook-ready',
  'scanner-deferred readyState=loading',
  'bridge-done',
  'markers 0 session-a none',
  'scanner-ready root=HTML interval=1000ms',
  'markers 1 session-a question:abc:1',
  'markers 0 session-a none',
  'markers 1 session-a question:xyz:1',
]

const phase1 = JSON.stringify(withoutDiagnose(sandbox.sent)) === JSON.stringify(expected)
const phase2 = JSON.stringify(withoutDiagnose(b.sent)) === JSON.stringify(pollExpected)
const phase3 = JSON.stringify(diagnose2) === JSON.stringify(diagnoseExpected)
const ok = deduped && phase1 && phase2 && phase3
if (!ok) {
  console.log(`BRIDGE SMOKE FAILED phase1=${phase1} phase2=${phase2} phase3=${phase3} deduped=${deduped}`)
  console.log('phase1 sent:', JSON.stringify(sandbox.sent))
  console.log('phase2 sent:', JSON.stringify(b.sent))
  console.log('diagnose:', JSON.stringify(diagnose2))
} else {
  console.log('BRIDGE SMOKE OK')
}
process.exit(ok ? 0 : 1)
"""


def main():
    script = HARNESS.replace("__CHANNEL__", shell.BRIDGE_CHANNEL)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as handle:
        handle.write(shell._bridge_js())
        bridge_path = handle.name
    try:
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script, bridge_path],
            text=True,
            capture_output=True,
        )
    finally:
        Path(bridge_path).unlink(missing_ok=True)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
