from __future__ import annotations

import re
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile


SUPPORTED_EXTENSIONS = {".txt", ".md", ".markdown", ".html", ".htm", ".pdf", ".docx", ".doc"}


class KnowledgeParseError(ValueError):
    """知识库文件解析失败，消息即面向用户的中文失败原因。"""


def extract_text(filename: str, content: bytes) -> tuple[str, str]:
    """按扩展名把上传文件抽成纯文本，返回（正文, 文件类型）二元组。

    扩展名不在 SUPPORTED_EXTENSIONS 内或为旧版 .doc 二进制时抛 KnowledgeParseError。
    """
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise KnowledgeParseError(f"暂不支持 {suffix or 'unknown'} 文件格式。")
    if suffix == ".doc":
        raise KnowledgeParseError("暂不支持旧版 .doc 二进制格式，请转换为 .docx 后上传。")
    if suffix in {".txt", ".md", ".markdown"}:
        return _decode_text(content), suffix.lstrip(".")
    if suffix in {".html", ".htm"}:
        return _extract_html(content), "html"
    if suffix == ".pdf":
        return _extract_pdf(content), "pdf"
    if suffix == ".docx":
        return _extract_docx(content), "docx"
    raise KnowledgeParseError(f"暂不支持 {suffix} 文件格式。")


def _decode_text(content: bytes) -> str:
    """依次尝试 utf-8、gb18030、latin-1 解码，全部失败时用 utf-8 忽略错误兜底。"""
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="ignore")


def _extract_html(content: bytes) -> str:
    """抽取 HTML 正文，优先用 bs4 并剔除 script/style/noscript 节点。

    未安装 bs4 或解析过程出错时，回退到标准库 HTMLParser 的纯文本结果。
    """
    text = _decode_text(content)
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(text, "html.parser")
        for item in soup(["script", "style", "noscript"]):
            item.decompose()
        return soup.get_text("\n")
    except Exception:
        parser = _HTMLTextExtractor()
        parser.feed(text)
        return parser.text


def _extract_pdf(content: bytes) -> str:
    """逐页抽取 PDF 文本并加「第 N 页」二级标题，空白页跳过。

    缺少 pypdf 依赖时抛 KnowledgeParseError；所有页都无文本时返回空串。
    """
    try:
        from pypdf import PdfReader
    except Exception as exc:  # pragma: no cover - dependency availability differs by env.
        raise KnowledgeParseError("缺少 pypdf，无法解析 PDF。") from exc
    reader = PdfReader(BytesIO(content))
    pages: list[str] = []
    for index, page in enumerate(reader.pages):
        page_text = page.extract_text() or ""
        if page_text.strip():
            pages.append(f"## 第 {index + 1} 页\n\n{page_text}")
    if not pages:
        return ""
    return "# PDF 文档\n\n" + "\n\n".join(pages)


def _extract_docx(content: bytes) -> str:
    """用 python-docx 抽取段落与表格文本，Heading 样式还原成 Markdown 标题。

    依赖缺失或解析异常时，回退到直接解析 word/document.xml 的兜底实现。
    """
    try:
        from docx import Document

        document = Document(BytesIO(content))
        rows: list[str] = []
        for paragraph in document.paragraphs:
            text = paragraph.text.strip()
            if not text:
                continue
            heading_level = _docx_heading_level(paragraph.style.name if paragraph.style else "")
            rows.append(f"{'#' * heading_level} {text}" if heading_level else text)
        for table in document.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if cells:
                    rows.append(" | ".join(cells))
        return "\n".join(rows)
    except Exception:
        return _extract_docx_with_zip(content)


def _docx_heading_level(style_name: str) -> int | None:
    """把 Heading 1-6 或「标题 1-6」样式名转成标题层级，非标题样式返回 None。"""
    match = re.match(r"^(?:Heading|标题)\s*([1-6])$", style_name.strip(), re.IGNORECASE)
    return int(match.group(1)) if match else None


def _extract_docx_with_zip(content: bytes) -> str:
    """绕开 python-docx，直接从 docx 压缩包的 document.xml 中抠文本。

    压缩包损坏或缺 document.xml 时抛 KnowledgeParseError。
    """
    try:
        with ZipFile(BytesIO(content)) as archive:
            xml = archive.read("word/document.xml").decode("utf-8", errors="ignore")
    except Exception as exc:
        raise KnowledgeParseError("无法解析 docx 文档。") from exc
    parser = _DocxTextExtractor()
    parser.feed(xml)
    return parser.text


class _HTMLTextExtractor(HTMLParser):
    """bs4 不可用时的兜底 HTML 解析器，只收集非空文本节点。"""

    def __init__(self) -> None:
        """初始化空的文本片段缓冲。"""
        super().__init__()
        self._parts: list[str] = []

    @property
    def text(self) -> str:
        """把已收集片段去掉首尾空白后按换行拼接。"""
        return "\n".join(part.strip() for part in self._parts if part.strip())

    def handle_data(self, data: str) -> None:
        """收下一个非空白文本节点。"""
        if data.strip():
            self._parts.append(data)


class _DocxTextExtractor(HTMLParser):
    """docx 兜底解析器：把 document.xml 当标记解析，只保留文本节点。"""

    def __init__(self) -> None:
        """初始化空的文本片段缓冲。"""
        super().__init__()
        self._parts: list[str] = []

    @property
    def text(self) -> str:
        """把已收集片段去掉首尾空白后按换行拼接。"""
        return "\n".join(part.strip() for part in self._parts if part.strip())

    def handle_data(self, data: str) -> None:
        """收下一个非空白文本节点。"""
        if data.strip():
            self._parts.append(data)
