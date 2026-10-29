import pytest

import desktop_launcher


def test_frozen_safe_main_calls_freeze_support_before_main(monkeypatch) -> None:
    """验证打包运行入口先调用 freeze_support 再进入 main，并透传返回值。"""
    import multiprocessing

    calls: list[str] = []
    monkeypatch.setattr(multiprocessing, "freeze_support", lambda: calls.append("freeze"))
    monkeypatch.setattr(desktop_launcher, "main", lambda: calls.append("main") or 7)

    assert desktop_launcher.run_frozen_safe_main() == 7
    assert calls == ["freeze", "main"]


def test_packaging_smoke_checks_lark_sdk_metadata_and_modules(monkeypatch, capsys) -> None:
    """验证打包自检输出 SDK 版本并按清单导入全部模块。"""
    imported: list[str] = []
    modules = {
        module_name: type("FakeModule", (), {symbol_name: object()})
        for module_name, symbol_name in desktop_launcher.LARK_PACKAGING_SMOKE_IMPORTS
    }
    monkeypatch.setattr(desktop_launcher.importlib_metadata, "version", lambda name: "1.2.0")
    monkeypatch.setattr(
        desktop_launcher.importlib,
        "import_module",
        lambda name: imported.append(name) or modules[name],
    )

    assert desktop_launcher.main(["--packaging-smoke"]) == 0
    assert imported == [
        module_name for module_name, _symbol_name in desktop_launcher.LARK_PACKAGING_SMOKE_IMPORTS
    ]
    assert "lark-channel-sdk==1.2.0" in capsys.readouterr().out


def test_packaging_smoke_rejects_wrong_lark_sdk_version(monkeypatch) -> None:
    """验证 lark-channel-sdk 版本不是 1.2.0 时自检直接报错。"""
    monkeypatch.setattr(desktop_launcher.importlib_metadata, "version", lambda name: "1.2.1")

    with pytest.raises(RuntimeError, match="must be exactly 1.2.0, got 1.2.1"):
        desktop_launcher.main(["--packaging-smoke"])


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("http://127.0.0.1:5173/workspace", False),
        ("http://127.0.0.1:5174/workspace", True),
        ("https://github.com/OpenBMB/StaffDeck/releases", True),
        ("http://127.0.0.1:invalid/workspace", False),
        ("staffdeck://open", False),
        ("not-a-url", False),
    ],
)
def test_external_web_url_detection(target: str, expected: bool) -> None:
    """验证仅外部 http(s) 地址被判定为外链，本地地址与非法地址都不算。"""
    assert desktop_launcher._is_external_web_url(target, "http://127.0.0.1:5173") is expected


def _clear_port_env(monkeypatch) -> None:
    """清除端口相关环境变量，避免宿主环境干扰端口选择的断言。"""
    monkeypatch.delenv("ULTRARAG_PORT", raising=False)
    monkeypatch.delenv("ULTRARAG_PORT_RANGE_START", raising=False)
    monkeypatch.delenv("ULTRARAG_PORT_RANGE_END", raising=False)


def test_build_server_config_defaults(monkeypatch) -> None:
    """验证无环境变量时使用环回地址、5173 端口与单端口应用。"""
    monkeypatch.delenv("ULTRARAG_HOST", raising=False)
    _clear_port_env(monkeypatch)
    monkeypatch.setattr(desktop_launcher, "port_in_use", lambda _host, _port: False)
    cfg = desktop_launcher.build_server_config()
    assert cfg["host"] == "127.0.0.1"
    assert cfg["port"] == 5173
    assert cfg["app"] == "single_port_app:app"


def test_setup_network_saves_local_mode(monkeypatch, tmp_path) -> None:
    """验证本地模式落盘的 network.json 内容与字段顺序。"""
    monkeypatch.setattr(desktop_launcher, "user_data_dir", lambda: tmp_path)

    assert desktop_launcher._setup_network(["--mode", "local", "--port", "5180"]) == 0
    assert (tmp_path / "network.json").read_text(encoding="utf-8") == (
        '{\n  "mode": "local",\n  "host": "127.0.0.1",\n  "port": 5180,\n  "public_url": ""\n}\n'
    )


