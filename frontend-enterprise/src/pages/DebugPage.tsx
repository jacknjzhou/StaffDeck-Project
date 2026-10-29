import { SendOutlined } from '../icons';
import { useState } from 'react';
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Input,
  notify,
} from '@/components/ui';
import { api, TENANT_ID } from '../api/client';
import type { ChatTurnResponse } from '../types';

type ChatMessage = { role: 'user' | 'assistant'; content: string };

/** Agent 调试页面：手动发起单轮对话，并在右侧查看最近一轮的 trace 快照。 */
export default function DebugPage() {
  const [sessionId, setSessionId] = useState('');
  const [input, setInput] = useState('');
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [lastTurn, setLastTurn] = useState<ChatTurnResponse | null>(null);
  const [loading, setLoading] = useState(false);

  /**
   * 发送一条调试消息，并把用户消息与助手回复追加到本地记录。
   *
   * 输入为空时直接返回；请求中置 loading，成功后复用返回的 session_id 并更新 trace，
   * 失败弹出错误提示，最后在 finally 中恢复 loading。
   */
  async function send() {
    if (!input.trim()) return;
    const userText = input;
    setInput('');
    setMessages((items) => [...items, { role: 'user', content: userText }]);
    setLoading(true);
    try {
      const result = await api.post<ChatTurnResponse>('/api/chat/turn', {
        tenant_id: TENANT_ID,
        session_id: sessionId || undefined,
        user_id: 'enterprise_debugger',
        message: userText,
        channel: 'enterprise_debug',
        debug: true,
      });
      setSessionId(result.session_id);
      setLastTurn(result);
      setMessages((items) => [...items, { role: 'assistant', content: result.reply }]);
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '发送失败');
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <div className="page-title">
        <h3>Agent 调试</h3>
        <Input
          className="page-field w-[240px]"
          value={sessionId}
          onChange={(event) => setSessionId(event.target.value)}
          placeholder="Session ID"
        />
      </div>
      <div className="grid-2">
        <Card>
          <CardContent>
            <div className="chat-panel">
              <div className="messages">
                {messages.map((item, index) => (
                  <div key={`${item.role}-${index}`} className={`message-row ${item.role}`}>
                    <div className="bubble">{item.content}</div>
                  </div>
                ))}
              </div>
              <div className="flex gap-[8px]">
                <Input
                  value={input}
                  onChange={(event) => setInput(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' && !event.nativeEvent.isComposing) {
                      event.preventDefault();
                      void send();
                    }
                  }}
                  placeholder="输入调试消息"
                />
                <Button disabled={loading} onClick={() => void send()}>
                  <SendOutlined />
                  发送
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Trace Snapshot</CardTitle>
          </CardHeader>
          <CardContent>
            <Accordion type="multiple" defaultValue={['router', 'session']}>
              <AccordionItem value="router">
                <AccordionTrigger>Router Decision</AccordionTrigger>
                <AccordionContent><pre>{JSON.stringify(lastTurn?.router_decision, null, 2)}</pre></AccordionContent>
              </AccordionItem>
              <AccordionItem value="step">
                <AccordionTrigger>Step Agent</AccordionTrigger>
                <AccordionContent><pre>{JSON.stringify(lastTurn?.step_result, null, 2)}</pre></AccordionContent>
              </AccordionItem>
              <AccordionItem value="tool">
                <AccordionTrigger>Tool Result</AccordionTrigger>
                <AccordionContent><pre>{JSON.stringify(lastTurn?.tool_result, null, 2)}</pre></AccordionContent>
              </AccordionItem>
              <AccordionItem value="session">
                <AccordionTrigger>Session State</AccordionTrigger>
                <AccordionContent><pre>{JSON.stringify(lastTurn?.session_state, null, 2)}</pre></AccordionContent>
              </AccordionItem>
            </Accordion>
          </CardContent>
        </Card>
      </div>
    </>
  );
}
