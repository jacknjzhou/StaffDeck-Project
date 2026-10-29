from __future__ import annotations

import signal

from app.channels.markdown_render import (
    CodeBlock,
    Heading,
    ListItem,
    Paragraph,
    Quote,
    TableBlock,
    ThematicBreak,
    ensure_code_fences,
    extract_dingtalk_title,
    has_markdown,
    parse_markdown,
    render_feishu_post,
    split_markdown_by_lines,
)

# ---------------------------------------------------------------------------
# has_markdown
# ---------------------------------------------------------------------------


def test_has_markdown_detects_common_syntax():
    """验证标题、粗体、行内代码、链接、列表、引用、围栏与分隔线都能被识别。"""
    assert has_markdown("# 标题")
    assert has_markdown("**bold**")
    assert has_markdown("`code`")
    assert has_markdown("[link](http://x)")
    assert has_markdown("- 列表项")
    assert has_markdown("1. 有序项")
    assert has_markdown("> 引用")
    assert has_markdown("```\ncode\n```")
    assert has_markdown("---")


def test_has_markdown_false_for_plain_text():
    """验证纯文本与乘号、下划线、单破折号等伪语法不会被误判为 Markdown。"""
    assert not has_markdown("hello world")
    assert not has_markdown("a * b = c")
    assert not has_markdown("file_name_with_underscores")
    assert not has_markdown("普通中文回复")
    assert not has_markdown("")
    # 单星号装饰不构成斜体语法（已被 _ITALIC_RE 排除检测），但 * 列表项会命中
    assert not has_markdown("价格 * 3 = 9")
    # 破折号不是分隔线（少于3个）
    assert not has_markdown("a - b")


# ---------------------------------------------------------------------------
# parse_markdown
# ---------------------------------------------------------------------------


def test_parse_heading():
    """验证二级标题解析为 Heading 块并保留级别与文本。"""
    blocks = parse_markdown("## 标题二")
    assert len(blocks) == 1
    assert isinstance(blocks[0], Heading)
    assert blocks[0].level == 2
    assert blocks[0].spans[0].text == "标题二"


def test_parse_bold_and_italic():
    """验证粗体与斜体分别解析为带 bold / italic 样式的 span。"""
    blocks = parse_markdown("**粗** 和 *斜*")
    para = blocks[0]
    assert isinstance(para, Paragraph)
    texts = [(s.text, set(s.styles)) for s in para.spans]
    assert ("粗", {"bold"}) in texts
    assert ("斜", {"italic"}) in texts


def test_parse_inline_code():
    """验证行内代码解析为带 code 样式的 span。"""
    blocks = parse_markdown("用 `printf` 输出")
    spans = blocks[0].spans
    code_span = next(s for s in spans if "code" in s.styles)
    assert code_span.text == "printf"


def test_parse_multiple_inline_code_no_infinite_loop():
    """回归：多个行内代码片段曾因占位符偏移量未推进而陷入死循环，现在必须正常完成解析。"""
    def _handler(signum, frame):
        """作为 SIGALRM 处理函数，超时立即抛出 TimeoutError 以中断死循环。"""
        raise TimeoutError("parse_markdown did not complete in time")

    signal.signal(signal.SIGALRM, _handler)
    signal.alarm(5)
    try:
        blocks = parse_markdown("a `b` c `d` e")
    finally:
        signal.alarm(0)
    spans = blocks[0].spans
    code_spans = [s for s in spans if "code" in s.styles]
    assert len(code_spans) == 2
    assert code_spans[0].text == "b"
    assert code_spans[1].text == "d"


def test_parse_code_in_bold_not_reparsed():
    """验证粗体包裹的行内代码不会被二次解析成独立的 code span。"""
    blocks = parse_markdown("**`x`**")
    spans = blocks[0].spans
    # 粗体包裹，内部代码不应被二次拆为 code span，而是粗体文本
    assert any(set(s.styles) == {"bold"} and s.text == "`x`" or s.text == "x"
               for s in spans)


