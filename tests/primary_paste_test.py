#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""中键粘贴桥的冒烟测试：拦下 contenteditable 的中键 paste，改用外壳读到的 PRIMARY。

用法：python3 tests/primary_paste_test.py（只需要 node）
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

function newBox() {
  const box = { sent, listeners: {}, dispatched: [] }
  box.globalThis = box
  box.console = console
  box.queueMicrotask = queueMicrotask
  box.setTimeout = (fn) => { fn(); return 0 }
  box.setInterval = () => 1
  box.clearInterval = () => {}
  box.Set = Set
  box.JSON = JSON
  box.String = String
  box.Object = Object
  box.ArrayBuffer = ArrayBuffer
  box.Uint8Array = Uint8Array
  box.TextDecoder = TextDecoder
  box.addEventListener = (type, fn) => { (box.listeners[type] ||= []).push(fn) }
  box.location = { href: 'http://127.0.0.1:3081/', pathname: '/' }
  box.Element = class Element {}
  box.ClipboardEvent = class ClipboardEvent {
    constructor(type, init) {
      this.type = type
      this.clipboardData = init.clipboardData
      this.bubbles = init.bubbles
      this.cancelable = init.cancelable
      this.defaultPrevented = false
      this.propagationStopped = false
    }
    preventDefault() { this.defaultPrevented = true }
    stopImmediatePropagation() { this.propagationStopped = true }
  }
  box.DataTransfer = class DataTransfer {
    constructor() { this.data = {} }
    setData(type, value) { this.data[type] = value }
    getData(type) { return this.data[type] ?? '' }
  }
  box.MessageEvent = class MessageEvent {}
  Object.defineProperty(box.MessageEvent.prototype, 'data', {
    configurable: true,
    get() { return this._data },
  })
  box.document = {
    readyState: 'complete',
    querySelector: () => null,
    querySelectorAll: () => [],
    getElementById: () => null,
    documentElement: { tagName: 'HTML' },
    addEventListener: () => {},
    body: {},
  }
  box.MutationObserver = class { observe() {} }
  box.window = box
  box.webkit = { messageHandlers: { __CHANNEL__: { postMessage: (payload) => sent.push(payload) } } }
  vm.createContext(box)
  vm.runInContext(source, box)
  return box
}

const box = newBox()
const target = new box.Element()
target.isContentEditable = true
target.dispatchEvent = (event) => { box.dispatched.push(event) }

// 1. 普通粘贴（没有中键按下）：不得拦截
const plain = new box.ClipboardEvent('paste', { clipboardData: { getData: () => 'KEYBOARD' } })
plain.target = target
box.listeners['paste'][0](plain)
const plainUntouched = plain.defaultPrevented === false

// 2. 中键粘贴：拦下 paste，向外壳请求 PRIMARY
box.listeners['mousedown'][0]({ button: 1, target })
const middle = new box.ClipboardEvent('paste', { clipboardData: { getData: () => 'CLIP-MARK' } })
middle.target = target
box.listeners['paste'][0](middle)
const request = sent.find(item => item.kind === 'primary-paste')
const intercepted = middle.defaultPrevented && middle.propagationStopped && request !== undefined

// 3. 外壳回填 PRIMARY：用合成 paste 交给页面自己的处理，且不会被再次拦截
box.__DSH_SHELL_PRIMARY_TEXT__(request?.requestId, 'PRIMARY-MARK')
const inserted = box.dispatched.length === 1
  && box.dispatched[0].clipboardData.getData('text/plain') === 'PRIMARY-MARK'
  && box.dispatched[0].defaultPrevented === false

// 4. 过期的回填请求：丢弃
box.__DSH_SHELL_PRIMARY_TEXT__('stale-request', 'STALE')
const staleIgnored = box.dispatched.length === 1

// 5. 中键点在不可编辑区域：不拦截
box.listeners['mousedown'][0]({ button: 1, target })
const readOnly = new box.Element()
const nonEditable = new box.ClipboardEvent('paste', { clipboardData: { getData: () => 'X' } })
nonEditable.target = readOnly
box.listeners['paste'][0](nonEditable)
const nonEditableIgnored = nonEditable.defaultPrevented === false

const ok = plainUntouched && intercepted && inserted && staleIgnored && nonEditableIgnored
console.log(ok ? 'PRIMARY PASTE OK' : `PRIMARY PASTE FAILED plain=${plainUntouched} intercepted=${intercepted} inserted=${inserted} stale=${staleIgnored} nonEditable=${nonEditableIgnored}`)
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
