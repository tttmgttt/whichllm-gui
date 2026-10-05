#!/usr/bin/env python
"""whichllm 图形界面启动脚本（双击或 ``python run_gui.py`` 均可）。"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    try:
        import tkinter  # noqa: F401
    except ImportError:
        print(
            "当前 Python 没有 tkinter，无法启动图形界面。\n"
            "Windows 官方安装包默认自带；Linux 请安装 python3-tk。",
            file=sys.stderr,
        )
        return 2

    try:
        import whichllm  # noqa: F401
    except ImportError:
        print(
            "没有找到 whichllm，请先安装：python -m pip install whichllm",
            file=sys.stderr,
        )
        return 3

    from whichllm_gui.__main__ import main as run_app

    return run_app()


if __name__ == "__main__":
    sys.exit(main())