def test_parse_fenced_code_block_with_language():
    """验证带语言标识的围栏代码块解析出语言与代码正文。"""
    blocks = parse_markdown("```python\nprint(1)\nprint(2)\n```")
    assert len(blocks) == 1
    assert isinstance(blocks[0], CodeBlock)
    assert blocks[0].language == "python"
    assert blocks[0].text == "print(1)\nprint(2)"


def test_parse_fenced_code_block_no_language():
    """验证无语言标识的围栏代码块语言字段为空字符串。"""
    blocks = parse_markdown("```\nraw\n```")
    assert isinstance(blocks[0], CodeBlock)
    assert blocks[0].language == ""
    assert blocks[0].text == "raw"


def test_parse_unclosed_fence_does_not_raise():
    """验证围栏未闭合时仍按代码块解析且不抛异常。"""
    blocks = parse_markdown("```\nunclosed code")
    assert isinstance(blocks[0], CodeBlock)
    assert "unclosed code" in blocks[0].text


def test_parse_indented_code_block():
    """验证 4 空格缩进的代码块被识别并去除缩进。"""
    blocks = parse_markdown("    print(1)\n    print(2)")
    assert isinstance(blocks[0], CodeBlock)
    assert blocks[0].text == "print(1)\nprint(2)"


def test_parse_indented_code_block_with_blank_lines():
    """验证缩进代码块内部的空行不会中断整个代码块。"""
    text = "    def f():\n        return 1\n\n    print(f())"
    blocks = parse_markdown(text)
    assert len(blocks) == 1
    assert isinstance(blocks[0], CodeBlock)
    assert "return 1" in blocks[0].text
    assert "print(f())" in blocks[0].text


def test_parse_indented_code_block_double_blank_merges():
    """验证缩进代码块内的连续空行被原样保留且不会拆块。"""
    text = "    line1\n\n\n    line2"
    blocks = parse_markdown(text)
    assert len(blocks) == 1
    assert isinstance(blocks[0], CodeBlock)
    assert blocks[0].text == "line1\n\n\nline2"


def test_parse_indented_code_block_followed_by_paragraph():
    """验证缩进代码块之后的普通段落被解析为独立的 Paragraph。"""
    text = "    code line\n\nparagraph text"
    blocks = parse_markdown(text)
    assert isinstance(blocks[0], CodeBlock)
    assert blocks[0].text == "code line"
    assert isinstance(blocks[1], Paragraph)
    assert blocks[1].spans[0].text == "paragraph text"


def test_parse_indented_code_block_after_paragraph():
    """验证段落之后的缩进代码块仍能被正确识别。"""
    text = "intro text\n\n    code here"
    blocks = parse_markdown(text)
    assert isinstance(blocks[0], Paragraph)
    assert blocks[0].spans[0].text == "intro text"
    assert isinstance(blocks[1], CodeBlock)
    assert blocks[1].text == "code here"


def test_parse_toplevel_code_block_def():
    """验证顶格的 def 代码段落被整体识别为代码块。"""
    text = "def f():\n    return 1"
    blocks = parse_markdown(text)
    assert isinstance(blocks[0], CodeBlock)
    assert "def f():" in blocks[0].text
    assert "return 1" in blocks[0].text


def test_parse_toplevel_code_block_with_following_paragraph():
    """验证顶格代码块结束后的中文段落被拆分为独立的 Paragraph。"""
    text = "def f():\n    return 1\n\n这是说明文字。"
    blocks = parse_markdown(text)
    assert isinstance(blocks[0], CodeBlock)
    assert "def f():" in blocks[0].text
    assert isinstance(blocks[1], Paragraph)
    assert blocks[1].spans[0].text == "这是说明文字。"


