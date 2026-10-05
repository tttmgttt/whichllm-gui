"""可复用控件：可排序表格、卡片、滚动容器、详情面板、后台任务调度。"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
import traceback
from dataclasses import dataclass, field
from tkinter import ttk
from typing import Any, Callable, Iterable, Sequence

from .theme import PALETTE, Fonts, style_text_widget


@dataclass
class Column:
    """表格列定义。"""

    key: str
    heading: str
    width: int = 120
    anchor: str = "w"
    stretch: bool = False
    sort_key: Callable[[dict], Any] | None = None
    tooltip: str = ""


class SortedTree(ttk.Frame):
    """支持点击表头排序、关键字过滤的表格。

    行的形式是一个 dict：``{"key": 显示文本, ..., "_obj": 原始对象, "_tags": (标签,)}``。
    """

    SORT_ASC = "▲"
    SORT_DESC = "▼"

    def __init__(
        self,
        master: tk.Misc,
        columns: Sequence[Column],
        *,
        fonts: Fonts,
        on_select: Callable[[dict | None], None] | None = None,
        on_activate: Callable[[dict], None] | None = None,
        height: int = 14,
    ) -> None:
        super().__init__(master, style="TFrame")
        self.columns = list(columns)
        self.fonts = fonts
        self.on_select = on_select
        self.on_activate = on_activate

        self._rows: list[dict] = []
        self._visible: list[dict] = []
        self._terms: list[str] = []
        self._sort_key: str | None = None
        self._sort_desc = True
        self._iid_to_row: dict[str, dict] = {}

        keys = [c.key for c in self.columns]
        self.tree = ttk.Treeview(
            self, columns=keys, show="headings", height=height, selectmode="browse"
        )
        for column in self.columns:
            self.tree.heading(
                column.key,
                text=column.heading,
                command=lambda key=column.key: self._on_heading(key),
            )
            self.tree.column(
                column.key,
                width=column.width,
                anchor=column.anchor,
                stretch=column.stretch,
                minwidth=48,
            )

        vsb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self.tree.tag_configure("odd", background=PALETTE["odd"])
        self.tree.tag_configure("even", background=PALETTE["even"])
        self.tree.tag_configure("fit:full_gpu", foreground=PALETTE["ok"])
        self.tree.tag_configure("fit:partial_offload", foreground=PALETTE["warn"])
        self.tree.tag_configure("fit:cpu_only", foreground=PALETTE["fg_dim"])
        self.tree.tag_configure("fit:too_small", foreground=PALETTE["danger"])
        self.tree.tag_configure("installed", font=self.fonts.bold)
        self.tree.tag_configure("top1", font=self.fonts.bold)

        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Double-1>", self._on_tree_activate)
        self.tree.bind("<Return>", self._on_tree_activate)

    # -- 数据 -------------------------------------------------------------- #

    def set_rows(self, rows: Iterable[dict]) -> None:
        selected = self.get_selected()
        selected_id = (selected or {}).get("_id")
        self._rows = list(rows)
        self._render()
        if selected_id is not None:
            for iid, row in self._iid_to_row.items():
                if row.get("_id") == selected_id:
                    self.tree.selection_set(iid)
                    self.tree.see(iid)
                    break

    def set_filter(self, text: str) -> None:
        self._terms = [term for term in (text or "").lower().split() if term]
        self._render()

    def rows(self) -> list[dict]:
        """当前可见（过滤+排序后）的行。"""
        return list(self._visible)

    def all_rows(self) -> list[dict]:
        return list(self._rows)

    def get_selected(self) -> dict | None:
        selection = self.tree.selection()
        if not selection:
            return None
        return self._iid_to_row.get(selection[0])

    def select_first(self) -> None:
        children = self.tree.get_children()
        if children and not self.tree.selection():
            self.tree.selection_set(children[0])

    def clear(self) -> None:
        self._rows = []
        self._render()

    def sort_by(self, key: str, desc: bool = True) -> None:
        self._sort_key = key
        self._sort_desc = desc
        self._render()

    # -- 内部 -------------------------------------------------------------- #

    def _render(self) -> None:
        rows = self._filtered()
        rows = self._sorted(rows)
        self._visible = rows

        self.tree.delete(*self.tree.get_children())
        self._iid_to_row.clear()
        for index, row in enumerate(rows):
            tags = list(row.get("_tags") or ())
            tags.append("odd" if index % 2 else "even")
            iid = self.tree.insert(
                "",
                "end",
                values=[row.get(column.key, "") for column in self.columns],
                tags=tuple(tags),
            )
            self._iid_to_row[iid] = row
        self._update_headings()

    def _filtered(self) -> list[dict]:
        if not self._terms:
            return list(self._rows)
        result = []
        for row in self._rows:
            haystack = " ".join(
                str(value) for key, value in row.items() if not key.startswith("_")
            ).lower()
            if all(term in haystack for term in self._terms):
                result.append(row)
        return result

    def _sorted(self, rows: list[dict]) -> list[dict]:
        if not self._sort_key:
            return rows
        column = next((c for c in self.columns if c.key == self._sort_key), None)
        if column is None:
            return rows

        def key_of(row: dict) -> Any:
            if column.sort_key is not None:
                return column.sort_key(row)
            return _normalise(row.get(column.key))

        try:
            return sorted(rows, key=key_of, reverse=self._sort_desc)
        except TypeError:
            # 混合类型（None 与数字）时退化为字符串排序
            return sorted(
                rows, key=lambda r: str(key_of(r)), reverse=self._sort_desc
            )

    def _on_heading(self, key: str) -> None:
        if self._sort_key == key:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_key = key
            self._sort_desc = True
        self._render()

    def _update_headings(self) -> None:
        for column in self.columns:
            text = column.heading
            if column.key == self._sort_key:
                text += " " + (self.SORT_DESC if self._sort_desc else self.SORT_ASC)
            self.tree.heading(column.key, text=text)

    def _on_tree_select(self, _event: tk.Event) -> None:
        if self.on_select:
            self.on_select(self.get_selected())

    def _on_tree_activate(self, _event: tk.Event) -> None:
        row = self.get_selected()
        if row and self.on_activate:
            self.on_activate(row)


def _normalise(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.lower()
    return value


class Card(ttk.Frame):
    """带标题的卡片容器，内容放到 ``self.body``。"""

    def __init__(
        self,
        master: tk.Misc,
        title: str | None = None,
        *,
        subtitle: str | None = None,
        padding: int = 12,
    ) -> None:
        super().__init__(master, style="Surface.TFrame", padding=1)
        self.body = ttk.Frame(self, style="Surface.TFrame", padding=padding)
        self.body.pack(fill="both", expand=True)
        if title:
            header = ttk.Frame(self.body, style="Surface.TFrame")
            header.pack(fill="x", pady=(0, 8))
            ttk.Label(header, text=title, style="CardTitle.TLabel").pack(side="left")
            if subtitle:
                ttk.Label(header, text=subtitle, style="CardDim.TLabel").pack(
                    side="left", padx=(8, 0)
                )


class ScrollFrame(ttk.Frame):
    """纵向滚动容器，内容放进 ``self.inner``。"""

    def __init__(self, master: tk.Misc, *, width: int | None = None) -> None:
        super().__init__(master, style="TFrame")
        self.canvas = tk.Canvas(
            self,
            background=PALETTE["bg"],
            highlightthickness=0,
            borderwidth=0,
            **({"width": width} if width else {}),
        )
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        self.inner = ttk.Frame(self.canvas, style="TFrame")
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")

        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<Enter>", self._bind_wheel)
        self.canvas.bind("<Leave>", self._unbind_wheel)

    def _on_inner_configure(self, _event: tk.Event) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)

    def _bind_wheel(self, _event: tk.Event) -> None:
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)
        self.canvas.bind_all("<Button-4>", self._on_wheel)
        self.canvas.bind_all("<Button-5>", self._on_wheel)

    def _unbind_wheel(self, _event: tk.Event) -> None:
        self.canvas.unbind_all("<MouseWheel>")
        self.canvas.unbind_all("<Button-4>")
        self.canvas.unbind_all("<Button-5>")

    def _on_wheel(self, event: tk.Event) -> None:
        if getattr(event, "num", None) == 4:
            delta = -1
        elif getattr(event, "num", None) == 5:
            delta = 1
        else:
            delta = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(delta, "units")

    def scroll_to_top(self) -> None:
        self.canvas.yview_moveto(0.0)


class DetailPane(ttk.Frame):
    """只读的多行文本面板（用于详情 / 代码）。"""

    def __init__(
        self,
        master: tk.Misc,
        fonts: Fonts,
        *,
        mono: bool = False,
        height: int = 10,
        title: str | None = None,
    ) -> None:
        super().__init__(master, style="TFrame")
        self.fonts = fonts
        if title:
            ttk.Label(self, text=title, style="Section.TLabel").pack(anchor="w", pady=(0, 4))
        wrapper = ttk.Frame(self, style="TFrame")
        wrapper.pack(fill="both", expand=True)
        self.text = tk.Text(wrapper, height=height, undo=False)
        style_text_widget(self.text, fonts, mono=mono)
        scrollbar = ttk.Scrollbar(wrapper, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=scrollbar.set)
        self.text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.text.configure(state="disabled")

    def set_text(self, content: str) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", content)
        self.text.configure(state="disabled")
        self.text.yview_moveto(0.0)

    def get_text(self) -> str:
        return self.text.get("1.0", "end-1c")

    def clear(self) -> None:
        self.set_text("")


def field_grid(master: tk.Misc, *, columns: int = 2, label_width: int = 10) -> ttk.Frame:
    """统一风格的「标签 + 控件」网格容器。"""
    frame = ttk.Frame(master, style="TFrame")
    for column in range(columns):
        frame.columnconfigure(column * 2 + 1, weight=1)
    return frame


def add_field(
    grid: ttk.Frame,
    row: int,
    label: str,
    widget: tk.Widget,
    *,
    label_width: int = 9,
    hint: str | None = None,
    column: int = 0,
    fonts: Fonts | None = None,
) -> None:
    """把「标签 + 控件（+ 提示）」放进 field_grid 的指定行。"""
    base = column * 3
    ttk.Label(grid, text=label, style="Dim.TLabel", width=label_width, anchor="w").grid(
        row=row, column=base, sticky="w", padx=(0, 6), pady=3
    )
    widget.grid(row=row, column=base + 1, sticky="ew", pady=3)
    if hint:
        style = "Dim.TLabel"
        ttk.Label(grid, text=hint, style=style).grid(
            row=row, column=base + 2, sticky="w", padx=(8, 0), pady=3
        )


class ChoiceField(ttk.Combobox):
    """只读下拉框：内部值 ↔ 中文显示文案的映射。"""

    def __init__(
        self,
        master: tk.Misc,
        options: Sequence[tuple[str, str]],
        default: str = "",
        *,
        width: int = 20,
    ) -> None:
        self._values = {label: value for value, label in options}
        self._labels = [label for _, label in options]
        super().__init__(master, values=self._labels, state="readonly", width=width)
        self.set_value(default)

    def value(self) -> str:
        return self._values.get(self.get(), self.get())

    def set_value(self, value: str) -> None:
        for label, mapped in self._values.items():
            if mapped == value:
                self.set(label)
                return
        if self._labels:
            self.set(self._labels[0])
        else:
            self.set(value)


class SuggestEntry(ttk.Combobox):
    """带候选下拉的输入框：输入时按子串过滤候选，可直接回车触发。"""

    def __init__(
        self,
        master: tk.Misc,
        values: Sequence[str] | Callable[[], Sequence[str]] = (),
        *,
        on_submit: Callable[[], None] | None = None,
        width: int = 32,
        limit: int = 300,
    ) -> None:
        self._value_source = values
        self._all_values: list[str] = []
        self._limit = limit
        super().__init__(master, width=width)
        self.configure(state="normal")
        self._reload_values()
        self.bind("<KeyRelease>", self._on_key)
        self.bind("<<ComboboxSelected>>", lambda _e: self._on_key(None))
        if on_submit:
            self.bind("<Return>", lambda _e: on_submit())

    def _reload_values(self) -> None:
        source = self._value_source
        try:
            self._all_values = list(source() if callable(source) else source)
        except Exception:  # noqa: BLE001 - 候选只是便利功能
            self._all_values = []
        self.configure(values=self._all_values[: self._limit])

    def set_values(self, values: Sequence[str]) -> None:
        self._value_source = list(values)
        self._reload_values()

    def _on_key(self, _event: tk.Event | None) -> None:
        typed = self.get().strip().lower()
        if not typed:
            self.configure(values=self._all_values[: self._limit])
            return
        matches = [value for value in self._all_values if typed in value.lower()]
        self.configure(values=matches[: self._limit])

    def value(self) -> str:
        return self.get().strip()

    def set_value(self, value: str) -> None:
        self.set(value)


class ItemList(ttk.Frame):
    """可增删的字符串列表（输入框 + 添加/删除/清空 + 列表框）。"""

    def __init__(
        self,
        master: tk.Misc,
        fonts: Fonts,
        *,
        values: Sequence[str] | Callable[[], Sequence[str]] = (),
        placeholder: str = "",
        height: int = 4,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(master, style="TFrame")
        self.fonts = fonts
        self.on_change = on_change
        self.entry = SuggestEntry(
            self, values, on_submit=self.add_from_entry, width=20
        )
        self.entry.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        buttons = ttk.Frame(self, style="TFrame")
        buttons.grid(row=0, column=1, sticky="w", padx=(6, 0), pady=(0, 4))
        ttk.Button(buttons, text="添加", style="Small.TButton", command=self.add_from_entry).pack(side="left")
        ttk.Button(buttons, text="删除", style="Small.TButton", command=self.remove_selected).pack(side="left", padx=4)
        ttk.Button(buttons, text="清空", style="Small.TButton", command=self.clear).pack(side="left")

        wrapper = ttk.Frame(self, style="TFrame")
        wrapper.grid(row=1, column=0, columnspan=2, sticky="nsew")
        self.listbox = tk.Listbox(
            wrapper,
            height=height,
            background=PALETTE["surface_alt"],
            foreground=PALETTE["fg"],
            selectbackground=PALETTE["accent"],
            selectforeground=PALETTE["accent_fg"],
            highlightthickness=1,
            highlightbackground=PALETTE["border"],
            highlightcolor=PALETTE["border_hi"],
            relief="flat",
            borderwidth=0,
            font=fonts.mono_small,
            activestyle="none",
        )
        scrollbar = ttk.Scrollbar(wrapper, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scrollbar.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        if placeholder:
            self.listbox.insert("end", placeholder)
            self.listbox.itemconfigure(0, foreground=PALETTE["fg_muted"])
            self._placeholder = placeholder
        else:
            self._placeholder = None

    # -- 数据 -------------------------------------------------------------- #

    def items(self) -> tuple[str, ...]:
        values = []
        for index in range(self.listbox.size()):
            text = self.listbox.get(index)
            if self._placeholder is not None and index == 0 and text == self._placeholder:
                continue
            values.append(text)
        return tuple(values)

    def set_items(self, values: Sequence[str]) -> None:
        self.listbox.delete(0, "end")
        if not values and self._placeholder:
            self.listbox.insert("end", self._placeholder)
            self.listbox.itemconfigure(0, foreground=PALETTE["fg_muted"])
        else:
            for value in values:
                self.listbox.insert("end", value)
        self._changed()

    def add(self, value: str) -> None:
        value = (value or "").strip()
        if not value:
            return
        existing = list(self.items())
        if value in existing:
            return
        if self._placeholder is not None and self.listbox.size() == 1 and not existing:
            self.listbox.delete(0, "end")
        self.listbox.insert("end", value)
        self._changed()

    def add_from_entry(self) -> None:
        value = self.entry.value()
        if value:
            self.add(value)
            self.entry.set_value("")

    def remove_selected(self) -> None:
        selection = list(self.listbox.curselection())
        if not selection:
            return
        for index in reversed(selection):
            if self._placeholder is not None and index == 0 and self.listbox.get(0) == self._placeholder:
                continue
            self.listbox.delete(index)
        if self.listbox.size() == 0 and self._placeholder:
            self.listbox.insert("end", self._placeholder)
            self.listbox.itemconfigure(0, foreground=PALETTE["fg_muted"])
        self._changed()

    def clear(self) -> None:
        self.set_items(())

    def _changed(self) -> None:
        if self.on_change:
            self.on_change()


class TaskRunner:
    """后台任务调度：耗时工作放到线程里，结果回到 Tk 主线程。

    - **按通道判定过期**：同一个 ``channel`` 上只有最新一次提交的结果会被采用
      （例如用户连续调整筛选条件时，旧结果不应覆盖新结果）；不同通道之间
      互不影响（推荐计算不该因为「读取运行环境」这类旁路任务而被丢弃）。
    - **错误永不静默丢弃**：即使已过期，异常依然会送到 ``on_error``。
    - 进度回调只往队列里塞消息，绝不直接操作 Tk 控件。
    """

    def __init__(self, widget: tk.Misc, *, on_state: Callable[[bool, str], None] | None = None) -> None:
        self.widget = widget
        self.on_state = on_state
        self._queue: queue.Queue[tuple] = queue.Queue()
        self._serial = 0
        self._channel_generation: dict[str, int] = {}
        self._active = 0
        self._destroyed = False
        self._callbacks: dict[
            int, tuple[Callable[[Any], None] | None, Callable[[BaseException], None] | None]
        ] = {}
        self._poll()

    # -- 对外接口 ---------------------------------------------------------- #

    @property
    def busy(self) -> bool:
        return self._active > 0

    def submit(
        self,
        label: str,
        work: Callable[[Callable[[str], None]], Any],
        *,
        on_done: Callable[[Any], None] | None = None,
        on_error: Callable[[BaseException], None] | None = None,
        channel: str | None = None,
    ) -> int:
        self._serial += 1
        serial = self._serial
        generation = 0
        if channel:
            generation = self._channel_generation.get(channel, 0) + 1
            self._channel_generation[channel] = generation

        self._active += 1
        self._callbacks[serial] = (on_done, on_error)
        self._notify(True, label)

        def progress(message: str) -> None:
            if not self._destroyed:
                self._queue.put(
                    ("progress", serial, channel, generation, message, label)
                )

        def run() -> None:
            try:
                value = work(progress)
            except BaseException as exc:  # noqa: BLE001 - 交给界面展示
                self._queue.put(("error", serial, channel, generation, exc, label))
            else:
                self._queue.put(("done", serial, channel, generation, value, label))

        threading.Thread(target=run, name=f"whichllm-gui-{label}", daemon=True).start()
        return serial

    def destroy(self) -> None:
        self._destroyed = True

    # -- 内部 -------------------------------------------------------------- #

    def _notify(self, busy: bool, label: str) -> None:
        if self.on_state and not self._destroyed:
            self.on_state(busy, label)

    def _is_stale(self, channel: str | None, generation: int) -> bool:
        if not channel:
            return False
        return generation != self._channel_generation.get(channel, 0)

    def _poll(self) -> None:
        if self._destroyed:
            return
        try:
            while True:
                kind, serial, channel, generation, payload, _label = self._queue.get_nowait()
                if kind == "progress":
                    if not self._is_stale(channel, generation) and self.on_state:
                        self.on_state(True, payload)
                    continue

                self._active = max(0, self._active - 1)
                callbacks = self._callbacks.pop(serial, (None, None))
                if self._active == 0:
                    self._notify(False, "")

                stale = self._is_stale(channel, generation)
                on_done, on_error = callbacks
                if kind == "done":
                    if stale:
                        continue
                    if on_done:
                        on_done(payload)
                else:
                    # 过期任务里的异常也要报出来，否则用户会以为「点了没反应」
                    if on_error:
                        on_error(payload)
                    else:
                        traceback.print_exception(
                            type(payload), payload, payload.__traceback__
                        )
        except queue.Empty:
            pass
        finally:
            if not self._destroyed:
                self.widget.after(80, self._poll)
