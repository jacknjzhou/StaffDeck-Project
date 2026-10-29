import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';
import type { KnowledgeConceptRead } from '@/types';

const CANVAS_WIDTH = 1000;
const DEFAULT_HEIGHT = 560;
const CLICK_SLOP_PX = 4;
const RING_GAP = 28;
const ORPHANS_PER_ROW = 6;

type GraphNode = {
  id: string;
  concept: KnowledgeConceptRead;
  label: string;
  color: string;
  isSource: boolean;
  width: number;
  height: number;
  x: number;
  y: number;
};

type GraphEdge = {
  source: number;
  target: number;
  kind: 'link' | 'citation';
};

type GraphData = {
  nodes: GraphNode[];
  edges: GraphEdge[];
};

type ViewTransform = {
  k: number;
  tx: number;
  ty: number;
};

const TYPE_STYLES: Record<string, { color: string; label: string }> = {
  'Source Document': { color: '#1a71ff', label: '原始资料' },
  'Source Section': { color: '#0ea5e9', label: '资料页' },
  Topic: { color: '#22c55e', label: '主题' },
  Playbook: { color: '#a855f7', label: '流程知识' },
  'Business Rule': { color: '#f59e0b', label: '业务规则' },
  'Query Analysis': { color: '#d946ef', label: '查询分析' },
};
const FALLBACK_TYPE_STYLE = { color: '#94a3b8', label: '概念' };
const TYPE_ORDER = Object.keys(TYPE_STYLES);

/** 把数值限制在 [min, max] 区间内。 */
function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}

/** 归一化概念引用：去掉开头的斜杠与结尾的 .md 后缀，便于按 concept_id 匹配。 */
function normalizeConceptRef(target: string) {
  let value = target.trim();
  if (value.startsWith('/')) value = value.slice(1);
  if (value.endsWith('.md')) value = value.slice(0, -3);
  return value;
}

