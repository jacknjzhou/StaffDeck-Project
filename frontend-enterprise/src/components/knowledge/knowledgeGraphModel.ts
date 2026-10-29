import type { KnowledgeConceptRead } from '@/types';

export const KNOWLEDGE_GRAPH_NODE_LIMIT = 500;

export type KnowledgeGraphUnresolvedLink = {
  label: string;
  target: string;
  kind: 'external' | 'unresolved';
};

export type KnowledgeGraphNode = {
  id: string;
  concept: KnowledgeConceptRead;
  degree: number;
  inbound: string[];
  outbound: string[];
  unresolvedLinks: KnowledgeGraphUnresolvedLink[];
};

export type KnowledgeGraphEdge = {
  id: string;
  source: string;
  target: string;
};

export type KnowledgeGraphModel = {
  nodes: KnowledgeGraphNode[];
  edges: KnowledgeGraphEdge[];
  nodeById: Map<string, KnowledgeGraphNode>;
  stats: {
    nodeCount: number;
    edgeCount: number;
    isolatedCount: number;
    unresolvedCount: number;
  };
};

export type BuildKnowledgeGraphOptions = {
  includeArchived?: boolean;
  conceptIds?: ReadonlySet<string>;
};

const EXTERNAL_SCHEME = /^[a-z][a-z\d+.-]*:/i;
const DEFAULT_PREVIEW_CHARACTER_LIMIT = 1_200;

/**
 * 由知识概念列表构建图模型：过滤概念、解析内部链接、收集外部 / 未解析链接并统计度数。
 *
 * 默认跳过 archived 概念，可用 includeArchived 保留；conceptIds 用于只保留指定概念；
 * 非字符串 target、自环与重复边会被忽略；返回节点、边、id 索引与四项统计数。
 */
export function buildKnowledgeGraph(
  concepts: KnowledgeConceptRead[],
  options: BuildKnowledgeGraphOptions = {},
): KnowledgeGraphModel {
  const includedConcepts = concepts.filter((concept) => {
    if (!options.includeArchived && concept.status === 'archived') return false;
    return !options.conceptIds || options.conceptIds.has(concept.concept_id);
  });
  const conceptsById = new Map(includedConcepts.map((concept) => [concept.concept_id, concept]));
  const inbound = new Map<string, Set<string>>();
  const outbound = new Map<string, Set<string>>();
  const unresolved = new Map<string, KnowledgeGraphUnresolvedLink[]>();
  const edgeByKey = new Map<string, KnowledgeGraphEdge>();

  for (const concept of includedConcepts) {
    inbound.set(concept.concept_id, new Set());
    outbound.set(concept.concept_id, new Set());
    unresolved.set(concept.concept_id, []);
  }

  for (const concept of includedConcepts) {
    const citationKeys = new Set(
      (concept.citations || []).map((citation) => relationKey(citation.label, citation.target)),
    );
    for (const link of concept.links || []) {
      const label = stringField(link.label);
      const target = stringField(link.target);
      if (!target || citationKeys.has(relationKey(label, target))) continue;
      const resolved = resolveKnowledgeLink(concept.concept_id, target);
      if (resolved.kind === 'external') {
        unresolved.get(concept.concept_id)?.push({ label, target, kind: 'external' });
        continue;
      }
      if (!resolved.conceptId || !conceptsById.has(resolved.conceptId)) {
        unresolved.get(concept.concept_id)?.push({ label, target, kind: 'unresolved' });
        continue;
      }
      if (resolved.conceptId === concept.concept_id) continue;
      const key = `${concept.concept_id}\u0000${resolved.conceptId}`;
      if (edgeByKey.has(key)) continue;
      edgeByKey.set(key, {
        id: `edge:${concept.concept_id}->${resolved.conceptId}`,
        source: concept.concept_id,
        target: resolved.conceptId,
      });
      outbound.get(concept.concept_id)?.add(resolved.conceptId);
      inbound.get(resolved.conceptId)?.add(concept.concept_id);
    }
  }

  const nodes = includedConcepts.map((concept) => {
    const nodeInbound = [...(inbound.get(concept.concept_id) || [])];
    const nodeOutbound = [...(outbound.get(concept.concept_id) || [])];
    return {
      id: concept.concept_id,
      concept,
      degree: nodeInbound.length + nodeOutbound.length,
      inbound: nodeInbound,
      outbound: nodeOutbound,
      unresolvedLinks: unresolved.get(concept.concept_id) || [],
    };
  });
  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  const edges = [...edgeByKey.values()];
  const unresolvedCount = nodes.reduce((total, node) => total + node.unresolvedLinks.length, 0);

  return {
    nodes,
    edges,
    nodeById,
    stats: {
      nodeCount: nodes.length,
      edgeCount: edges.length,
      isolatedCount: nodes.filter((node) => node.degree === 0).length,
      unresolvedCount,
    },
  };
}

