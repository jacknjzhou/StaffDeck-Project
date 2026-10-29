import { useEffect, useRef, useState } from 'react';
import { api, TENANT_ID } from '../api/client';
import { getEnterpriseAuthSession } from '../auth';

type Receipt = { success?: boolean; data?: { detached?: boolean; task_id?: string }; error?: unknown };
type Task = { id: string; status: string; poll_attempts?: number; result?: unknown; error?: unknown };
const terminal = new Set(['completed', 'failed', 'cancelled', 'expired', 'outcome_unknown']);
/** 生成客户端请求标识；非 HTTPS 环境缺少 randomUUID 时退化为时间戳加随机串。 */
function requestId() {
  // randomUUID is unavailable on non-HTTPS deployments such as port 10086.
  return globalThis.crypto?.randomUUID?.()
    || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
}
/** 将任务状态写入 sessionStorage，值为空时删除；私密模式或配额异常时静默忽略。 */
function persist(key: string, value: unknown) {
  try {
    if (value) sessionStorage.setItem(key, JSON.stringify(value));
    else sessionStorage.removeItem(key);
  } catch { /* Private mode/quota must not wedge the call button. */ }
}

/**
 * 外部工具试运行 Hook：提交调用、轮询任务状态，并把任务 ID 持久化以支持刷新恢复。
 *
 * - 首次挂载或 toolId/租户/用户变化时重置状态并按会话存储恢复未完成任务
 * - 卸载时递增代际并中止在途请求，避免过期响应写回
 *
 * @param toolId 工具 ID，参与请求路径与本地存储键
 * @param agentQuery 附加查询串，非空时去掉开头 `?` 后拼到提交地址
 * @param pollSeconds 轮询间隔秒数，默认 5
 * @returns result/status/taskId/busy/error 状态，以及 submit、retryStatus 与参数锁定标记
 */
export function useToolTest(toolId: string, agentQuery: string, pollSeconds = 5) {
  const key = `staffdeck-tool-test:${TENANT_ID}:${getEnterpriseAuthSession()?.user.id || ''}:${toolId}`;
  const [result, setResult] = useState('');
  const [status, setStatus] = useState('等待调用');
  const [taskId, setTaskId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const generation = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  const controller = useRef<AbortController>();
  const inFlight = useRef(false);
  const submission = useRef<{ id: string; arguments: Record<string, unknown> }>();
  const labels: Record<string, string> = {
    queued: '本地排队中', submitting: '正在提交', accepted: '外部已受理', working: '执行中',
    completed: '已完成', failed: '失败', cancelled: '已取消', expired: '已超时', outcome_unknown: '提交结果待核实',
  };

  /** 将当前任务 ID 写入或清除会话存储，供刷新后恢复轮询。 */
  function save(id: string | null) {
    persist(key, id ? { taskId: id } : null);
  }

  /**
   * 查询一次任务状态，并按结果决定继续轮询或结束；单次请求 30 秒超时。
   *
   * 仅当代际版本匹配时才写回状态；终态为 outcome_unknown 时保留任务 ID，
   * 避免用户直接一键重复提交。
   *
   * @param id 任务 ID
   * @param version 代际版本，用于丢弃过期响应
   */
  async function poll(id: string, version: number) {
    if (version !== generation.current) return;
    const requestController = new AbortController();
    controller.current = requestController;
    const timeout = setTimeout(() => requestController.abort(), 30000);
    try {
      const task = await api.getWithSignal<Task>(
        `/api/enterprise/external-business-tasks/${encodeURIComponent(id)}?tenant_id=${TENANT_ID}&tool_id=${encodeURIComponent(toolId)}`,
        requestController.signal,
      );
      if (version !== generation.current) return;
      setResult(JSON.stringify(task, null, 2));
      setError('');
      setStatus(labels[task.status] || task.status);
      if (terminal.has(task.status)) {
        // Unknown outcome must not enable a one-click re-submission.
        if (task.status !== 'outcome_unknown') {
          setTaskId(null);
          save(null);
        }
        setBusy(false);
      } else {
        timer.current = setTimeout(() => void poll(id, version), Math.max(1, pollSeconds) * 1000);
      }
    } catch (e) {
      if (version !== generation.current) return;
      setError(`状态查询失败，任务不会重新提交：${e instanceof Error ? e.message : String(e)}`);
      setStatus('状态查询中断');
      setBusy(false);
    } finally {
      clearTimeout(timeout);
    }
  }

  useEffect(() => {
    const version = ++generation.current;
    inFlight.current = false;
    submission.current = undefined;
    setResult(''); setStatus('等待调用'); setError(''); setBusy(false); setTaskId(null);
    try {
      const saved = JSON.parse(sessionStorage.getItem(key) || 'null');
      if (typeof saved?.taskId === 'string') {
        setTaskId(saved.taskId);
        setBusy(true);
        void poll(saved.taskId, version);
      } else if (saved?.submission?.id && saved?.submission?.arguments) {
        submission.current = saved.submission;
        setStatus('提交结果待确认');
        setError('上次提交尚未确认。重试将使用相同请求标识和原参数，不会新建重复任务。');
      }
    } catch { persist(key, null); }
    return () => {
      ++generation.current;
      clearTimeout(timer.current);
      controller.current?.abort();
    };
  }, [key]);

  /**
   * 提交一次工具调用；提交期间锁定参数，失败时保留请求标识以便原样重试。
   *
   * 若返回 detached 回执则转入轮询，否则直接结束并释放 busy。
   *
   * @param args 工具调用参数
   */
  async function submit(args: Record<string, unknown>) {
    if (inFlight.current || taskId) return;
    inFlight.current = true;
    const version = generation.current;
    submission.current ||= { id: requestId(), arguments: args };
    persist(key, { submission: submission.current });
    setBusy(true); setError(''); setStatus('正在提交');
    const requestController = new AbortController();
    controller.current = requestController;
    const timeout = setTimeout(() => requestController.abort(), 30000);
    try {
      const receipt = await api.postWithSignal<Receipt>(
        `/api/enterprise/tools/${toolId}/test${agentQuery ? `?${agentQuery.slice(1)}` : ''}`,
        { tenant_id: TENANT_ID, arguments: submission.current.arguments, client_request_id: submission.current.id },
        requestController.signal,
      );
      if (version !== generation.current) return;
      setResult(JSON.stringify(receipt, null, 2));
      submission.current = undefined;
      const id = receipt.data?.detached ? receipt.data.task_id : undefined;
      if (id) {
        save(id); setTaskId(id); setStatus('本地排队中');
        clearTimeout(timeout);
        void poll(id, version);
      } else {
        save(null); setStatus(receipt.success === false ? '调用失败' : '已返回'); setBusy(false);
      }
    } catch (e) {
      if (version !== generation.current) return;
      setError(`提交结果未确认；重试会沿用原参数和请求标识：${e instanceof Error ? e.message : String(e)}`);
      setStatus('提交结果待确认'); setBusy(false);
    } finally {
      clearTimeout(timeout);
      if (version === generation.current) inFlight.current = false;
    }
  }

  /** 手动重试一次状态查询；当前没有任务或正在忙时不做任何处理。 */
  function retryStatus() {
    if (!taskId || busy) return;
    setBusy(true); setError('');
    void poll(taskId, generation.current);
  }
  return { result, status, taskId, busy, error, submit, retryStatus,
    parametersLocked: busy || Boolean(taskId) || Boolean(submission.current) };
}
