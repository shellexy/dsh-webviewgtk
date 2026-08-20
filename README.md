# dsh-webviewgtk

基于 GTK4 + WebKitGTK 6.0 的 dsh web 启动器。

注：这只是一个启动器，不包含 dsh，启动时会执行 `npx @deepseek-ai/dsh web --port 3081`

## 依赖

Debian/Ubuntu：

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-webkit-6.0
```

还需要 `npx` 命令在 `PATH` 中。

## 直接运行

```bash
python3 dsh-webviewgtk.py
```

启动后会：

1. 打开 WebKitGTK 窗口并显示 `Loading...`；
2. 以子进程启动 `dsh web --port 3081`；
3. 把 dsh 的标准输出/错误同时显示在 Loading 页和终端；
4. 轮询 `http://127.0.0.1:3081`，可用后自动加载正式页面；
5. 网页下载时弹出保存对话框；
6. 关闭窗口时结束整个 dsh 进程树。

## 安装

在仓库目录下执行：

```bash
pip install . --break-system-packages
```

或：

```bash
python3 setup.py install
```

安装后会提供 `dsh-webviewgtk` 命令，并安装：

- `dsh-webviewgtk` 启动命令
- `dsh-webviewgtk.svg` 图标到 hicolor 图标主题
- `dsh-webviewgtk.desktop` 到 applications 目录

## 图标与 StartupWMClass

- 窗口图标使用 `dsh-webviewgtk.svg`。
- 程序名/窗口类设置为 `dsh-webviewgtk`。
- `dsh-webviewgtk.desktop` 中声明了 `StartupWMClass=dsh-webviewgtk`。