/** 判断概念是否命中搜索词：空关键词恒为真，否则在标题、概念 id 与 frontmatter 标签中做不区分大小写的子串匹配。 */
export function knowledgeConceptMatches(concept: KnowledgeConceptRead, rawQuery: string): boolean {
  const query = rawQuery.trim().toLocaleLowerCase();
  if (!query) return true;
  const tags = Array.isArray(concept.frontmatter?.tags) ? concept.frontmatter.tags : [];
  return [concept.title, concept.concept_id, ...tags]
    .filter((value) => typeof value === 'string')
    .some((value) => String(value).toLocaleLowerCase().includes(query));
}

/**
 * 生成概念预览用的 Markdown：优先取 description，缺失时退回首段正文。
 *
 * 会剥离 OKF frontmatter、去掉与标题重复的首个标题，并在块边界截断到字符上限。
 */
export function knowledgeConceptPreviewMarkdown(
  concept: KnowledgeConceptRead,
  characterLimit = DEFAULT_PREVIEW_CHARACTER_LIMIT,
): string {
  const description = normalizePreviewMarkdown(concept.description || '');
  const source = description || firstMeaningfulMarkdownSection(stripOkfFrontmatter(concept.content_md || ''));
  const withoutDuplicateTitle = removeDuplicateLeadingHeading(source, concept.title || '');
  return truncateMarkdownAtBlockBoundary(withoutDuplicateTitle, characterLimit);
}

/** 返回度数最高的非归档节点；度数相同时按 id 字典序取首个，无可用节点时返回 undefined。 */
export function highestDegreeActiveNode(model: KnowledgeGraphModel): KnowledgeGraphNode | undefined {
  return [...model.nodes]
    .filter((node) => node.concept.status !== 'archived')
    .sort((left, right) => right.degree - left.degree || left.id.localeCompare(right.id))[0];
}

/**
 * 解析链接目标：带协议、以 // 开头或为空的 target 视为外部链接，其余按内部概念路径解析。
 *
 * 内部链接会去掉锚点、查询串与 .md 后缀，并按源概念所在目录处理相对路径与 ..；
 * 仅含锚点或查询串时指向源概念自身，非法百分号编码保留原文以便报告为未解析。
 */
export function resolveKnowledgeLink(
  sourceConceptId: string,
  rawTarget: string,
): { kind: 'internal'; conceptId: string } | { kind: 'external'; conceptId?: undefined } {
  const target = rawTarget.trim();
  if (!target || target.startsWith('//') || EXTERNAL_SCHEME.test(target)) {
    return { kind: 'external' };
  }
  let pathname = target.split('#', 1)[0]?.split('?', 1)[0] || '';
  try {
    pathname = decodeURIComponent(pathname);
  } catch {
    // Keep malformed URI text so it is reported as unresolved instead of crashing the graph.
  }
  if (!pathname) return { kind: 'internal', conceptId: sourceConceptId };
  const isAbsolute = pathname.startsWith('/');
  const sourceParts = sourceConceptId.split('/').filter(Boolean);
  const targetParts = pathname.split('/');
  const parts = isAbsolute ? [] : sourceParts.slice(0, -1);
  for (const part of targetParts) {
    if (!part || part === '.') continue;
    if (part === '..') {
      parts.pop();
      continue;
    }
    parts.push(part);
  }
  const lastIndex = parts.length - 1;
  if (lastIndex >= 0) parts[lastIndex] = parts[lastIndex].replace(/\.md$/i, '');
  return { kind: 'internal', conceptId: parts.join('/') };
}

