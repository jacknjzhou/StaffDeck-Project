import type { ReactNode } from 'react';

type TokenType =
  | 'plain'
  | 'comment'
  | 'string'
  | 'number'
  | 'keyword'
  | 'builtin'
  | 'function'
  | 'operator'
  | 'punctuation'
  | 'property'
  | 'boolean';

type CodeToken = {
  text: string;
  type: TokenType;
};

const PYTHON_KEYWORDS = new Set([
  'False', 'None', 'True', 'and', 'as', 'assert', 'async', 'await', 'break', 'class',
  'continue', 'def', 'del', 'elif', 'else', 'except', 'finally', 'for', 'from', 'global',
  'if', 'import', 'in', 'is', 'lambda', 'nonlocal', 'not', 'or', 'pass', 'raise',
  'return', 'try', 'while', 'with', 'yield',
]);

const PYTHON_BUILTINS = new Set([
  'dict', 'list', 'set', 'tuple', 'str', 'int', 'float', 'bool', 'len', 'range', 'print',
  'open', 'enumerate', 'zip', 'map', 'filter', 'sum', 'min', 'max', 'json', 'Path',
  'Exception', 'ValueError', 'TypeError',
]);

/** 将非空纯文本片段追加到 token 列表，空字符串直接忽略。 */
function appendPlain(tokens: CodeToken[], text: string) {
  if (text) tokens.push({ text, type: 'plain' });
}

/** 将 JSON 文本切分为属性名、字符串、布尔值、数字与标点 token。 */
function tokenizeJson(code: string): CodeToken[] {
  const tokens: CodeToken[] = [];
  const pattern = /("(?:\\.|[^"\\])*"(?=\s*:)|"(?:\\.|[^"\\])*"|true|false|null|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|[{}\[\]:,])/g;
  let cursor = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(code)) !== null) {
    appendPlain(tokens, code.slice(cursor, match.index));
    const value = match[0];
    if (value.startsWith('"') && code.slice(pattern.lastIndex).trimStart().startsWith(':')) {
      tokens.push({ text: value, type: 'property' });
    } else if (value.startsWith('"')) {
      tokens.push({ text: value, type: 'string' });
    } else if (/^(true|false|null)$/.test(value)) {
      tokens.push({ text: value, type: 'boolean' });
    } else if (/^-?\d/.test(value)) {
      tokens.push({ text: value, type: 'number' });
    } else {
      tokens.push({ text: value, type: 'punctuation' });
    }
    cursor = pattern.lastIndex;
  }
  appendPlain(tokens, code.slice(cursor));
  return tokens;
}

/**
 * 按 Python 语法把源码切分为注释、字符串、数字、关键字、内建名等 token。
 *
 * `def`/`class` 之后的标识符会被标记为函数名；未命中的片段保持 plain。
 */
function tokenizePython(code: string): CodeToken[] {
  const tokens: CodeToken[] = [];
  const pattern = /(#.*$|"""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\b\d+(?:\.\d+)?\b|\b[A-Za-z_]\w*\b|[+\-*/%=<>!&|^~]+|[(){}\[\],.:;])/gm;
  let cursor = 0;
  let expectFunctionName = false;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(code)) !== null) {
    appendPlain(tokens, code.slice(cursor, match.index));
    const value = match[0];
    let type: TokenType = 'plain';
    if (value.startsWith('#')) type = 'comment';
    else if (value.startsWith('"') || value.startsWith("'")) type = 'string';
    else if (/^\d/.test(value)) type = 'number';
    else if (expectFunctionName && /^[A-Za-z_]\w*$/.test(value)) {
      type = 'function';
      expectFunctionName = false;
    } else if (PYTHON_KEYWORDS.has(value)) {
      type = 'keyword';
      expectFunctionName = value === 'def' || value === 'class';
    } else if (PYTHON_BUILTINS.has(value)) type = 'builtin';
    else if (/^[+\-*/%=<>!&|^~]+$/.test(value)) type = 'operator';
    else if (/^[(){}\[\],.:;]$/.test(value)) type = 'punctuation';
    tokens.push({ text: value, type });
    cursor = pattern.lastIndex;
  }
  appendPlain(tokens, code.slice(cursor));
  return tokens;
}

/** 将 Markdown 文本切分为标题、列表符号、行内代码、链接与数字 token。 */
function tokenizeMarkdown(code: string): CodeToken[] {
  const tokens: CodeToken[] = [];
  const pattern = /(^#{1,6}\s.*$|^[-*+]\s+|^>\s.*$|`[^`\n]+`|\*\*[^*\n]+\*\*|\[[^\]\n]+\]\([^)]+\)|https?:\/\/[^\s)]+|\b\d+(?:\.\d+)?\b)/gm;
  let cursor = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(code)) !== null) {
    appendPlain(tokens, code.slice(cursor, match.index));
    const value = match[0];
    let type: TokenType = 'plain';
    if (value.startsWith('#') || value.startsWith('>')) type = 'keyword';
    else if (/^[-*+]\s+$/.test(value)) type = 'operator';
    else if (value.startsWith('`') || value.startsWith('**')) type = 'string';
    else if (value.startsWith('[') || value.startsWith('http')) type = 'function';
    else if (/^\d/.test(value)) type = 'number';
    tokens.push({ text: value, type });
    cursor = pattern.lastIndex;
  }
  appendPlain(tokens, code.slice(cursor));
  return tokens;
}

/** 按语言名分派到对应的分词器，未知语言回退为 Python 分词。 */
function tokenize(code: string, language?: string): CodeToken[] {
  const normalized = (language || '').toLowerCase();
  if (['text', 'txt', 'log', 'stdout', 'stderr', 'plain'].includes(normalized)) return [{ text: code, type: 'plain' }];
  if (normalized.includes('json')) return tokenizeJson(code);
  if (normalized.includes('python') || normalized === 'py') return tokenizePython(code);
  if (normalized.includes('markdown') || normalized === 'md') return tokenizeMarkdown(code);
  return tokenizePython(code);
}

/**
 * 把源码渲染为带高亮 class 的 React 节点数组。
 *
 * 纯文本片段原样返回，其余片段包在 `span.code-token` 中交给外部样式上色。
 */
export function renderCodeTokens(code: string, language?: string) {
  const tokens = tokenize(code, language);
  return tokens.map((token, index): ReactNode => (
    token.type === 'plain'
      ? token.text
      : <span key={`${token.type}-${index}`} className={`code-token ${token.type}`}>{token.text}</span>
  ));
}

/** 代码块组件：按语言做轻量语法高亮，并在 `pre` 上透出 data-language。 */
export default function CodeBlock({ code, language, className }: { code: string; language?: string; className?: string }) {
  return (
    <pre className={['code-block-vscode', className].filter(Boolean).join(' ')} data-language={language || undefined}>
      <code>
        {renderCodeTokens(code, language)}
      </code>
    </pre>
  );
}