def test_apply_network_config_uses_persisted_lan_mode(monkeypatch, tmp_path) -> None:
    """验证已持久化的局域网模式会写入监听地址与端口环境变量。"""
    monkeypatch.setattr(desktop_launcher, "user_data_dir", lambda: tmp_path)
    desktop_launcher._save_network_config("lan", "", 5190)
    monkeypatch.delenv("ULTRARAG_HOST", raising=False)
    monkeypatch.delenv("ULTRARAG_PORT", raising=False)

    desktop_launcher._apply_network_config([])

    assert desktop_launcher.os.environ["ULTRARAG_HOST"] == "0.0.0.0"
    assert desktop_launcher.os.environ["ULTRARAG_PORT"] == "5190"


def test_apply_network_config_preserves_environment_overrides(monkeypatch, tmp_path) -> None:
    """验证显式环境变量优先于持久化的网络配置。"""
    monkeypatch.setattr(desktop_launcher, "user_data_dir", lambda: tmp_path)
    desktop_launcher._save_network_config("lan", "", 5190)
    monkeypatch.setenv("ULTRARAG_HOST", "0.0.0.0")
    monkeypatch.setenv("ULTRARAG_PORT", "6200")

    desktop_launcher._apply_network_config([])

    assert desktop_launcher.os.environ["ULTRARAG_HOST"] == "0.0.0.0"
    assert desktop_launcher.os.environ["ULTRARAG_PORT"] == "6200"


def test_public_mode_uses_inferred_public_url(monkeypatch, tmp_path) -> None:
    """验证公网模式采用推断出的公网地址并落盘。"""
    monkeypatch.setattr(desktop_launcher, "user_data_dir", lambda: tmp_path)
    monkeypatch.setattr(desktop_launcher, "_infer_public_url", lambda port: f"http://203.0.113.9:{port}")

    assert desktop_launcher._setup_network(["--mode", "public", "--port", "5173"]) == 0
    assert (tmp_path / "network.json").read_text(encoding="utf-8") == (
        '{\n  "mode": "public",\n  "host": "0.0.0.0",\n  "port": 5173,\n  "public_url": "http://203.0.113.9:5173"\n}\n'
    )


def test_public_mode_requires_public_url_when_inference_fails(monkeypatch, tmp_path) -> None:
    """验证推断失败且非交互时，公网模式直接以提示信息退出。"""
    monkeypatch.setattr(desktop_launcher, "user_data_dir", lambda: tmp_path)
    monkeypatch.setattr(desktop_launcher, "_infer_public_url", lambda _port: "")
    monkeypatch.setattr(desktop_launcher.sys.stdin, "isatty", lambda: False)

    with pytest.raises(SystemExit, match="公网模式必须提供 --public-url"):
        desktop_launcher._setup_network(["--mode", "public", "--port", "5173"])


def test_public_url_does_not_redirect_backend_tool_calls(monkeypatch) -> None:
    """验证公网地址只进入 CORS 配置，不替换后端工具调用使用的回环地址。"""
    monkeypatch.delenv("TOOL_BASE_URL", raising=False)
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    desktop_launcher.apply_runtime_env(
        {"host": "0.0.0.0", "port": 5173, "public_url": "https://staff.example.com"}
    )

    assert desktop_launcher.os.environ["TOOL_BASE_URL"] == "http://127.0.0.1:5173"
    assert "https://staff.example.com" in desktop_launcher.os.environ["CORS_ORIGINS"]


def test_build_server_config_env_override(monkeypatch) -> None:
    """验证 ULTRARAG_PORT 环境变量覆盖默认端口。"""
    _clear_port_env(monkeypatch)
    monkeypatch.setenv("ULTRARAG_PORT", "6000")
    monkeypatch.setattr(desktop_launcher, "port_in_use", lambda _host, _port: False)
    cfg = desktop_launcher.build_server_config()
    assert cfg["port"] == 6000


def test_build_server_config_uses_next_port_in_range(monkeypatch) -> None:
    """验证默认端口被占用时顺延到区间内下一个可用端口。"""
    _clear_port_env(monkeypatch)
    monkeypatch.setattr(desktop_launcher, "port_in_use", lambda _host, port: port == 5173)
    cfg = desktop_launcher.build_server_config()
    assert cfg["port"] == 5174


def test_build_server_config_honors_custom_port_range(monkeypatch) -> None:
    """验证自定义端口区间生效并选中首个空闲端口。"""
    _clear_port_env(monkeypatch)
    monkeypatch.setenv("ULTRARAG_PORT_RANGE_START", "6200")
    monkeypatch.setenv("ULTRARAG_PORT_RANGE_END", "6202")
    monkeypatch.setattr(desktop_launcher, "port_in_use", lambda _host, port: port in {6200, 6201})
    cfg = desktop_launcher.build_server_config()
    assert cfg["port"] == 6202


