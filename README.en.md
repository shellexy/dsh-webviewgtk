# dsh-webviewgtk

A dsh web launcher based on GTK4 + WebKitGTK 6.0.

Can be used as a GTK4 desktop frontend for [deepseek-harness](https://github.com/deepseek-ai/deepseek-harness/).

**Note:** This is just a launcher – it does not include dsh itself, nor does it pre‑install any extra plugins. 

When launched, it executes `npx --loglevel verbose --yes @deepseek-ai/dsh web --no-open --port 3081`.

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
2. Start `npx --loglevel verbose --yes @deepseek-ai/dsh web --no-open --port 3081` as a child process;
3. Show dsh's stdout/stderr both on the loading page and in the terminal, with the log area auto-scrolling to the bottom;
4. Parse the tokenized URL from dsh output (for example `dsh web: http://127.0.0.1:3081/?token=...`) and automatically load it once available;
5. Support pasting images from the system clipboard into web page input fields;
6. Open external links or links requesting a new window in the system default application (http, https, mailto, tel, ftp, magnet, etc.);
7. Show a save dialog when the page triggers a download;
8. Terminate the entire dsh process tree when the window is closed.

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
- The `dsh-webviewgtk.desktop` desktop entry into the applications directory

## Icon and StartupWMClass

- The window icon uses `dsh-webviewgtk.svg`.
- The application name / window class is set to `dsh-webviewgtk`.
- The `dsh-webviewgtk.desktop` file declares `StartupWMClass=dsh-webviewgtk`.
