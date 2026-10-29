import {
  createContext,
  useCallback,
  useContext,
  useLayoutEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

import englishCatalog from './en.json';

export type AppLocale = 'zh-CN' | 'en-US';

type I18nContextValue = {
  locale: AppLocale;
  setLocale: (locale: AppLocale) => void;
  toggleLocale: () => void;
  t: (source: string, values?: Record<string | number, string | number>) => string;
};

type CompiledTemplate = {
  pattern: RegExp;
  placeholderOrder: number[];
  target: string;
};

const STORAGE_KEY = 'staffdeck_locale';
const CATALOG = englishCatalog as Record<string, string>;
const SAFE_ATTRIBUTES = ['placeholder', 'aria-label', 'title', 'alt', 'data-placeholder'] as const;
const TEMPLATE_TOKEN = /\{(\d+)\}/g;
const textOriginals = new WeakMap<Text, string>();
const attributeOriginals = new WeakMap<Element, Map<string, string>>();
const translationCache = new Map<string, string | null>();

/** 读取初始语言：SSR 环境固定 zh-CN，浏览器环境仅识别已持久化的 en-US。 */
function initialLocale(): AppLocale {
  if (typeof window === 'undefined') return 'zh-CN';
  return window.localStorage.getItem(STORAGE_KEY) === 'en-US' ? 'en-US' : 'zh-CN';
}

let currentLocale: AppLocale = initialLocale();

/** 还原英文文案源中被 HTML 转义的实体（&gt;、&lt;、&amp; 等）。 */
function decodeSourceEntities(value: string): string {
  return value
    .replace(/&gt;/g, '>')
    .replace(/&lt;/g, '<')
    .replace(/&amp;/g, '&')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'");
}

/** 转义正则元字符，供把带占位符的文案编译成正则模板时使用。 */
function escapePattern(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

const exactCatalog = new Map<string, string>();
const compiledTemplates: CompiledTemplate[] = [];

for (const [rawSource, target] of Object.entries(CATALOG)) {
  const sources = new Set([rawSource, decodeSourceEntities(rawSource)]);
  for (const source of sources) {
    if (!source) continue;
    exactCatalog.set(source, target);
    if (!TEMPLATE_TOKEN.test(source)) {
      TEMPLATE_TOKEN.lastIndex = 0;
      continue;
    }
    TEMPLATE_TOKEN.lastIndex = 0;
    const placeholderOrder: number[] = [];
    let cursor = 0;
    let pattern = '^';
    for (const match of source.matchAll(TEMPLATE_TOKEN)) {
      pattern += escapePattern(source.slice(cursor, match.index));
      pattern += '([\\s\\S]*?)';
      placeholderOrder.push(Number(match[1]));
      cursor = (match.index || 0) + match[0].length;
    }
    pattern += `${escapePattern(source.slice(cursor))}$`;
    compiledTemplates.push({ pattern: new RegExp(pattern), placeholderOrder, target });
  }
}

compiledTemplates.sort((left, right) => right.pattern.source.length - left.pattern.source.length);

/** 用 values 替换 target 中的 {n} 占位符，缺少对应值时保留原占位符。 */
function interpolate(target: string, values: Record<string | number, string | number>): string {
  return target.replace(TEMPLATE_TOKEN, (_, key: string) => String(values[key] ?? `{${key}}`));
}

/**
 * 把中文源文案翻译成英文，未命中时返回 null。
 *
 * - 先查精确目录，再按正则模板长度从长到短匹配带占位符的文案；
 * - 命中与未命中的结果都会写入 translationCache，避免重复计算。
 */
function translateCore(source: string): string | null {
  if (translationCache.has(source)) return translationCache.get(source) || null;
  const exact = exactCatalog.get(source);
  if (exact) {
    translationCache.set(source, exact);
    return exact;
  }
  for (const template of compiledTemplates) {
    const match = source.match(template.pattern);
    if (!match) continue;
    const values: Record<number, string> = {};
    template.placeholderOrder.forEach((placeholder, index) => {
      values[placeholder] = match[index + 1] || '';
    });
    const translated = interpolate(template.target, values);
    translationCache.set(source, translated);
    return translated;
  }
  translationCache.set(source, null);
  return null;
}

/** 拆分字符串的首尾空白与核心文本，供翻译后保持原有排版。 */
function splitWhitespace(value: string): { leading: string; core: string; trailing: string } {
  const leading = value.match(/^\s*/)?.[0] || '';
  const trailing = value.match(/\s*$/)?.[0] || '';
  return {
    leading,
    core: value.slice(leading.length, value.length - trailing.length),
    trailing,
  };
}

/** 翻译核心文本并原样拼回首尾空白；没有译文时返回原值。 */
function translatePreservingWhitespace(value: string): string {
  const { leading, core, trailing } = splitWhitespace(value);
  if (!core) return value;
  const translated = translateCore(core);
  return translated ? `${leading}${translated}${trailing}` : value;
}

/** 判断节点是否位于代码块、输入框等不应被自动翻译的区域内。 */
function shouldIgnore(element: Element | null): boolean {
  return Boolean(
    element?.closest(
      '[data-i18n-ignore], code, pre, script, style, textarea, [contenteditable="true"]',
    ),
  );
}

/** 判断元素的属性是否位于不参与属性翻译的容器内。 */
function shouldIgnoreAttribute(element: Element): boolean {
  return Boolean(element.closest('[data-i18n-ignore], code, pre, script, style'));
}

/**
 * 按目标语言就地改写单个文本节点的内容。
 *
 * - 中文模式下仅把已翻译文本还原为原文，避免覆盖运行时新增的内容；
 * - 英文模式下复用已记录的原文，DOM 内容变化时重新识别来源文本。
 */
function localizeTextNode(node: Text, locale: AppLocale): void {
  if (shouldIgnore(node.parentElement)) return;
  const current = node.data;
  const previousSource = textOriginals.get(node);
  const previousTarget = previousSource ? translatePreservingWhitespace(previousSource) : '';

  if (locale === 'zh-CN') {
    if (previousSource && current === previousTarget && current !== previousSource) {
      node.data = previousSource;
    } else if (!previousSource || (current !== previousSource && current !== previousTarget)) {
      textOriginals.set(node, current);
    }
    return;
  }

  let source = previousSource;
  if (!source || (current !== source && current !== previousTarget)) {
    source = current;
    textOriginals.set(node, source);
  }
  const target = translatePreservingWhitespace(source);
  if (target !== current) node.data = target;
}

/**
 * 按目标语言就地改写元素的指定属性值。
 *
 * - 与文本节点一致：中文模式下还原原文，英文模式下翻译后写回；
 * - 原始属性值缓存在 attributeOriginals 中，切换语言时用于回退。
 */
function localizeAttribute(element: Element, name: string, locale: AppLocale): void {
  if (shouldIgnoreAttribute(element)) return;
  const current = element.getAttribute(name);
  if (current == null) return;
  let originals = attributeOriginals.get(element);
  if (!originals) {
    originals = new Map();
    attributeOriginals.set(element, originals);
  }
  const previousSource = originals.get(name);
  const previousTarget = previousSource ? translatePreservingWhitespace(previousSource) : '';

  if (locale === 'zh-CN') {
    if (previousSource && current === previousTarget && current !== previousSource) {
      element.setAttribute(name, previousSource);
    } else if (!previousSource || (current !== previousSource && current !== previousTarget)) {
      originals.set(name, current);
    }
    return;
  }

  let source = previousSource;
  if (!source || (current !== source && current !== previousTarget)) {
    source = current;
    originals.set(name, source);
  }
  const target = translatePreservingWhitespace(source);
  if (target !== current) element.setAttribute(name, target);
}

/** 依次本地化元素上允许翻译的属性（SAFE_ATTRIBUTES）。 */
function localizeElement(element: Element, locale: AppLocale): void {
  for (const name of SAFE_ATTRIBUTES) localizeAttribute(element, name, locale);
}

/**
 * 递归本地化一个 DOM 子树中的文本节点与可翻译属性。
 *
 * - 文本节点直接处理；元素与文档节点通过 TreeWalker 遍历全部后代；
 * - 其他节点类型直接返回，避免影响注释等无关节点。
 */
function localizeSubtree(root: Node, locale: AppLocale): void {
  if (root.nodeType === Node.TEXT_NODE) {
    localizeTextNode(root as Text, locale);
    return;
  }
  if (root.nodeType !== Node.ELEMENT_NODE && root.nodeType !== Node.DOCUMENT_NODE) return;
  if (root.nodeType === Node.ELEMENT_NODE) localizeElement(root as Element, locale);
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
  let current = walker.nextNode();
  while (current) {
    if (current.nodeType === Node.TEXT_NODE) localizeTextNode(current as Text, locale);
    else localizeElement(current as Element, locale);
    current = walker.nextNode();
  }
}

const I18nContext = createContext<I18nContextValue | null>(null);

/** 返回当前生效的语言，读取的是模块级 currentLocale。 */
export function getStoredLocale(): AppLocale {
  return currentLocale;
}

/** 返回用于日期时间格式化的语言标识，未启用英文时统一为 zh-CN。 */
export function getDateLocale(): string {
  return currentLocale === 'en-US' ? 'en-US' : 'zh-CN';
}

/**
 * 全局国际化 Provider：维护语言状态并向子树注入翻译能力。
 *
 * - 切换语言时写入 localStorage、更新 html lang 与模块级 currentLocale；
 * - 通过 MutationObserver 监听 DOM 变化，自动翻译后续渲染出的文案。
 */
export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<AppLocale>(initialLocale);

  /** 持久化并切换语言，同时更新 html lang 与模块级 currentLocale。 */
  const setLocale = useCallback((nextLocale: AppLocale) => {
    currentLocale = nextLocale;
    window.localStorage.setItem(STORAGE_KEY, nextLocale);
    document.documentElement.lang = nextLocale;
    setLocaleState(nextLocale);
  }, []);

  /** 在中文与英文之间切换当前语言。 */
  const toggleLocale = useCallback(() => {
    setLocale(locale === 'zh-CN' ? 'en-US' : 'zh-CN');
  }, [locale, setLocale]);

  /** 翻译函数：中文直接插值，英文先查目录命中后再插值，未命中回退原文。 */
  const t = useCallback(
    (source: string, values: Record<string | number, string | number> = {}) => {
      if (locale === 'zh-CN') return interpolate(source, values);
      return interpolate(translateCore(source) || source, values);
    },
    [locale],
  );

  useLayoutEffect(() => {
    currentLocale = locale;
    document.documentElement.lang = locale;
    const root = document.documentElement;
    localizeSubtree(root, locale);

    let scheduled = false;
    let disposed = false;
    const pending = new Set<Node>();
    /** 在同一微任务内批量消费待本地化的节点，Provider 已卸载时直接跳过。 */
    const flush = () => {
      scheduled = false;
      if (disposed) return;
      for (const node of pending) localizeSubtree(node, locale);
      pending.clear();
    };
    /** 收集待本地化的节点并按微任务合并处理，避免每次 DOM 变更都立即扫描。 */
    const enqueue = (node: Node) => {
      pending.add(node);
      if (scheduled) return;
      scheduled = true;
      window.queueMicrotask(flush);
    };
    const observer = new MutationObserver((mutations) => {
      for (const mutation of mutations) {
        if (mutation.type === 'characterData') enqueue(mutation.target);
        else if (mutation.type === 'attributes') enqueue(mutation.target);
        else mutation.addedNodes.forEach(enqueue);
      }
    });
    observer.observe(root, {
      subtree: true,
      childList: true,
      characterData: true,
      attributes: true,
      attributeFilter: [...SAFE_ATTRIBUTES],
    });
    return () => {
      disposed = true;
      observer.disconnect();
      pending.clear();
    };
  }, [locale]);

  const value = useMemo<I18nContextValue>(
    () => ({ locale, setLocale, toggleLocale, t }),
    [locale, setLocale, t, toggleLocale],
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

/** 读取国际化 Context，必须在 I18nProvider 内部调用，否则抛出错误。 */
export function useI18n(): I18nContextValue {
  const context = useContext(I18nContext);
  if (!context) throw new Error('useI18n must be used inside I18nProvider');
  return context;
}