def test_explicit_port_is_tried_before_range(monkeypatch) -> None:
    """验证显式端口先于区间探测，被占用后再回退到区间。"""
    _clear_port_env(monkeypatch)
    monkeypatch.setenv("ULTRARAG_PORT", "7000")
    monkeypatch.setenv("ULTRARAG_PORT_RANGE_START", "5173")
    monkeypatch.setenv("ULTRARAG_PORT_RANGE_END", "5174")
    checked_ports = []

    def fake_port_in_use(_host, port):
        """记录被探测的端口并按脚本返回占用状态，便于断言探测顺序。"""
        checked_ports.append(port)
        return port == 7000

    monkeypatch.setattr(desktop_launcher, "port_in_use", fake_port_in_use)
    cfg = desktop_launcher.build_server_config()
    assert checked_ports == [7000, 5173]
    assert cfg["port"] == 5173


def test_port_in_use_false_for_unused_port() -> None:
    """验证未被监听的端口被判定为空闲。"""
    assert desktop_launcher.port_in_use("127.0.0.1", 59999) is False


def test_health_requires_staffdeck_marker(monkeypatch) -> None:
    """验证健康检查要求响应中包含 StaffDeck 标识。"""
    class FakeResponse:
        """模拟 urllib 响应上下文，返回预置的 JSON 负载。"""
        def __init__(self, payload: bytes):
            """保存预置响应体，供 read 返回。"""
            self.payload = payload

        def __enter__(self):
            """返回自身以支持 with 语句。"""
            return self

        def __exit__(self, *_args):
            """不吞异常，正常退出上下文。"""
            return False

        def read(self) -> bytes:
            """返回预置的响应体字节。"""
            return self.payload

    def fake_urlopen(url, timeout):
        """校验健康检查的地址与超时，并返回带 StaffDeck 标识的响应。"""
        assert url == "http://127.0.0.1:5173/api/health"
        assert timeout == 1
        return FakeResponse(b'{"status":"ok","app":"StaffDeck"}')

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert desktop_launcher._health_ok("http://127.0.0.1:5173") is True


def test_health_rejects_other_local_service(monkeypatch) -> None:
    """验证缺少 StaffDeck 标识的本地服务不被认作健康。"""
    class FakeResponse:
        """模拟只返回 status 字段的响应，用于冒充其他本地服务。"""
        def __enter__(self):
            """返回自身以支持 with 语句。"""
            return self

        def __exit__(self, *_args):
            """不吞异常，正常退出上下文。"""
            return False

        def read(self) -> bytes:
            """返回不含 StaffDeck 标识的响应体。"""
            return b'{"status":"ok"}'

    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: FakeResponse())
    assert desktop_launcher._health_ok("http://127.0.0.1:5175") is False


def test_preload_server_app_imports_reference_on_calling_thread(monkeypatch) -> None:
    """验证预加载在调用线程完成导入，并把 app 对象写回服务配置。"""
    app = object()

    class FakeModule:
        """模拟单端口应用模块，仅提供 app 属性。"""

    module = FakeModule()
    module.app = app
    monkeypatch.setattr(desktop_launcher.importlib, "import_module", lambda name: module)
    cfg = {"app": "single_port_app:app"}

    desktop_launcher.preload_server_app(cfg)

    assert cfg["app"] is app


def test_windows_taskbar_app_only_used_for_frozen_windows(monkeypatch) -> None:
    """验证仅打包后的 Windows 进程才启用任务栏应用。"""
    monkeypatch.delenv("STAFFDECK_HEADLESS", raising=False)
    monkeypatch.setattr(desktop_launcher.sys, "platform", "win32")
    monkeypatch.delattr(desktop_launcher.sys, "frozen", raising=False)
    assert desktop_launcher._use_windows_taskbar_app() is False

    monkeypatch.setattr(desktop_launcher.sys, "frozen", True, raising=False)
    assert desktop_launcher._use_windows_taskbar_app() is True


def test_windows_taskbar_app_disabled_in_headless_mode(monkeypatch) -> None:
    """验证无头模式下打包的 Windows 进程也不启用任务栏应用。"""
    monkeypatch.setattr(desktop_launcher.sys, "platform", "win32")
    monkeypatch.setattr(desktop_launcher.sys, "frozen", True, raising=False)
    monkeypatch.setenv("STAFFDECK_HEADLESS", "1")
    assert desktop_launcher._use_windows_taskbar_app() is False


