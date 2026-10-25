"""Windows/Ubuntu desktop entry point: python -m src.desktop."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import queue
import threading
import uuid

from .desktop_service import DesktopService, configure_backend, default_config, load_config, make_payload, save_config


class DesktopApp:
    def __init__(self, root, config=None, config_path=None):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk, self.root = tk, ttk, root
        self.config_path = Path(config_path or Path.home() / ".config" / "ai-orchestrator" / "client.json")
        self.config = config or (load_config(self.config_path) if self.config_path.exists() else default_config(Path.home()))
        self.events, self.busy = queue.Queue(), False
        root.title("AI Orchestrator · 统一客户端")
        root.geometry("1080x780")
        root.minsize(850, 620)
        style = ttk.Style(root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Title.TLabel", font=("Segoe UI", 20, "bold"))
        style.configure("TButton", padding=(12, 7))
        shell = ttk.Frame(root, padding=20)
        shell.pack(fill="both", expand=True)
        ttk.Label(shell, text="AI Orchestrator", style="Title.TLabel").pack(anchor="w")
        ttk.Label(shell, text="一个客户端管理任务、执行器与额度 · 判断与免费发现按需启用").pack(anchor="w", pady=(4, 16))
        self.book = ttk.Notebook(shell)
        self.book.pack(fill="both", expand=True)
        self.task_tab = self._tab("任务")
        self.backend_tab = self._tab("执行器与额度")
        self.extension_tab = self._tab("可选扩展")
        self.settings_tab = self._tab("客户端与云端")
        self.receipt_tab = self._tab("结果回执")
        self._tasks()
        self._backends()
        self._extensions()
        self._settings()
        self.output = tk.Text(self.receipt_tab, wrap="word", font=("Consolas", 10), state="disabled")
        scroll = ttk.Scrollbar(self.receipt_tab, command=self.output.yview)
        self.output.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.output.pack(fill="both", expand=True)
        self.status = tk.StringVar(value="就绪 · 扩展默认关闭 · 尚未执行任务")
        ttk.Label(shell, textvariable=self.status).pack(anchor="w", pady=(12, 0))
        self._refresh()
        root.protocol("WM_DELETE_WINDOW", self._close)
        root.after(100, self._poll)

    def _tab(self, title):
        frame = self.ttk.Frame(self.book, padding=16)
        self.book.add(frame, text=title)
        return frame

    def _row(self, frame, title, variable, values=None):
        line = self.ttk.Frame(frame)
        line.pack(fill="x", pady=4)
        self.ttk.Label(line, text=title, width=23).pack(side="left")
        widget = (self.ttk.Combobox(line, textvariable=variable, values=values, state="readonly")
                  if values else self.ttk.Entry(line, textvariable=variable))
        widget.pack(side="left", fill="x", expand=True)
        return line

    def _tasks(self):
        tk, ttk, frame = self.tk, self.ttk, self.task_tab
        self.task_id = tk.StringVar(value=uuid.uuid4().hex)
        self.workdir = tk.StringVar(value=self.config["workspace_root"])
        self.task_tools = tk.StringVar()
        self.task_type, self.value = tk.StringVar(value="general"), tk.StringVar(value="normal")
        self._row(frame, "任务 ID（重试保留）", self.task_id)
        self._row(frame, "工作目录", self.workdir)
        self._row(frame, "本次工具（逗号分隔）", self.task_tools)
        self._row(frame, "任务类型", self.task_type, ["general", "code_generation", "research", "writing", "multimodal"])
        self._row(frame, "任务价值", self.value, ["trivial", "normal", "important", "critical"])
        ttk.Label(frame, text="任务描述").pack(anchor="w", pady=(10, 4))
        self.prompt = tk.Text(frame, height=7, wrap="word")
        self.prompt.pack(fill="both", expand=True)
        bar = ttk.Frame(frame)
        bar.pack(fill="x", pady=12)
        for label, action in [("预览路由", "plan"), ("在本机执行", "run"), ("提交到云端", "submit"),
                              ("领取云端任务", "node"), ("查询状态", "status")]:
            ttk.Button(bar, text=label, command=lambda a=action: self._task_action(a)).pack(side="left", padx=(0, 6))
        ttk.Button(frame, text="新任务", command=lambda: self.task_id.set(uuid.uuid4().hex)).pack(anchor="w")
        ttk.Label(frame, text="执行会启动已配置的后端；只有独立验收通过才显示 done。未知结果保留原任务 ID 查询与核对。",
                  wraplength=900).pack(anchor="w", pady=8)

    def _backends(self):
        tk, ttk, frame = self.tk, self.ttk, self.backend_tab
        self.model_list = tk.Listbox(frame, height=4)
        self.model_list.pack(fill="x", pady=(0, 12))
        self.kind, self.model, self.provider = tk.StringVar(value="hermes"), tk.StringVar(), tk.StringVar(value="openai-codex")
        self.executable, self.billing = tk.StringVar(value="hermes"), tk.StringVar(value="subscription")
        self.backend_tools, self.account, self.amount = tk.StringVar(value="terminal"), tk.StringVar(), tk.StringVar(value="1")
        self.ollama_url = tk.StringVar(value="http://127.0.0.1:11434")
        self._row(frame, "执行后端", self.kind, ["hermes", "deepseek", "ollama"])
        self._row(frame, "模型名称（精确）", self.model)
        self._row(frame, "供应商 / provider", self.provider)
        self._row(frame, "已安装的可执行文件", self.executable)
        self._row(frame, "额度类型", self.billing, ["subscription", "local"])
        self._row(frame, "工具 / toolsets", self.backend_tools)
        self._row(frame, "额度账户名", self.account)
        self._row(frame, "每次预留上界（原单位）", self.amount)
        self._row(frame, "Ollama 地址", self.ollama_url)
        self.kind.trace_add("write", self._kind_changed)
        self.model_list.bind("<<ListboxSelect>>", self._select_model)
        bar = ttk.Frame(frame)
        bar.pack(fill="x", pady=10)
        ttk.Button(bar, text="添加 / 替换模型", command=self._add_backend).pack(side="left")
        ttk.Button(bar, text="移除所选模型", command=self._remove_backend).pack(side="left", padx=8)
        ttk.Button(bar, text="导入额度 JSON", command=self._import_quota).pack(side="left")
        ttk.Label(frame, text="后端通过自身官方工具登录。本客户端不保存密码或订阅凭据。CC Switch 额度使用经过清理的标准 JSON 导入；共享额度在云端主机导入。",
                  wraplength=900).pack(anchor="w", pady=8)

    def _extensions(self):
        tk, ttk, frame = self.tk, self.ttk, self.extension_tab
        extensions = self.config.get("extensions", {})
        judge, discovery = extensions.get("judge", {}), extensions.get("discovery", {})
        self.judge_enabled = tk.BooleanVar(value=judge.get("enabled") is True)
        self.discovery_enabled = tk.BooleanVar(value=discovery.get("enabled") is True)
        self.judge_model = tk.StringVar(value=judge.get("config", {}).get("model", "qwen3:4b"))
        self.judge_url = tk.StringVar(value=judge.get("config", {}).get("base_url", "http://127.0.0.1:11434"))
        self.cache = tk.StringVar(value=discovery.get("cache", str(Path(self.config["workspace_root"]) / ".orchestrator-discovery.json")))
        ttk.Checkbutton(frame, text="启用客户端判断模型（本地 Ollama）", variable=self.judge_enabled).pack(anchor="w", pady=10)
        self._row(frame, "判断模型", self.judge_model)
        self._row(frame, "本地 Ollama 地址", self.judge_url)
        ttk.Label(frame, text="关闭时使用现有确定性评分器，不调用判断模型；判断模型不可突破额度与工具准入规则。").pack(anchor="w", pady=12)
        ttk.Separator(frame).pack(fill="x", pady=12)
        ttk.Checkbutton(frame, text="启用免费 / 限免模型发现", variable=self.discovery_enabled).pack(anchor="w", pady=10)
        self._row(frame, "发现缓存", self.cache)
        self.discovery_source = tk.StringVar(value="token-scout")
        self._row(frame, "导入来源", self.discovery_source, ["token-scout", "free-models-monitor", "ollama"])
        ttk.Button(frame, text="导入发现数据…", command=self._import_discovery).pack(anchor="w", pady=10)
        ttk.Label(frame, text="来源数据由已安装的发现工具提供；这里只在点击导入时读取文件。不自动运行后台扫描。免费候选仍需配置执行器与可信额度。",
                  wraplength=880).pack(anchor="w", pady=10)
        ttk.Button(frame, text="保存扩展设置", command=self._save).pack(anchor="w", pady=10)

    def _settings(self):
        tk, ttk, frame = self.tk, self.ttk, self.settings_tab
        self.workspace = tk.StringVar(value=self.config["workspace_root"])
        self.target = tk.StringVar(value=self.config.get("target", "desktop"))
        self.client_id = tk.StringVar(value=self.config.get("client_id", "desktop"))
        server = self.config.get("server", {})
        self.cloud_enabled = tk.BooleanVar(value=bool(server))
        self.cloud_url = tk.StringVar(value=server.get("url", "http://127.0.0.1:8787"))
        self.token_env = tk.StringVar(value=server.get("token_env", "ORCHESTRATOR_TOKEN"))
        self.private_http = tk.BooleanVar(value=server.get("allow_http") is True)
        self.verifier = tk.StringVar(value=json.dumps(self.config.get("verification", {}).get("command", [])))
        self._row(frame, "工作区根目录", self.workspace)
        self._row(frame, "节点 target", self.target)
        self._row(frame, "客户端 client_id", self.client_id)
        self._row(frame, "验收命令（JSON argv）", self.verifier)
        ttk.Label(frame, text='例如 ["python", "-m", "pytest", "-q"]；在任务目录运行，空列表表示结果未验收。').pack(anchor="w", pady=8)
        ttk.Checkbutton(frame, text="连接云端调度与共享账本", variable=self.cloud_enabled).pack(anchor="w", pady=12)
        self._row(frame, "云端地址", self.cloud_url)
        self._row(frame, "访问令牌的环境变量名", self.token_env)
        ttk.Checkbutton(frame, text="允许可信私网 HTTP（例如 Tailscale）", variable=self.private_http).pack(anchor="w", pady=8)
        bar = ttk.Frame(frame)
        bar.pack(fill="x", pady=16)
        ttk.Button(bar, text="保存客户端设置", command=self._save).pack(side="left")
        ttk.Button(bar, text="打开配置…", command=self._open).pack(side="left", padx=8)
        ttk.Button(bar, text="高级 JSON 配置…", command=self._advanced).pack(side="left")
        ttk.Label(frame, text=f"配置文件：{self.config_path}", wraplength=900).pack(anchor="w", pady=8)

    def _collect(self):
        config = deepcopy(self.config)
        config["workspace_root"], config["target"], config["client_id"] = self.workspace.get(), self.target.get(), self.client_id.get()
        extensions = config.setdefault("extensions", {})
        judge = extensions.setdefault("judge", {})
        judge["enabled"] = self.judge_enabled.get()
        judge.setdefault("config", {}).update(base_url=self.judge_url.get(), model=self.judge_model.get())
        extensions.setdefault("discovery", {}).update(enabled=self.discovery_enabled.get(), cache=self.cache.get())
        argv = json.loads(self.verifier.get())
        if not isinstance(argv, list) or any(not isinstance(arg, str) or not arg for arg in argv):
            raise ValueError("验收命令必须为 JSON 字符串列表")
        if argv:
            config.setdefault("verification", {}).update(command=argv)
        else:
            config.pop("verification", None)
        if self.cloud_enabled.get():
            config.setdefault("server", {}).update(url=self.cloud_url.get(), token_env=self.token_env.get(),
                                                  allow_http=self.private_http.get())
        else:
            config.pop("server", None)
        return config

    def _save(self):
        if self.busy:
            return False
        try:
            config = self._collect()
            save_config(self.config_path, config)
            self.config = load_config(self.config_path)
            self.status.set("已保存客户端设置")
            return True
        except Exception as exc:
            self._error(exc)
            return False

    def _refresh(self):
        self.model_list.delete(0, "end")
        for card in self.config.get("models", []):
            self.model_list.insert("end", f"{card['model_id']}   ·   {card['provider']}   ·   {card['billing_mode']}")

    def _add_backend(self):
        if self.busy:
            return
        try:
            config = configure_backend(self._collect(), kind=self.kind.get(), model=self.model.get().strip(),
                provider=self.provider.get().strip(), executable=self.executable.get().strip(),
                billing_mode=self.billing.get(), tools=self._tools(self.backend_tools.get()),
                account=self.account.get().strip(), reservation_amount=float(self.amount.get()), base_url=self.ollama_url.get())
            save_config(self.config_path, config)
            self.config = config
            self._refresh()
            self.task_tools.set(self.backend_tools.get())
            self.status.set("执行器已配置；请确认后端已独立登录，并导入真实额度")
        except Exception as exc:
            self._error(exc)

    def _remove_backend(self):
        if self.busy:
            return
        selected = self.model_list.curselection()
        if not selected:
            return
        card = self.config["models"].pop(selected[0])
        self.config["adapters"].pop(card["executor"], None)
        self.config.get("model_accounts", {}).pop(card["model_id"], None)
        if self._save():
            self._refresh()

    @staticmethod
    def _tools(value):
        return [part.strip() for part in value.split(",") if part.strip()]

    def _kind_changed(self, *_):
        kind = self.kind.get()
        if kind == "ollama":
            self.billing.set("local")
            self.provider.set("ollama")
            self.backend_tools.set("")
            self.executable.set("")
        else:
            self.billing.set("subscription")
            self.executable.set("hermes" if kind == "hermes" else "dsh")
            self.provider.set("openai-codex" if kind == "hermes" else "deepseek")
            self.backend_tools.set("terminal" if kind == "hermes" else "")

    def _select_model(self, _event=None):
        selected = self.model_list.curselection()
        if selected:
            card = self.config["models"][selected[0]]
            self.task_tools.set(",".join(card.get("metadata", {}).get("required_tools", [])))

    def _task_action(self, action):
        if self.busy or not self._save():
            return
        try:
            service = DesktopService(self.config)
            task_id = self.task_id.get().strip()
            if not task_id:
                raise ValueError("任务 ID 不能为空")
            if action == "node":
                operation = service.node_once
            elif action == "status":
                operation = lambda: service.status(task_id)
            else:
                payload = make_payload(self.config, self.prompt.get("1.0", "end"), workdir=self.workdir.get(),
                                       tools=self._tools(self.task_tools.get()), task_type=self.task_type.get(), value_level=self.value.get())
                operation = (lambda: service.submit(task_id, payload)) if action == "submit" else (
                    lambda: service.run(task_id, payload, execute=action == "run"))
            self._background(operation)
        except Exception as exc:
            self._error(exc)

    def _background(self, operation):
        if self.busy:
            return
        self.busy = True
        self.status.set("处理中… 窗口保持响应；重复执行已锁定")
        def work():
            try:
                self.events.put((True, operation()))
            except Exception as exc:
                # Avoid echoing arbitrary credential-bearing backend responses.
                self.events.put((False, f"{type(exc).__name__}: 操作未完成，请核对配置、额度和现有回执。"))
        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        try:
            while True:
                ok, result = self.events.get_nowait()
                self.busy = False
                self.output.configure(state="normal")
                self.output.delete("1.0", "end")
                self.output.insert("end", json.dumps(result, ensure_ascii=False, indent=2) if ok else result)
                self.output.configure(state="disabled")
                self.book.select(self.receipt_tab)
                status = result.get("status", "已返回结果") if ok and isinstance(result, dict) else "请核对结果"
                self.status.set(f"状态：{status}")
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _import_quota(self):
        from tkinter import filedialog
        if self.busy or not self._save():
            return
        path = filedialog.askopenfilename(title="选择清理后的标准额度 JSON", filetypes=[("JSON", "*.json")])
        if path:
            self._background(lambda: DesktopService(self.config).import_quota(path))

    def _import_discovery(self):
        from tkinter import filedialog
        if self.busy or not self._save():
            return
        source = self.discovery_source.get()
        path = filedialog.askopenfilename(title="选择发现 JSON / snapshot", filetypes=[("JSON", "*.json")])
        report = None
        if path and source == "free-models-monitor":
            report = filedialog.askopenfilename(title="选择对应 report JSON", filetypes=[("JSON", "*.json")])
            if not report:
                return
        if path:
            config = deepcopy(self.config)
            self._background(lambda: DesktopService(config).import_discovery(path, source, report))

    def _open(self):
        from tkinter import filedialog
        if self.busy:
            return
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if path:
            try:
                config = load_config(path)
                self.config_path = Path(path)
                self._apply_config(config)
                self.status.set("配置已加载")
            except Exception as exc:
                self._error(exc)

    def _advanced(self):
        if self.busy:
            return
        window = self.tk.Toplevel(self.root)
        window.title("高级配置 · 验收、MCP 与节点限制")
        window.geometry("840x600")
        editor = self.tk.Text(window, wrap="none")
        editor.pack(fill="both", expand=True)
        editor.insert("1.0", json.dumps(self._collect(), ensure_ascii=False, indent=2))
        def apply():
            try:
                config = json.loads(editor.get("1.0", "end"))
                save_config(self.config_path, config)
                self._apply_config(config)
                self.status.set("高级配置已保存并应用")
                window.destroy()
            except Exception as exc:
                self._error(exc)
        self.ttk.Button(window, text="保存并应用", command=apply).pack(pady=10)

    def _apply_config(self, config):
        self.config = config
        self.workspace.set(config["workspace_root"])
        self.workdir.set(config["workspace_root"])
        self.target.set(config.get("target", "desktop"))
        self.client_id.set(config.get("client_id", "desktop"))
        server = config.get("server", {})
        self.cloud_enabled.set(bool(server))
        self.cloud_url.set(server.get("url", "http://127.0.0.1:8787"))
        self.token_env.set(server.get("token_env", "ORCHESTRATOR_TOKEN"))
        self.private_http.set(server.get("allow_http") is True)
        self.verifier.set(json.dumps(config.get("verification", {}).get("command", [])))
        extensions = config.get("extensions", {})
        discovery, judge = extensions.get("discovery", {}), extensions.get("judge", {})
        self.discovery_enabled.set(discovery.get("enabled") is True)
        self.cache.set(discovery.get("cache", str(Path(config["workspace_root"]) / ".orchestrator-discovery.json")))
        self.judge_enabled.set(judge.get("enabled") is True)
        self.judge_url.set(judge.get("config", {}).get("base_url", "http://127.0.0.1:11434"))
        self.judge_model.set(judge.get("config", {}).get("model", "qwen3:4b"))
        self._refresh()

    def _error(self, exc):
        from tkinter import messagebox
        messagebox.showerror("设置或任务无效", str(exc) if isinstance(exc, ValueError) else type(exc).__name__)

    def _close(self):
        if self.busy:
            from tkinter import messagebox
            messagebox.showinfo("任务进行中", "请等待当前操作完成。执行进程关闭后的结果可能未知，不能直接重复任务。")
            return
        self.root.destroy()


def legacy_main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="AI Orchestrator desktop client")
    parser.add_argument("--config", type=Path)
    args = parser.parse_args(argv)
    try:
        import tkinter as tk
    except ImportError:
        parser.exit(1, "Desktop requires Python Tk. Ubuntu: install python3-tk; Windows: enable Tcl/Tk in Python setup.\n")
    try:
        root = tk.Tk()
        DesktopApp(root, config_path=args.config)
        root.mainloop()
    except tk.TclError as exc:
        parser.exit(1, f"Desktop requires Python Tk and a graphical desktop session ({type(exc).__name__}).\nUbuntu: install python3-tk; Windows: enable Tcl/Tk in Python setup.\n")
    return 0


def main(argv=None):
    """Launch MISAKA AGENT on the complete native Hermes runtime."""
    import argparse
    import os
    import subprocess
    import sys
    arguments = list(sys.argv[1:] if argv is None else argv)
    if "--legacy" in arguments:
        arguments.remove("--legacy")
        return legacy_main(arguments)
    parser = argparse.ArgumentParser(description="MISAKA AGENT desktop client")
    parser.add_argument("--hermes-home", type=Path,
                        default=Path(os.environ.get("HERMES_HOME", str(Path.home() / ".misaka-agent"))))
    parser.add_argument("--upstream", type=Path, help="Use a checkout of the locked native client source")
    parser.add_argument("--skip-deps", action="store_true", help="Use already installed upstream dependencies")
    parser.add_argument("--prepare-only", action="store_true", help="Build the native client without opening a window")
    args = parser.parse_args(arguments)
    script = Path(__file__).resolve().parents[1] / "scripts" / "prepare_native_client.py"
    command = [sys.executable, str(script), "--hermes-home", str(args.hermes_home), "--build"]
    if args.upstream:
        command.extend(["--upstream", str(args.upstream)])
    if args.skip_deps:
        command.append("--skip-deps")
    if not args.prepare_only:
        command.append("--launch")
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