/** 去掉正文开头的 OKF YAML frontmatter（由 --- 包围的块）并修剪首尾空白。 */
function stripOkfFrontmatter(markdown: string): string {
  return markdown
    .replace(/^\uFEFF?---[ \t]*\n[\s\S]*?\n---[ \t]*(?:\n|$)/, '')
    .trim();
}

/**
 * 归一化预览 Markdown：统一换行、把紧跟在行内的标题 / 列表标记拆到新行，并修复被压平的表格。
 *
 * 用于容忍知识库中按段落存储导致的标记粘连；返回修剪首尾空白后的结果。
 */
function normalizePreviewMarkdown(markdown: string): string {
  const normalized = markdown
    .replace(/\r\n/g, '\n')
    .replace(/[ \t]+(#{1,6}\s+)/g, '\n\n$1')
    .replace(/[ \t]+([-*]\s+)/g, '\n$1')
    .replace(/[ \t]+(\d+[.)]\s+)/g, '\n$1')
    .trim();
  return repairFlattenedTables(normalized);
}

/**
 * 修复被压平成一行的 Markdown 表格：定位分隔行后回溯表头，再按列数重新切分数据行。
 *
 * 无法可靠判断表格边界时跳过该处继续向后扫描；返回重建后的全文。
 */
function repairFlattenedTables(markdown: string): string {
  let result = markdown;
  const separatorPattern = /\|\s*:?-{1,}:?\s*(?:\|\s*:?-{1,}:?\s*)+\|/;
  let searchFrom = 0;
  while (searchFrom < result.length) {
    const tail = result.slice(searchFrom);
    const match = tail.match(separatorPattern);
    if (!match || match.index === undefined) break;
    const separatorStart = searchFrom + match.index;
    const separatorEnd = separatorStart + match[0].length;
    const columnCount = match[0].split('|').slice(1, -1).length;
    const pipePositions: number[] = [];
    for (let index = 0; index < separatorStart; index += 1) {
      if (result[index] === '|') pipePositions.push(index);
    }
    const headerStart = pipePositions[pipePositions.length - columnCount - 1];
    if (headerStart === undefined || headerStart < result.lastIndexOf('\n', separatorStart)) {
      searchFrom = separatorEnd;
      continue;
    }

    const afterSeparator = result.slice(separatorEnd);
    const blockBoundary = afterSeparator.search(/\n|\s+(?=#{1,6}\s+|[-*]\s+|\d+[.)]\s+)/);
    const tableTail = blockBoundary >= 0 ? afterSeparator.slice(0, blockBoundary) : afterSeparator;
    const remainder = blockBoundary >= 0 ? afterSeparator.slice(blockBoundary).trimStart() : '';
    const headerCells = tableCells(result.slice(headerStart, separatorStart));
    const rowCells = tableCells(tableTail);
    if (headerCells.length !== columnCount || rowCells.length < columnCount) {
      searchFrom = separatorEnd;
      continue;
    }

    const rows: string[] = [];
    for (let index = 0; index + columnCount <= rowCells.length; index += columnCount) {
      rows.push(markdownTableRow(rowCells.slice(index, index + columnCount)));
    }
    const rebuilt = [
      markdownTableRow(headerCells),
      markdownTableRow(Array.from({ length: columnCount }, () => '---')),
      ...rows,
    ].join('\n');
    const prefix = result.slice(0, headerStart).trimEnd();
    result = `${prefix}${prefix ? '\n\n' : ''}${rebuilt}${remainder ? `\n\n${remainder}` : ''}`;
    searchFrom = prefix.length + rebuilt.length + 2;
  }
  return result.trim();
}

/** 按 | 切分表格行并丢弃空单元格，用于还原表头与数据行的列内容。 */
function tableCells(value: string): string[] {
  return value.split('|').map((cell) => cell.trim()).filter(Boolean);
}

/** 把单元格数组还原为 `| a | b |` 形式的 Markdown 表格行。 */
function markdownTableRow(cells: string[]): string {
  return `| ${cells.join(' | ')} |`;
}

/**
 * 取首个非空行起始的内容段：若以标题开头，则截取到同级或更高级的下一个标题之前。
 *
 * 无标题时截取到下一个标题前；全文为空时返回空串。
 */
function firstMeaningfulMarkdownSection(markdown: string): string {
  const lines = normalizePreviewMarkdown(markdown).split('\n');
  const firstLineIndex = lines.findIndex((line) => line.trim());
  if (firstLineIndex < 0) return '';

  const firstHeading = lines[firstLineIndex].trim().match(/^(#{1,6})\s+(.+)$/);
  let endIndex = lines.length;
  if (firstHeading) {
    const firstLevel = firstHeading[1].length;
    for (let index = firstLineIndex + 1; index < lines.length; index += 1) {
      const heading = lines[index].trim().match(/^(#{1,6})\s+/);
      if (heading && heading[1].length <= firstLevel) {
        endIndex = index;
        break;
      }
    }
  } else {
    const nextHeadingOffset = lines
      .slice(firstLineIndex + 1)
      .findIndex((line) => /^(#{1,6})\s+/.test(line.trim()));
    if (nextHeadingOffset >= 0) endIndex = firstLineIndex + 1 + nextHeadingOffset;
  }
  return lines.slice(firstLineIndex, endIndex).join('\n').trim();
}

/**
 * 去掉正文开头与概念标题重复的标题行；标题后带额外文字时只保留额外的文字。
 *
 * 比较标题时会忽略反引号等强调符号、多余空白与大小写；无标题或无标题文本时原样返回。
 */
function removeDuplicateLeadingHeading(markdown: string, title: string): string {
  const normalized = normalizePreviewMarkdown(markdown);
  const lines = normalized.split('\n');
  const firstLineIndex = lines.findIndex((line) => line.trim());
  if (firstLineIndex < 0 || !title.trim()) return normalized;

  const heading = lines[firstLineIndex].trim().match(/^(#{1,6})\s+(.+)$/);
  if (!heading) return normalized;
  const headingContent = heading[2].trim();
  const normalizedTitle = normalizeHeadingText(title);
  if (normalizeHeadingText(headingContent) === normalizedTitle) {
    lines.splice(firstLineIndex, 1);
    return lines.join('\n').trim();
  }

  const rawTitle = title.trim();
  if (headingContent.startsWith(rawTitle) && /^\s/.test(headingContent.slice(rawTitle.length, rawTitle.length + 1))) {
    lines[firstLineIndex] = headingContent.slice(rawTitle.length).trim();
    return lines.join('\n').trim();
  }
  return normalized;
}

/** 归一化标题文本：去掉强调符号、压缩空白并转小写，用于重复标题判断。 */
function normalizeHeadingText(value: string): string {
  return value
    .replace(/[`*_~]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .toLocaleLowerCase();
}

/**
 * 按块（段落 / 围栏代码块）边界把 Markdown 截断到字符上限，避免切断行内标记。
 *
 * 超限时保留首个块，之后整块纳入直到越界；未超限或上限非正时原样返回。
 */
function truncateMarkdownAtBlockBoundary(markdown: string, characterLimit: number): string {
  const normalized = normalizePreviewMarkdown(markdown);
  if (!normalized || normalized.length <= characterLimit || characterLimit <= 0) return normalized;
  const blocks = markdownBlocks(normalized);
  const included: string[] = [];
  let length = 0;
  for (const block of blocks) {
    const nextLength = length + (included.length ? 2 : 0) + block.length;
    if (included.length && nextLength > characterLimit) break;
    included.push(block);
    length = nextLength;
    if (length >= characterLimit) break;
  }
  return included.join('\n\n').trim();
}

/** 按空行切分 Markdown 块，围栏代码块内部的空行不参与切分。 */
function markdownBlocks(markdown: string): string[] {
  const lines = markdown.split('\n');
  const blocks: string[] = [];
  let current: string[] = [];
  let inFence = false;
  /** 把当前累积的行作为一个块写入结果并重置缓冲，空块被丢弃。 */
  const flush = () => {
    const block = current.join('\n').trim();
    if (block) blocks.push(block);
    current = [];
  };

  for (const line of lines) {
    if (line.trim().startsWith('```')) inFence = !inFence;
    if (!inFence && !line.trim()) {
      flush();
      continue;
    }
    current.push(line);
  }
  flush();
  return blocks;
}

/** 生成「标签 + 目标」去重键，用空字符分隔以避免不同字段拼接后互相冲突。 */
function relationKey(label: unknown, target: unknown): string {
  return `${stringField(label)}\u0000${stringField(target)}`;
}

/** 安全读取字符串字段：非字符串一律返回空串，字符串则修剪首尾空白。 */
function stringField(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}
