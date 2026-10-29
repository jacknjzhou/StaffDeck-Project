import { getEnterpriseAuthSession } from '../auth';

/** 解析 API 基础地址：优先取环境变量，未配置时使用同源相对路径。 */
const resolveApiBase = () => {
  if (import.meta.env.VITE_API_BASE_URL) {
    return import.meta.env.VITE_API_BASE_URL;
  }

  return '';
};

const API_BASE = resolveApiBase();

export const TENANT_ID = import.meta.env.VITE_TENANT_ID || 'tenant_demo';
export const SHOW_DEBUG = import.meta.env.VITE_SHOW_DEBUG === 'true';

/** 统一 API 错误类型，保留 HTTP 状态码、原始响应体与稳定错误码。 */
export class ApiError extends Error {
  status: number;
  body: string;
  code?: string;

  /**
   * 解析错误响应体并构造错误实例。
   *
   * 文案优先取响应体 message，其次 HTTP 状态文本，最后回退 `HTTP ${status}`。
   */
  constructor(status: number, body: string, statusText: string) {
    const parsed = parseErrorPayload(body);
    super(parsed.message || statusText || `HTTP ${status}`);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
    this.code = parsed.code;
  }
}

/** 判断错误是否为未授权（401）的 ApiError。 */
export function isAuthError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

/**
 * 发送 JSON 请求并解析响应体。
 *
 * 自动附加 Content-Type 与鉴权头；非 2xx 响应抛出 ApiError，空响应体返回空对象。
 */
async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...authHeader(),
      ...(options.headers || {}),
    },
    ...options,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new ApiError(response.status, text, response.statusText);
  }
  const text = await response.text();
  return (text ? JSON.parse(text) : {}) as T;
}

/**
 * 以 keepalive 方式发送 POST 请求，适用于页面卸载后的上报场景。
 *
 * body 为 undefined 时不携带请求体，错误处理与 request 保持一致。
 */
async function keepalivePost<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    keepalive: true,
    headers: {
      'Content-Type': 'application/json',
      ...authHeader(),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    const text = await response.text();
    throw new ApiError(response.status, text, response.statusText);
  }
  const text = await response.text();
  return (text ? JSON.parse(text) : {}) as T;
}

/** 读取当前登录会话，生成 Bearer 鉴权请求头；无会话时返回空对象。 */
function authHeader(): Record<string, string> {
  const session = getEnterpriseAuthSession();
  return session?.token ? { Authorization: `Bearer ${session.token}` } : {};
}

export const api = {
  /** 发送 GET 请求并返回解析后的 JSON。 */
  get: <T>(path: string) => request<T>(path),
  /** 发送带 AbortSignal 的 GET 请求，可用于取消进行中的加载。 */
  getWithSignal: <T>(path: string, signal: AbortSignal) => request<T>(path, { signal }),
  /** 发送 POST 请求；body 为 undefined 时不携带请求体。 */
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) }),
  /** 发送带 AbortSignal 的 POST 请求。 */
  postWithSignal: <T>(path: string, body: unknown, signal?: AbortSignal) =>
    request<T>(path, { method: 'POST', body: JSON.stringify(body), signal }),
  /** 发送 keepalive POST 请求，用于页面关闭后仍需送达的调用。 */
  postKeepalive: <T>(path: string, body?: unknown) => keepalivePost<T>(path, body),
  /** 发送 PUT 请求并返回解析后的 JSON。 */
  put: <T>(path: string, body: unknown) => request<T>(path, { method: 'PUT', body: JSON.stringify(body) }),
  /** 发送 PATCH 请求并返回解析后的 JSON。 */
  patch: <T>(path: string, body: unknown) => request<T>(path, { method: 'PATCH', body: JSON.stringify(body) }),
  /** 发送 DELETE 请求并返回解析后的 JSON。 */
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
  /** 以 Blob 形式下载 GET 响应内容，常用于附件与导出文件。 */
  blob: async (path: string) => {
    const response = await fetch(`${API_BASE}${path}`, {
      headers: {
        ...authHeader(),
      },
    });
    if (!response.ok) {
      const text = await response.text();
      throw new ApiError(response.status, text, response.statusText);
    }
    return response.blob();
  },
  /** 以 Blob 形式下载 POST 响应内容。 */
  postBlob: async (path: string, body: unknown) => {
    const response = await fetch(`${API_BASE}${path}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...authHeader(),
      },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      const text = await response.text();
      throw new ApiError(response.status, text, response.statusText);
    }
    return response.blob();
  },
  /** 以 multipart/form-data 提交表单并解析 JSON 响应。 */
  postForm: async <T>(path: string, form: FormData) => {
    const response = await fetch(`${API_BASE}${path}`, {
      method: 'POST',
      headers: { ...authHeader() },
      body: form,
    });
    if (!response.ok) {
      const text = await response.text();
      throw new ApiError(response.status, text, response.statusText);
    }
    return (await response.json()) as T;
  },
};

/**
 * 上传聊天附件，以 multipart/form-data 提交到聊天附件接口。
 *
 * 支持传入 AbortSignal 取消上传；非 2xx 响应抛出 ApiError。
 */
export async function uploadChatAttachments<T>(
  tenantId: string,
  files: File[],
  signal?: AbortSignal,
): Promise<T> {
  const form = new FormData();
  files.forEach((file) => form.append('files', file));
  const response = await fetch(`${API_BASE}/api/chat/attachments?tenant_id=${encodeURIComponent(tenantId)}`, {
    method: 'POST',
    headers: { ...authHeader() },
    body: form,
    signal,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new ApiError(response.status, text, response.statusText);
  }
  return response.json() as Promise<T>;
}

/** 发起一轮聊天流式请求，按 SSE 事件逐个回调给调用方。 */
export async function streamChatTurn(
  body: Record<string, unknown>,
  onEvent: (item: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  return streamPost('/api/chat/stream', body, onEvent, signal);
}

export type StreamEvent = {
  event: string;
  data: Record<string, unknown>;
};

/**
 * 通过 POST 建立 SSE 流并逐块解析事件。
 *
 * 以空行切分事件块，末尾残留内容在流结束后补发；浏览器不支持流式响应时抛出错误。
 */
export async function streamPost(
  path: string,
  body: Record<string, unknown>,
  onEvent: (item: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeader() },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new ApiError(response.status, text, response.statusText);
  }
  if (!response.body) {
    throw new Error('当前浏览器不支持流式响应');
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const blocks = buffer.split('\n\n');
    buffer = blocks.pop() || '';
    blocks.forEach((block) => {
      const parsed = parseSseBlock(block);
      if (parsed) onEvent(parsed);
    });
  }

  buffer += decoder.decode();
  const parsed = parseSseBlock(buffer);
  if (parsed) onEvent(parsed);
}

/** 通过 GET 建立 SSE 流，事件解析与派发逻辑同 streamPost。 */
export async function streamGet(
  path: string,
  onEvent: (item: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_BASE}${path}`, { headers: { ...authHeader() }, signal });
  if (!response.ok) {
    const text = await response.text();
    throw new ApiError(response.status, text, response.statusText);
  }
  if (!response.body) {
    throw new Error('当前浏览器不支持流式响应');
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const blocks = buffer.split('\n\n');
    buffer = blocks.pop() || '';
    blocks.forEach((block) => {
      const parsed = parseSseBlock(block);
      if (parsed) onEvent(parsed);
    });
  }

  buffer += decoder.decode();
  const parsed = parseSseBlock(buffer);
  if (parsed) onEvent(parsed);
}

