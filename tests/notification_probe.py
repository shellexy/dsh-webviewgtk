#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证 Gtk.Application + Gio.Notification 的通知身份与点击默认动作。

用法：python3 tests/notification_probe.py
会真的弹出一条系统通知，5 秒后自动撤回退出。
"""

import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gio", "2.0")

from gi.repository import Gio, GLib, Gtk  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dsh_webviewgtk as shell  # noqa: E402

hit = []


def main():
    app = Gtk.Application(application_id=shell.APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
    action = Gio.SimpleAction.new("notification", GLib.VariantType.new("s"))
    action.connect("activate", lambda _action, parameter: hit.append(parameter.get_string()))
    app.add_action(action)
    app.register(None)

    notification = Gio.Notification.new(shell.APP_NAME)
    notification.set_body("通知探测：点击本通知应回到 dsh-webviewgtk")
    notification.set_icon(Gio.ThemedIcon.new(shell.ICON_NAME))
    notification.set_default_action_and_target("app.notification", GLib.Variant("s", "probe-session"))
    notification_id = f"{shell.APP_ID}.probe"
    app.send_notification(notification_id, notification)
    print("已发送探测通知，10 秒后撤回；请点击它以验证点击回调", flush=True)

    def finish():
        print("点击回调命中：" + repr(hit), flush=True)
        app.withdraw_notification(notification_id)
        loop.quit()
        return False

    GLib.timeout_add_seconds(10, finish)
    loop = GLib.MainLoop()
    loop.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
