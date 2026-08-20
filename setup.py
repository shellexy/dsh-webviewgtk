#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dsh-webviewgtk 安装脚本。"""

from pathlib import Path

from setuptools import setup

HERE = Path(__file__).resolve().parent

setup(
    name="dsh-webviewgtk",
    version="0.1.0",
    description="Launch dsh web in a WebKitGTK 6.0 / GTK4 window",
    long_description=(HERE / "README.md").read_text(encoding="utf-8"),
    long_description_content_type="text/markdown",
    py_modules=["dsh_webviewgtk"],
    entry_points={
        "console_scripts": [
            "dsh-webviewgtk=dsh_webviewgtk:main",
        ],
    },
    data_files=[
        ("share/icons/hicolor/scalable/apps", ["dsh-webviewgtk.svg"]),
        ("share/applications", ["dsh-webviewgtk.desktop"]),
    ],
    python_requires=">=3.8",
)
