#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""窗口标题格式化与同步的冒烟测试。

用法：python3 tests/window_title_test.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dsh_webviewgtk as shell  # noqa: E402


class FakeWebView:
    def __init__(self, title):
        self.title = title

    def get_title(self):
        return self.title


class FakeWindow:
    def __init__(self):
        self.title = None

    def set_title(self, title):
        self.title = title


def main():
    cases = [
        (None, "dsh-webviewgtk"),
        ("", "dsh-webviewgtk"),
        ("   ", "dsh-webviewgtk"),
        ("测试 — DeepSeek Harness", "测试 — DeepSeek Harness · dsh-webviewgtk"),
        ("New Session", "New Session · dsh-webviewgtk"),
    ]
    bad = [(value, shell.window_title(value), expected) for value, expected in cases
           if shell.window_title(value) != expected]

    instance = shell.DshWebviewGtk.__new__(shell.DshWebviewGtk)
    instance.window = FakeWindow()
    instance.on_webview_title_changed(FakeWebView("测试 — DeepSeek Harness"), None)
    handler_ok = instance.window.title == "测试 — DeepSeek Harness · dsh-webviewgtk"

    ok = not bad and handler_ok
    if ok:
        print("WINDOW TITLE OK")
    else:
        print("WINDOW TITLE FAILED")
        for value, actual, expected in bad:
            print(f"  {value!r}: got {actual!r}, want {expected!r}")
        print(f"  handler_ok={handler_ok}, window title={instance.window.title!r}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