/** 从引用链接（形如 documents/<id>）中提取文档 ID，匹配不到时返回 null。 */
function extractCitationDocumentId(target: string) {
  const match = /documents\/([^/?#\s]+)/.exec(target);
  return match ? decodeURIComponent(match[1]) : null;
}

/** 截断过长的节点标题，超过 max 个字符时以省略号结尾。 */
function truncateLabel(value: string, max = 12) {
  return value.length > max ? `${value.slice(0, max)}…` : value;
}

/** 估算文本在给定字号下的像素宽度：中日韩字符按全角计，其余字符按 0.56 倍字号计。 */
function measureLabel(text: string, fontSize: number) {
  let width = 0;
  for (const char of text) {
    width += /[⺀-鿿＀-･￠-￥]/.test(char) ? fontSize : fontSize * 0.56;
  }
  return width;
}

/**
 * 把知识概念列表转换为可绘制的图数据（节点 + 连线）。
 *
 * - 按 concept_id 去重，未登记的概念类型回退到默认样式与标签。
 * - link 边按概念引用匹配，citation 边按链接中的文档 ID 匹配，同一方向同类型只保留一条。
 */
function buildGraph(concepts: KnowledgeConceptRead[]): GraphData {
  const nodes: GraphNode[] = [];
  const indexById = new Map<string, number>();
  const indexByDocument = new Map<string, number>();

  concepts.forEach((concept) => {
    if (!concept.concept_id || indexById.has(concept.concept_id)) return;
    const style = TYPE_STYLES[concept.concept_type] || FALLBACK_TYPE_STYLE;
    const isSource = concept.concept_type === 'Source Document';
    const label = truncateLabel(concept.title || concept.concept_id);
    const fontSize = isSource ? 12 : 11;
    const textWidth = measureLabel(label, fontSize);
    indexById.set(concept.concept_id, nodes.length);
    nodes.push({
      id: concept.concept_id,
      concept,
      label,
      color: style.color,
      isSource,
      width: Math.ceil(textWidth + (isSource ? 42 : 36)),
      height: isSource ? 32 : 28,
      x: 0,
      y: 0,
    });
  });

  nodes.forEach((node, index) => {
    const documentId = node.concept.document_id;
    if (!documentId) return;
    const existing = indexByDocument.get(documentId);
    if (existing === undefined || node.concept.concept_type === 'Source Document') {
      indexByDocument.set(documentId, index);
    }
  });

  const edges: GraphEdge[] = [];
  const seen = new Set<string>();
  nodes.forEach((node, source) => {
    (Array.isArray(node.concept.links) ? node.concept.links : []).forEach((link) => {
      const rawTarget = typeof link?.target === 'string' ? link.target : '';
      if (!rawTarget) return;
      const target = indexById.get(normalizeConceptRef(rawTarget));
      if (target === undefined || target === source) return;
      const key = `${source}->${target}:link`;
      if (seen.has(key)) return;
      seen.add(key);
      edges.push({ source, target, kind: 'link' });
    });
    (Array.isArray(node.concept.citations) ? node.concept.citations : []).forEach((citation) => {
      const rawTarget = typeof citation?.target === 'string' ? citation.target : '';
      const documentId = rawTarget ? extractCitationDocumentId(rawTarget) : null;
      const target = documentId ? indexByDocument.get(documentId) : undefined;
      if (target === undefined || target === source) return;
      const key = `${source}->${target}:citation`;
      if (seen.has(key)) return;
      seen.add(key);
      edges.push({ source, target, kind: 'citation' });
    });
  });

  return { nodes, edges };
}

/**
 * 生成节点索引比较器：先按类型在 TYPE_ORDER 中的次序排序，同类型再按标题的中文顺序排序。
 *
 * 返回的函数可直接传给 Array.prototype.sort 使用。
 */
function compareNodeIndices(nodes: GraphNode[]) {
  return (left: number, right: number) => {
    const leftRank = TYPE_ORDER.indexOf(nodes[left].concept.concept_type);
    const rightRank = TYPE_ORDER.indexOf(nodes[right].concept.concept_type);
    const leftOrder = leftRank === -1 ? TYPE_ORDER.length : leftRank;
    const rightOrder = rightRank === -1 ? TYPE_ORDER.length : rightRank;
    if (leftOrder !== rightOrder) return leftOrder - rightOrder;
    return nodes[left].label.localeCompare(nodes[right].label, 'zh-CN');
  };
}

/**
 * 确定性的分层放射布局：原始资料居中，直接邻居排第一圈，其余有连线的节点排第二圈，孤立节点按行置底。
 *
 * - 位置只在图数据变化时计算一次，交互过程不会重新布局。
 * - 因此拖动某个节点只会移动它自己，不会带动其他任何节点。
 */
function layoutGraph(graph: GraphData) {
  const { nodes, edges } = graph;
  if (nodes.length === 0) return;

  const degree = new Array<number>(nodes.length).fill(0);
  const neighbors = nodes.map(() => new Set<number>());
  edges.forEach((edge) => {
    degree[edge.source] += 1;
    degree[edge.target] += 1;
    neighbors[edge.source].add(edge.target);
    neighbors[edge.target].add(edge.source);
  });

  let center = 0;
  let bestScore = -1;
  nodes.forEach((node, index) => {
    const score = (node.isSource ? 100000 : 0) + degree[index];
    if (score > bestScore) {
      bestScore = score;
      center = index;
    }
  });

  const byType = compareNodeIndices(nodes);
  const ring1 = [...neighbors[center]].sort(byType);
  const assigned = new Set<number>([center, ...ring1]);
  const rest = nodes.map((_, index) => index).filter((index) => !assigned.has(index));
  const ring2 = rest.filter((index) => degree[index] > 0).sort(byType);
  const orphans = rest.filter((index) => degree[index] === 0).sort(byType);

  nodes[center].x = 0;
  nodes[center].y = 0;

  const ring1Arc = ring1.reduce((sum, index) => sum + nodes[index].width + RING_GAP, 0);
  const radius1 = Math.max(200, ring1Arc / (2 * Math.PI));
  ring1.forEach((index, position) => {
    const angle = -Math.PI / 2 + (position / ring1.length) * Math.PI * 2;
    nodes[index].x = Math.cos(angle) * radius1;
    nodes[index].y = Math.sin(angle) * radius1;
  });

  const ring2Arc = ring2.reduce((sum, index) => sum + nodes[index].width + RING_GAP, 0);
  const radius2 = Math.max(radius1 + 130, ring2Arc / (2 * Math.PI));
  ring2.forEach((index, position) => {
    const angle = -Math.PI / 2 + ((position + 0.5) / ring2.length) * Math.PI * 2;
    nodes[index].x = Math.cos(angle) * radius2;
    nodes[index].y = Math.sin(angle) * radius2;
  });

  const orphanTop = (ring2.length > 0 ? radius2 : radius1) + 140;
  for (let row = 0; row * ORPHANS_PER_ROW < orphans.length; row += 1) {
    const rowItems = orphans.slice(row * ORPHANS_PER_ROW, (row + 1) * ORPHANS_PER_ROW);
    const rowWidth =
      rowItems.reduce((sum, index) => sum + nodes[index].width, 0) + (rowItems.length - 1) * 20;
    let cursor = -rowWidth / 2;
    rowItems.forEach((index) => {
      nodes[index].x = cursor + nodes[index].width / 2;
      nodes[index].y = orphanTop + row * 48;
      cursor += nodes[index].width + 20;
    });
  }
}

/**
 * 计算让全部节点完整落入视野的自适应视图。
 *
 * - 围绕节点包围盒等比缩放，四周预留 60px 留白，缩放系数限制在 0.2–1.1。
 * - 没有节点时返回居中的默认视图。
 */
function computeFitView(graph: GraphData, height: number): ViewTransform {
  if (graph.nodes.length === 0) {
    return { k: 1, tx: CANVAS_WIDTH / 2, ty: height / 2 };
  }
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  graph.nodes.forEach((node) => {
    minX = Math.min(minX, node.x - node.width / 2);
    minY = Math.min(minY, node.y - node.height / 2);
    maxX = Math.max(maxX, node.x + node.width / 2);
    maxY = Math.max(maxY, node.y + node.height / 2);
  });
  const boundsWidth = Math.max(maxX - minX, 1);
  const boundsHeight = Math.max(maxY - minY, 1);
  const k = clamp(Math.min((CANVAS_WIDTH - 120) / boundsWidth, (height - 120) / boundsHeight), 0.2, 1.1);
  return {
    k,
    tx: CANVAS_WIDTH / 2 - ((minX + maxX) / 2) * k,
    ty: height / 2 - ((minY + maxY) / 2) * k,
  };
}

/**
 * 估算从胶囊节点中心沿给定单位方向到边框的距离。
 *
 * 用椭圆近似胶囊形状，用于把连线端点裁剪到节点边缘之外。
 */
function nodeBorderRadius(node: GraphNode, ux: number, uy: number) {
  const a = node.width / 2 + 2;
  const b = node.height / 2 + 2;
  return (a * b) / Math.sqrt(b * b * ux * ux + a * a * uy * uy);
}

/**
 * 知识图谱画布：用 SVG 渲染概念节点与连线，支持缩放、平移、拖动节点与悬浮高亮。
 *
 * - 点击节点（且未发生拖动）时回调 onSelectConcept 交给外层选中概念。
 * - 滚轮以指针位置为锚点缩放，空白处拖拽平移，「复位」回到自适应全局视图。
 */
export default function KnowledgeGraphCanvas({
  concepts,
  onSelectConcept,
  height = DEFAULT_HEIGHT,
}: {
  concepts: KnowledgeConceptRead[];
  onSelectConcept: (concept: KnowledgeConceptRead) => void;
  height?: number;
}) {
  /** 概念列表变化时重新构图并布局，得到带世界坐标的节点与连线。 */
  const graph = useMemo(() => {
    const data = buildGraph(concepts);
    layoutGraph(data);
    return data;
  }, [concepts]);
  /** 依据当前图与画布高度推导自适应视图，作为初始值与「复位」目标。 */
  const fitView = useMemo(() => computeFitView(graph, height), [graph, height]);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const viewRef = useRef<ViewTransform>(fitView);
  const nodeDragRef = useRef<{
    id: string;
    startX: number;
    startY: number;
    baseX: number;
    baseY: number;
    moved: boolean;
  } | null>(null);
  const panRef = useRef<{ startX: number; startY: number; tx: number; ty: number } | null>(null);
  const [view, setView] = useState<ViewTransform>(fitView);
  const [offsets, setOffsets] = useState<Record<string, { x: number; y: number }>>({});
  const [hoverId, setHoverId] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [isPanning, setIsPanning] = useState(false);

  /** 保持 viewRef 与 view 同步，让指针事件回调总能读到最新视图。 */
  useEffect(() => {
    viewRef.current = view;
  }, [view]);

  /** 图或画布高度变化时重置为自适应视图，并清空拖动偏移与选中、悬浮状态。 */
  useEffect(() => {
    setView(fitView);
    viewRef.current = fitView;
    setOffsets({});
    setSelectedId(null);
    setHoverId(null);
  }, [fitView]);

  /** 把布局坐标与用户拖动产生的偏移合并，得到每个节点最终用于渲染的位置。 */
  const positions = useMemo(() => {
    const map = new Map<string, { x: number; y: number }>();
    graph.nodes.forEach((node) => {
      const offset = offsets[node.id];
      map.set(node.id, { x: node.x + (offset?.x ?? 0), y: node.y + (offset?.y ?? 0) });
    });
    return map;
  }, [graph, offsets]);

  /**
   * 为画布注册滚轮缩放监听。
   *
   * 以 passive: false 绑定以便阻止画布区域的页面滚动，缩放时保持指针下的世界坐标不动。
   */
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return undefined;
    /** 滚轮缩放：把指针坐标换算成世界坐标，缩放后仍保持该点在指针下不动。 */
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const rect = svg.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) return;
      const localX = ((event.clientX - rect.left) / rect.width) * CANVAS_WIDTH;
      const localY = ((event.clientY - rect.top) / rect.height) * height;
      const factor = event.deltaY < 0 ? 1.15 : 1 / 1.15;
      setView((current) => {
        const k = clamp(current.k * factor, 0.2, 4);
        const worldX = (localX - current.tx) / current.k;
        const worldY = (localY - current.ty) / current.k;
        return { k, tx: localX - worldX * k, ty: localY - worldY * k };
      });
    };
    svg.addEventListener('wheel', onWheel, { passive: false });
    return () => svg.removeEventListener('wheel', onWheel);
  }, [height]);

  /** 以画布中心为锚点按比例缩放，缩放系数限制在 0.2–4 之间。 */
  const zoomBy = useCallback(
    (factor: number) => {
      setView((current) => {
        const k = clamp(current.k * factor, 0.2, 4);
        const centerX = CANVAS_WIDTH / 2;
        const centerY = height / 2;
        const worldX = (centerX - current.tx) / current.k;
        const worldY = (centerY - current.ty) / current.k;
        return { k, tx: centerX - worldX * k, ty: centerY - worldY * k };
      });
    },
    [height],
  );

  /** 把视图恢复到自适应全局视图（清空手动缩放与平移）。 */
  const resetView = useCallback(() => {
    setView(fitView);
  }, [fitView]);

  /** 节点上按下主键时记录拖动起点与既有偏移，并捕获指针以持续接收移动事件。 */
  const handleNodePointerDown = useCallback(
    (event: ReactPointerEvent<SVGGElement>, node: GraphNode) => {
      if (event.button !== 0) return;
      event.stopPropagation();
      const offset = offsets[node.id];
      nodeDragRef.current = {
        id: node.id,
        startX: event.clientX,
        startY: event.clientY,
        baseX: offset?.x ?? 0,
        baseY: offset?.y ?? 0,
        moved: false,
      };
      svgRef.current?.setPointerCapture(event.pointerId);
    },
    [offsets],
  );

  /** 画布空白处按下主键时进入平移模式，记录起始指针位置与当前平移量。 */
  const handleBackgroundPointerDown = useCallback((event: ReactPointerEvent<SVGSVGElement>) => {
    if (event.button !== 0) return;
    panRef.current = {
      startX: event.clientX,
      startY: event.clientY,
      tx: viewRef.current.tx,
      ty: viewRef.current.ty,
    };
    svgRef.current?.setPointerCapture(event.pointerId);
    setIsPanning(true);
  }, []);

  /**
   * 指针移动时优先处理节点拖动，否则处理画布平移。
   *
   * - 节点拖动会把屏幕像素位移按当前缩放换算成世界坐标偏移。
   * - 位移超过 CLICK_SLOP_PX 即标记为已移动，抬起时便不再触发选中。
   */
  const handlePointerMove = useCallback(
    (event: ReactPointerEvent<SVGSVGElement>) => {
      const svg = svgRef.current;
      if (!svg) return;
      const rect = svg.getBoundingClientRect();
      const nodeDrag = nodeDragRef.current;
      if (nodeDrag) {
        if (Math.hypot(event.clientX - nodeDrag.startX, event.clientY - nodeDrag.startY) >= CLICK_SLOP_PX) {
          nodeDrag.moved = true;
        }
        const k = viewRef.current.k;
        const dx = ((event.clientX - nodeDrag.startX) * CANVAS_WIDTH) / Math.max(rect.width, 1) / k;
        const dy = ((event.clientY - nodeDrag.startY) * height) / Math.max(rect.height, 1) / k;
        setOffsets((current) => ({
          ...current,
          [nodeDrag.id]: { x: nodeDrag.baseX + dx, y: nodeDrag.baseY + dy },
        }));
        return;
      }
      const pan = panRef.current;
      if (pan) {
        const dx = ((event.clientX - pan.startX) / Math.max(rect.width, 1)) * CANVAS_WIDTH;
        const dy = ((event.clientY - pan.startY) / Math.max(rect.height, 1)) * height;
        setView((current) => ({ ...current, tx: pan.tx + dx, ty: pan.ty + dy }));
      }
    },
    [height],
  );

  /**
   * 指针抬起或取消时收尾本次手势。
   *
   * - 未发生位移的节点点击会选中节点并回调 onSelectConcept。
   * - 随后清空拖动/平移状态并释放指针捕获。
   */
  const handlePointerEnd = useCallback(
    (event: ReactPointerEvent<SVGSVGElement>) => {
      const nodeDrag = nodeDragRef.current;
      if (nodeDrag) {
        const node = graph.nodes.find((item) => item.id === nodeDrag.id);
        if (node && !nodeDrag.moved) {
          setSelectedId(node.id);
          onSelectConcept(node.concept);
        }
        nodeDragRef.current = null;
      }
      panRef.current = null;
      setIsPanning(false);
      if (svgRef.current?.hasPointerCapture(event.pointerId)) {
        svgRef.current.releasePointerCapture(event.pointerId);
      }
    },
    [graph, onSelectConcept],
  );

  /** 悬浮节点自身与邻居节点的 ID 集合，用于高亮相关连线；无悬浮节点时返回 null。 */
  const neighborIds = useMemo(() => {
    if (hoverId === null) return null;
    const ids = new Set<string>([hoverId]);
    graph.edges.forEach((edge) => {
      const sourceId = graph.nodes[edge.source]?.id;
      const targetId = graph.nodes[edge.target]?.id;
      if (sourceId === hoverId && targetId) ids.add(targetId);
      if (targetId === hoverId && sourceId) ids.add(sourceId);
    });
    return ids;
  }, [graph, hoverId]);

  /** 图例项：按 TYPE_ORDER 排列图中实际出现的类型，未登记的类型排在末尾并使用兜底样式。 */
  const legendItems = useMemo(() => {
    const present = new Set(graph.nodes.map((node) => node.concept.concept_type));
    const ordered = TYPE_ORDER.filter((type) => present.has(type));
    present.forEach((type) => {
      if (!TYPE_STYLES[type]) ordered.push(type);
    });
    return ordered.map((type) => ({ type, ...(TYPE_STYLES[type] || FALLBACK_TYPE_STYLE) }));
  }, [graph]);

  if (graph.nodes.length === 0) {
    return <div className="knowledge-graph-empty">暂无知识图谱数据</div>;
  }

  return (
    <div className="knowledge-graph-canvas">
      <svg
        ref={svgRef}
        viewBox={`0 0 ${CANVAS_WIDTH} ${height}`}
        style={{ height }}
        className={isPanning ? 'is-panning' : undefined}
        role="img"
        aria-label="知识图谱画布"
        onPointerDown={handleBackgroundPointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerEnd}
        onPointerCancel={handlePointerEnd}
      >
        <defs>
          <marker
            id="knowledge-graph-arrow-link"
            viewBox="0 0 8 8"
            refX="7"
            refY="4"
            markerWidth="6.5"
            markerHeight="6.5"
            orient="auto-start-reverse"
          >
            <path d="M 0.6 0.9 L 7 4 L 0.6 7.1 z" fill="#c3c9d4" />
          </marker>
          <marker
            id="knowledge-graph-arrow-link-active"
            viewBox="0 0 8 8"
            refX="7"
            refY="4"
            markerWidth="6.5"
            markerHeight="6.5"
            orient="auto-start-reverse"
          >
            <path d="M 0.6 0.9 L 7 4 L 0.6 7.1 z" fill="#9aa5b5" />
          </marker>
          <marker
            id="knowledge-graph-arrow-citation"
            viewBox="0 0 8 8"
            refX="7"
            refY="4"
            markerWidth="6.5"
            markerHeight="6.5"
            orient="auto-start-reverse"
          >
            <path d="M 0.6 0.9 L 7 4 L 0.6 7.1 z" fill="#ddba85" />
          </marker>
          <marker
            id="knowledge-graph-arrow-citation-active"
            viewBox="0 0 8 8"
            refX="7"
            refY="4"
            markerWidth="6.5"
            markerHeight="6.5"
            orient="auto-start-reverse"
          >
            <path d="M 0.6 0.9 L 7 4 L 0.6 7.1 z" fill="#cf9a4a" />
          </marker>
        </defs>
        <g transform={`translate(${view.tx} ${view.ty}) scale(${view.k})`}>
          {graph.edges.map((edge) => {
            const source = graph.nodes[edge.source];
            const target = graph.nodes[edge.target];
            const sourcePos = source ? positions.get(source.id) : undefined;
            const targetPos = target ? positions.get(target.id) : undefined;
            if (!source || !target || !sourcePos || !targetPos) return null;
            const dx = targetPos.x - sourcePos.x;
            const dy = targetPos.y - sourcePos.y;
            const distance = Math.hypot(dx, dy) || 1;
            const ux = dx / distance;
            const uy = dy / distance;
            const direction = edge.source < edge.target ? 1 : -1;
            const bend = direction * clamp(distance * 0.12, 6, 34);
            const controlX = (sourcePos.x + targetPos.x) / 2 - uy * bend;
            const controlY = (sourcePos.y + targetPos.y) / 2 + ux * bend;
            const startTrim = nodeBorderRadius(source, ux, uy) + 3;
            const endTrim = nodeBorderRadius(target, ux, uy) + 8;
            const x1 = sourcePos.x + ux * startTrim;
            const y1 = sourcePos.y + uy * startTrim;
            const x2 = targetPos.x - ux * endTrim;
            const y2 = targetPos.y - uy * endTrim;
            const isCitation = edge.kind === 'citation';
            const isActive = hoverId !== null && (source.id === hoverId || target.id === hoverId);
            const isDimmed = neighborIds !== null && !isActive;
            const markerName = `knowledge-graph-arrow-${isCitation ? 'citation' : 'link'}${isActive ? '-active' : ''}`;
            return (
              <path
                key={`${source.id}->${target.id}:${edge.kind}`}
                className={`knowledge-graph-edge${isCitation ? ' is-citation' : ''}${isActive ? ' is-active' : ''}${isDimmed ? ' is-dimmed' : ''}`}
                d={`M ${x1} ${y1} Q ${controlX} ${controlY} ${x2} ${y2}`}
                markerEnd={`url(#${markerName})`}
              />
            );
          })}
          {graph.nodes.map((node) => {
            const position = positions.get(node.id);
            if (!position) return null;
            const isDimmed = neighborIds !== null && !neighborIds.has(node.id);
            const dotX = -node.width / 2 + 13;
            return (
              <g
                key={node.id}
                className={`knowledge-graph-node${isDimmed ? ' is-dimmed' : ''}${selectedId === node.id ? ' is-selected' : ''}`}
                transform={`translate(${position.x} ${position.y})`}
                onPointerDown={(event) => handleNodePointerDown(event, node)}
                onPointerEnter={() => setHoverId(node.id)}
                onPointerLeave={() => setHoverId(null)}
              >
                <g className="knowledge-graph-node-body">
                  <rect
                    className={`knowledge-graph-node-card${node.isSource ? ' is-source' : ''}`}
                    x={-node.width / 2}
                    y={-node.height / 2}
                    width={node.width}
                    height={node.height}
                    rx={node.height / 2}
                  />
                  <circle className="knowledge-graph-node-dot" cx={dotX} cy={0} r={3.5} fill={node.color} />
                  <text
                    className={`knowledge-graph-node-title${node.isSource ? ' is-source' : ''}`}
                    x={dotX + 10}
                    y={0}
                    dominantBaseline="central"
                  >
                    {node.label}
                  </text>
                </g>
              </g>
            );
          })}
        </g>
      </svg>
      <div className="knowledge-graph-legend">
        {legendItems.map((item) => (
          <span key={item.type}>
            <i style={{ background: item.color }} />
            {item.label}
          </span>
        ))}
      </div>
      <div className="knowledge-graph-zoom-controls">
        <button type="button" className="knowledge-graph-zoom-btn" aria-label="放大" onClick={() => zoomBy(1.25)}>
          +
        </button>
        <button type="button" className="knowledge-graph-zoom-btn" aria-label="缩小" onClick={() => zoomBy(1 / 1.25)}>
          −
        </button>
        <button type="button" className="knowledge-graph-zoom-btn is-wide" onClick={resetView}>
          复位
        </button>
      </div>
    </div>
  );
}
