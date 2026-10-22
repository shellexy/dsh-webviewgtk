#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在真实 WebKitGTK 里验证通知桥：UserScript 注入、页面→外壳通道、帧解析。

不需要 dsh 服务，用一个本地伪造页面代替：页面自己派发一条 mux 帧，
外壳应当从脚本消息通道收到一条 completed 通知意图。
用法：python3 tests/bridge_webkit_probe.py

注意：load_html 的页面是 blank origin，因此这里只验证"注入脚本能执行、
页面能回调外壳、帧能被解析"；_page_is_allowed 的来源校验与通知发送本身
由 tests/bridge_smoke_test.py 与手工运行覆盖。
"""

import json
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gio", "2.0")
gi.require_version("WebKit", "6.0")

from gi.repository import GLib, Gio, Gtk, WebKit  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dsh_webviewgtk as shell  # noqa: E402

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>probe</title></head>
<body><div id="root"></div>
<script>
  // 页面骨架就绪后，像 dsh 的 mux 流一样派发一帧 api-session/status。
  window.dispatchEvent(new MessageEvent('message', {
    data: JSON.stringify({
      type: 'item', streamId: 1,
      value: { type: 'emit', event: 'api-session/status', args: ['session-probe', true] },
    }),
  }))
  window.dispatchEvent(new MessageEvent('message', {
    data: JSON.stringify({
      type: 'item', streamId: 1,
      value: { type: 'emit', event: 'api-session/status', args: ['session-probe', false] },
    }),
  }))
</script>
</body></html>"""

received = []


def collect(_manager, message):
    """与生产代码同形的通道回调：message 是页面的 JSON 字符串。"""
    try:
        received.append(json.loads(message))
    except Exception:
        received.append({"unparsed": message})
    return None


def main():
    app = Gtk.Application(application_id=shell.APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
    app.register(None)
    window = Gtk.Window(application=app, title=shell.APP_NAME)
    view = WebKit.WebView()
    window.set_child(view)

    manager = view.get_user_content_manager()
    if not manager.register_script_message_handler(shell.BRIDGE_CHANNEL):
        print("FAILED: register_script_message_handler 返回 False")
        return 1
    manager.connect_data(shell.BRIDGE_CHANNEL, "script-message-received", collect)
    manager.add_script(
        WebKit.UserScript.new(
            shell._bridge_js(),
            WebKit.UserContentInjectedFrames.TOP_FRAME,
            WebKit.UserScriptInjectionTime.START,
            None,
            None,
        )
    )

    def finish():
        view.stop_loading()
        window.destroy()
        expected = [{"kind": "completed", "sessionId": "session-probe", "body": "这一轮已经结束", "pendingKey": ""}]
        if received == expected:
            print("WEBKIT BRIDGE OK:", json.dumps(received, ensure_ascii=False))
        else:
            print("WEBKIT BRIDGE FAILED:", json.dumps(received, ensure_ascii=False))
        app.quit()
        return GLib.SOURCE_REMOVE

    # 探针只需要一个同源 base URI，不需要真实监听端口。
    view.load_html(PAGE, f"http://{shell.HOST}:3081/")
    window.present()
    GLib.timeout_add_seconds(3, finish)
    app.run([])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
