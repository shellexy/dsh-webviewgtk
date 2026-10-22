# dsh-webviewgtk

A dsh web launcher based on GTK4 + WebKitGTK 6.0.

Can be used as a GTK4 desktop frontend for [deepseek-harness](https://github.com/deepseek-ai/deepseek-harness/).

**Note:** This is just a launcher – it does not include dsh itself, nor does it pre‑install any extra plugins. 

When launched, it executes `npx --loglevel verbose --yes @deepseek-ai/dsh web --no-open --port 0`.

The goal is to always use the latest official dsh version.

## Screenshot

![dsh-webviewgtk mainwindow](screenshot.webp)

## Dependencies

Debian/Ubuntu:

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-webkit-6.0
```

The `npx` command must also be available in your `PATH`.

## Download

```bash
git clone https://github.com/shellexy/dsh-webviewgtk.git
cd dsh-webviewgtk
```

## Run directly

```bash
python3 dsh-webviewgtk.py
```

When launched, it will:

1. Open a WebKitGTK window with a centered icon and `Loading...`;
2. Start `npx --loglevel verbose --yes @deepseek-ai/dsh web --no-open --port 0` as a child process, letting dsh choose a free port;
3. Show dsh's stdout/stderr both on the loading page and in the terminal, with the log area auto-scrolling to the bottom;
4. Parse the tokenized URL from dsh output (for example `dsh web: http://127.0.0.1:54321/?token=...`, with the port chosen by dsh) and automatically load it once available;
5. Support pasting images from the system clipboard into web page input fields;
6. Open external links or links requesting a new window in the system default application (http, https, mailto, tel, ftp, magnet, etc.);
7. Show a save dialog when the page triggers a download;
8. Show a system desktop notification while the window is not in the foreground (see below);
9. Terminate the entire dsh process tree when the window is closed;
10. Run as a single instance: launching it again only activates the existing window and does not start another dsh process.

## Developer tools

Press **F12** or **Ctrl+Shift+I** to open or close WebKit's developer tools (docked in the window,
with Console, Elements, and Network). They also open while the loading page is showing, which is
what you want when diagnosing the page or the notification bridge.

## Desktop notifications

The official dsh web UI emits no notifications of its own, so the shell injects a bridge script at document start and lets it translate dsh's own session state into desktop notifications:

- **A turn finished** — dsh's client drives its session state from one forwarded event argument pair (`sessionId`, `running`); the bridge wraps `MessageEvent.data` to observe that stream and matches on the **argument shape only, never the event name**, so `running` turning `false` means the turn is over.
- **Waiting for your approval or answer** — decided by the pending panel dsh renders, using its own stable markers: `[data-approval-key]` (permission approval), `[data-question-key]` (an `ask_user` question), and `[data-plan-review-key]` (plan confirmation). A `MutationObserver` plus one-second polling detects them, and this path **depends on no dsh event name at all**. Every question of one `ask_user` call collapses into one request (the panel key minus its trailing index), so that call notifies once and the next call notifies again. The session id is the one selected in the sidebar, so clicking the notification switches back to it.
  Some dsh versions never forward this kind of wait to the browser as a waterfall, which is why the DOM is the primary path; if `approval/request` or `user-questions/request` waterfall frames do arrive, they only sharpen the title (waiting for approval / waiting for your answer) and cover the case of a background session waiting with no panel in the current view.
- No interruption when the window is in the foreground and the notification belongs to the session you are viewing; clicking a notification focuses the window and switches to that session; returning to the app marks everything read and withdraws the notifications sent during this run.

To turn notifications off, run `globalThis.__DSH_SHELL_NOTIFY_DISABLE__ = true` in the page console; to adapt to a future dsh version, change `PENDING_MARKERS` (the DOM selector), `PENDING_TRIGGERS` (optional waterfall event names), and `PENDING_POLL_INTERVAL_MS` in `BRIDGE_JS_TEMPLATE` inside `dsh_webviewgtk.py`.

The bridge relies on two kinds of dsh internals: the `(sessionId, running)` argument convention of the state event (not its name) and the `data-*` panel attributes above. If one kind of notification stops after a dsh upgrade, check those two in the page console first.

### Traps already hit (read before changing this code)