def test_parse_toplevel_code_block_includes_assignment_and_print():
    """验证顶格代码块会连续包含赋值、print 与注释行，直到出现说明文字才结束。"""
    text = (
        "def f():\n"
        "    return 1\n"
        "\n"
        "result = f()\n"
        "print(result)\n"
        "# output: 1\n"
        "\n"
        "说明文字。"
    )
    blocks = parse_markdown(text)
    assert isinstance(blocks[0], CodeBlock)
    assert "def f():" in blocks[0].text
    assert "result = f()" in blocks[0].text
    assert "print(result)" in blocks[0].text
    assert "# output: 1" in blocks[0].text
    assert isinstance(blocks[1], Paragraph)
    assert blocks[1].spans[0].text == "说明文字。"


def test_parse_toplevel_code_block_hash_comment_not_heading():
    """验证代码块内的 # 注释不会被误判为标题。"""
    text = "def f():\n    return 1\n\n# [1, 2, 3]\n\n说明。"
    blocks = parse_markdown(text)
    assert isinstance(blocks[0], CodeBlock)
    assert "# [1, 2, 3]" in blocks[0].text
    assert not any(isinstance(b, Heading) for b in blocks)


def test_parse_link():
    """验证链接解析出显示文本与 href 属性。"""
    blocks = parse_markdown("[StaffDeck](https://staffdeck.ai)")
    spans = blocks[0].spans
    link = next(s for s in spans if s.href)
    assert link.text == "StaffDeck"
    assert link.href == "https://staffdeck.ai"


def test_parse_unordered_list():
    """验证无序列表每行解析为 ordered 为 False 的 ListItem。"""
    blocks = parse_markdown("- 项一\n- 项二")
    assert all(isinstance(b, ListItem) for b in blocks)
    assert blocks[0].ordered is False
    assert blocks[0].spans[0].text == "项一"
    assert blocks[1].spans[0].text == "项二"


def test_parse_ordered_list_keeps_index():
    """验证有序列表保留原始序号。"""
    blocks = parse_markdown("1. 第一\n2. 第二")
    assert blocks[0].ordered is True
    assert blocks[0].index == 1
    assert blocks[1].index == 2


def test_parse_quote():
    """验证连续多行引用合并为一个 Quote 块。"""
    blocks = parse_markdown("> 引用一\n> 引用二")
    assert len(blocks) == 1
    assert isinstance(blocks[0], Quote)
    assert "引用一" in blocks[0].spans[0].text
    assert "引用二" in blocks[0].spans[0].text


def test_parse_thematic_break():
    """验证分隔线解析为 ThematicBreak。"""
    blocks = parse_markdown("---")
    assert isinstance(blocks[0], ThematicBreak)


def test_parse_table_degrades_to_text():
    """验证表格降级为保留全部三行的 TableBlock。"""
    md = "| a | b |\n|---|---|\n| 1 | 2 |"
    blocks = parse_markdown(md)
    assert len(blocks) == 1
    assert isinstance(blocks[0], TableBlock)
    assert len(blocks[0].lines) == 3


def test_parse_html_tags_treated_as_text():
    """验证 HTML 标签按纯文本处理，避免被当作富文本执行。"""
    blocks = parse_markdown("<script>alert(1)</script>")
    para = blocks[0]
    assert isinstance(para, Paragraph)
    assert "<script>" in para.spans[0].text


def test_parse_mixed_blocks():
    """验证标题、段落、代码块与列表混排时按出现顺序解析为对应的块类型。"""
    md = "# 标题\n\n正文 **粗**\n\n```\ncode\n```\n\n- 项"
    blocks = parse_markdown(md)
    assert isinstance(blocks[0], Heading)
    assert isinstance(blocks[1], Paragraph)
    assert isinstance(blocks[2], CodeBlock)
    assert isinstance(blocks[3], ListItem)


def test_parse_empty_returns_empty():
    """验证空字符串解析结果为空列表。"""
    assert parse_markdown("") == []


def test_bold_italic_combined():
    """验证粗斜体嵌套时同时携带 bold 与 italic 样式。"""
    blocks = parse_markdown("**_粗斜_**")
    spans = blocks[0].spans
    assert any(set(s.styles) == {"bold", "italic"} for s in spans)