def test_macos_dock_app_disabled_in_headless_mode(monkeypatch) -> None:
    """验证无头模式下打包的 macOS 进程不启用 Dock 应用。"""
    monkeypatch.setattr(desktop_launcher.sys, "platform", "darwin")
    monkeypatch.setattr(desktop_launcher.sys, "frozen", True, raising=False)
    monkeypatch.setenv("STAFFDECK_HEADLESS", "1")
    assert desktop_launcher._use_macos_dock_app() is False


def test_windows_restore_command_detection() -> None:
    """验证只有系统菜单的还原命令被识别。"""
    assert desktop_launcher._is_windows_restore_command(0x0112, 0xF120) is True
    assert desktop_launcher._is_windows_restore_command(0x0112, 0xF122) is True
    assert desktop_launcher._is_windows_restore_command(0x0112, 0xF020) is False
    assert desktop_launcher._is_windows_restore_command(0x0002, 0xF120) is False


@pytest.mark.parametrize(
    ("width", "height", "expected"),
    [
        (1280, 800, (360, 768, 660, 32)),
        (900, 600, (360, 568, 280, 32)),
        (60, 8, (60, 0, 0, 8)),
    ],
)
def test_macos_drag_region_stays_on_safe_top_edge(
    width: float,
    height: float,
    expected: tuple[float, float, float, float],
) -> None:
    """验证拖拽热区贴合标题栏并避开红绿灯按钮与内容区。"""
    assert desktop_launcher._macos_drag_region_frame(width, height) == expected


@pytest.mark.parametrize(
    ("x", "y", "expected"),
    [
        (360, 768, True),
        (1019, 799, True),
        (359, 795, False),
        (500, 767, False),
        (1020, 795, False),
    ],
)
def test_macos_drag_region_excludes_traffic_lights_and_web_controls(
    x: float,
    y: float,
    expected: bool,
) -> None:
    """验证拖拽热区命中判定排除红绿灯按钮与会落到网页控件的区域。"""
    assert desktop_launcher._point_is_in_macos_drag_region(x, y, 1280, 800) is expected


