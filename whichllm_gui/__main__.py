"""入口：``python -m whichllm_gui``。

注意：用 ``pythonw.exe``（无控制台）启动时 ``sys.stdout`` 会是 ``None``，
而 whichllm 内部使用 rich 输出，因此这里先把标准流指向 devnull 兜底。
"""

from __future__ import annotations

import os
import sys


def _ensure_stdio() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            try:
                setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
            except OSError:
                pass


def main() -> int:
    _ensure_stdio()
    from .app import main as run_app

    return run_app()


if __name__ == "__main__":
    sys.exit(main())