# ---------------------------------------------------------------------------
# render_feishu_post
# ---------------------------------------------------------------------------


def test_render_post_heading_bold_style():
    """验证飞书 post 渲染中标题降级为加粗文本且整体标题留空。"""
    blocks = parse_markdown("## 标题")
    post = render_feishu_post(blocks)
    assert post["zh_cn"]["title"] == ""
    row = post["zh_cn"]["content"][0]
    assert row[0]["tag"] == "text"
    assert "bold" in (row[0].get("style") or [])


def test_render_post_link_tag():
    """验证链接渲染为 a 标签并携带文本与 href。"""
    blocks = parse_markdown("[点这里](https://x.com)")
    post = render_feishu_post(blocks)
    tag = post["zh_cn"]["content"][0][0]
    assert tag["tag"] == "a"
    assert tag["text"] == "点这里"
    assert tag["href"] == "https://x.com"


def test_render_post_code_block_tag():
    """验证代码块渲染为 code_block 标签并保留语言标识。"""
    blocks = parse_markdown("```js\nfoo()\n```")
    post = render_feishu_post(blocks)
    tag = post["zh_cn"]["content"][0][0]
    assert tag["tag"] == "code_block"
    assert tag["language"] == "js"
    assert tag["text"] == "foo()"


def test_render_post_bold_style_flag():
    """验证粗体文本渲染为带 bold 样式的 text 标签。"""
    blocks = parse_markdown("**粗体**")
    post = render_feishu_post(blocks)
    tag = post["zh_cn"]["content"][0][0]
    assert tag["style"] == ["bold"]


def test_render_post_list_item_prefix():
    """验证无序列表项渲染时添加项目符号前缀。"""
    blocks = parse_markdown("- 项")
    post = render_feishu_post(blocks)
    tag = post["zh_cn"]["content"][0][0]
    assert tag["text"].startswith("• ")
    assert "项" in tag["text"]


def test_render_post_ordered_list_prefix():
    """验证有序列表项渲染时保留原始序号前缀。"""
    blocks = parse_markdown("3. 第三")
    post = render_feishu_post(blocks)
    tag = post["zh_cn"]["content"][0][0]
    assert tag["text"].startswith("3. ")


def test_render_post_quote_prefix():
    """验证引用内容渲染为包含原文的文本标签。"""
    blocks = parse_markdown("> 引用")
    post = render_feishu_post(blocks)
    tag = post["zh_cn"]["content"][0][0]
    assert "引用" in tag["text"]


def test_render_post_script_stays_text():
    """验证 script 标签渲染为普通文本，不生成富文本节点。"""
    blocks = parse_markdown("<script>x</script>")
    post = render_feishu_post(blocks)
    tag = post["zh_cn"]["content"][0][0]
    assert tag["tag"] == "text"
    assert "<script>" in tag["text"]


# ---------------------------------------------------------------------------
# split_markdown_by_lines
# ---------------------------------------------------------------------------


def test_split_short_text_single_chunk():
    """验证短文本不切分，整体作为单个分片返回。"""
    assert split_markdown_by_lines("短文本", 2000) == ["短文本"]


def test_split_on_line_boundary():
    """验证切分只发生在行边界，不会把单行拆成两半。"""
    text = "第一行\n第二行\n第三行"
    chunks = split_markdown_by_lines(text, 10)
    assert len(chunks) >= 2
    # 任何 chunk 不应把一行切成两半
    for chunk in chunks:
        assert chunk in text or chunk == text


def test_split_preserves_code_block_integrity():
    """验证围栏代码块被切分后围栏仍成对存在。"""
    text = "```\nline1\nline2\n```"
    chunks = split_markdown_by_lines(text, 20)
    # 围栏代码块不应被切断到把 ``` 和内容分到不同块
    joined = "\n".join(chunks)
    assert "```" in joined


