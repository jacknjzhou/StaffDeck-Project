import importlib
from pathlib import Path

# 渠道(微信/企微)打包必需依赖:PyInstaller hiddenimports 防回归删漏
REQUIRED_MODULES = ("aibot", "websockets", "aiohttp", "pyee", "dotenv", "cryptography")


def test_packaging_dependencies_importable() -> None:
    """验证渠道打包所需依赖均可导入，防止运行环境缺包。"""
    for module in REQUIRED_MODULES:
        importlib.import_module(module)


def test_pyinstaller_spec_keeps_channel_hiddenimports() -> None:
    """验证 PyInstaller spec 保留渠道依赖的 hiddenimports，防止打包漏模块。"""
    spec_path = Path(__file__).resolve().parents[2] / "packaging" / "ultrarag.spec"
    content = spec_path.read_text(encoding="utf-8")
    for module in REQUIRED_MODULES:
        assert f'"{module}"' in content, f"packaging/ultrarag.spec 缺少 hiddenimport: {module}"


def test_macos_bundle_keeps_webkit_packaging_support() -> None:
    """验证 macOS 打包保留 WebKit 支持，spec 与构建脚本、依赖声明一致。"""
    root = Path(__file__).resolve().parents[2]
    spec = (root / "packaging" / "ultrarag.spec").read_text(encoding="utf-8")
    build_script = (root / "packaging" / "build_macos.sh").read_text(encoding="utf-8")
    pyproject = (root / "backend" / "pyproject.toml").read_text(encoding="utf-8")
    assert '"WebKit"' in spec
    assert "pyobjc-framework-WebKit" in build_script
    assert "pyobjc-framework-WebKit" in pyproject


def test_pyinstaller_bundle_contains_the_build_version_resource() -> None:
    """验证冻结产物把构建版本资源文件复制到包根目录。"""
    spec_path = Path(__file__).resolve().parents[2] / "packaging" / "ultrarag.spec"
    content = spec_path.read_text(encoding="utf-8")
    assert 'staffdeck-version.txt' in content
    assert '(str(VERSION_FILE), ".")' in content


def test_pyinstaller_bundle_contains_lark_sdk_metadata() -> None:
    """验证冻结产物收集 lark-channel-sdk 元数据，保证运行时可查询版本。"""
    spec_path = Path(__file__).resolve().parents[2] / "packaging" / "ultrarag.spec"
    content = spec_path.read_text(encoding="utf-8")
    assert 'copy_metadata("lark-channel-sdk")' in content


def test_release_builds_run_packaged_lark_sdk_smoke() -> None:
    """验证各平台发布脚本都会对冻结产物执行 Lark SDK 冒烟校验。"""
    packaging_dir = Path(__file__).resolve().parents[2] / "packaging"
    for script_name in ("build_macos.sh", "build_linux.sh", "build_windows.ps1"):
        content = (packaging_dir / script_name).read_text(encoding="utf-8")
        assert "--packaging-smoke" in content, f"{script_name} 未校验冻结产物中的 Lark SDK"


def test_windows_release_supports_external_signer_and_fails_closed() -> None:
    """验证 Windows 打包支持外部签名脚本，且签名无效时按失败处理。"""
    root = Path(__file__).resolve().parents[2]
    build = (root / "packaging" / "build_windows.ps1").read_text(encoding="utf-8")
    signer = (root / "packaging" / "sign_windows.ps1").read_text(encoding="utf-8")

    assert "WINDOWS_SIGNER_SCRIPT" in build
    assert "UNSIGNED" in build
    assert 'Get-AuthenticodeSignature -FilePath $target' in signer
    assert '$signature.Status -ne "Valid"' in signer
    for extension in ('".exe"', '".dll"', '".pyd"', '".node"'):
        assert extension in build