1. **Injection timing**: the bridge runs at `UserScriptInjectionTime.START`, when `<div id="root">` does **not exist yet**. The scanner therefore cannot require `#root`; it attaches to `documentElement` and waits for `DOMContentLoaded` (`installScanner`). An earlier version returned early on `getElementById('root') === null`, so the scanner never installed and every notification except "turn finished" silently vanished.
2. **Origin-check window**: `get_uri()` is empty right after `load_uri` while the bridge is already reporting. Parse the payload first and validate the origin only for `completed` / `pending` (the kinds that drive shell actions), falling back to "the dsh URL was loaded" (`_trusted_uri`) while the URI is empty — otherwise the shell drops its own real notifications.
3. **Log level**: the WebKit inspector may filter `console.debug`, so the bridge logs with `console.log`.
4. **Never catch silently**: every bridge step reports failures to the terminal as `kind: 'diagnose'`; a silent throw otherwise makes a whole notification class disappear without a trace. With `DSH_WEBVIEWGTK_DEBUG=1` these diagnostics print step by step.
5. **Payload type**: the `script-message-received` callback may hand over JSON text or an already-decoded object; `_parse_script_message` accepts both.

### Diagnostics

```bash
# Shell side: prints the injected bridge script, every payload received, and why one was dropped.
# It also forces the page-side debug on, so nothing has to be set in the console.
DSH_WEBVIEWGTK_DEBUG=1 python3 dsh-webviewgtk.py
```

A healthy run prints these in execution order:

```
[dsh-webviewgtk] 桥诊断：bridge-start href=http://127.0.0.1:3081/…
[dsh-webviewgtk] 桥诊断：scanner-deferred readyState=loading
[dsh-webviewgtk] 桥诊断：markers 0 … none
[dsh-webviewgtk] 桥诊断：scanner-ready root=DIV interval=1000ms
[dsh-webviewgtk] 桥诊断：markers 1 <session id> <panel key>      ← panel appeared
[dsh-webviewgtk] 收到页面通知载荷：{'kind': 'pending', …}
桌面通知：DeepSeek Harness 在等你 — 有新的待处理交互
```

`scanner-ready root=none`, or no such line at all, means the scanner never installed (trap 1 above).

Page side (dsh page developer console):

```js
typeof globalThis.__DSH_SHELL_NOTIFY__        // 'function' = bridge is running; 'undefined' = it was never injected
globalThis.__DSH_SHELL_NOTIFY_DEBUG__ = true  // log every observed frame, plus a 5-second heartbeat
globalThis.__DSH_SHELL_NOTIFY_DISABLE__ = true // turn desktop notifications off
```

- No `[dsh-webviewgtk] 已注入的通知桥脚本` line in the terminal: a stale installed copy is running
  (`pip show -f dsh-webviewgtk` names its location); reinstall it or run the repository copy.
- No `收到页面通知载荷` line: the bridge did not start or the message channel was not registered
  (startup would print `警告：无法注册通知桥消息通道`); read the console's `[dsh-shell-notify]` output too.
- The console only shows `heartbeat … markers=0` and never `pending-panel` while the panel is on screen: that panel
  carries none of the known `data-*` markers (or does not live under `#root`). Share the first few hundred
  characters of its `outerHTML` so `PENDING_MARKERS` can match what it really uses.
- Not even `heartbeat` appears: the debug flag was not set (it must be `true`, and the first line arrives within five
  seconds), or the bridge was never injected (check the `typeof` line above first).

## Installation

In the repository directory, run:

```bash
pip install . --break-system-packages
```

or:

```bash
python3 setup.py install --user
```

After installation, the `dsh-webviewgtk` command will be available, and the following will be installed:

- The `dsh-webviewgtk` launcher command
- The `dsh-webviewgtk.svg` icon into the hicolor icon theme
- The `com.github.shellexy.DshWebviewGtk.desktop` desktop entry into the applications directory (the same application id the notifications use)

## Tests

```bash
python3 tests/bridge_smoke_test.py   # bridge frame parsing (needs node only)
python3 tests/notification_probe.py  # pops one real notification to verify the click callback (needs a desktop session)
```

## Icon and StartupWMClass

- The window icon uses `dsh-webviewgtk.svg`.
- The application name / window class is set to `dsh-webviewgtk`.
- The `com.github.shellexy.DshWebviewGtk.desktop` file declares `StartupWMClass=dsh-webviewgtk`.

## Known issues

- **`corrupted double-linked list` after exit (harmless)**: it appears only after a real dsh page has been
  loaded, after the main loop returned and during interpreter teardown, and it does not affect use. It is not
  caused by the shell: the window-only path stays clean and opening the inspector does not reproduce it;
  `WEBKIT_DISABLE_DMABUF_RENDERER=1 python3 dsh_webviewgtk.py` removes it, so it is a WebKitGTK 2.52.6
  DMA-BUF rendering defect during exit. **Do not** try to "clean it up" by explicitly calling
  `webview.destroy()` / `window.destroy()` after the main loop returns: measured, that turns the harmless
  message into a segfault (core dumped).

Per-version changes live in [CHANGELOG.md](CHANGELOG.md).