def test_split_hard_cuts_overlong_line():
    """验证超长单行被硬切后每片不超限且内容可完整拼回。"""
    text = "x" * 50
    chunks = split_markdown_by_lines(text, 20)
    assert all(len(c) <= 20 for c in chunks)
    assert "".join(chunks) == text


def test_split_empty_returns_empty():
    """验证空文本的切分结果为空列表。"""
    assert split_markdown_by_lines("", 2000) == []


def _count_fences(text: str) -> int:
    """统计文本中围栏行（``` 或 ~~~）数量。"""
    return sum(1 for line in text.split("\n") if line.strip().startswith(("```", "~~~")))


def test_split_overlong_fenced_block_each_chunk_balanced():
    """回归：超长围栏代码块被切分后，每个 chunk 必须是自包含的合法 Markdown。

    每段要么完全在围栏外，要么围栏成对出现（开 + 闭），避免跨消息断裂。
    """
    code_lines = [f"line_{i} = {i}" for i in range(200)]
    text = "```python\n" + "\n".join(code_lines) + "\n```"
    chunks = split_markdown_by_lines(text, 200)
    assert len(chunks) >= 2
    for chunk in chunks:
        fence_count = _count_fences(chunk)
        # 围栏必须成对（0 个或偶数个），绝不出现单个围栏
        assert fence_count % 2 == 0, f"chunk has unbalanced fences: {chunk[:80]!r}"
        # 每段可被 parse_markdown 独立解析为合法块，且代码块内容非空或为正常文本
        blocks = parse_markdown(chunk)
        assert blocks, f"chunk produced no blocks: {chunk[:80]!r}"


def test_split_overlong_fenced_block_preserves_language():
    """切分后重新打开的围栏应保留原语言标识。"""
    code = "\n".join(f"print({i})" for i in range(300))
    text = f"```python\n{code}\n```"
    chunks = split_markdown_by_lines(text, 150)
    assert len(chunks) >= 2
    # 第一段以 ```python 开头
    assert chunks[0].split("\n")[0].strip() == "```python"
    # 后续段开头重新打开的围栏也应带 python
    for chunk in chunks[1:]:
        first_line = chunk.split("\n")[0].strip()
        assert first_line == "```python", f"missing language reopen: {first_line!r}"


def test_split_overlong_fenced_block_code_content_recovered():
    """多段切分后，去掉围栏装饰后拼回的代码内容应与原文一致。"""
    code_lines = [f"x_{i} = {i}" for i in range(150)]
    text = "```\n" + "\n".join(code_lines) + "\n```"
    chunks = split_markdown_by_lines(text, 120)
    assert len(chunks) >= 2
    recovered: list[str] = []
    for chunk in chunks:
        blocks = parse_markdown(chunk)
        for block in blocks:
            if isinstance(block, CodeBlock):
                recovered.append(block.text)
    assert "\n".join(recovered) == "\n".join(code_lines)


def test_split_fence_inside_chunk_not_split():
    """短围栏代码块整体应在单段内，不被切分。"""
    text = "前文说明\n\n```python\nprint(1)\n```\n\n后文说明"
    chunks = split_markdown_by_lines(text, 2000)
    assert chunks == [text]


def test_split_tilde_fences_balanced():
    """使用 ~~~ 围栏的代码块切分后同样应保持每段平衡。"""
    code = "\n".join(f"v{i} = {i}" for i in range(200))
    text = f"~~~python\n{code}\n~~~"
    chunks = split_markdown_by_lines(text, 180)
    assert len(chunks) >= 2
    for chunk in chunks:
        fence_count = _count_fences(chunk)
        assert fence_count % 2 == 0


def test_split_code_block_then_text_each_chunk_valid():
    """代码块 + 后续段落：切分后每段独立合法，后续段落不误入未闭合围栏。"""
    code = "\n".join(f"line{i}()" for i in range(100))
    text = f"```python\n{code}\n```\n\n这是后续说明文字，解释上面的代码。"
    chunks = split_markdown_by_lines(text, 200)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert _count_fences(chunk) % 2 == 0


