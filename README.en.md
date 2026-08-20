# dsh-webviewgtk

A GTK4 + WebKitGTK 6.0 based web launcher for dsh.

**Note:** This is only a launcher; it does not include dsh. When launched, it executes `npx @deepseek-ai/dsh web --port 3081`.

## Dependencies

Debian/Ubuntu:

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-webkit-6.0
```

The `npx` command must also be available in your `PATH`.

## Run directly

```bash
python3 dsh-webviewgtk.py
```

After startup, it will:

1. Open a WebKitGTK window showing `Loading...`;
2. Start `dsh web --port 3081` as a subprocess;
3. Display dsh's stdout/stderr both on the loading page and in the terminal;
4. Poll `http://127.0.0.1:3081` and automatically load the full page once available;
5. Show a save dialog when the page triggers a download;
6. Terminate the entire dsh process tree when the window is closed.

## Installation

Run the following in the repository directory:

```bash
pip install . --break-system-packages
```

Or:

```bash
python3 setup.py install
```

After installation, the `dsh-webviewgtk` command will be available, and the following will be installed:

- The `dsh-webviewgtk` launcher command
- The `dsh-webviewgtk.svg` icon into the hicolor icon theme
- The `dsh-webviewgtk.desktop` desktop file into the applications directory

## Icon and StartupWMClass

- The window icon uses `dsh-webviewgtk.svg`.
- The application name / window class is set to `dsh-webviewgtk`.
- `StartupWMClass=dsh-webviewgtk` is declared in `dsh-webviewgtk.desktop`.