def test_macos_window_embeds_local_ui() -> None:
    """验证 macOS 窗口以内嵌 WebView 加载本地界面，并转发拖拽与缩放事件。"""
    events: dict[str, object] = {}

    class FakeContentView:
        """模拟 NSView，只返回窗口内容区域的尺寸。"""
        def bounds(self):
            """返回固定的内容区边界，供拖拽热区计算使用。"""
            return (0, 0, 1280, 800)

    class FakeWindow:
        """模拟 NSWindow，记录创建与样式相关调用以便断言。"""
        @classmethod
        def alloc(cls):
            """模拟 ObjC 的 alloc，返回未初始化的实例。"""
            return cls()

        def initWithContentRect_styleMask_backing_defer_(self, frame, style, backing, defer):
            """记录窗口初始化参数并返回自身。"""
            events["window_init"] = (frame, style, backing, defer)
            events["window"] = self
            return self

        def setTitle_(self, title):
            """记录窗口标题。"""
            events["title"] = title

        def setTitleVisibility_(self, visibility):
            """记录标题可见性设置。"""
            events["title_visibility"] = visibility

        def setTitlebarAppearsTransparent_(self, transparent):
            """记录标题栏透明设置。"""
            events["titlebar_transparent"] = transparent

        def setTitlebarSeparatorStyle_(self, style):
            """记录标题栏分隔线样式。"""
            events["titlebar_separator_style"] = style

        def setMinSize_(self, size):
            """记录窗口最小尺寸。"""
            events["min_size"] = size

        def setReleasedWhenClosed_(self, released):
            """记录关闭时是否释放窗口。"""
            events["released_when_closed"] = released

        def center(self):
            """记录窗口已完成居中。"""
            events["centered"] = True

        def contentView(self):
            """返回当前内容视图，缺省时新建一个假视图。"""
            return events.get("content_view", FakeContentView())

        def setContentView_(self, view):
            """记录设置的内容视图。"""
            events["content_view"] = view

        def makeKeyAndOrderFront_(self, sender):
            """记录窗口已置前显示。"""
            events["ordered_front"] = sender

        def performZoom_(self, sender):
            """记录触发的缩放动作。"""
            events["zoom_sender"] = sender

        def performWindowDragWithEvent_(self, event):
            """记录触发的窗口拖拽事件。"""
            events["drag_event"] = event

        def sendEvent_(self, event):
            """记录被转发的鼠标事件。"""
            events["forwarded_event"] = event

    class FakeWebView:
        """模拟 WKWebView，记录加载请求与布局参数。"""
        @classmethod
        def alloc(cls):
            """模拟 ObjC 的 alloc，返回未初始化的实例。"""
            return cls()

        def initWithFrame_(self, frame):
            """记录 WebView 初始尺寸并返回自身。"""
            events["webview_frame"] = frame
            return self

        def setAutoresizingMask_(self, mask):
            """记录自适应掩码。"""
            events["autoresizing_mask"] = mask

        def bounds(self):
            """返回带宽高的边界对象，供拖拽热区计算使用。"""
            return type(
                "Bounds",
                (),
                {"size": type("Size", (), {"width": 1280, "height": 800})()},
            )()

        def loadRequest_(self, request):
            """记录加载的请求地址。"""
            events["request"] = request

    class FakeURL:
        """模拟 NSURL，把字符串包装成假 URL。"""
        @staticmethod
        def URLWithString_(target):
            """把目标地址转换成带 url: 前缀的假 URL。"""
            return f"url:{target}"

    class FakeRequest:
        """模拟 NSURLRequest，把假 URL 包装成假请求。"""
        @staticmethod
        def requestWithURL_(url):
            """把假 URL 转换成带 request: 前缀的假请求。"""
            return f"request:{url}"

    class FakeAppKit:
        """模拟 AppKit 模块，提供窗口类与所需常量。"""
        NSWindow = FakeWindow
        NSEventTypeLeftMouseDown = 1
        NSWindowStyleMaskTitled = 1
        NSWindowStyleMaskClosable = 2
        NSWindowStyleMaskMiniaturizable = 4
        NSWindowStyleMaskResizable = 8
        NSWindowStyleMaskFullSizeContentView = 16
        NSWindowTitleHidden = 1
        NSWindowTitlebarSeparatorStyleNone = 0
        NSBackingStoreBuffered = 2
        NSViewWidthSizable = 2
        NSViewHeightSizable = 16

        @staticmethod
        def NSMakeRect(x, y, width, height):
            """按参数原样返回矩形元组。"""
            return (x, y, width, height)

        @staticmethod
        def NSMakeSize(width, height):
            """按参数原样返回尺寸元组。"""
            return (width, height)

    class FakeFoundation:
        """模拟 Foundation 模块，提供 NSURL 与 NSURLRequest。"""
        NSURL = FakeURL
        NSURLRequest = FakeRequest

    class FakeWebKit:
        """模拟 WebKit 模块，提供 WKWebView 类。"""
        WKWebView = FakeWebView

    original_window_class = desktop_launcher._MACOS_WINDOW_CLASS
    desktop_launcher._MACOS_WINDOW_CLASS = None
    try:
        window, webview = desktop_launcher._create_macos_webview_window(
            FakeAppKit,
            FakeFoundation,
            FakeWebKit,
            "http://127.0.0.1:5173/chat/",
        )
    finally:
        desktop_launcher._MACOS_WINDOW_CLASS = original_window_class

    assert isinstance(window, FakeWindow)
    assert isinstance(webview, FakeWebView)
    assert webview is events["content_view"]
    assert events["request"] == "request:url:http://127.0.0.1:5173/chat/"
    assert events["title"] == "StaffDeck"
    assert events["window_init"][1] & FakeAppKit.NSWindowStyleMaskFullSizeContentView
    assert events["title_visibility"] == FakeAppKit.NSWindowTitleHidden
    assert events["titlebar_transparent"] is True
    assert events["titlebar_separator_style"] == FakeAppKit.NSWindowTitlebarSeparatorStyleNone
    assert events["min_size"] == (900, 600)
    assert events["released_when_closed"] is False
    assert events["centered"] is True

    point = type("Point", (), {"x": 500, "y": 795})()
    drag_event = type(
        "Event",
        (),
        {
            "type": lambda self: FakeAppKit.NSEventTypeLeftMouseDown,
            "locationInWindow": lambda self: point,
            "clickCount": lambda self: 1,
        },
    )()
    window.sendEvent_(drag_event)
    assert events["drag_event"] is drag_event

    zoom_event = type(
        "Event",
        (),
        {
            "type": lambda self: FakeAppKit.NSEventTypeLeftMouseDown,
            "locationInWindow": lambda self: point,
            "clickCount": lambda self: 2,
        },
    )()
    window.sendEvent_(zoom_event)
    assert events["zoom_sender"] is window

    web_control_point = type("Point", (), {"x": 100, "y": 795})()
    web_event = type(
        "Event",
        (),
        {
            "type": lambda self: FakeAppKit.NSEventTypeLeftMouseDown,
            "locationInWindow": lambda self: web_control_point,
            "clickCount": lambda self: 1,
        },
    )()
    window.sendEvent_(web_event)
    assert events["forwarded_event"] is web_event