def test_split_overlong_single_code_line_reopens_fence():
    """围栏内单行代码超长：硬切后仍应在围栏内重新打开/闭合。"""
    long_line = "x" * 500
    text = f"```python\n{long_line}\n```"
    chunks = split_markdown_by_lines(text, 100)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert _count_fences(chunk) % 2 == 0


def test_split_overlong_single_code_line_content_recovered():
    """围栏内单行代码超长：每个硬切片段都应被围栏包裹，内容可完整恢复。"""
    long_line = "x" * 500
    text = f"```python\n{long_line}\n```"
    chunks = split_markdown_by_lines(text, 100)
    assert len(chunks) >= 2
    recovered: list[str] = []
    for chunk in chunks:
        assert _count_fences(chunk) % 2 == 0, f"unbalanced fences: {chunk[:80]!r}"
        blocks = parse_markdown(chunk)
        for block in blocks:
            if isinstance(block, CodeBlock):
                recovered.append(block.text)
    assert "".join(recovered) == long_line, "content lost in hard-cut chunks"


def test_split_overlong_single_code_line_no_lang_content_recovered():
    """无语言标识的围栏内单行超长：同样应完整包裹围栏。"""
    long_line = "z" * 300
    text = f"```\n{long_line}\n```"
    chunks = split_markdown_by_lines(text, 80)
    assert len(chunks) >= 2
    recovered: list[str] = []
    for chunk in chunks:
        assert _count_fences(chunk) % 2 == 0
        blocks = parse_markdown(chunk)
        for block in blocks:
            if isinstance(block, CodeBlock):
                recovered.append(block.text)
    assert "".join(recovered) == long_line


def test_split_overlong_single_code_line_tilde_content_recovered():
    """~~~ 围栏内单行超长：同样应完整包裹围栏。"""
    long_line = "y" * 300
    text = f"~~~python\n{long_line}\n~~~"
    chunks = split_markdown_by_lines(text, 80)
    assert len(chunks) >= 2
    recovered: list[str] = []
    for chunk in chunks:
        assert _count_fences(chunk) % 2 == 0
        blocks = parse_markdown(chunk)
        for block in blocks:
            if isinstance(block, CodeBlock):
                recovered.append(block.text)
    assert "".join(recovered) == long_line


def test_split_every_chunk_respects_limit_overlong_single_line():
    """回归：围栏内单行超长硬切时，每个 chunk 长度必须 <= limit。

    之前的实现先截取 limit 字符再添加围栏，导致 chunk 实际长度
    达到 limit + 围栏开销（如 100 + 14 = 114），超过渠道限制。
    """
    long_line = "x" * 500
    text = f"```python\n{long_line}\n```"
    chunks = split_markdown_by_lines(text, 100)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(chunk) <= 100, f"chunk exceeds limit: len={len(chunk)} chunk={chunk[:60]!r}"


def test_split_every_chunk_respects_limit_overlong_block():
    """回归：超长围栏代码块（多行）切分时，每个 chunk 长度必须 <= limit。"""
    code_lines = [f"line_{i} = {i}" for i in range(200)]
    text = "```python\n" + "\n".join(code_lines) + "\n```"
    chunks = split_markdown_by_lines(text, 100)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(chunk) <= 100, f"chunk exceeds limit: len={len(chunk)} chunk={chunk[:60]!r}"


def test_split_every_chunk_respects_limit_tilde_fence():
    """~~~ 围栏超长单行：每个 chunk 长度必须 <= limit。"""
    long_line = "y" * 300
    text = f"~~~python\n{long_line}\n~~~"
    chunks = split_markdown_by_lines(text, 80)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(chunk) <= 80, f"chunk exceeds limit: len={len(chunk)} chunk={chunk[:60]!r}"


