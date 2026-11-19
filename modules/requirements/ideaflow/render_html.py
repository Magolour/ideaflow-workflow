"""把 Proposal 的 Markdown 渲染成 HTML：预览与下载共用同一份结果。

只覆盖 Proposal 实际用到的语法子集：标题、段落、表格、列表、引用、
分隔线、行内加粗与行内代码。不追求 CommonMark 完整兼容，也不引入
第三方依赖——渲染器只有一百多行，行为可预测，便于测试。
"""

from __future__ import annotations

import html
import re

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_UL = re.compile(r"^[-*+]\s+(.*)$")
_OL = re.compile(r"^\d+[.)]\s+(.*)$")
_HR = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})$")
_SEP_CELL = re.compile(r"^:?-{3,}:?$")
_CJK = re.compile(r"[\u3000-\u9fff\uff00-\uffef]")

_SAFE_HREF = re.compile(r"^(?:https?://|mailto:|#|/|\./|\.\./)")


def escape(text: str) -> str:
    return html.escape(text, quote=False)


def _is_table_separator(line: str) -> bool:
    stripped = line.strip()
    if "|" not in stripped or "-" not in stripped:
        return False
    cells = [cell.strip() for cell in stripped.strip("|").split("|")]
    return bool(cells) and all(_SEP_CELL.match(cell) for cell in cells)


def _split_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def inline(text: str) -> str:
    """行内格式：先整体转义，再逐类替换，代码内容不参与后续替换。"""
    value = escape(text)
    stash: list[str] = []

    def keep(fragment: str) -> str:
        stash.append(fragment)
        return f"\x00{len(stash) - 1}\x00"

    value = re.sub(r"`([^`]+)`", lambda m: keep(f"<code>{m.group(1)}</code>"), value)
    value = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", value)
    value = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)",
                   lambda m: keep(_anchor(m.group(1), m.group(2))), value)

    for index, fragment in enumerate(stash):
        value = value.replace(f"\x00{index}\x00", fragment)
    return value


def _anchor(label: str, href: str) -> str:
    if not _SAFE_HREF.match(href):
        return f"<span>{label}</span>"
    return f'<a href="{html.escape(href, quote=True)}" rel="noreferrer noopener">{label}</a>'


def _join_lines(lines: list[str]) -> str:
    """段落内换行：中文直接拼接，西文补一个空格。"""
    if not lines:
        return ""
    out = lines[0]
    for line in lines[1:]:
        sep = "" if (_CJK.search(out[-1:]) or _CJK.search(line[:1])) else " "
        out += sep + line
    return out


def render_markdown(markdown: str) -> str:
    """返回不带 `<html>` 外壳的正文片段。"""
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[str] = []
    paragraph: list[str] = []
    heading_index = 0
    index = 0

    def flush() -> None:
        if paragraph:
            blocks.append(f"<p>{inline(_join_lines(paragraph))}</p>")
            paragraph.clear()

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if not stripped:
            flush()
            index += 1
            continue

        if stripped.startswith("```"):
            flush()
            code: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code.append(lines[index])
                index += 1
            index += 1
            body = escape("\n".join(code))
            blocks.append(f'<pre class="code"><code>{body}</code></pre>')
            continue

        heading = _HEADING.match(stripped)
        if heading:
            flush()
            level = len(heading.group(1))
            heading_index += 1
            text = inline(heading.group(2).strip())
            blocks.append(f'<h{level} id="h{heading_index}">{text}</h{level}>')
            index += 1
            continue

        if _HR.match(stripped):
            flush()
            blocks.append("<hr />")
            index += 1
            continue

        if stripped.startswith(">"):
            flush()
            quote: list[str] = []
            while index < len(lines) and lines[index].strip().startswith(">"):
                quote.append(lines[index].strip()[1:].strip())
                index += 1
            inner = [f"<p>{inline(part)}</p>" for part in _split_quote(quote) if part]
            blocks.append("<blockquote>" + "".join(inner) + "</blockquote>")
            continue

        if ("|" in stripped and index + 1 < len(lines)
                and _is_table_separator(lines[index + 1])):
            flush()
            headers = _split_row(stripped)
            index += 2
            rows: list[list[str]] = []
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                rows.append(_split_row(lines[index]))
                index += 1
            blocks.append(_render_table(headers, rows))
            continue

        if _UL.match(stripped) or _OL.match(stripped):
            flush()
            ordered = bool(_OL.match(stripped))
            pattern = _OL if ordered else _UL
            items: list[str] = []
            while index < len(lines):
                match = pattern.match(lines[index].strip())
                if not match:
                    break
                items.append(f"<li>{inline(match.group(1).strip())}</li>")
                index += 1
            tag = "ol" if ordered else "ul"
            blocks.append(f"<{tag}>" + "".join(items) + f"</{tag}>")
            continue

        paragraph.append(stripped)
        index += 1

    flush()
    return "\n".join(blocks)