/**
 * 解析单个 SSE 事件块，提取 event 名称与 data 负载。
 *
 * 缺少 event 或 data 行时返回 null；data 非合法 JSON 时回退为 `{ raw }`。
 */
function parseSseBlock(block: string): StreamEvent | null {
  const lines = block.split('\n').map((line) => line.trimEnd());
  const eventLine = lines.find((line) => line.startsWith('event:'));
  const dataLines = lines.filter((line) => line.startsWith('data:'));
  if (!eventLine || dataLines.length === 0) return null;
  const event = eventLine.replace(/^event:\s*/, '');
  const rawData = dataLines.map((line) => line.replace(/^data:\s*/, '')).join('\n');
  try {
    return { event, data: JSON.parse(rawData) as Record<string, unknown> };
  } catch {
    return { event, data: { raw: rawData } };
  }
}

type ParsedApiError = {
  message: string;
  code?: string;
};

const STABLE_ERROR_CODE_PATTERN = /^[A-Z][A-Z0-9_]+$/;

/** 校验并返回符合大写常量命名规则的稳定错误码，否则返回 undefined。 */
function stableErrorCode(value: unknown): string | undefined {
  return typeof value === 'string' && STABLE_ERROR_CODE_PATTERN.test(value)
    ? value
    : undefined;
}

/**
 * 从错误响应体解析可展示的错误文案与错误码。
 *
 * 兼容字符串 detail/message/error、校验错误数组与嵌套对象，
 * 解析失败或为空时直接使用原始文本。
 */
function parseErrorPayload(text: string): ParsedApiError {
  if (!text) return { message: '' };
  try {
    const payload = JSON.parse(text) as {
      code?: unknown;
      detail?: unknown;
      message?: unknown;
      error?: unknown;
    };
    const detail = payload.detail ?? payload.message ?? payload.error;
    const topLevelCode = stableErrorCode(payload.code);
    if (typeof detail === 'string') {
      return { message: detail, code: topLevelCode ?? stableErrorCode(detail) };
    }
    if (Array.isArray(detail)) {
      return {
        message: detail
          .map(formatValidationDetail)
          .filter(Boolean)
          .join('；'),
        code: topLevelCode,
      };
    }
    if (detail && typeof detail === 'object') {
      const structured = detail as { code?: unknown; message?: unknown; detail?: unknown };
      const message = typeof structured.message === 'string'
        ? structured.message
        : typeof structured.detail === 'string'
          ? structured.detail
          : '';
      const code = stableErrorCode(structured.code) ?? topLevelCode;
      if (message || code) return { message: message || String(code), code };
    }
  } catch {
    return { message: text };
  }
  return { message: text };
}

/** 将 FastAPI 校验错误项格式化为 `字段路径: 错误信息` 文案。 */
function formatValidationDetail(item: unknown): string {
  if (typeof item === 'string') return item;
  if (!item || typeof item !== 'object') return '';

  const detail = item as { loc?: unknown; msg?: unknown };
  const message = typeof detail.msg === 'string' ? detail.msg : '';
  const location = Array.isArray(detail.loc)
    ? detail.loc.map((part) => String(part)).filter(Boolean).join('.')
    : '';

  if (location && message) return `${location}: ${message}`;
  return message;
}