def test_macos_main_menu_routes_edit_shortcuts_through_responder_chain() -> None:
    """验证 macOS 主菜单中应用与编辑菜单的动作、快捷键与响应链目标。"""
    command = 1 << 20
    option = 1 << 19

    class FakeMenuItem:
        """模拟 NSMenuItem，记录标题、动作、快捷键与目标。"""
        def __init__(self, title="", action=None, key="", separator=False):
            """初始化菜单项的默认字段。"""
            self.title = title
            self.action = action
            self.key = key
            self.separator = separator
            self.target = None
            self.modifiers = command
            self.submenu = None

        @classmethod
        def alloc(cls):
            """模拟 ObjC 的 alloc，返回未初始化的实例。"""
            return cls()

        @classmethod
        def separatorItem(cls):
            """返回标记为分隔符的菜单项。"""
            return cls(separator=True)

        def initWithTitle_action_keyEquivalent_(self, title, action, key):
            """写入标题、动作与快捷键并返回自身。"""
            self.title = title
            self.action = action
            self.key = key
            return self

        def setTarget_(self, target):
            """记录菜单项目标。"""
            self.target = target

        def setSubmenu_(self, submenu):
            """记录子菜单。"""
            self.submenu = submenu

        def setKeyEquivalentModifierMask_(self, modifiers):
            """记录快捷键的修饰键掩码。"""
            self.modifiers = modifiers

    class FakeMenu:
        """模拟 NSMenu，保存标题与菜单项列表。"""
        def __init__(self):
            """初始化一个空菜单。"""
            self.title = ""
            self.items = []

        @classmethod
        def alloc(cls):
            """模拟 ObjC 的 alloc，返回未初始化的实例。"""
            return cls()

        def initWithTitle_(self, title):
            """写入菜单标题并返回自身。"""
            self.title = title
            return self

        def addItem_(self, item):
            """向菜单追加一个菜单项。"""
            self.items.append(item)

    class FakeAppKit:
        """模拟 AppKit 模块，提供菜单类与修饰键常量。"""
        NSMenu = FakeMenu
        NSMenuItem = FakeMenuItem
        NSEventModifierFlagCommand = command
        NSEventModifierFlagOption = option

    delegate = object()
    main_menu = desktop_launcher._create_macos_main_menu(FakeAppKit, delegate)

    assert [item.title for item in main_menu.items] == ["StaffDeck", "编辑"]

    app_items = [item for item in main_menu.items[0].submenu.items if not item.separator]
    assert [(item.title, item.action, item.key) for item in app_items] == [
        ("关于 StaffDeck", "showAbout:", ""),
        ("隐藏 StaffDeck", "hide:", "h"),
        ("隐藏其他", "hideOtherApplications:", "h"),
        ("全部显示", "unhideAllApplications:", ""),
        ("退出 StaffDeck", "quitStaffDeck:", "q"),
    ]
    assert app_items[0].target is delegate
    assert app_items[2].modifiers == command | option
    assert app_items[-1].target is delegate

    edit_items = [item for item in main_menu.items[1].submenu.items if not item.separator]
    assert [(item.action, item.key) for item in edit_items] == [
        ("undo:", "z"),
        ("redo:", "Z"),
        ("cut:", "x"),
        ("copy:", "c"),
        ("paste:", "v"),
        ("selectAll:", "a"),
    ]
    assert all(item.target is None for item in edit_items)
    assert all(item.modifiers == command for item in edit_items)


def test_frozen_server_disables_api_access_logging(monkeypatch) -> None:
    """验证打包运行时关闭 uvicorn 的访问日志并禁用日志配置。"""
    import uvicorn

    calls = []
    monkeypatch.setattr(desktop_launcher.sys, "frozen", True, raising=False)
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs)))

    desktop_launcher._serve({"app": "single_port_app:app", "host": "127.0.0.1", "port": 5173})

    assert calls[0][1]["access_log"] is False
    assert calls[0][1]["log_config"] is None