def test_split_every_chunk_respects_limit_no_lang():
    """无语言标识围栏超长单行：每个 chunk 长度必须 <= limit。"""
    long_line = "z" * 300
    text = f"```\n{long_line}\n```"
    chunks = split_markdown_by_lines(text, 80)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(chunk) <= 80, f"chunk exceeds limit: len={len(chunk)} chunk={chunk[:60]!r}"


def test_split_overlong_single_code_line_still_recovers_content_with_limit():
    """围栏内单行超长：在保证 len(chunk) <= limit 的同时内容仍可完整恢复。"""
    long_line = "x" * 500
    text = f"```python\n{long_line}\n```"
    chunks = split_markdown_by_lines(text, 100)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(chunk) <= 100
        assert _count_fences(chunk) % 2 == 0
    recovered: list[str] = []
    for chunk in chunks:
        blocks = parse_markdown(chunk)
        for block in blocks:
            if isinstance(block, CodeBlock):
                recovered.append(block.text)
    assert "".join(recovered) == long_line


# ---------------------------------------------------------------------------
# extract_dingtalk_title
# ---------------------------------------------------------------------------


def test_title_from_heading():
    """验证标题行被取作钉钉消息标题。"""
    assert extract_dingtalk_title("# 周报标题") == "周报标题"


def test_title_from_first_line_when_no_heading():
    """验证无标题时取首个非空行作为标题。"""
    assert extract_dingtalk_title("这是首行内容\n第二行") == "这是首行内容"


def test_title_default_for_empty():
    """验证空内容或纯空白内容返回默认标题「消息」。"""
    assert extract_dingtalk_title("") == "消息"
    assert extract_dingtalk_title("   ") == "消息"


def test_title_truncated_to_max_length():
    """验证标题按 max_length 截断到指定长度。"""
    long = "# " + "字" * 30
    assert len(extract_dingtalk_title(long, max_length=20)) == 20


def test_title_skips_blank_lines_to_first_non_empty():
    """验证跳过前置空行取第一行非空内容。"""
    assert extract_dingtalk_title("\n\n实际首行") == "实际首行"


def test_title_heading_takes_priority_over_first_line():
    """验证标题行优先于首行普通内容。"""
    assert extract_dingtalk_title("首行\n# 标题优先") == "标题优先"


def test_ensure_code_fences_toplevel_def():
    """验证顶格代码段落被包裹围栏且保留前后说明文字。"""
    text = "说明：\n\ndef f():\n    return 1\n\n后续。"
    result = ensure_code_fences(text)
    assert "```" in result
    assert result.index("```") < result.index("def f():")
    assert result.rindex("```") > result.index("return 1")


def test_ensure_code_fences_no_duplicate_for_fenced():
    """验证已带围栏的代码块不会被重复包裹。"""
    text = "```python\ndef f():\n    return 1\n```\n\n后续。"
    result = ensure_code_fences(text)
    assert result.count("```") == 2  # 只有一对围栏


def test_ensure_code_fences_plain_text_unchanged():
    """验证纯文本内容原样返回。"""
    text = "这是纯文本，没有代码。"
    assert ensure_code_fences(text) == text


def test_ensure_code_fences_preserves_paragraphs_outside():
    """验证围栏只包裹代码行，前后段落保持在围栏之外。"""
    text = (
        "# 标题\n\n"
        "说明文字。\n\n"
        "def f():\n    return 1\n\n"
        "print(f())\n\n"
        "后续说明。\n"
    )
    result = ensure_code_fences(text)
    lines = result.split("\n")
    # 围栏应在 def 之前、print(f()) 之后
    fence_positions = [i for i, line in enumerate(lines) if line.strip() == "```"]
    assert len(fence_positions) == 2
    def_pos = next(i for i, line in enumerate(lines) if line.startswith("def"))
    print_pos = next(i for i, line in enumerate(lines) if line.startswith("print"))
    assert fence_positions[0] < def_pos
    assert fence_positions[1] > print_pos
