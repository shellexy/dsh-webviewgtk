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
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("WebKit", "6.0")

from gi.repository import GLib, Gtk, WebKit  # noqa: E402

HOST = "127.0.0.1"
PORT = 3081
URL = f"http://{HOST}:{PORT}"
APP_NAME = "dsh-webviewgtk"
ICON_NAME = APP_NAME
ICON_FILE = Path(__file__).resolve().parent / f"{APP_NAME}.svg"


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
        color: #333;
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
        self.process = None
        self._closing = False
        self._download_dialog = None
        self._download_dialog_loop = None
        self._download_dialog_accepted = False
        self._shutdown_pgid = None
        self._shutdown_started = None
        self.main_loop = GLib.MainLoop()

        self.window = Gtk.Window(title=APP_NAME)
        self._register_icon()
        self.window.set_default_size(1200, 800)
        self.window.set_icon_name(ICON_NAME)
        self.window.connect("close-request", self.on_close_request)
        self.window.connect("destroy", self.on_destroy)

        self.webview = WebKit.WebView()
        self.webview.load_html(_loading_html(), None)
        self.window.set_child(self.webview)
        self.window.present()

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

    def start_dsh(self):
        """启动 dsh web 子进程，并开启输出读取和服务就绪监测线程。"""
        try:
            self.process = subprocess.Popen(
                ["npx", "@deepseek-ai/dsh", "web", "--port", str(PORT)],
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
        threading.Thread(target=self._monitor_loop, daemon=True).start()

    def _reader_loop(self):
        """读取 dsh 输出：同步写标准输出，同时追加到 WebView 的 Loading 页。"""
        try:
            assert self.process is not None and self.process.stdout is not None
            for raw_line in self.process.stdout:
                sys.stdout.write(raw_line)
                sys.stdout.flush()
                line = raw_line.rstrip("\n")
                GLib.idle_add(self.append_log, line)
        except Exception as exc:
            print(f"读取 dsh 输出出错：{exc}", file=sys.stderr)

    def _monitor_loop(self):
        """轮询 http://127.0.0.1:3081，可用后让 WebView 加载正式页面。"""
        while not self.ready:
            if self._closing:
                return
            if self.process is None or self.process.poll() is not None:
                GLib.idle_add(self.on_dsh_exited)
                return
            try:
                with urllib.request.urlopen(URL, timeout=0.5) as response:
                    response.read(1)
                GLib.idle_add(self.on_ready)
                return
            except Exception:
                time.sleep(0.5)

    def append_log(self, line):
        """向 Loading 页追加一行日志；页面切换后不再追加。"""
        if self.ready or self._closing:
            return False

        safe_line = json.dumps(line + "\n")
        script = (
            "var el = document.getElementById('log');"
            f"el.textContent += {safe_line};"
            "window.scrollTo(0, document.body.scrollHeight);"
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
        return False

    def on_ready(self):
        """服务就绪，切换到正式页面。"""
        if self.ready or self._closing:
            return False
        self.ready = True
        print(f"服务已就绪，加载 {URL}", flush=True)
        self.webview.load_uri(URL)
        return False

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
        if self.main_loop.is_running():
            self.main_loop.quit()


def main():
    app = DshWebviewGtk()
    app.main_loop.run()


if __name__ == "__main__":
    main()