def _split_quote(lines: list[str]) -> list[str]:
    """引用块内的空行视为分段，其余行并入同一段。"""
    parts: list[str] = []
    current: list[str] = []
    for line in lines:
        if line:
            current.append(line)
        elif current:
            parts.append(_join_lines(current))
            current = []
    if current:
        parts.append(_join_lines(current))
    return parts


def _render_table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{inline(cell)}</th>" for cell in headers)
    body = []
    for row in rows:
        padded = row + [""] * (len(headers) - len(row))
        body.append("<tr>" + "".join(f"<td>{inline(cell)}</td>"
                                     for cell in padded[:len(headers)]) + "</tr>")
    return ('<div class="table-wrap"><table><thead><tr>' + head
            + "</tr></thead><tbody>" + "".join(body) + "</tbody></table></div>")


def document_title(markdown: str, fallback: str = "设计文档") -> str:
    for line in markdown.splitlines():
        match = _HEADING.match(line.strip())
        if match and len(match.group(1)) == 1:
            return match.group(2).strip()
    return fallback


def document_html(markdown: str) -> str:
    """生成可离线打开、可直接打印为 PDF 的单文件 HTML。"""
    title = document_title(markdown)
    body = _decorate(render_markdown(markdown))
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{escape(title)}</title>
<style>
{PAPER_CSS}
{PAGE_CSS}
</style>
</head>
<body>
<main class="paper">
{body}
<footer class="doc-foot">本文档为离线可复现产物：同一输入、同一角色实现，可得到结构一致的结果。</footer>
</main>
</body>
</html>
"""


def _decorate(body: str) -> str:
    """在标题后插入目录：先标题与元信息，再目录，最后正文。"""
    heading = re.match(r'\s*(<h1 id="h\d+">.*?</h1>)', body, re.S)
    kicker = '<p class="doc-kicker">IdeaFlow · 多智能体生成的设计文档</p>'
    if not heading:
        return kicker + "\n" + body

    lead = heading.group(1)
    rest = body[heading.end():]
    meta = re.match(r"\s*(<blockquote>.*?</blockquote>)", rest, re.S)
    if meta:
        lead += "\n" + meta.group(1)
        rest = rest[meta.end():]
    return "\n".join(part for part in (kicker, lead, _toc(body), rest.strip()) if part)


def _toc(body: str) -> str:
    items = re.findall(r'<h2 id="(h\d+)">(.+?)</h2>', body)
    if len(items) < 3:
        return ""
    links = "".join(
        f'<li><a href="#{anchor}">{re.sub(r"<[^>]+>", "", text)}</a></li>'
        for anchor, text in items)
    return f'<nav class="toc"><p class="toc-title">目录</p><ul>{links}</ul></nav>'


PAPER_CSS = """
.paper {
  --ink: #1c1c1a;
  --muted: #6d6a60;
  --line: #e5e1d6;
  --wash: #f8f6f0;
  color: var(--ink);
  font-family: "Noto Serif CJK SC", "Source Han Serif SC", "Songti SC", SimSun, Georgia, serif;
  font-size: 15.5px;
  line-height: 1.85;
  word-wrap: break-word;
}
.paper h1, .paper h2, .paper h3, .paper h4 {
  font-family: "Inter", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
  line-height: 1.4;
  color: #14150f;
}
.paper h1 { font-size: 26px; margin: 0 0 6px; letter-spacing: -0.01em; }
.paper h2 { font-size: 19px; margin: 38px 0 12px; padding-bottom: 8px; border-bottom: 1px solid var(--line); }
.paper h3 { font-size: 15.5px; margin: 26px 0 10px; color: #2f3128; }
.paper h4 { font-size: 14px; margin: 20px 0 8px; }
.paper p { margin: 0 0 12px; }
.paper strong { font-weight: 650; }
.paper blockquote {
  margin: 16px 0;
  padding: 12px 18px;
  background: var(--wash);
  border-left: 3px solid #c8b488;
  color: #57544a;
  font-size: 14.5px;
}
.paper blockquote p:last-child { margin-bottom: 0; }
.paper ul, .paper ol { margin: 0 0 14px; padding-left: 22px; }
.paper li { margin: 4px 0; }
.paper .table-wrap { overflow-x: auto; margin: 14px 0 20px; }
.paper table {
  width: 100%;
  border-collapse: collapse;
  font-family: "Inter", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
  font-size: 12.5px;
  line-height: 1.65;
}
.paper th, .paper td { border: 1px solid var(--line); padding: 8px 10px; vertical-align: top; }
.paper th { background: #f6f3ea; text-align: left; font-weight: 600; white-space: nowrap; }
.paper tbody tr:nth-child(even) { background: #fcfbf7; }
.paper code {
  font-family: "JetBrains Mono", ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 12px;
  background: #f2eee3;
  border-radius: 4px;
  padding: 1px 5px;
}
.paper pre.code { background: #201f1b; color: #efece2; padding: 14px 16px; border-radius: 8px; overflow-x: auto; }
.paper pre.code code { background: none; color: inherit; padding: 0; }
.paper hr { border: 0; border-top: 1px solid var(--line); margin: 28px 0; }
.paper a { color: #1d5f86; text-underline-offset: 3px; }
.paper .doc-meta { margin-bottom: 8px; color: var(--muted); font-size: 12.5px; }
.paper .doc-kicker { margin: 0 0 10px; font-family: "Inter", "PingFang SC", sans-serif; font-size: 10.5px; letter-spacing: 0.16em; text-transform: uppercase; color: #b08d57; }
.paper .toc { margin: 20px 0 12px; padding: 14px 18px; background: var(--wash); border: 1px solid var(--line); border-radius: 6px; }
.paper .toc-title { margin: 0 0 6px; font-family: "Inter", "PingFang SC", sans-serif; font-size: 11px; letter-spacing: 0.12em; text-transform: uppercase; color: var(--muted); }
.paper .toc ul { margin: 0; padding: 0; list-style: none; column-count: 2; column-gap: 28px; font-size: 13px; }
.paper .toc li { margin: 2px 0; }
.paper .toc a { color: #33362c; text-decoration: none; }
.paper .toc a:hover { text-decoration: underline; }
.paper .doc-foot { margin-top: 46px; padding-top: 14px; border-top: 1px solid var(--line); color: var(--muted); font-size: 12px; }
"""

PAGE_CSS = """
* { box-sizing: border-box; }
body { margin: 0; background: #f2f0e9; }
.paper { max-width: 820px; margin: 40px auto; background: #fff; padding: 56px 60px 48px; box-shadow: 0 8px 30px rgba(30, 30, 20, 0.08); }
@media (max-width: 720px) { .paper { margin: 0; padding: 28px 20px; box-shadow: none; } .paper .toc ul { column-count: 1; } }
@page { margin: 18mm 16mm; }
@media print {
  body { background: #fff; }
  .paper { max-width: none; margin: 0; padding: 0; box-shadow: none; font-size: 11.5pt; }
  .paper h2 { break-after: avoid; }
  .paper table { break-inside: auto; font-size: 9.5pt; }
  .paper tr { break-inside: avoid; }
  .paper .toc { break-after: page; }
}
"""
