import {
  ApiOutlined,
  ArrowLeftOutlined,
  BranchesOutlined,
  CheckCircleOutlined,
  CheckOutlined,
  CodeOutlined,
  CloseOutlined,
  CloseCircleOutlined,
  DeleteOutlined,
  DownOutlined,
  FileTextOutlined,
  InfoCircleOutlined,
  LoadingOutlined,
  PlusOutlined,
  RightOutlined,
  SaveOutlined,
  SendOutlined,
  StopOutlined,
  UploadOutlined,
  WarningOutlined,
} from '../icons';
import { Maximize2, Minimize2, PanelLeftClose, PanelLeftOpen, PanelRightClose, PanelRightOpen } from 'lucide-react';
import {
  useEffect,
  useCallback,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEventHandler,
  type ClipboardEvent,
  type CSSProperties,
  type DragEvent,
  type HTMLAttributes,
  type HTMLInputTypeAttribute,
  type KeyboardEvent,
  type MouseEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
  type RefObject,
  type WheelEvent as ReactWheelEvent,
} from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  Checkbox,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogTitle,
  Input,
  Popover,
  PopoverContent,
  PopoverTrigger,
  Select as UISelect,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Textarea,
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui';
import { Button as UIButton } from '@/components/ui/button';
import { notify } from '@/components/ui/app-toast';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import AppHeader from '@/components/AppHeader';
import {
  CapabilityScopeBadge,
  CapabilityScopeControl,
  normalizeCapabilityScope,
} from '@/components/CapabilityScopeControl';
import { ModelConfigDropdown } from '@/components/ModelConfigDropdown';
import { cn } from '@/lib/utils';
import { isTeamScope, readEmployeeScope } from '@/lib/agent-scope-storage';
import { subscribeEnterpriseCapabilityCatalogRefresh } from '@/lib/capability-catalog-events';
import { SELECT_TRIGGER_CLASS } from '@/lib/enterprise-ui';
import { formatHandoffAssigneeValue, parseHandoffAssigneeValue } from '@/lib/handoff-assignee';
import type { EnterpriseAuthUser } from '../auth';
import {
  ACTION_EMPTY_CLASS,
  ACTION_LIST_CLASS,
  CARD_OUTLINE_BUTTON_CLASS,
  CHAT_ACTIONS_GROUP_CLASS,
  CHAT_CARD_BODY_CLASS,
  CHAT_ATTACHMENT_CLASS,
  CHAT_ATTACHMENT_ICON_CLASS,
  CHAT_ATTACHMENT_MAIN_CLASS,
  CHAT_ATTACHMENT_NAME_CLASS,
  CHAT_ATTACHMENT_TYPE_CLASS,
  CHAT_ATTACHMENT_USER_CLASS,
  CHAT_ATTACHMENTS_CLASS,
  CHAT_ATTACHMENTS_USER_CLASS,
  CHAT_CARD_CLASS,
  CHAT_CARD_DRAGGING_CLASS,
  CHAT_CARD_FULLSCREEN_CLASS,
  CHAT_ACTIONS_CLASS,
  CHAT_COMPOSER_SHELL_CLASS,
  CHAT_CONFIRM_CLASS,
  CHAT_CONTENT_CLASS,
  CHAT_CONTENT_USER_ATTACHMENTS_CLASS,
  CHAT_DECISION_CLASS,
  CHAT_EDIT_ACTIONS_CLASS,
  CHAT_EDIT_PANEL_CLASS,
  CHAT_EDIT_PANEL_USER_ATTACHMENTS_CLASS,
  CHAT_EDIT_TEXTAREA_CLASS,
  CHAT_FAILURE_BUTTON_CLASS,
  CHAT_FAILURE_CLASS,
  CHAT_FAILURE_COPY_CLASS,
  CHAT_FAILURE_DETAILS_CLASS,
  CHAT_FAILURE_ICON_CLASS,
  CHAT_FAILURE_LABEL_CLASS,
  CHAT_FAILURE_META_CLASS,
  CHAT_FAILURE_RAW_CLASS,
  CHAT_FAILURE_STAGE_CLASS,
  CHAT_FAILURE_SUMMARY_CLASS,
  CHAT_FAILURE_VALUE_CLASS,
  CHAT_HOVER_ACTIONS_CLASS,
  CHAT_HOVER_BUTTON_CLASS,
  CHAT_MESSAGES_CLASS,
  CHAT_PANEL_CLASS,
  CHAT_COMPOSER_CLASS,
  CHAT_TEXTAREA_CLASS,
  CHAT_THINKING_BLOCK_CLASS,
  CHAT_THINKING_BUTTON_CLASS,
  CHAT_THINKING_DETAIL_CLASS,
  CHAT_THINKING_DETAILS_CLASS,
  CHAT_TIME_CLASS,
  CHAT_UPLOAD_DROP_HINT_CLASS,
  CHAT_WARNING_CLASS,
  CHAT_WARNING_ITEM_CLASS,
  CHAT_WARNING_TITLE_CLASS,
  CONDITION_EDITOR_CLASS,
  CONDITION_INPUT_CLASS,
  CONDITION_PRESET_CLASS,
  CONDITION_READABLE_CLASS,
  DIFF_NEW_CLASS,
  DIFF_OLD_CLASS,
  DISTILL_CARD_BODY_CLASS,
  DISTILL_CARD_CLASS,
  DISTILL_CARD_HEADER_CLASS,
  DISTILL_ACTIONS_CLASS,
  DISTILL_PAGE_CLASS,
  FLOW_CHIP_CLASS,
  FLOW_CHIP_LIST_CLASS,
  FLOW_CHIP_MUTED_CLASS,
  FLOW_CLASS,
  FLOW_COMPACT_META_CLASS,
  FLOW_COMPACT_ROW_CLASS,
  FLOW_CONNECTION_PREVIEW_CLASS,
  FLOW_EDGES_CLASS,
  FLOW_EDGE_PATH_CLASS,
  FLOW_GRAPH_CANVAS_CLASS,
  FLOW_META_CLASS,
  FLOW_META_LABEL_CLASS,
  FLOW_META_ROW_CLASS,
  FLOW_NODE_BADGES_CLASS,
  FLOW_NODE_CONNECT_HANDLE_ACTIVE_CLASS,
  FLOW_NODE_CONNECT_HANDLE_CLASS,
  FLOW_NODE_POSITION_CLASS,
  FLOW_NODE_SHELL_CLASS,
  FLOW_NODE_SUMMARY_CLASS,
  FLOW_ROOT_POSITION_CLASS,
  FLOW_FULLSCREEN_CLASS,
  FLOW_FULLSCREEN_PANNABLE_CLASS,
  FLOW_FULLSCREEN_WITH_AI_CLASS,
  FLOW_INSPECTOR_BODY_CLASS,
  FLOW_INSPECTOR_CLASS,
  FLOW_INSPECTOR_EMPTY_CLASS,
  FLOW_INSPECTOR_HEADER_CLASS,
  FLOW_ROUTE_COUNT_CLASS,
  FLOW_RULE_CONDITION_CONTROLS_CLASS,
  FLOW_RULE_CONDITION_INPUT_CLASS,
  FLOW_RULE_DELETE_CLASS,
  FLOW_RULE_EDITOR_CLASS,
  FLOW_RULE_EMPTY_CLASS,
  FLOW_RULE_FIELD_CLASS,
  FLOW_RULE_FIELD_CONDITION_CLASS,
  FLOW_RULE_FIELD_LABEL_CLASS,
  FLOW_RULE_FIELD_PRIORITY_CLASS,
  FLOW_RULE_FIELD_TARGET_CLASS,
  FLOW_RULE_HEAD_CLASS,
  FLOW_RULE_ITEM_CLASS,
  FLOW_RULE_LABEL_INPUT_CLASS,
  FLOW_RULE_LIST_CLASS,
  FLOW_RULE_PRIORITY_CLASS,
  FLOW_RULE_TARGET_CLASS,
  FLOW_ZOOM_SHELL_CLASS,
  FLOW_ZOOM_CONTROLS_CLASS,
  FLOW_ZOOM_STEP_BUTTON_CLASS,
  FLOW_ZOOM_TOOLBAR_CLASS,
  FLOW_ZOOM_TOOLBAR_FULLSCREEN_CLASS,
  FLOW_ZOOM_VALUE_CLASS,
  FLOW_VIEWER_CLASS,
  FLOW_VIEWER_BODY_CLASS,
  FLOW_VIEWER_FULLSCREEN_CLASS,
  FLOW_VIEWER_FULLSCREEN_PANEL_CLASS,
  FLOW_VIEWER_TITLE_CLASS,
  FLOW_VIEWER_TITLE_MARK_CLASS,
  FLOW_VIEWER_TITLE_META_CLASS,
  flowZoomPresetButtonClass,
  INLINE_ADD_CLASS,
  INLINE_ADD_SETTLED_CLASS,
  INLINE_REMOVE_CLASS,
  NODE_DELETE_CONFIRM_CLASS,
  PILL_OUTLINE_BUTTON_CLASS,
  NODE_INSERT_BUTTON_CLASS,
  NODE_INSERT_ROW_CLASS,
  NODE_INSERT_ROW_EDGE_CLASS,
  RETRY_POLICY_EDITOR_CLASS,
  RETRY_POLICY_FIELD_CLASS,
  PRIMARY_BUTTON_CLASS,
  RETURN_BUTTON_CLASS,
  REWRITE_MODEL_BUTTON_CLASS,
  SAVE_REVIEW_ACTION_DIFF_CLASS,
  SAVE_REVIEW_ACTION_DIFF_NEW_CLASS,
  SAVE_REVIEW_ACTION_DIFF_OLD_CLASS,
  SAVE_REVIEW_DIFF_CLASS,
  SAVE_REVIEW_DIFF_PATH_CLASS,
  SAVE_REVIEW_DIFF_ROW_CLASS,
  SAVE_REVIEW_DIFF_SIGN_CLASS,
  SAVE_REVIEW_DIFF_SIGN_NEW_CLASS,
  SAVE_REVIEW_DIFF_SIGN_OLD_CLASS,
  SAVE_REVIEW_FORM_CLASS,
  SAVE_REVIEW_FORM_LABEL_CLASS,
  SECTION_CARD_TITLE_CLASS,
  SELECTION_MARK_CLASS,
  SOURCE_ACTION_ADD_CLASS,
  SOURCE_ACTION_EDIT_BUTTON_CLASS,
  SOURCE_ACTION_EMPTY_CLASS,
  SOURCE_ACTION_EDITOR_CLASS,
  SOURCE_ACTION_LIST_CLASS,
  SOURCE_ACTION_LIST_EDITABLE_CLASS,
  SOURCE_ACTION_PICKER_CLASS,
  SOURCE_ACTION_REMOVE_CLASS,
  SOURCE_ACTION_SELECT_CLASS,
  SOURCE_ACTION_TOKEN_CLASS,
  SOURCE_EMPTY_STATE_CLASS,
  SOURCE_EMPTY_TEXT_CLASS,
  SOURCE_CARD_CLASS,
  SOURCE_COLLAPSIBLE_EDITOR_CLASS,
  SOURCE_COLLAPSIBLE_HEAD_CLASS,
  SOURCE_COLLAPSIBLE_PREVIEW_CLASS,
  SOURCE_COLLAPSIBLE_PREVIEW_MUTED_CLASS,
  SOURCE_COLLAPSIBLE_TOGGLE_CLASS,
  SOURCE_EDIT_FIELD_CLASS,
  SOURCE_EDIT_HINT_CLASS,
  SOURCE_EDIT_INPUT_CLASS,
  SOURCE_GROUP_TITLE_CLASS,
  SOURCE_INPUT_CLASS,
  SOURCE_JSON_INLINE_CLASS,
  SOURCE_KEY_CLASS,
  SOURCE_LINE_CLASS,
  SOURCE_MD_CLASS,
  SOURCE_META_LIST_CLASS,
  SOURCE_READONLY_VALUE_CLASS,
  SOURCE_RENDERED_CLASS,
  SOURCE_SELECT_CLASS,
  SOURCE_STEP_BLOCK_CLASS,
  SOURCE_STEP_HEADER_CLASS,
  SOURCE_STEP_TITLE_EDIT_CLASS,
  SOURCE_STEPS_CLASS,
  SOURCE_TITLE_INPUT_CLASS,
  SOURCE_TOOLBAR_CLASS,
  SOURCE_VALUE_CLASS,
  TOOL_ACTION_BUTTON_CLASS,
  TOOL_ACTION_CONFIRM_CLASS,
  TOOL_ACTION_GROUP_CLASS,
  TOOL_ACTION_GROUP_DETAIL_CLASS,
  TOOL_ACTION_REJECT_CLASS,
  TOOL_METHOD_CLASS,
  TOOL_SUGGESTION_ACTIONS_CLASS,
  TOOL_SUGGESTION_CLASS,
  TOOL_SUGGESTION_DESC_CLASS,
  TOOL_SUGGESTION_DETAIL_CLASS,
  TOOL_SUGGESTION_DETAIL_FOOTER_CLASS,
  TOOL_SUGGESTION_DETAIL_PRE_CLASS,
  TOOL_SUGGESTION_HEAD_CLASS,
  TOOL_SUGGESTION_MAIN_CLASS,
  TOOL_SUGGESTION_META_CLASS,
  TOOL_SUGGESTION_TITLE_CLASS,
  TOOL_SUGGESTIONS_CLASS,
  UPLOAD_LIST_CLASS,
  UPLOAD_NAME_CLASS,
  UPLOAD_STATUS_CLASS,
  WORKBENCH_CLASS,
  actionChipClass,
  chatBubbleClass,
  chatRowClass,
  distillFlowNodeClass,
  distillSourceSectionClass,
  flowEdgeLabelClass,
  toolStatusBadgeClass,
  uploadItemClass,
  type ToolStatusBadgeVariant,
} from './distillPageStyles';
import { buildDistillFailure, type DistillFailure } from './distillFailure';
import {
  normalizeSkillFlowWheelDelta,
  reanchorSkillFlowConnection,
  skillNodeFlowPosition,
  withSkillNodeFlowPosition,
  withoutSkillEdgeAt,
  type SkillFlowConnectionPreview,
  type SkillFlowPosition,
} from './skillFlowModel';
import { api, ApiError, streamGet, streamPost, TENANT_ID } from '../api/client';
import { copyTextToClipboard } from '@/lib/clipboard';
import type {
  GeneralSkillRead,
  KnowledgeBaseRead,
  ModelConfigRead,
  SkillCard,
  SkillRead,
  ToolProbeResponse,
  ToolRead,
  ToolSuggestion,
} from '../types';

type ChatItem = {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  attachments?: ChatAttachment[];
  outgoingText?: string;
  createdAt?: string;
  thinking?: 'running' | 'done';
  thinkingDetails?: string[];
  thinkingOpen?: boolean;
  failure?: DistillFailure;
  failureOpen?: boolean;
  warnings?: string[];
  toolSuggestions?: ToolSuggestionItem[];
  actionState?: 'pending' | 'confirmed' | 'rejected';
  snapshotBefore?: DistillHistorySnapshot;
  operations?: DistillHistoryOperation[];
};

type ChatAttachment = {
  id: string;
  name: string;
  type: string;
};

type ToolSuggestionItem = ToolSuggestion & {
  status?: 'pending' | 'accepted' | 'created' | 'rejected';
  probeStatus?: 'idle' | 'probing' | 'success' | 'error';
};

type ProbeToolOptions = {
  sampleArguments?: Record<string, unknown>;
  silent?: boolean;
  allowWhileLoading?: boolean;
};

type UploadAttachment = {
  id: string;
  name: string;
  status: 'uploading' | 'ready' | 'error';
  text?: string;
  error?: string;
};

type TargetSelection = {
  path: string;
  label: string;
};
type ToolDescriptionMap = Record<string, string>;
type ToolActionStatus = 'existing' | 'pending' | 'accepted' | 'created' | 'rejected' | 'incomplete';
type ToolStatusMap = Record<string, ToolActionStatus>;

type ViewMode = 'source' | 'flow';

type SelectOption = {
  value: string;
  label: string;
};

type CapabilityReferenceOption = SelectOption & {
  description?: string;
  capabilityScope?: unknown;
  unavailableReason?: string;
};

const CAPABILITY_REFERENCE_FIELDS = [
  'general_skill_ids',
  'tool_ids',
  'knowledge_base_ids',
  'required_general_skill_ids',
  'required_tool_ids',
  'required_knowledge_base_ids',
];

const REQUIRED_CAPABILITY_FIELD_BY_ALLOWED: Record<string, string> = {
  general_skill_ids: 'required_general_skill_ids',
  tool_ids: 'required_tool_ids',
  knowledge_base_ids: 'required_knowledge_base_ids',
};

const NODE_TYPE_OPTIONS: SelectOption[] = [
  { value: 'collect_info', label: '收集信息' },
  { value: 'decision', label: '条件判断' },
  { value: 'tool_call', label: '调用工具' },
  { value: 'knowledge_query', label: '检索知识' },
  { value: 'response', label: '回复用户' },
  { value: 'handoff', label: '转人工' },
  { value: 'subflow', label: '调用子 SOP' },
];

const BASE_ACTION_OPTIONS: SelectOption[] = [
  { value: 'ask_user', label: '询问用户' },
  { value: 'continue_flow', label: '继续流程' },
  { value: 'answer_user', label: '回复用户' },
  { value: 'handoff_human', label: '转人工' },
  { value: 'ask_clarification', label: '澄清问题' },
  { value: 'clarify_user', label: '澄清用户需求' },
  { value: 'update_memory', label: '更新记忆' },
  { value: 'reflect', label: '反思检查' },
  { value: 'finish', label: '结束流程' },
  { value: 'stop', label: '停止流程' },
];

const CONDITION_PRESET_OPTIONS: SelectOption[] = [
  { value: '__always__', label: '总是可进入' },
  { value: 'missing_required_info', label: '缺少任一必填信息' },
  { value: 'missing_slots([])', label: '缺少指定字段' },
  { value: 'all_required_info_collected', label: '必填信息已收集完成' },
  { value: 'tool_success', label: '工具执行成功' },
  { value: 'tool_failed', label: '工具执行失败' },
  { value: 'user_confirmed', label: '用户已确认' },
  { value: 'user_rejected', label: '用户已拒绝' },
  { value: '__custom__', label: '自定义条件' },
];

const CONDITION_PRESET_TEXT: Record<string, string> = {
  missing_required_info: '还有必填信息没有收集到时进入',
  'missing_slots([])': '缺少某个指定字段时进入',
  all_required_info_collected: '所有必填信息都收集完成后进入',
  tool_success: '上一步工具调用成功后进入',
  tool_failed: '上一步工具调用失败后进入',
  user_confirmed: '用户明确确认后进入',
  user_rejected: '用户明确拒绝后进入',
};

const RETRY_STRATEGY_OPTIONS: SelectOption[] = [
  { value: 'ask_user', label: '继续追问用户' },
  { value: 'reflect', label: '反思并修正' },
  { value: 'retry_tool', label: '重新调用工具' },
  { value: 'handoff_human', label: '转人工处理' },
  { value: 'skip', label: '跳过当前节点' },
  { value: 'stop', label: '停止流程' },
];

type PendingChange = {
  assistantId: string;
  previousDraft: SkillCard;
  nextDraft: SkillCard;
  changedPaths: string[];
};
type TextDiffPhase = 'mark' | 'type' | 'settled';
type TextDiffAnimation = {
  key: string;
  path: string;
  field: string;
  prefix: string;
  removed: string;
  inserted: string;
  suffix: string;
  phase: TextDiffPhase;
  progress: number;
};

type ActiveDistillJob = {
  jobId: string;
  kind: 'distill' | 'rewrite';
  assistantId: string;
  lastSeq: number;
  status?: string;
  createPayload?: { title: string; raw_content: string };
  previousDraft?: SkillCard;
  targets?: string[];
};

const DEFAULT_TARGET_PATHS: string[] = [];
const DEFAULT_DISTILL_MESSAGES: ChatItem[] = [
  {
    id: 'welcome',
    role: 'assistant',
    content: '请粘贴原始技能说明，或点击右侧某一块后告诉我需要怎样改写。',
  },
];
const DISTILL_REWRITE_MODEL_STORAGE_KEY = 'skill-distill-rewrite-model';
const _CHANNEL_LABELS: Record<string, string> = { feishu: '飞书', dingtalk: '钉钉', wecom: '企业微信', wechat: '微信', web: '网页端' };
const UNASSIGNED_USER_VALUE = '__unassigned__';
// 渠道转接通知运行时已支持飞书/企微私聊,处理人选项提供对应渠道标注
// (后端同样拒绝其他渠道)。钉钉/微信适配器只能回会话内消息,不在此列。
const HANDOFF_NOTIFY_CHANNELS = new Set(['feishu', 'wecom']);

type HandoffAssigneeUser = {
  id: string;
  username: string;
  display_name?: string;
  source?: string;
  channel_identities?: Array<{ channel: string; display_name?: string; external_user_id?: string }>;
};

/** 构造转接处理人下拉选项：内部成员按网页端投递，已绑定飞书/企微身份的成员额外生成带渠道标注的选项。 */
export function handoffAssigneeUserOptions(tenantUsers: HandoffAssigneeUser[]): SelectOption[] {
  // 内部成员一律可选(网页端投递);已绑定支持渠道身份的成员追加"姓名（渠道）"选项,
  // 选中后运行时按该渠道转接。其他渠道身份不生成选项(通知未实现),
  // 渠道懒建账号(渠道客户/群聊)也不进入处理人选项。
  const options: SelectOption[] = [];
  tenantUsers.filter((user) => !user.source || user.source === 'web').forEach((user) => {
    const name = user.display_name || user.username || user.id;
    options.push({ value: user.id, label: `${name}（${_CHANNEL_LABELS.web}）` });
    const channels = new Set<string>();
    (user.channel_identities || []).forEach((identity) => {
      const channel = String(identity.channel || '').trim();
      if (channel && HANDOFF_NOTIFY_CHANNELS.has(channel)) channels.add(channel);
    });
    channels.forEach((channel) => {
      options.push({
        value: `${user.id}::${channel}`,
        label: `${name}（${_CHANNEL_LABELS[channel] || channel}）`,
      });
    });
  });
  return options;
}

type DistillCacheSnapshot = {
  draft: SkillCard | null;
  loadedSkill: SkillRead | null;
  lastSavedDraft: SkillCard | null;
  messages: ChatItem[];
  input: string;
  selectedPaths: string[];
  highlightedPaths: string[];
  updatingPaths: string[];
  dirtyPaths: string[];
  textDiffs: TextDiffAnimation[];
  pendingChange: PendingChange | null;
  viewMode: ViewMode;
  attachments: UploadAttachment[];
  streamStatus: string;
  activeJob: ActiveDistillJob | null;
};

type DistillHistoryOperationKind = 'skill_change' | 'version_save' | 'tool_add';

type DistillHistoryOperation = {
  kind: DistillHistoryOperationKind;
  label: string;
  skillId?: string;
  version?: string;
  toolId?: string;
  toolName?: string;
};

type DistillHistorySnapshot = {
  draft: SkillCard | null;
  loadedSkill: SkillRead | null;
  lastSavedDraft: SkillCard | null;
  selectedPaths: string[];
  highlightedPaths: string[];
  updatingPaths: string[];
  dirtyPaths: string[];
  textDiffs: TextDiffAnimation[];
  pendingChange: PendingChange | null;
  viewMode: ViewMode;
  tools: ToolRead[];
  attachments: UploadAttachment[];
  streamStatus: string;
};

type EditingMessage = {
  id: string;
  text: string;
};

type DistillPageProps = {
  active?: boolean;
  searchParamsOverride?: URLSearchParams;
  currentUser?: EnterpriseAuthUser;
  onLogout?: () => void;
};

/** 把草稿的 skill_id 锁定为当前加载的技能 ID；无需改动时原样返回入参对象。 */
function lockSkillIdForDraft(draft: SkillCard, lockedSkillId: string): SkillCard {
  if (!lockedSkillId || draft.skill_id === lockedSkillId) return draft;
  return { ...cloneSkill(draft), skill_id: lockedSkillId };
}

/** lockSkillIdForDraft 的空值安全版本：草稿为 null 时直接返回 null。 */
function lockNullableSkillIdForDraft(draft: SkillCard | null, lockedSkillId: string): SkillCard | null {
  return draft ? lockSkillIdForDraft(draft, lockedSkillId) : null;
}

/** 同步锁定待确认变更中前后两份草稿的 skill_id，避免切换技能后变更落到旧 ID。 */
function lockPendingChangeSkillId(change: PendingChange | null, lockedSkillId: string): PendingChange | null {
  if (!change || !lockedSkillId) return change;
  return {
    ...change,
    previousDraft: lockSkillIdForDraft(change.previousDraft, lockedSkillId),
    nextDraft: lockSkillIdForDraft(change.nextDraft, lockedSkillId),
  };
}

/**
 * 技能蒸馏工作台页面：左侧对话生成或改写 SOP 草稿，右侧预览源码与流程图。
 *
 * - 首次进入按 URL 的 skill_id / mode 读取会话缓存或加载服务端技能。
 * - 对话触发生成与局部改写，流式事件实时更新草稿并暂存待确认变更。
 * - active 为真时给 body 添加 skill-distill-fixed 类以固定整页布局。
 */
export default function DistillPage({ active = true, searchParamsOverride, currentUser, onLogout }: DistillPageProps = {}) {
  const navigate = useNavigate();
  const [routerSearchParams] = useSearchParams();
  const searchParams = searchParamsOverride || routerSearchParams;
  const skillId = searchParams.get('skill_id');
  const mode = searchParams.get('mode') || '';
  const workspaceId = searchParams.get('workspace_id') || '';
  const [selectedAgentId, setSelectedAgentId] = useState(readEmployeeScope);
  const activeAgentId = searchParams.get('agent_id') || selectedAgentId;
  const agentQuery = activeAgentId ? `&agent_id=${encodeURIComponent(activeAgentId)}` : '';
  const agentSearchParam = activeAgentId ? `agent_id=${encodeURIComponent(activeAgentId)}` : '';
  const agentOnlyQuery = agentSearchParam ? `?${agentSearchParam}` : '';
  const cacheIdentity = skillId || (mode === 'create' ? `create:${workspaceId || 'pending'}` : mode || 'new');
  const cacheKey = `skill-distill:${TENANT_ID}:${activeAgentId || 'default'}:${cacheIdentity}`;
  const [draft, setDraft] = useState<SkillCard | null>(null);
  const [loadedSkill, setLoadedSkill] = useState<SkillRead | null>(null);
  const [lastSavedDraft, setLastSavedDraft] = useState<SkillCard | null>(null);
  const [messages, setMessages] = useState<ChatItem[]>(DEFAULT_DISTILL_MESSAGES);
  const [input, setInput] = useState('');
  const [selectedPaths, setSelectedPaths] = useState<string[]>(DEFAULT_TARGET_PATHS);
  const [highlightedPaths, setHighlightedPaths] = useState<string[]>([]);
  const [updatingPaths, setUpdatingPaths] = useState<string[]>([]);
  const [dirtyPaths, setDirtyPaths] = useState<string[]>([]);
  const [textDiffs, setTextDiffs] = useState<TextDiffAnimation[]>([]);
  const [pendingChange, setPendingChange] = useState<PendingChange | null>(null);
  const [saveReviewOpen, setSaveReviewOpen] = useState(false);
  const [saveDraftSnapshot, setSaveDraftSnapshot] = useState<SkillCard | null>(null);
  const [saveName, setSaveName] = useState('');
  const [saveDomain, setSaveDomain] = useState('');
  const [saveVersion, setSaveVersion] = useState('');
  const [clearConfirmOpen, setClearConfirmOpen] = useState(false);
  const [clearAfterSave, setClearAfterSave] = useState(false);
  const [clearNewConfirm, setClearNewConfirm] = useState<{ title: string; description: string } | null>(null);
  const [rerunConfirm, setRerunConfirm] = useState<{
    index: number;
    snapshot: DistillHistorySnapshot;
    rollbackOperations: DistillHistoryOperation[];
    text: string;
    outgoingText: string;
  } | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>('flow');
  const [flowFullscreen, setFlowFullscreen] = useState(false);
  const [flowAssistantPanelOpen, setFlowAssistantPanelOpen] = useState(true);
  const [loading, setLoading] = useState(false);
  const [attachments, setAttachments] = useState<UploadAttachment[]>([]);
  const [dragActive, setDragActive] = useState(false);
  const [toolDetail, setToolDetail] = useState<ToolSuggestionItem | null>(null);
  const [toolDetailMessageId, setToolDetailMessageId] = useState<string | null>(null);
  const [probeArgsText, setProbeArgsText] = useState('');
  const [tools, setTools] = useState<ToolRead[]>([]);
  const [generalSkills, setGeneralSkills] = useState<GeneralSkillRead[]>([]);
  const [sopSkills, setSopSkills] = useState<SkillRead[]>([]);
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBaseRead[]>([]);
  const [tenantUsers, setTenantUsers] = useState<Array<{ id: string; username: string; display_name?: string; source?: string; channel_identities?: Array<{ channel: string; display_name?: string; external_user_id?: string; external_account_scope?: string }> }>>([]);
  const [modelConfigs, setModelConfigs] = useState<ModelConfigRead[]>([]);
  const [selectedRewriteModelId, setSelectedRewriteModelId] = useState(
    () => window.localStorage.getItem(`${DISTILL_REWRITE_MODEL_STORAGE_KEY}:${TENANT_ID}`) || '',
  );
  const [streamStatus, setStreamStatus] = useState('');
  const [activeJob, setActiveJob] = useState<ActiveDistillJob | null>(null);
  const [editingMessage, setEditingMessage] = useState<EditingMessage | null>(null);
  const [sourceAutoScroll, setSourceAutoScroll] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const manualStopRef = useRef(false);
  const uploadControllersRef = useRef<Record<string, AbortController>>({});
  const dragDepthRef = useRef(0);
  const animationTimersRef = useRef<number[]>([]);
  const capabilityCatalogRequestRef = useRef(0);
  const sourceScrollRef = useRef<HTMLDivElement | null>(null);
  const chatMessagesRef = useRef<HTMLDivElement | null>(null);
  const [cacheReady, setCacheReady] = useState(false);
  const [hydratedCacheKey, setHydratedCacheKey] = useState('');

  useEffect(() => {
    /** 监听全局员工/团队范围切换事件，切换到具体员工时更新当前 agent。 */
    const onScopeChange = (event: Event) => {
      const next = (event as CustomEvent<{ agentId?: string }>).detail?.agentId || '';
      setSelectedAgentId(next && !isTeamScope(next) ? next : readEmployeeScope());
    };
    window.addEventListener('ultrarag-enterprise-agent-scope-change', onScopeChange);
    return () => window.removeEventListener('ultrarag-enterprise-agent-scope-change', onScopeChange);
  }, []);

  useEffect(() => {
    if (!active || skillId || mode !== 'create' || workspaceId) return;
    const nextParams = new URLSearchParams(searchParams);
    nextParams.set('workspace_id', createDistillWorkspaceId());
    navigate(`/enterprise/skills/distill?${nextParams.toString()}`, { replace: true });
  }, [active, mode, navigate, searchParams, skillId, workspaceId]);

  useEffect(() => {
    setCacheReady(false);
    setHydratedCacheKey('');
    if (mode === 'create' && !workspaceId) return;
    const cached = readDistillCache(cacheKey);
    if (cached) {
      if (skillId && isBlankDistillWorkspace(cached)) {
        removeDistillCache(cacheKey);
      } else {
        const cachedLockedSkillId = cached.loadedSkill?.skill_id || skillId || '';
        setDraft(lockNullableSkillIdForDraft(cached.draft, cachedLockedSkillId));
        setLoadedSkill(cached.loadedSkill);
        setLastSavedDraft(lockNullableSkillIdForDraft(cached.lastSavedDraft, cachedLockedSkillId));
        setMessages(cached.messages.length > 0 ? cached.messages : DEFAULT_DISTILL_MESSAGES);
        setInput(cached.input);
        setSelectedPaths(normalizeInitialSelectedPaths(cached.selectedPaths));
        setHighlightedPaths(cached.highlightedPaths);
        setUpdatingPaths(cached.updatingPaths);
        setDirtyPaths(cached.dirtyPaths);
        setTextDiffs(cached.textDiffs);
        setPendingChange(lockPendingChangeSkillId(cached.pendingChange, cachedLockedSkillId));
        setViewMode('flow');
        setAttachments(cached.attachments.filter((item) => item.status !== 'uploading'));
        setStreamStatus(cached.streamStatus);
        setActiveJob(cached.activeJob || null);
        if (cached.activeJob && cached.activeJob.status !== 'succeeded' && cached.activeJob.status !== 'failed') {
          setLoading(true);
        }
        setSaveDraftSnapshot(null);
        setHydratedCacheKey(cacheKey);
        setCacheReady(true);
        return;
      }
    }

    if (!skillId) {
      setDraft(null);
      setLoadedSkill(null);
      setLastSavedDraft(null);
      setMessages(DEFAULT_DISTILL_MESSAGES);
      setInput('');
      setSelectedPaths(DEFAULT_TARGET_PATHS);
      setPendingChange(null);
      setHighlightedPaths([]);
      setUpdatingPaths([]);
      setDirtyPaths([]);
      setTextDiffs([]);
      setAttachments([]);
      setStreamStatus('');
      setSaveDraftSnapshot(null);
      setHydratedCacheKey(cacheKey);
      setCacheReady(true);
      return;
    }

    api
      .get<SkillRead>(`/api/enterprise/skills/${encodeURIComponent(skillId)}?tenant_id=${TENANT_ID}${agentQuery}`)
      .then((result) => {
        const nextContent = lockSkillIdForDraft(result.content, result.skill_id || skillId || '');
        const nextResult = nextContent === result.content ? result : { ...result, content: nextContent };
        setDraft(nextContent);
        setLoadedSkill(nextResult);
        setLastSavedDraft(nextContent);
        setSelectedPaths(DEFAULT_TARGET_PATHS);
        setPendingChange(null);
        setHighlightedPaths([]);
        setUpdatingPaths([]);
        setDirtyPaths([]);
        setTextDiffs([]);
        setAttachments([]);
        setStreamStatus('');
        setSaveDraftSnapshot(null);
        setMessages([
          {
            id: 'loaded',
            role: 'assistant',
            content: `已加载「${result.name}」。你可以在右侧选择一个或多个区域，然后在这里描述需要怎样改写。`,
          },
        ]);
        setHydratedCacheKey(cacheKey);
        setCacheReady(true);
      })
      .catch((error) => {
        notify.error(error instanceof Error ? error.message : '加载技能失败');
        setHydratedCacheKey(cacheKey);
        setCacheReady(true);
      });
  }, [agentQuery, cacheKey, mode, skillId, workspaceId]);

  useEffect(() => {
    if (!cacheReady || hydratedCacheKey !== cacheKey) return;
    writeDistillCache(cacheKey, {
      draft,
      loadedSkill,
      lastSavedDraft,
      messages,
      input,
      selectedPaths,
      highlightedPaths,
      updatingPaths,
      dirtyPaths,
      textDiffs,
      pendingChange,
      viewMode,
      attachments: attachments.filter((item) => item.status !== 'uploading'),
      streamStatus,
      activeJob,
    });
  }, [
    attachments,
    cacheKey,
    cacheReady,
    dirtyPaths,
    draft,
    highlightedPaths,
    hydratedCacheKey,
    input,
    lastSavedDraft,
    loadedSkill,
    loading,
    messages,
    pendingChange,
    selectedPaths,
    streamStatus,
    activeJob,
    textDiffs,
    updatingPaths,
    viewMode,
  ]);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      Object.values(uploadControllersRef.current).forEach((controller) => controller.abort());
      clearAnimationTimers();
    };
  }, []);

  useEffect(() => {
    if (!cacheReady || hydratedCacheKey !== cacheKey || !activeJob) return;
    if (activeJob.status === 'succeeded' || activeJob.status === 'failed') return;
    if (abortRef.current) return;
    const controller = new AbortController();
    manualStopRef.current = false;
    abortRef.current = controller;
    setLoading(true);
    void streamGet(
      `/api/enterprise/skills/jobs/${encodeURIComponent(activeJob.jobId)}/stream?after_seq=${activeJob.lastSeq || 0}`,
      (item) => handleResumedJobEvent(activeJob, item),
      controller.signal,
    )
      .catch((error) => {
        if (controller.signal.aborted) return;
        updateMessage(activeJob.assistantId, '生成连接已断开，后端任务仍可继续。', { thinking: 'done' });
        notify.error(error instanceof Error ? error.message : '恢复生成失败');
      })
      .finally(() => finishStream(controller));
  }, [activeJob, cacheKey, cacheReady, hydratedCacheKey]);

  useEffect(() => {
    if (!active) {
      document.body.classList.remove('skill-distill-fixed');
      return;
    }
    document.body.classList.add('skill-distill-fixed');
    return () => {
      document.body.classList.remove('skill-distill-fixed');
    };
  }, [active]);

  /** 并行拉取工具、通用技能、知识库与 SOP 列表，并用请求序号丢弃过期响应。 */
  const refreshCapabilityCatalog = useCallback(async () => {
    if (!active) return;
    const requestId = capabilityCatalogRequestRef.current + 1;
    capabilityCatalogRequestRef.current = requestId;
    const [toolResult, skillResult, knowledgeResult, sopResult] = await Promise.allSettled([
      api.get<ToolRead[]>(`/api/enterprise/tools?tenant_id=${TENANT_ID}${agentQuery}`),
      api.get<GeneralSkillRead[]>(
        `/api/enterprise/general-skills?tenant_id=${TENANT_ID}${agentQuery}`,
      ),
      api.get<KnowledgeBaseRead[]>(
        `/api/enterprise/knowledge-bases?tenant_id=${TENANT_ID}${agentQuery}`,
      ),
      api.get<SkillRead[]>(`/api/enterprise/skills?tenant_id=${TENANT_ID}${agentQuery}`),
    ]);
    if (requestId !== capabilityCatalogRequestRef.current) return;
    if (toolResult.status === 'fulfilled') setTools(toolResult.value);
    if (skillResult.status === 'fulfilled') setGeneralSkills(skillResult.value);
    if (knowledgeResult.status === 'fulfilled') setKnowledgeBases(knowledgeResult.value);
    if (sopResult.status === 'fulfilled') setSopSkills(sopResult.value);
  }, [active, agentQuery]);

  useEffect(() => {
    void refreshCapabilityCatalog();
    return () => {
      capabilityCatalogRequestRef.current += 1;
    };
  }, [refreshCapabilityCatalog]);

  useEffect(() => {
    if (!active) return;
    return subscribeEnterpriseCapabilityCatalogRefresh(() => {
      void refreshCapabilityCatalog();
    });
  }, [active, refreshCapabilityCatalog]);

  useEffect(() => {
    api
      .get<Array<{ id: string; username: string; display_name?: string; source?: string; channel_identities?: Array<{ channel: string; display_name?: string; external_user_id?: string }> }>>(
        `/api/auth/users?tenant_id=${TENANT_ID}&include_channel=true`,
      )
      .then(setTenantUsers)
      .catch(() => setTenantUsers([]));
  }, []);

  useEffect(() => {
    api
      .get<ModelConfigRead[]>(`/api/enterprise/model-configs?tenant_id=${TENANT_ID}`)
      .then((rows) => {
        const enabled = rows.filter((item) => item.enabled);
        setModelConfigs(enabled);
        setSelectedRewriteModelId((current) => {
          if (current && enabled.some((item) => item.id === current)) return current;
          const fallback = enabled.find((item) => item.is_default)?.id || enabled[0]?.id || '';
          if (fallback) {
            window.localStorage.setItem(`${DISTILL_REWRITE_MODEL_STORAGE_KEY}:${TENANT_ID}`, fallback);
          }
          return fallback;
        });
      })
      .catch(() => setModelConfigs([]));
  }, []);

  useEffect(() => {
    if (!chatMessagesRef.current) return;
    chatMessagesRef.current.scrollTop = chatMessagesRef.current.scrollHeight;
  }, [attachments, loading, messages]);

  useEffect(() => {
    if (!loading || !sourceAutoScroll || !sourceScrollRef.current) return;
    sourceScrollRef.current.scrollTop = sourceScrollRef.current.scrollHeight;
  }, [draft, loading, sourceAutoScroll, textDiffs, viewMode]);

  const allPaths = useMemo(() => (draft ? allTargetPaths(draft) : DEFAULT_TARGET_PATHS), [draft]);
  const uploadingFile = attachments.some((item) => item.status === 'uploading');
  const readyAttachments = attachments.filter((item) => item.status === 'ready' && item.text?.trim());
  const allSelected = draft ? selectedPaths.length > 0 && allPaths.every((path) => selectedPaths.includes(path)) : false;
  const toolDescriptions = useMemo(() => buildToolDescriptionMap(tools, messages), [messages, tools]);
  const toolStatuses = useMemo(() => buildToolStatusMap(tools, messages), [messages, tools]);
  const lockedSkillId = loadedSkill?.skill_id || skillId || '';
  const saveReviewDraft = useMemo(() => {
    const sourceDraft = saveDraftSnapshot || draft;
    if (!sourceDraft) return null;
    const nextDraft = {
      ...cloneSkill(sourceDraft),
      name: saveName.trim() || sourceDraft.name,
      business_domain: saveDomain.trim() || undefined,
      version: saveVersion.trim() || sourceDraft.version,
    };
    return lockSkillIdForDraft(nextDraft, lockedSkillId);
  }, [draft, lockedSkillId, saveDomain, saveDraftSnapshot, saveName, saveVersion]);
  const saveReviewDiffs = useMemo(() => {
    if (!saveReviewDraft) return [];
    const baseDraft = lastSavedDraft || blankSkillForAnimation(saveReviewDraft);
    const changedPaths = diffTargetPaths(baseDraft, saveReviewDraft, allTargetPaths(saveReviewDraft));
    return collectTextDiffs(baseDraft, saveReviewDraft, changedPaths).filter((diff) => diff.field !== 'version');
  }, [lastSavedDraft, saveReviewDraft]);
  const hasSaveableDraftChanges = useMemo(
    () => hasSkillContentChanges(lockNullableSkillIdForDraft(pendingChange?.nextDraft || draft, lockedSkillId), lastSavedDraft),
    [draft, lastSavedDraft, lockedSkillId, pendingChange],
  );
  const saveReviewHasContentChanges = useMemo(
    () => hasSkillContentChanges(saveReviewDraft, lastSavedDraft),
    [lastSavedDraft, saveReviewDraft],
  );

  useEffect(() => {
    if (!lockedSkillId) return;
    setDraft((current) => lockNullableSkillIdForDraft(current, lockedSkillId));
    setLastSavedDraft((current) => lockNullableSkillIdForDraft(current, lockedSkillId));
    setSaveDraftSnapshot((current) => lockNullableSkillIdForDraft(current, lockedSkillId));
    setPendingChange((current) => lockPendingChangeSkillId(current, lockedSkillId));
  }, [lockedSkillId]);

  /**
   * 发送输入框内容：没有草稿时走首次生成，已有草稿时改写选中区域。
   *
   * - 发送前先应用待确认的改写结果，随后清空输入框与附件。
   * - 附件正文与输入拼成 outgoingText 一并提交给模型。
   */
  async function send() {
    const text = buildOutgoingText(input, readyAttachments);
    if (!text || loading || uploadingFile) return;
    const displayText = input.trim();
    const displayAttachments = buildDisplayAttachments(readyAttachments);
    const snapshotBefore = createHistorySnapshot();
    const confirmedDraft = lockNullableSkillIdForDraft(pendingChange?.nextDraft || draft, lockedSkillId);
    confirmPendingChange(false);
    setInput('');
    setAttachments([]);
    pushMessage('user', displayText, { attachments: displayAttachments, outgoingText: text, snapshotBefore });
    if (!confirmedDraft) {
      await createDraftFromText(text);
      return;
    }
    await rewriteSelectedTarget(text, confirmedDraft);
  }

  /**
   * 根据用户首条描述流式生成 SOP 草稿。
   *
   * - 先写入预览种子草稿，再逐块解析模型输出并实时刷新右侧预览。
   * - 完成后写入提示、告警与工具建议，并自动探测建议的工具。
   * - 失败时保留当前草稿并记录失败详情。
   */
  async function createDraftFromText(text: string) {
    const payload = parseInitialSkillPrompt(text);
    setLoading(true);
    setSourceAutoScroll(true);
    setStreamStatus('正在生成 SOP 草稿');
    let streamBuffer = '';
    let latestPreview = createStreamingDraftSeed(payload);
    let latestPreviewSignature = JSON.stringify(latestPreview);
    setDraft(latestPreview);
    setSelectedPaths(DEFAULT_TARGET_PATHS);
    setHighlightedPaths([]);
    setUpdatingPaths([]);
    setTextDiffs([]);
    const assistantId = pushMessage('assistant', '', {
      thinking: 'running',
      thinkingDetails: ['正在理解技能目标与输入信息'],
      thinkingOpen: false,
    });
    const baseJob: ActiveDistillJob = {
      jobId: '',
      kind: 'distill',
      assistantId,
      lastSeq: 0,
      status: 'queued',
      createPayload: payload,
    };
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      await streamPost(
        '/api/enterprise/skills/distill/stream',
        {
          tenant_id: TENANT_ID,
          agent_id: activeAgentId || undefined,
          ...payload,
          model_config_id: selectedRewriteModelId || undefined,
        },
        (item) => {
          trackActiveJobEvent(item, baseJob);
          if (item.event === 'status') {
            appendThinkingDetail(assistantId, String(item.data.text || '正在处理'));
            return;
          }
          if (item.event === 'chunk_reset') {
            streamBuffer = '';
            latestPreview = createStreamingDraftSeed(payload);
            latestPreviewSignature = JSON.stringify(latestPreview);
            setDraft(latestPreview);
            return;
          }
          if (item.event === 'chunk') {
            const content = typeof item.data.content === 'string' ? item.data.content : '';
            if (!content) return;
            streamBuffer += content;
            const preview = previewSkillFromStream(streamBuffer, latestPreview, payload);
            const previewSignature = JSON.stringify(preview);
            if (previewSignature !== latestPreviewSignature) {
              latestPreview = preview;
              latestPreviewSignature = previewSignature;
              setDraft(preview);
              setStreamStatus('正在解码技能结构');
            }
            return;
          }
          if (item.event === 'complete') {
            const draftSkill = lockSkillIdForDraft(item.data.draft_skill as SkillCard, lockedSkillId);
            const nextWarnings = Array.isArray(item.data.warnings) ? item.data.warnings.map(String) : [];
            const nextToolSuggestions = normalizeToolSuggestions(item.data.tool_suggestions);
            appendThinkingDetail(assistantId, `已生成 SOP 草稿：${draftSkill.name}`);
            clearAnimationTimers();
            setDraft(draftSkill);
            setHighlightedPaths([]);
            setUpdatingPaths([]);
            setTextDiffs([]);
            setSelectedPaths(DEFAULT_TARGET_PATHS);
            updateMessage(
              assistantId,
              `已生成「${draftSkill.name}」草稿。你可以在右侧选择一个或多个区域继续改写。`,
              {
                thinking: 'done',
                warnings: nextWarnings,
                toolSuggestions: nextToolSuggestions,
                operations: [{ kind: 'skill_change', label: `生成 SOP 草稿：${draftSkill.name}`, skillId: draftSkill.skill_id }],
              },
            );
            setStreamStatus('生成完成');
            if (nextToolSuggestions.length > 0) {
              void autoProbeToolSuggestions(assistantId, nextToolSuggestions);
            }
            setActiveJob(null);
            return;
          }
          if (item.event === 'error') {
            applyDistillFailure(assistantId, item.data.message, 'distill');
            setActiveJob(null);
          }
        },
        controller.signal,
      );
    } catch (error) {
      if (controller.signal.aborted && !manualStopRef.current) return;
      appendThinkingDetail(assistantId, '生成失败，已保留当前草稿');
      applyDistillFailure(assistantId, error, 'distill');
      if (controller.signal.aborted) {
        notify.info('已停止生成');
      } else {
        notify.error(error instanceof Error ? error.message : '生成失败');
      }
    } finally {
      finishStream(controller);
    }
  }

  /**
   * 对选中区域发起局部改写，完成后暂存待确认变更。
   *
   * - 未传 targetPathsOverride 时使用当前选中路径，未选中则改写全部路径。
   * - 流式拼接助手消息，complete 后播放差异动画并等待用户确认。
   */
  async function rewriteSelectedTarget(
    text: string,
    currentDraft: SkillCard | null = draft,
    targetPathsOverride?: string[],
    initialThinkingDetails?: string[],
    conversationOverride?: ChatItem[],
  ) {
    if (!currentDraft) return;
    setSourceAutoScroll(false);
    const editableDraft = canonicalizeSkillCapabilityRefs(lockSkillIdForDraft(currentDraft, lockedSkillId));
    const previousDraft = cloneSkill(editableDraft);
    const targets = targetPathsOverride?.length
      ? targetPathsOverride
      : selectedPaths.length > 0
        ? selectedPaths
        : allTargetPaths(editableDraft);
    const scopeLabel = targetLabel(targets, editableDraft);
    setLoading(true);
    setStreamStatus('正在改写选中内容');
    const assistantId = pushMessage('assistant', '', {
      thinking: 'running',
      thinkingDetails: initialThinkingDetails || [`改写范围：${scopeLabel}`],
      thinkingOpen: false,
    });
    const baseJob: ActiveDistillJob = {
      jobId: '',
      kind: 'rewrite',
      assistantId,
      lastSeq: 0,
      status: 'queued',
      previousDraft,
      targets,
    };
    const controller = new AbortController();
    let receivedMessageChunk = false;
    manualStopRef.current = false;
    abortRef.current = controller;
    try {
      await streamPost(
        `/api/enterprise/skills/${encodeURIComponent(editableDraft.skill_id)}/rewrite/stream`,
        {
          tenant_id: TENANT_ID,
          agent_id: activeAgentId || undefined,
          current_skill: editableDraft,
          instruction: text,
          model_config_id: selectedRewriteModelId || undefined,
          target_path: targets[0],
          target_paths: targets,
          target_label: scopeLabel,
          conversation: (conversationOverride || messages).map((item) => ({ role: item.role, content: item.content })),
        },
        (item) => {
          trackActiveJobEvent(item, baseJob);
          if (item.event === 'status') {
            appendThinkingDetail(assistantId, String(item.data.text || '正在处理'));
            return;
          }
          if (item.event === 'message_chunk') {
            const content = typeof item.data.content === 'string' ? item.data.content : '';
            if (content) {
              receivedMessageChunk = true;
              appendMessage(assistantId, content);
            }
            return;
          }
          if (item.event === 'complete') {
            const nextDraft = lockSkillIdForDraft(item.data.draft_skill as SkillCard, lockedSkillId);
            const nextWarnings = Array.isArray(item.data.warnings) ? item.data.warnings.map(String) : [];
            const nextToolSuggestions = normalizeToolSuggestions(item.data.tool_suggestions);
            const changedPaths = diffTargetPaths(previousDraft, nextDraft, targets);
            const changedLabel = changedPaths.length > 0 ? targetLabel(changedPaths, nextDraft) : '未检测到结构变化';
            appendThinkingDetail(assistantId, `模型返回改写结果：${changedLabel}`);
            appendThinkingDetail(assistantId, '右侧已更新预览，等待确认或拒绝');
            animateDraftChange(previousDraft, nextDraft, changedPaths);
            setPendingChange({ assistantId, previousDraft, nextDraft, changedPaths });
            setSelectedPaths((current) => reconcileSelectedPaths(current, nextDraft));
            setStreamStatus('改写完成');
            if (!receivedMessageChunk) {
              updateMessage(
                assistantId,
                String(item.data.assistant_message || '已完成局部改写。'),
                {
                  thinking: 'done',
                  warnings: nextWarnings,
                  toolSuggestions: nextToolSuggestions,
                  actionState: 'pending',
                  operations: changedPaths.length
                    ? [{ kind: 'skill_change', label: `改写：${changedLabel}`, skillId: nextDraft.skill_id }]
                    : [],
                },
              );
            } else {
              updateMessage(assistantId, undefined, {
                thinking: 'done',
                warnings: nextWarnings,
                toolSuggestions: nextToolSuggestions,
                actionState: 'pending',
                operations: changedPaths.length
                  ? [{ kind: 'skill_change', label: `改写：${changedLabel}`, skillId: nextDraft.skill_id }]
                  : [],
              });
            }
            if (nextToolSuggestions.length > 0) {
              void autoProbeToolSuggestions(assistantId, nextToolSuggestions);
            }
            setActiveJob(null);
            return;
          }
          if (item.event === 'error') {
            applyDistillFailure(assistantId, item.data.message, 'rewrite');
            setActiveJob(null);
          }
        },
        controller.signal,
      );
    } catch (error) {
      if (controller.signal.aborted && !manualStopRef.current) return;
      appendThinkingDetail(assistantId, '改写失败，已保留当前草稿');
      applyDistillFailure(assistantId, error, 'rewrite');
      if (controller.signal.aborted) {
        notify.info('已停止改写');
      } else {
        notify.error(error instanceof Error ? error.message : '改写失败');
      }
    } finally {
      finishStream(controller);
    }
  }

  /** 打开保存草稿弹窗：先应用待确认变更，并按已有版本号预填下一个版本。 */
  function openSaveReview(options: { clearAfterSave?: boolean } = {}) {
    const targetDraft = lockNullableSkillIdForDraft(pendingChange?.nextDraft || draft, lockedSkillId);
    if (!targetDraft) return;
    if (!hasSkillContentChanges(targetDraft, lastSavedDraft)) {
      notify.info('当前没有内容变化，无需保存草稿。');
      return;
    }
    confirmPendingChange(false);
    setClearAfterSave(Boolean(options.clearAfterSave));
    setSaveDraftSnapshot(targetDraft);
    setSaveName(targetDraft.name);
    setSaveDomain(targetDraft.business_domain || '');
    setSaveVersion(loadedSkill ? bumpSkillVersion(loadedSkill.version || targetDraft.version) : '1.0.0');
    setSaveReviewOpen(true);
  }

  /**
   * 保存当前草稿为新版本或新技能，成功后同步已保存快照。
   *
   * - 技能 ID 冲突（409）时自动改名重试，并在提示中告知新 ID。
   * - clearAfterSave 为真时保存完成后清空当前改写工作区。
   */
  async function saveDraft() {
    if (!saveReviewDraft) return;
    if (!hasSkillContentChanges(saveReviewDraft, lastSavedDraft)) {
      notify.info('当前没有内容变化，无需保存草稿。');
      return;
    }
    let finalDraft: SkillCard = canonicalizeSkillCapabilityRefs(
      normalizeSubflowNodes(lockSkillIdForDraft(saveReviewDraft, lockedSkillId)),
    );
    let renamedSkillId = '';
    try {
      let savedSkill: SkillRead;
      if (loadedSkill) {
        savedSkill = await api.put<SkillRead>(`/api/enterprise/skills/${loadedSkill.skill_id}${agentOnlyQuery}`, {
          tenant_id: TENANT_ID,
          content: finalDraft,
          status: loadedSkill.status,
        });
      } else {
        try {
          savedSkill = await api.post<SkillRead>(`/api/enterprise/skills${agentOnlyQuery}`, { tenant_id: TENANT_ID, content: finalDraft, status: 'published' });
        } catch (error) {
          if (!(error instanceof ApiError) || error.status !== 409) throw error;
          finalDraft = {
            ...cloneSkill(finalDraft),
            skill_id: uniqueDraftSkillId(finalDraft.skill_id),
          };
          renamedSkillId = finalDraft.skill_id;
          savedSkill = await api.post<SkillRead>(`/api/enterprise/skills${agentOnlyQuery}`, { tenant_id: TENANT_ID, content: finalDraft, status: 'published' });
        }
      }
      const savedContent = lockSkillIdForDraft(savedSkill.content, savedSkill.skill_id || lockedSkillId);
      if (savedContent !== savedSkill.content) {
        savedSkill = { ...savedSkill, content: savedContent };
      }
      setLoadedSkill(savedSkill);
      setDraft(savedContent);
      setLastSavedDraft(savedContent);
      setSaveDraftSnapshot(null);
      setHighlightedPaths([]);
      setDirtyPaths([]);
      setSaveReviewOpen(false);
      appendOperationToLatestMessage({
        kind: 'version_save',
        label: `保存版本 ${savedSkill.version}`,
        skillId: savedSkill.skill_id,
        version: savedSkill.version,
      });
      if (clearAfterSave) {
        setClearAfterSave(false);
        clearDistillWorkspace();
        notify.success('SOP 已保存，当前改写已清空');
      } else {
        notify.success(renamedSkillId ? `SOP ID 已存在，已另存为 ${renamedSkillId}` : 'SOP 已保存');
      }
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '保存失败');
    }
  }

  /** 停止当前生成或改写：请求服务端取消任务并中断本地流。 */
  function stopStream() {
    const jobId = activeJob?.jobId;
    manualStopRef.current = true;
    if (jobId) {
      void api.post(`/api/enterprise/skills/jobs/${encodeURIComponent(jobId)}/cancel`);
    }
    abortRef.current?.abort();
    abortRef.current = null;
    setLoading(false);
    setActiveJob(null);
    setStreamStatus('已停止');
  }

  /** 记录流式事件中的 job_id、序号与状态，供断线后按 after_seq 续传。 */
  function trackActiveJobEvent(item: { event: string; data: Record<string, unknown> }, baseJob: ActiveDistillJob) {
    const jobId = typeof item.data.job_id === 'string' ? item.data.job_id : baseJob.jobId;
    if (!jobId) return;
    const seq = typeof item.data.seq === 'number' ? item.data.seq : baseJob.lastSeq;
    const status = typeof item.data.status === 'string' ? item.data.status : baseJob.status;
    setActiveJob((current) => ({
      ...baseJob,
      ...(current?.jobId === jobId ? current : {}),
      jobId,
      lastSeq: Math.max(current?.jobId === jobId ? current.lastSeq : 0, seq || 0),
      status,
    }));
  }

  /** 处理续传流事件：复用正常流的消息追加、完成与失败分支。 */
  function handleResumedJobEvent(job: ActiveDistillJob, item: { event: string; data: Record<string, unknown> }) {
    trackActiveJobEvent(item, job);
    if (item.event === 'status') {
      appendThinkingDetail(job.assistantId, String(item.data.text || '正在处理'));
      return;
    }
    if (item.event === 'message_chunk') {
      const content = typeof item.data.content === 'string' ? item.data.content : '';
      if (content) appendMessage(job.assistantId, content);
      return;
    }
    if (item.event === 'complete') {
      if (job.kind === 'distill') {
        completeResumedDistillJob(job, item.data);
      } else {
        completeResumedRewriteJob(job, item.data);
      }
      setActiveJob(null);
      return;
    }
    if (item.event === 'error') {
      applyDistillFailure(job.assistantId, item.data.message, job.kind);
      setActiveJob(null);
      setLoading(false);
      return;
    }
    if (item.event === 'job_complete') {
      const status = String(item.data.status || '');
      if (status === 'failed') {
        applyDistillFailure(job.assistantId, item.data.error, job.kind);
        setActiveJob(null);
      }
    }
  }

  /** 续传场景下应用蒸馏任务完成结果：写入草稿、告警与工具建议。 */
  function completeResumedDistillJob(job: ActiveDistillJob, data: Record<string, unknown>) {
    const rawDraftSkill = data.draft_skill as SkillCard | undefined;
    if (!rawDraftSkill) return;
    const draftSkill = lockSkillIdForDraft(rawDraftSkill, lockedSkillId);
    const nextWarnings = Array.isArray(data.warnings) ? data.warnings.map(String) : [];
    const nextToolSuggestions = normalizeToolSuggestions(data.tool_suggestions);
    clearAnimationTimers();
    setDraft(draftSkill);
    setHighlightedPaths([]);
    setUpdatingPaths([]);
    setTextDiffs([]);
    setSelectedPaths(DEFAULT_TARGET_PATHS);
    appendThinkingDetail(job.assistantId, `已生成 SOP 草稿：${draftSkill.name}`);
    updateMessage(
      job.assistantId,
      `已生成「${draftSkill.name}」草稿。你可以在右侧选择一个或多个区域继续改写。`,
      {
        thinking: 'done',
        warnings: nextWarnings,
        toolSuggestions: nextToolSuggestions,
        operations: [{ kind: 'skill_change', label: `生成 SOP 草稿：${draftSkill.name}`, skillId: draftSkill.skill_id }],
      },
    );
    setStreamStatus('生成完成');
    if (nextToolSuggestions.length > 0) {
      void autoProbeToolSuggestions(job.assistantId, nextToolSuggestions);
    }
  }

  /** 续传场景下应用改写任务完成结果：生成待确认变更并播放差异动画。 */
  function completeResumedRewriteJob(job: ActiveDistillJob, data: Record<string, unknown>) {
    const rawNextDraft = data.draft_skill as SkillCard | undefined;
    if (!rawNextDraft) return;
    const nextDraft = lockSkillIdForDraft(rawNextDraft, lockedSkillId);
    const previousDraft = lockNullableSkillIdForDraft(job.previousDraft || draft, lockedSkillId);
    if (!previousDraft) {
      setDraft(nextDraft);
      updateMessage(job.assistantId, String(data.assistant_message || '已完成改写。'), { thinking: 'done' });
      return;
    }
    const targets = job.targets?.length ? job.targets : allTargetPaths(previousDraft);
    const nextWarnings = Array.isArray(data.warnings) ? data.warnings.map(String) : [];
    const nextToolSuggestions = normalizeToolSuggestions(data.tool_suggestions);
    const changedPaths = diffTargetPaths(previousDraft, nextDraft, targets);
    const changedLabel = changedPaths.length > 0 ? targetLabel(changedPaths, nextDraft) : '未检测到结构变化';
    appendThinkingDetail(job.assistantId, `模型返回改写结果：${changedLabel}`);
    appendThinkingDetail(job.assistantId, '右侧已更新预览，等待确认或拒绝');
    animateDraftChange(previousDraft, nextDraft, changedPaths);
    setPendingChange({ assistantId: job.assistantId, previousDraft, nextDraft, changedPaths });
    setSelectedPaths((current) => reconcileSelectedPaths(current, nextDraft));
    setStreamStatus('改写完成');
    updateMessage(job.assistantId, String(data.assistant_message || '已完成局部改写。'), {
      thinking: 'done',
      warnings: nextWarnings,
      toolSuggestions: nextToolSuggestions,
      actionState: 'pending',
      operations: changedPaths.length
        ? [{ kind: 'skill_change', label: `改写：${changedLabel}`, skillId: nextDraft.skill_id }]
        : [],
    });
    if (nextToolSuggestions.length > 0) {
      void autoProbeToolSuggestions(job.assistantId, nextToolSuggestions);
    }
  }

  /**
   * 上传并解析单个附件文档，把提取到的正文写入附件列表。
   *
   * - 仅允许 .md/.txt/.doc/.docx，不支持的类型直接提示错误。
   * - 解析请求可被 cancelAttachment 或清空工作区中断。
   */
  async function stageFileUpload(file: File) {
    if (loading) return;
    const suffix = file.name.toLowerCase().split('.').pop() || '';
    if (!['md', 'txt', 'doc', 'docx'].includes(suffix)) {
      notify.error('仅支持 .md、.doc、.docx、.txt 文件');
      return;
    }
    const id = `file_${Date.now()}_${Math.random().toString(16).slice(2)}`;
    const controller = new AbortController();
    uploadControllersRef.current[id] = controller;
    setAttachments((current) => [...current, { id, name: file.name, status: 'uploading' }]);
    try {
      const contentBase64 = await fileToBase64(file);
      if (controller.signal.aborted) return;
      const result = await api.postWithSignal<{ filename: string; text: string }>(
        '/api/enterprise/skills/files/extract',
        {
          filename: file.name,
          content_base64: contentBase64,
        },
        controller.signal,
      );
      setAttachments((current) =>
        current.map((item) =>
          item.id === id ? { id, name: result.filename, status: 'ready', text: result.text } : item,
        ),
      );
    } catch (error) {
      if (controller.signal.aborted) return;
      setAttachments((current) =>
        current.map((item) =>
          item.id === id
            ? { ...item, status: 'error', error: error instanceof Error ? error.message : '读取文件失败' }
            : item,
        ),
      );
    } finally {
      delete uploadControllersRef.current[id];
    }
  }

  /** 批量上传文件，逐个异步解析，不阻塞其余附件。 */
  function uploadFiles(files: File[]) {
    files.forEach((file) => {
      void stageFileUpload(file);
    });
  }

  /** 取消指定附件的解析请求并从附件列表移除。 */
  function cancelAttachment(id: string) {
    uploadControllersRef.current[id]?.abort();
    delete uploadControllersRef.current[id];
    setAttachments((current) => current.filter((item) => item.id !== id));
  }

  /** 粘贴文件时拦截默认行为，改为走附件上传流程。 */
  function handleComposerPaste(event: ClipboardEvent<HTMLTextAreaElement>) {
    const files = Array.from(event.clipboardData.files || []);
    if (files.length === 0) return;
    event.preventDefault();
    uploadFiles(files);
  }

  /** 拖拽进入时累加深度，仅在拖入文件时显示上传提示。 */
  function handleDragEnter(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    dragDepthRef.current += 1;
    if (event.dataTransfer.types.includes('Files')) setDragActive(true);
  }

  /** 拖拽悬停时阻止默认行为，允许文件落入对话卡片。 */
  function handleDragOver(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
  }

  /** 拖拽离开时递减深度，深度归零才取消高亮。 */
  function handleDragLeave(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
    if (dragDepthRef.current === 0) setDragActive(false);
  }

  /** 释放文件时结束拖拽状态并上传全部文件。 */
  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    dragDepthRef.current = 0;
    setDragActive(false);
    uploadFiles(Array.from(event.dataTransfer.files || []));
  }

  /** 打开工具建议详情弹窗，并用样例参数初始化 JSON 编辑框。 */
  function openToolDetail(messageId: string, suggestion: ToolSuggestionItem) {
    setToolDetailMessageId(messageId);
    setToolDetail(suggestion);
    setProbeArgsText(JSON.stringify(suggestion.sample_arguments || {}, null, 2));
  }

  /**
   * 自动探测新工具建议：逐个调用接口测试并写入学习记录。
   *
   * - 已有工具只记录匹配结果，不发起探测。
   * - 探测结果决定后续是否允许确认新增。
   */
  async function autoProbeToolSuggestions(messageId: string, suggestions: ToolSuggestionItem[]) {
    const extractedSuggestions = suggestions.filter((suggestion) => suggestion.resolution_status !== 'incomplete');
    const pendingSuggestions = suggestions.filter(
      (suggestion) => toolSuggestionResolution(suggestion) === 'new_candidate' && !suggestion.probe_result,
    );
    if (extractedSuggestions.length === 0) return;

    appendThinkingDetail(
      messageId,
      `正在抽取工具：${extractedSuggestions.map((item) => item.display_name || item.name).join('、')}`,
    );
    const existingSuggestions = suggestions.filter((suggestion) => toolSuggestionResolution(suggestion) === 'existing');
    existingSuggestions.forEach((suggestion) => {
      appendThinkingDetail(messageId, `已匹配现有工具：${suggestion.matched_tool_display_name || suggestion.display_name || suggestion.name}`);
    });
    if (pendingSuggestions.length === 0) return;
    appendThinkingDetail(messageId, '正在测试工具接口');
    setStreamStatus('正在测试工具接口');

    let successCount = 0;
    let failureCount = 0;
    for (const suggestion of pendingSuggestions) {
      const result = await probeToolSuggestion(messageId, suggestion, {
        silent: true,
        allowWhileLoading: true,
      });
      if (!result) {
        failureCount += 1;
        appendThinkingDetail(messageId, `工具测试失败：${suggestion.display_name || suggestion.name}`);
        continue;
      }
      if (result.success) {
        successCount += 1;
        appendThinkingDetail(messageId, `工具测试成功：${suggestion.display_name || suggestion.name}`);
      } else {
        failureCount += 1;
        const reason = result.error?.message ? `，${result.error.message}` : '';
        appendThinkingDetail(messageId, `工具测试失败：${suggestion.display_name || suggestion.name}${reason}`);
      }
    }

    appendThinkingDetail(messageId, `工具测试完成：${successCount} 个成功，${failureCount} 个失败`);
    setStreamStatus('工具测试完成');
  }

  /**
   * 对单个工具建议执行接口探测，并把结果写回该建议。
   *
   * - 仅处理 new_candidate 且不在探测中的建议，否则返回 null。
   * - 缺少样例参数时直接返回失败结果，不发起请求。
   * - silent 为真时不弹 toast，供批量自动探测复用。
   */
  async function probeToolSuggestion(
    messageId: string,
    suggestion: ToolSuggestionItem,
    options: ProbeToolOptions = {},
  ): Promise<ToolProbeResponse | null> {
    if (toolSuggestionResolution(suggestion) !== 'new_candidate') return null;
    if ((!options.allowWhileLoading && loading) || suggestion.probeStatus === 'probing') return null;
    const args = options.sampleArguments || suggestion.sample_arguments || {};
    if (Object.keys(args).length === 0) {
      if (!options.silent) notify.warning('缺少样例参数，无法测试接口');
      const result: ToolProbeResponse = {
        success: false,
        inferred_output_schema: {},
        error: { code: 'MISSING_SAMPLE_ARGUMENTS', message: '缺少样例参数，无法测试接口' },
      };
      setToolSuggestionPatch(messageId, suggestion.name, { probeStatus: 'error', probe_result: result });
      return result;
    }
    setToolSuggestionPatch(messageId, suggestion.name, { probeStatus: 'probing' });
    try {
      const payload = {
        ...toolPayloadFromSuggestion(suggestion, lockNullableSkillIdForDraft(pendingChange?.nextDraft || draft, lockedSkillId)?.skill_id),
        sample_arguments: args,
      };
      const result = await api.post<ToolProbeResponse>('/api/enterprise/tools/probe', payload);
      const nextOutputSchema = result.success && result.inferred_output_schema
        ? result.inferred_output_schema
        : suggestion.output_schema;
      setToolSuggestionPatch(messageId, suggestion.name, {
        probeStatus: result.success ? 'success' : 'error',
        probe_result: result,
        sample_arguments: args,
        output_schema: nextOutputSchema || {},
      });
      if (result.success) {
        if (!options.silent) notify.success('接口测试成功');
      } else {
        if (!options.silent) notify.error(result.error?.message || '接口测试失败');
      }
      return result;
    } catch (error) {
      const result: ToolProbeResponse = {
        success: false,
        inferred_output_schema: {},
        error: { code: 'CLIENT_ERROR', message: error instanceof Error ? error.message : '接口测试失败' },
      };
      setToolSuggestionPatch(messageId, suggestion.name, {
        probeStatus: 'error',
        probe_result: result,
      });
      if (!options.silent) notify.error(result.error?.message || '接口测试失败');
      return result;
    }
  }

  /** 校验详情弹窗中的 JSON 样例参数，合法时写回对应工具建议。 */
  function applyProbeArgumentsFromDetail() {
    if (!toolDetail || !toolDetailMessageId) return;
    const parsed = parseJsonObject(probeArgsText);
    if (!parsed) {
      notify.error('样例参数必须是 JSON 对象');
      return;
    }
    setToolSuggestionPatch(toolDetailMessageId, toolDetail.name, { sample_arguments: parsed });
    setToolDetail({ ...toolDetail, sample_arguments: parsed });
    notify.success('样例参数已更新');
  }

  /** 用详情弹窗中填写的样例参数触发一次工具探测。 */
  function probeToolDetail() {
    if (!toolDetail || !toolDetailMessageId) return;
    const parsed = parseJsonObject(probeArgsText);
    if (!parsed) {
      notify.error('样例参数必须是 JSON 对象');
      return;
    }
    void probeToolSuggestion(toolDetailMessageId, { ...toolDetail, sample_arguments: parsed }, { sampleArguments: parsed });
  }

  /** 确认新增单个工具建议；所有建议处理完后统一写入 SOP 草稿。 */
  async function confirmToolSuggestion(messageId: string, suggestion: ToolSuggestionItem) {
    if (loading) return;
    if (toolSuggestionResolution(suggestion) !== 'new_candidate') {
      notify.warning('该工具不是可新增候选');
      return;
    }
    if (!suggestion.probe_result?.success) {
      notify.warning('请先测试接口成功后再新增工具');
      return;
    }
    const nextSuggestions = nextToolSuggestionsWithPatch(messageId, suggestion.name, { status: 'accepted' });
    setToolSuggestionStatus(messageId, suggestion.name, 'accepted');
    const shouldCommit = toolSuggestionSelectionsComplete(nextSuggestions);
    if (!shouldCommit) {
      notify.success('已确认，等待其他工具建议处理完成后统一更新 SOP');
      return;
    }
    await commitToolSuggestionSelections(messageId, nextSuggestions);
  }

  /**
   * 落库已接受的工具建议，并把工具调用动作合并进当前草稿。
   *
   * - 已存在的工具（409）复用现有记录，不重复创建。
   * - 本轮确认的变更会记录到消息操作历史，供后续回退。
   */
  async function commitToolSuggestionSelections(messageId: string, suggestions: ToolSuggestionItem[]) {
    const activeDraft = lockNullableSkillIdForDraft(pendingChange?.nextDraft || draft, lockedSkillId);
    const acceptedSuggestions = suggestions.filter(
      (item) => toolSuggestionResolution(item) === 'new_candidate' && item.status === 'accepted',
    );
    if (acceptedSuggestions.length === 0) {
      notify.info('所有工具建议已拒绝，SOP 草稿未变更');
      return;
    }
    try {
      const createdTools: ToolRead[] = [];
      const createdNewTools: ToolRead[] = [];
      for (const suggestion of acceptedSuggestions) {
        if (!suggestion.probe_result?.success) {
          throw new Error(`工具「${suggestion.display_name || suggestion.name}」尚未测试通过`);
        }
        const payload = toolPayloadFromSuggestion(suggestion, activeDraft?.skill_id);
        let createdTool: ToolRead;
        let createdNewTool = false;
        try {
          createdTool = await api.post<ToolRead>(`/api/enterprise/tools${agentQuery ? `?${agentQuery.slice(1)}` : ''}`, payload);
          createdNewTool = true;
        } catch (error) {
          if (!(error instanceof ApiError) || error.status !== 409) throw error;
          createdTool = toolReadFromSuggestion(suggestion, activeDraft?.skill_id);
        }
        createdTools.push(createdTool);
        if (createdNewTool) createdNewTools.push(createdTool);
        setToolSuggestionStatus(messageId, suggestion.name, 'created');
      }
      setTools((current) => createdTools.reduce((nextTools, tool) => upsertToolRead(nextTools, tool), current));
      createdNewTools.forEach((createdTool) => {
        appendOperationToMessage(messageId, {
          kind: 'tool_add',
          label: `新增工具：${createdTool.display_name || createdTool.name}`,
          toolId: createdTool.id,
          toolName: createdTool.name,
        });
      });
      if (!activeDraft) return;
      const toolNames = acceptedSuggestions.map((item) => item.display_name || item.name).join('、');
      const nextDraft = lockSkillIdForDraft(
        integrateToolSuggestionsIntoDraft(
          activeDraft,
          acceptedSuggestions,
          pendingChange?.changedPaths?.length ? pendingChange.changedPaths : selectedPaths,
        ),
        lockedSkillId,
      );
      const changedPaths = diffTargetPaths(activeDraft, nextDraft, allTargetPaths(nextDraft));
      confirmPendingChange(false);
      clearAnimationTimers();
      setDraft(nextDraft);
      setPendingChange(null);
      setUpdatingPaths([]);
      setTextDiffs([]);
      if (changedPaths.length > 0) {
        setHighlightedPaths((current) => mergePaths(current, changedPaths));
        setDirtyPaths((current) => mergePaths(current, changedPaths));
        appendOperationToMessage(messageId, {
          kind: 'skill_change',
          label: `接入工具：${toolNames}`,
          skillId: nextDraft.skill_id,
        });
      }
      notify.success(`已确认 ${acceptedSuggestions.length} 个工具，当前草稿已局部更新`);
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '新增工具或更新 SOP 失败');
    }
  }

  /** 拒绝单个工具建议：移除草稿中对应动作，全部处理完后统一提交。 */
  function rejectToolSuggestion(messageId: string, toolName: string) {
    const nextSuggestions = nextToolSuggestionsWithPatch(messageId, toolName, { status: 'rejected' });
    setToolSuggestionStatus(messageId, toolName, 'rejected');
    removeToolActionFromDraft(toolName);
    if (toolSuggestionSelectionsComplete(nextSuggestions)) {
      void commitToolSuggestionSelections(messageId, nextSuggestions);
    }
  }

  /** 从当前草稿与待确认变更中删除指定工具的调用动作。 */
  function removeToolActionFromDraft(toolName: string) {
    setDraft((current) => (current ? lockSkillIdForDraft(removeToolActionFromSkill(current, toolName), lockedSkillId) : current));
    setPendingChange((current) =>
      current
        ? {
            ...current,
            nextDraft: lockSkillIdForDraft(removeToolActionFromSkill(current.nextDraft, toolName), lockedSkillId),
          }
        : current,
    );
  }

  /** 更新某条消息里指定工具建议的处理状态。 */
  function setToolSuggestionStatus(messageId: string, toolName: string, status: ToolSuggestionItem['status']) {
    setToolSuggestionPatch(messageId, toolName, { status });
  }

  /** 按补丁计算指定工具建议的下一份建议列表，不改动组件状态。 */
  function nextToolSuggestionsWithPatch(
    messageId: string,
    toolName: string,
    patch: Partial<ToolSuggestionItem>,
  ): ToolSuggestionItem[] {
    const targetMessage = messages.find((item) => item.id === messageId);
    return (targetMessage?.toolSuggestions || []).map((suggestion) =>
      suggestion.name === toolName ? { ...suggestion, ...patch } : suggestion,
    );
  }

  /** 把补丁写入消息中的工具建议，并同步已打开的详情弹窗。 */
  function setToolSuggestionPatch(messageId: string, toolName: string, patch: Partial<ToolSuggestionItem>) {
    setMessages((current) =>
      current.map((item) =>
        item.id === messageId
          ? {
              ...item,
              toolSuggestions: (item.toolSuggestions || []).map((suggestion) =>
                suggestion.name === toolName ? { ...suggestion, ...patch } : suggestion,
              ),
            }
          : item,
      ),
    );
    setToolDetail((current) => (current?.name === toolName ? { ...current, ...patch } : current));
  }

  /** 关闭保存弹窗并清理保存快照与清空标记。 */
  function closeSaveReview() {
    setSaveReviewOpen(false);
    setSaveDraftSnapshot(null);
    setClearAfterSave(false);
  }

  /** 处理清空入口：有未保存变更时二次确认，否则直接进入清空确认。 */
  function handleClearClick() {
    if (loading) return;
    if (!hasUnsavedSkillChanges()) {
      setClearNewConfirm({
        title: skillId ? '清空并新建 SOP？' : '清空当前改写？',
        description: skillId
          ? '清空只会进入一个新的 SOP 草稿工作台，不会删除或替换当前正在编辑的 SOP。'
          : '当前技能没有未保存变更，确认清空当前改写内容和对话记录？',
      });
      return;
    }
    setClearConfirmOpen(true);
  }

  /** 判断当前草稿（含待确认变更）相对已保存内容是否有未保存修改。 */
  function hasUnsavedSkillChanges() {
    const targetDraft = lockNullableSkillIdForDraft(pendingChange?.nextDraft || draft, lockedSkillId);
    if (!targetDraft) return false;
    return hasSkillContentChanges(targetDraft, lastSavedDraft);
  }

  /**
   * 清空当前改写工作区：中断进行中的任务并跳转到新的空白工作台。
   *
   * - 会移除当前与目标会话的本地缓存，重置全部草稿与对话状态。
   */
  function clearDistillWorkspace() {
    clearAnimationTimers();
    abortRef.current?.abort();
    Object.values(uploadControllersRef.current).forEach((controller) => controller.abort());
    uploadControllersRef.current = {};
    const nextWorkspaceId = createDistillWorkspaceId();
    const nextParams = new URLSearchParams({ mode: 'create', workspace_id: nextWorkspaceId });
    if (activeAgentId) nextParams.set('agent_id', activeAgentId);
    const nextRoute = `/enterprise/skills/distill?${nextParams.toString()}`;
    const nextCacheKey = `skill-distill:${TENANT_ID}:${activeAgentId || 'default'}:create:${nextWorkspaceId}`;
    removeDistillCache(cacheKey);
    removeDistillCache(nextCacheKey);
    setCacheReady(false);
    setHydratedCacheKey('');
    setDraft(null);
    setLoadedSkill(null);
    setLastSavedDraft(null);
    setMessages(DEFAULT_DISTILL_MESSAGES);
    setInput('');
    setSelectedPaths(DEFAULT_TARGET_PATHS);
    setHighlightedPaths([]);
    setUpdatingPaths([]);
    setDirtyPaths([]);
    setTextDiffs([]);
    setPendingChange(null);
    setSaveDraftSnapshot(null);
    setSaveReviewOpen(false);
    setClearConfirmOpen(false);
    setClearAfterSave(false);
    setAttachments([]);
    setStreamStatus('');
    setActiveJob(null);
    if (skillId || workspaceId !== nextWorkspaceId) {
      navigate(nextRoute, { replace: true });
    } else {
      setHydratedCacheKey(cacheKey);
      setCacheReady(true);
    }
  }

  /** 切换单个改写目标的选中状态。 */
  function toggleTarget(target: TargetSelection) {
    setSelectedPaths((current) => {
      if (current.includes(target.path)) {
        return current.filter((path) => path !== target.path);
      }
      return [...current, target.path];
    });
  }

  /** 应用源码区的就地编辑，并把该路径标记为改动与高亮。 */
  function handleSourceEdit(nextDraft: SkillCard, path: string) {
    const lockedDraft = lockSkillIdForDraft(nextDraft, lockedSkillId);
    setDraft(lockedDraft);
    setDirtyPaths((current) => mergePaths(current, [path]));
    setHighlightedPaths((current) => mergePaths(current, [path]));
  }

  /** 全选或取消全选所有改写目标。 */
  function toggleAllTargets() {
    setSelectedPaths(allSelected ? [] : allPaths);
  }

  /** 追加一条对话消息并返回其 ID，便于后续流式更新。 */
  function pushMessage(role: ChatItem['role'], content: string, extra: Partial<ChatItem> = {}) {
    const id = `${role}_${Date.now()}_${Math.random().toString(16).slice(2)}`;
    setMessages((current) => [...current, { id, role, content, createdAt: new Date().toISOString(), ...extra }]);
    return id;
  }

  /** 按 ID 局部更新消息；content 为 undefined 时保留原内容。 */
  function updateMessage(id: string, content?: string, extra: Partial<ChatItem> = {}) {
    setMessages((current) =>
      current.map((item) => (item.id === id ? { ...item, ...(content === undefined ? {} : { content }), ...extra } : item)),
    );
  }

  /** 把失败信息格式化为可展开的失败卡片，并标记思考已结束。 */
  function applyDistillFailure(
    assistantId: string,
    error: unknown,
    kind: ActiveDistillJob['kind'],
  ) {
    const failure = buildDistillFailure(error, kind);
    updateMessage(assistantId, '当前草稿未变更，可以调整要求或模型配置后重试。', {
      thinking: 'done',
      failure,
      failureOpen: false,
    });
  }

  /** 展开或收起指定消息的失败详情。 */
  function toggleFailureDetails(messageId: string) {
    setMessages((current) =>
      current.map((item) =>
        item.id === messageId ? { ...item, failureOpen: !item.failureOpen } : item,
      ),
    );
  }

  /** 复制失败摘要、错误码与详细信息到剪贴板。 */
  async function copyFailureDetails(failure: DistillFailure) {
    const text = [
      `${failure.summary} · ${failure.stage}`,
      failure.code ? `错误码：${failure.code}` : '',
      failure.detail,
    ]
      .filter(Boolean)
      .join('\n');
    try {
      await copyTextToClipboard(text);
      notify.success('错误详情已复制');
    } catch {
      notify.error('复制错误详情失败');
    }
  }

  /** 在指定消息的操作历史末尾追加一条操作记录。 */
  function appendOperationToMessage(id: string, operation: DistillHistoryOperation) {
    setMessages((current) =>
      current.map((item) =>
        item.id === id ? { ...item, operations: [...(item.operations || []), operation] } : item,
      ),
    );
  }

  /** 把操作记录追加到最后一条对话消息上。 */
  function appendOperationToLatestMessage(operation: DistillHistoryOperation) {
    setMessages((current) => {
      const index = [...current].reverse().findIndex((item) => item.role === 'assistant' || item.role === 'user');
      if (index < 0) return current;
      const targetIndex = current.length - 1 - index;
      return current.map((item, currentIndex) =>
        currentIndex === targetIndex ? { ...item, operations: [...(item.operations || []), operation] } : item,
      );
    });
  }

  /** 向指定消息追加流式文本片段。 */
  function appendMessage(id: string, content: string) {
    setMessages((current) =>
      current.map((item) => (item.id === id ? { ...item, content: `${item.content}${content}` } : item)),
    );
  }

  /** 追加学习记录明细，自动忽略空内容与相邻重复项。 */
  function appendThinkingDetail(id: string, detail: string) {
    const nextDetail = detail.trim();
    if (!nextDetail) return;
    setMessages((current) =>
      current.map((item) => {
        if (item.id !== id) return item;
        const previous = item.thinkingDetails || [];
        if (previous[previous.length - 1] === nextDetail) return item;
        return { ...item, thinkingDetails: [...previous, nextDetail] };
      }),
    );
  }

  /** 展开或收起指定消息的学习记录。 */
  function toggleThinking(id: string) {
    setMessages((current) =>
      current.map((item) => (item.id === id ? { ...item, thinkingOpen: !item.thinkingOpen } : item)),
    );
  }

  /** 收尾一次流式请求：解除当前中断器引用并结束加载态。 */
  function finishStream(controller: AbortController) {
    if (abortRef.current === controller) abortRef.current = null;
    setLoading(false);
  }

  /** 为编辑历史消息创建深度拷贝快照，便于回退到发送前状态。 */
  function createHistorySnapshot(): DistillHistorySnapshot {
    return {
      draft: draft ? cloneSkill(draft) : null,
      loadedSkill: loadedSkill ? cloneSkillRead(loadedSkill) : null,
      lastSavedDraft: lastSavedDraft ? cloneSkill(lastSavedDraft) : null,
      selectedPaths: [...selectedPaths],
      highlightedPaths: [...highlightedPaths],
      updatingPaths: [...updatingPaths],
      dirtyPaths: [...dirtyPaths],
      textDiffs: textDiffs.map((item) => ({ ...item })),
      pendingChange: pendingChange
        ? {
            assistantId: pendingChange.assistantId,
            previousDraft: cloneSkill(pendingChange.previousDraft),
            nextDraft: cloneSkill(pendingChange.nextDraft),
            changedPaths: [...pendingChange.changedPaths],
          }
        : null,
      viewMode,
      tools: tools.map((tool) => ({ ...tool })),
      attachments: attachments.map((item) => ({ ...item })),
      streamStatus,
    };
  }

  /**
   * 用历史快照恢复草稿、选中路径、待确认变更与工具列表。
   *
   * - 恢复前会中断进行中的流式请求并清理差异动画。
   */
  function restoreHistorySnapshot(snapshot: DistillHistorySnapshot) {
    clearAnimationTimers();
    abortRef.current?.abort();
    const snapshotLockedSkillId = snapshot.loadedSkill?.skill_id || lockedSkillId;
    setDraft(lockNullableSkillIdForDraft(snapshot.draft ? cloneSkill(snapshot.draft) : null, snapshotLockedSkillId));
    setLoadedSkill(snapshot.loadedSkill ? cloneSkillRead(snapshot.loadedSkill) : null);
    setLastSavedDraft(lockNullableSkillIdForDraft(snapshot.lastSavedDraft ? cloneSkill(snapshot.lastSavedDraft) : null, snapshotLockedSkillId));
    setSelectedPaths([...snapshot.selectedPaths]);
    setHighlightedPaths([...snapshot.highlightedPaths]);
    setUpdatingPaths([...snapshot.updatingPaths]);
    setDirtyPaths([...snapshot.dirtyPaths]);
    setTextDiffs(snapshot.textDiffs.map((item) => ({ ...item })));
    setPendingChange(lockPendingChangeSkillId(
      snapshot.pendingChange
        ? {
            assistantId: snapshot.pendingChange.assistantId,
            previousDraft: cloneSkill(snapshot.pendingChange.previousDraft),
            nextDraft: cloneSkill(snapshot.pendingChange.nextDraft),
            changedPaths: [...snapshot.pendingChange.changedPaths],
          }
        : null,
      snapshotLockedSkillId,
    ));
    setViewMode(snapshot.viewMode);
    setTools(snapshot.tools.map((tool) => ({ ...tool })));
    setAttachments(snapshot.attachments.filter((item) => item.status !== 'uploading').map((item) => ({ ...item })));
    setStreamStatus(snapshot.streamStatus);
    setActiveJob(null);
    setLoading(false);
  }

  /** 确认待确认变更：采用新草稿并标记该条消息为已确认。 */
  function confirmPendingChange(showToast = true) {
    if (!pendingChange) return;
    clearAnimationTimers();
    setDraft(lockSkillIdForDraft(pendingChange.nextDraft, lockedSkillId));
    setUpdatingPaths([]);
    setTextDiffs([]);
    updateMessage(pendingChange.assistantId, undefined, { actionState: 'confirmed' });
    setPendingChange(null);
    if (showToast) notify.success('已确认改写');
  }

  /** 拒绝待确认变更：回滚到改写前草稿并标记该条消息为已拒绝。 */
  function rejectPendingChange() {
    if (!pendingChange) return;
    clearAnimationTimers();
    setDraft(lockSkillIdForDraft(pendingChange.previousDraft, lockedSkillId));
    setHighlightedPaths([]);
    setUpdatingPaths([]);
    setTextDiffs([]);
    updateMessage(pendingChange.assistantId, undefined, { actionState: 'rejected' });
    setPendingChange(null);
    notify.info('已拒绝改写并还原');
  }

  /** 进入历史用户消息的编辑态；生成中或非用户消息不处理。 */
  function requestEditHistoryMessage(item: ChatItem, index: number) {
    if (loading || item.role !== 'user') return;
    setEditingMessage({ id: item.id, text: visibleChatContent(item) });
  }

  /** 复制历史消息的可见文本到剪贴板。 */
  async function copyHistoryMessage(item: ChatItem) {
    const text = visibleChatContent(item);
    try {
      await navigator.clipboard.writeText(text);
      notify.success('已复制');
    } catch {
      notify.error('复制失败');
    }
  }

  /** 退出历史消息编辑态，不提交任何修改。 */
  function cancelEditingMessage() {
    setEditingMessage(null);
  }

  /**
   * 提交编辑后的历史消息：按需回退后续操作并重新生成。
   *
   * - 消息带快照时可能弹出二次确认，确认后重跑后续步骤。
   * - 无快照时只替换该条消息的文本。
   */
  function submitEditingMessage() {
    if (!editingMessage || loading) return;
    const text = editingMessage.text.trim();
    if (!text) return;
    const index = messages.findIndex((item) => item.id === editingMessage.id);
    const item = index >= 0 ? messages[index] : null;
    if (!item || item.role !== 'user') {
      setEditingMessage(null);
      return;
    }
    const outgoingText = buildEditedOutgoingText(item, text);
    const snapshot = item.snapshotBefore;
    if (!snapshot) {
      updateMessage(item.id, text, { outgoingText });
      setEditingMessage(null);
      return;
    }
    const rollbackOperations = collectRollbackOperations(messages.slice(index + 1));
    if (rollbackOperations.length === 0) {
      void rerunEditedMessage(index, snapshot, rollbackOperations, text, outgoingText);
      return;
    }
    setRerunConfirm({ index, snapshot, rollbackOperations, text, outgoingText });
  }

  /** 回退历史操作后以编辑后的文本重新执行生成或改写。 */
  async function rerunEditedMessage(
    index: number,
    snapshot: DistillHistorySnapshot,
    operations: DistillHistoryOperation[],
    displayText: string,
    outgoingText: string,
  ) {
    try {
      await rollbackPersistedOperations(snapshot, operations);
      const snapshotLockedSkillId = snapshot.loadedSkill?.skill_id || lockedSkillId;
      const confirmedDraft = lockNullableSkillIdForDraft(snapshot.pendingChange?.nextDraft || snapshot.draft, snapshotLockedSkillId);
      restoreHistorySnapshot({
        ...snapshot,
        draft: confirmedDraft ? cloneSkill(confirmedDraft) : null,
        pendingChange: null,
        updatingPaths: [],
        textDiffs: [],
      });
      const editedUser: ChatItem = {
        id: `user_${Date.now()}_${Math.random().toString(16).slice(2)}`,
        role: 'user',
        content: displayText,
        outgoingText,
        createdAt: new Date().toISOString(),
        snapshotBefore: snapshot,
      };
      const previousMessages = messages.slice(0, index);
      const nextMessages = [...previousMessages, editedUser];
      setMessages(nextMessages);
      setEditingMessage(null);
      if (!confirmedDraft) {
        await createDraftFromText(outgoingText);
        return;
      }
      await rewriteSelectedTarget(outgoingText, confirmedDraft, undefined, undefined, nextMessages);
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '回退失败');
    }
  }

  /**
   * 回退已落库的操作：删除新建工具、还原技能内容并清理新增版本。
   *
   * - 回退失败时仅忽略单条错误，随后仍按快照恢复本地状态。
   */
  async function rollbackPersistedOperations(
    snapshot: DistillHistorySnapshot,
    operations: DistillHistoryOperation[],
  ) {
    const toolOps = operations.filter((operation) => operation.kind === 'tool_add' && operation.toolId);
    for (const operation of toolOps) {
      try {
        await api.delete(`/api/enterprise/tools/${encodeURIComponent(String(operation.toolId))}?tenant_id=${TENANT_ID}${agentQuery}`);
      } catch {
        // Tool may already have been removed. Local state is restored from the snapshot below.
      }
    }

    const versionOps = operations.filter((operation) => operation.kind === 'version_save' && operation.skillId);
    for (const operation of versionOps) {
      const skillId = String(operation.skillId);
      if (snapshot.loadedSkill) {
        await api.put<SkillRead>(`/api/enterprise/skills/${encodeURIComponent(snapshot.loadedSkill.skill_id)}${agentOnlyQuery}`, {
          tenant_id: TENANT_ID,
          content: snapshot.loadedSkill.content,
          status: snapshot.loadedSkill.status,
        });
        if (operation.version && operation.version !== snapshot.loadedSkill.version) {
          try {
            await api.delete(
              `/api/enterprise/skills/${encodeURIComponent(skillId)}/versions/${encodeURIComponent(operation.version)}?tenant_id=${TENANT_ID}${agentQuery}`,
            );
          } catch {
            // A saved version may be shared with current state or already removed. The active draft has been restored.
          }
        }
      } else {
        try {
          await api.delete(`/api/enterprise/skills/${encodeURIComponent(skillId)}?tenant_id=${TENANT_ID}${agentQuery}`);
        } catch {
          // If the skill was not persisted, there is nothing else to roll back.
        }
      }
    }
  }

  /**
   * 播放草稿差异动画：先标记改动路径，再逐字回放新旧文本。
   *
   * - markDelay 为动画启动前的标记停留时长（毫秒）。
   * - 无改动路径时直接切换到新草稿。
   */
  function animateDraftChange(
    previousDraft: SkillCard,
    nextDraft: SkillCard,
    changedPaths: string[],
    markDelay = 520,
  ) {
    clearAnimationTimers();
    const lockedPreviousDraft = lockSkillIdForDraft(previousDraft, lockedSkillId);
    const lockedNextDraft = lockSkillIdForDraft(nextDraft, lockedSkillId);
    const paths = changedPaths;
    if (paths.length === 0) {
      setDraft(lockedNextDraft);
      setHighlightedPaths([]);
      setUpdatingPaths([]);
      setTextDiffs([]);
      return;
    }
    const nextTextDiffs = collectTextDiffs(lockedPreviousDraft, lockedNextDraft, paths);
    setHighlightedPaths(paths);
    setUpdatingPaths(paths);
    setTextDiffs(nextTextDiffs);
    setDraft(lockedPreviousDraft);
    const startTimer = window.setTimeout(() => {
      setTextDiffs((current) => current.map((diff) => ({ ...diff, phase: 'type', progress: 0 })));
      const steps = 24;
      let tick = 0;
      const interval = window.setInterval(() => {
        tick += 1;
        const progress = Math.min(tick / steps, 1);
        setTextDiffs((current) => current.map((diff) => ({ ...diff, phase: 'type', progress })));
        setDraft(typedDraft(lockedPreviousDraft, lockedNextDraft, nextTextDiffs, progress));
        if (progress >= 1) {
          window.clearInterval(interval);
          animationTimersRef.current = animationTimersRef.current.filter((timer) => timer !== interval);
          setTextDiffs((current) => current.map((diff) => ({ ...diff, phase: 'settled', progress: 1 })));
          setDraft(lockedNextDraft);
          setUpdatingPaths([]);
          setDirtyPaths((current) => mergePaths(current, paths));
        }
      }, 38);
      animationTimersRef.current.push(interval);
    }, markDelay);
    animationTimersRef.current.push(startTimer);
  }

  /** 清理全部差异动画定时器，避免卸载或切换后继续写入状态。 */
  function clearAnimationTimers() {
    animationTimersRef.current.forEach((timer) => {
      window.clearTimeout(timer);
      window.clearInterval(timer);
    });
    animationTimersRef.current = [];
  }

  const pageTitle = mode === 'create' && !skillId ? '新建 SOP' : '编辑 SOP';

  return (
    <div className={DISTILL_PAGE_CLASS}>
      <AppHeader className="shrink-0" onLogout={onLogout} userName={currentUser?.username} title={pageTitle} />
      <div className={DISTILL_ACTIONS_CLASS}>
        <UIButton variant="outline" className={RETURN_BUTTON_CLASS} onClick={() => navigate('/enterprise/skills')}>
          <ArrowLeftOutlined />
          返回
        </UIButton>
      </div>
      <div className={WORKBENCH_CLASS}>
        <DistillSectionCard
          className={cn(
            CHAT_CARD_CLASS,
            'h-full min-h-0',
            dragActive && CHAT_CARD_DRAGGING_CLASS,
            flowFullscreen && flowAssistantPanelOpen && CHAT_CARD_FULLSCREEN_CLASS,
          )}
          bodyClassName={CHAT_CARD_BODY_CLASS}
          title={flowFullscreen ? 'AI 修改' : '对话蒸馏'}
          extra={flowFullscreen && flowAssistantPanelOpen ? (
            <UIButton
              variant="ghost"
              size="icon"
              className="size-7 rounded-[8px] text-[#858b9c] hover:bg-[#f1f3f6] hover:text-[#18181a]"
              aria-label="收起 AI 修改面板"
              title="收起 AI 修改面板"
              onClick={() => setFlowAssistantPanelOpen(false)}
            >
              <PanelLeftClose />
            </UIButton>
          ) : undefined}
          onDragEnter={handleDragEnter}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
        >
          <div className={CHAT_PANEL_CLASS}>
            {dragActive && <div className={CHAT_UPLOAD_DROP_HINT_CLASS}>松开上传文档</div>}
            <div className={CHAT_MESSAGES_CLASS} ref={chatMessagesRef}>
              {messages.map((item, index) => (
                <div key={item.id} className={chatRowClass(item.role)}>
                  <div
                    className={chatBubbleClass({
                      role: item.role,
                      editing: editingMessage?.id === item.id,
                      hasAttachments: item.role === 'user' && Boolean(item.attachments?.length),
                    })}
                  >
                    {item.role === 'assistant' && item.thinking && (
                      <div className={CHAT_THINKING_BLOCK_CLASS}>
                        <button
                          type="button"
                          className={CHAT_THINKING_BUTTON_CLASS}
                          onClick={() => toggleThinking(item.id)}
                        >
                          {item.thinking === 'running' ? <LoadingOutlined /> : <CheckOutlined />}
                          <span>{item.thinking === 'running' ? '正在学习' : '学习记录'}</span>
                          {item.thinkingOpen ? <DownOutlined /> : <RightOutlined />}
                        </button>
                        {item.thinkingOpen && (
                          <div className={CHAT_THINKING_DETAILS_CLASS}>
                            {(item.thinkingDetails || []).map((detail, index) => (
                              <div key={`${item.id}_detail_${index}`} className={CHAT_THINKING_DETAIL_CLASS}>
                                {detail}
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    )}
                    {item.role === 'assistant' && item.failure && (
                      <div className={CHAT_FAILURE_CLASS}>
                        <button
                          type="button"
                          className={CHAT_FAILURE_BUTTON_CLASS}
                          aria-expanded={Boolean(item.failureOpen)}
                          onClick={() => toggleFailureDetails(item.id)}
                        >
                          <span className={CHAT_FAILURE_ICON_CLASS}>
                            <CloseCircleOutlined />
                          </span>
                          <span className={CHAT_FAILURE_SUMMARY_CLASS}>{item.failure.summary}</span>
                          <span className={CHAT_FAILURE_STAGE_CLASS}>{item.failure.stage}</span>
                          {item.failureOpen ? <DownOutlined /> : <RightOutlined />}
                        </button>
                        {item.failureOpen && (
                          <div className={CHAT_FAILURE_DETAILS_CLASS}>
                            <div className={CHAT_FAILURE_META_CLASS}>
                              <span className={CHAT_FAILURE_LABEL_CLASS}>阶段</span>
                              <span className={CHAT_FAILURE_VALUE_CLASS}>{item.failure.stage}</span>
                              <span className={CHAT_FAILURE_LABEL_CLASS}>错误码</span>
                              <span className={CHAT_FAILURE_VALUE_CLASS}>{item.failure.code || '未提供'}</span>
                            </div>
                            <pre className={CHAT_FAILURE_RAW_CLASS}>{item.failure.detail}</pre>
                            <button
                              type="button"
                              className={CHAT_FAILURE_COPY_CLASS}
                              onClick={() => void copyFailureDetails(item.failure!)}
                            >
                              <CopyGlyph />
                              复制错误详情
                            </button>
                          </div>
                        )}
                      </div>
                    )}
                    {item.role === 'user' && item.attachments && item.attachments.length > 0 && (
                      <div className={cn(CHAT_ATTACHMENTS_CLASS, CHAT_ATTACHMENTS_USER_CLASS)}>
                        {item.attachments.map((attachment) => (
                          <div className={cn(CHAT_ATTACHMENT_CLASS, CHAT_ATTACHMENT_USER_CLASS)} key={attachment.id} title={attachment.name}>
                            <span className={CHAT_ATTACHMENT_ICON_CLASS}>
                              <FileTextOutlined />
                            </span>
                            <span className={CHAT_ATTACHMENT_MAIN_CLASS}>
                              <span className={CHAT_ATTACHMENT_NAME_CLASS}>{attachment.name}</span>
                              <span className={CHAT_ATTACHMENT_TYPE_CLASS}>{attachment.type}</span>
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                    {item.role === 'user' && editingMessage?.id === item.id ? (
                      <div
                        className={cn(
                          CHAT_EDIT_PANEL_CLASS,
                          item.attachments?.length ? CHAT_EDIT_PANEL_USER_ATTACHMENTS_CLASS : undefined,
                        )}
                      >
                        <Textarea
                          className={CHAT_EDIT_TEXTAREA_CLASS}
                          value={editingMessage.text}
                          rows={3}
                          autoFocus
                          onChange={(event) => setEditingMessage({ id: item.id, text: event.target.value })}
                          onKeyDown={(event) => {
                            if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                              event.preventDefault();
                              submitEditingMessage();
                            }
                          }}
                        />
                        <div className={CHAT_EDIT_ACTIONS_CLASS}>
                          <UIButton variant="outline" onClick={cancelEditingMessage}>取消</UIButton>
                          <UIButton onClick={submitEditingMessage} disabled={!(editingMessage?.text || '').trim()}>
                            发送
                          </UIButton>
                        </div>
                      </div>
                    ) : (
                      <>
                        {item.content ? (
                          <div
                            className={cn(
                              CHAT_CONTENT_CLASS,
                              item.role === 'user' && item.attachments?.length ? CHAT_CONTENT_USER_ATTACHMENTS_CLASS : undefined,
                            )}
                          >
                            {visibleChatContent(item)}
                          </div>
                        ) : item.role === 'assistant' && item.thinking === 'running' ? null : item.role === 'assistant' ? (
                          '正在处理...'
                        ) : null}
                        {item.role === 'user' && (
                          <div className={CHAT_HOVER_ACTIONS_CLASS}>
                            <span className={CHAT_TIME_CLASS}>{formatMessageTime(item.createdAt)}</span>
                            <button type="button" className={CHAT_HOVER_BUTTON_CLASS} title="复制" onClick={() => void copyHistoryMessage(item)}>
                              <CopyGlyph />
                            </button>
                            <button
                              type="button"
                              className={CHAT_HOVER_BUTTON_CLASS}
                              title="修改"
                              onClick={() => requestEditHistoryMessage(item, index)}
                              disabled={loading}
                            >
                              <PencilGlyph />
                            </button>
                          </div>
                        )}
                      </>
                    )}
                    {item.warnings && item.warnings.length > 0 && (() => {
                      const warnings = compactWarningItems(item.warnings || [], item.toolSuggestions);
                      if (warnings.length === 0) return null;
                      return (
                        <div className={CHAT_WARNING_CLASS}>
                          <div className={CHAT_WARNING_TITLE_CLASS}>
                            <WarningOutlined />
                            <span>提示</span>
                          </div>
                          {warnings.map((warning, index) => (
                            <div key={`${item.id}_warning_${index}`} className={CHAT_WARNING_ITEM_CLASS} title={warning.title}>
                              {warning.text}
                            </div>
                          ))}
                        </div>
                      );
                    })()}
                    {item.toolSuggestions && item.toolSuggestions.length > 0 && (
                      <div className={TOOL_SUGGESTIONS_CLASS}>
                        {item.toolSuggestions.map((suggestion) => {
                          const canResolveSuggestion =
                            toolSuggestionResolution(suggestion) === 'new_candidate' &&
                            suggestion.status !== 'accepted' &&
                            suggestion.status !== 'created' &&
                            suggestion.status !== 'rejected';
                          return (
                            <div className={TOOL_SUGGESTION_CLASS} key={`${item.id}_${suggestion.name}`}>
                              <div className={TOOL_SUGGESTION_MAIN_CLASS}>
                                <div className={TOOL_SUGGESTION_HEAD_CLASS}>
                                  <div className={TOOL_SUGGESTION_TITLE_CLASS}>{toolSuggestionTitle(suggestion)}</div>
                                  <span className={toolStatusBadgeClass(toolSuggestionStatusClass(suggestion))}>
                                    {toolSuggestionStatusText(suggestion)}
                                  </span>
                                </div>
                                <div className={TOOL_SUGGESTION_DESC_CLASS}>
                                  {suggestion.reason || suggestion.description || suggestion.name}
                                </div>
                                <div className={TOOL_SUGGESTION_META_CLASS}>
                                  <span className={TOOL_METHOD_CLASS}>{suggestion.method || 'POST'}</span>
                                  <span>{suggestion.url || '-'}</span>
                                </div>
                              </div>
                              <div className={TOOL_SUGGESTION_ACTIONS_CLASS}>
                                <span className={cn(TOOL_ACTION_GROUP_CLASS, TOOL_ACTION_GROUP_DETAIL_CLASS)}>
                                  <SimpleTooltip title="查看详情">
                                    <UIButton
                                      className={TOOL_ACTION_BUTTON_CLASS}
                                      variant="ghost"
                                      size="icon"
                                      onClick={() => openToolDetail(item.id, suggestion)}
                                    >
                                      <InfoCircleOutlined />
                                    </UIButton>
                                  </SimpleTooltip>
                                </span>
                                {canResolveSuggestion && (
                                  <span className={TOOL_ACTION_GROUP_CLASS}>
                                    <SimpleTooltip title="确认新增">
                                      <UIButton
                                        className={cn(TOOL_ACTION_BUTTON_CLASS, TOOL_ACTION_CONFIRM_CLASS)}
                                        variant="ghost"
                                        size="icon"
                                        disabled={!suggestion.probe_result?.success}
                                        onClick={() => void confirmToolSuggestion(item.id, suggestion)}
                                      >
                                        <CheckCircleOutlined />
                                      </UIButton>
                                    </SimpleTooltip>
                                    <SimpleTooltip title="拒绝">
                                      <UIButton
                                        className={cn(TOOL_ACTION_BUTTON_CLASS, TOOL_ACTION_REJECT_CLASS)}
                                        variant="ghost"
                                        size="icon"
                                        onClick={() => rejectToolSuggestion(item.id, suggestion.name)}
                                      >
                                        <CloseCircleOutlined />
                                      </UIButton>
                                    </SimpleTooltip>
                                  </span>
                                )}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    )}
                    {item.actionState === 'pending' && (
                      <div className={CHAT_CONFIRM_CLASS}>
                        <UIButton size="sm" onClick={() => confirmPendingChange()}>
                          确认
                        </UIButton>
                        <UIButton size="sm" variant="outline" onClick={rejectPendingChange}>
                          拒绝
                        </UIButton>
                      </div>
                    )}
                    {item.actionState === 'confirmed' && <div className={CHAT_DECISION_CLASS}>已确认</div>}
                    {item.actionState === 'rejected' && <div className={CHAT_DECISION_CLASS}>已拒绝</div>}
                  </div>
                </div>
              ))}
            </div>
            <div className={CHAT_COMPOSER_SHELL_CLASS}>
              <div className={CHAT_COMPOSER_CLASS}>
              {attachments.length > 0 && (
                <div className={UPLOAD_LIST_CLASS}>
                  {attachments.map((attachment) => (
                    <div className={uploadItemClass(attachment.status)} key={attachment.id}>
                      <FileTextOutlined />
                      <span className={UPLOAD_NAME_CLASS}>{attachment.name}</span>
                      <span className={UPLOAD_STATUS_CLASS}>
                        {attachment.status === 'uploading' && '读取中'}
                        {attachment.status === 'ready' && '已读取'}
                        {attachment.status === 'error' && (attachment.error || '读取失败')}
                      </span>
                      <UIButton
                        size="icon"
                        variant="ghost"
                        onClick={() => cancelAttachment(attachment.id)}
                      >
                        <CloseOutlined />
                      </UIButton>
                    </div>
                  ))}
                </div>
              )}
              <Textarea
                className={CHAT_TEXTAREA_CLASS}
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onPaste={handleComposerPaste}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                    event.preventDefault();
                    void send();
                  }
                }}
                rows={4}
                placeholder={
                  draft
                    ? '说明你要如何改写右侧选中的部分'
                    : '输入或粘贴需要整理的 SOP 流程说明'
                }
              />
              <div className={cn(CHAT_ACTIONS_CLASS, flowFullscreen && 'flex-nowrap gap-[6px]')}>
                <span className={cn('min-w-0 truncate text-[12px] text-[#858b9c]', flowFullscreen && 'sr-only')}>{streamStatus}</span>
                <div className={cn(CHAT_ACTIONS_GROUP_CLASS, flowFullscreen && 'w-full flex-nowrap justify-start gap-[6px]')}>
                  <label>
                    <input
                      type="file"
                      accept=".md,.txt,.doc,.docx"
                      multiple
                      className="hidden"
                      disabled={loading}
                      onChange={(event) => {
                        const files = event.target.files ? Array.from(event.target.files) : [];
                        files.forEach((file) => void stageFileUpload(file));
                        event.target.value = '';
                      }}
                    />
                    <UIButton
                      asChild
                      variant="outline"
                      disabled={uploadingFile || loading}
                      className={cn(CARD_OUTLINE_BUTTON_CLASS, flowFullscreen && 'shrink-0 px-[10px]')}
                    >
                      <span>
                        <UploadOutlined />
                        {flowFullscreen ? '上传' : '上传文件'}
                      </span>
                    </UIButton>
                  </label>
                  {loading && (
                    <UIButton
                      variant="outline"
                      className={cn(CARD_OUTLINE_BUTTON_CLASS, flowFullscreen && 'shrink-0 px-[10px]')}
                      onClick={stopStream}
                    >
                      <StopOutlined />
                      停止
                    </UIButton>
                  )}
                  <ModelConfigDropdown
                    models={modelConfigs}
                    value={selectedRewriteModelId}
                    onChange={(modelId) => {
                      setSelectedRewriteModelId(modelId);
                      window.localStorage.setItem(`${DISTILL_REWRITE_MODEL_STORAGE_KEY}:${TENANT_ID}`, modelId);
                    }}
                    buttonClassName={cn(
                      REWRITE_MODEL_BUTTON_CLASS,
                      flowFullscreen && 'min-w-0 flex-1 px-[10px]',
                    )}
                    menuClassName={flowFullscreen ? 'z-[130]' : undefined}
                  />
                  <UIButton
                    disabled={loading || uploadingFile || (!input.trim() && readyAttachments.length === 0)}
                    className={cn(PRIMARY_BUTTON_CLASS, flowFullscreen && 'shrink-0 px-[12px]')}
                    onClick={() => void send()}
                  >
                    {loading ? <LoadingOutlined className="animate-spin" /> : <SendOutlined />}
                    发送
                  </UIButton>
                </div>
              </div>
              </div>
            </div>
          </div>
        </DistillSectionCard>
        <DistillSectionCard
          className={cn(SOURCE_CARD_CLASS, 'h-full min-h-0')}
          bodyClassName={DISTILL_CARD_BODY_CLASS}
          title={viewMode === 'source' ? '源码' : '流程图'}
          extra={
            <div className="flex flex-wrap justify-end gap-[8px]">
              <UIButton variant="outline" className={CARD_OUTLINE_BUTTON_CLASS} disabled={loading} onClick={handleClearClick}>
                清空
              </UIButton>
              <SimpleTooltip title={draft && !hasSaveableDraftChanges ? '当前没有内容变化' : ''}>
                <UIButton
                  variant="outline"
                  className={CARD_OUTLINE_BUTTON_CLASS}
                  disabled={!draft || loading || !hasSaveableDraftChanges}
                  onClick={() => openSaveReview()}
                >
                  <SaveOutlined />
                  保存草稿
                </UIButton>
              </SimpleTooltip>
            </div>
          }
        >
          <div className={SOURCE_TOOLBAR_CLASS}>
            <div className="flex flex-wrap items-center gap-[8px]">
              <UIButton
                variant="outline"
                className={CARD_OUTLINE_BUTTON_CLASS}
                onClick={() => setViewMode(viewMode === 'source' ? 'flow' : 'source')}
              >
                {viewMode === 'source' ? <BranchesOutlined /> : <CodeOutlined />}
                {viewMode === 'source' ? '显示流程' : '显示源码'}
              </UIButton>
              <UIButton variant="outline" className={CARD_OUTLINE_BUTTON_CLASS} disabled={!draft} onClick={toggleAllTargets}>
                {allSelected ? '清空选择' : '全选'}
              </UIButton>
            </div>
          </div>
          {!draft ? (
            <div className={SOURCE_EMPTY_STATE_CLASS}>
              <FileTextOutlined className="text-[28px] text-[#c0c6d4]" />
              <p className={SOURCE_EMPTY_TEXT_CLASS}>暂无 SOP 草稿</p>
              <p className="text-[12px] leading-[18px] text-[#c0c6d4]">在左侧输入说明或上传文档后开始生成</p>
            </div>
          ) : viewMode === 'source' ? (
            <SkillSource
              skill={draft}
              selectedPaths={selectedPaths}
              highlightedPaths={highlightedPaths}
              updatingPaths={updatingPaths}
              dirtyPaths={dirtyPaths}
              textDiffs={textDiffs}
              toolDescriptions={toolDescriptions}
              toolStatuses={toolStatuses}
              generalSkills={generalSkills}
              sopSkills={sopSkills}
              tools={tools}
              knowledgeBases={knowledgeBases}
              tenantUsers={tenantUsers}
              containerRef={sourceScrollRef}
              lockSkillId={Boolean(lockedSkillId)}
              onToggle={toggleTarget}
              onEdit={handleSourceEdit}
            />
          ) : (
            <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
              <SkillFlow
                skill={draft}
                selectedPaths={selectedPaths}
                highlightedPaths={highlightedPaths}
                updatingPaths={updatingPaths}
                dirtyPaths={dirtyPaths}
                textDiffs={textDiffs}
                toolDescriptions={toolDescriptions}
                toolStatuses={toolStatuses}
                generalSkills={generalSkills}
                sopSkills={sopSkills}
                tools={tools}
                knowledgeBases={knowledgeBases}
                tenantUsers={tenantUsers}
                containerRef={sourceScrollRef}
                lockSkillId={Boolean(lockedSkillId)}
                assistantPanelOpen={flowAssistantPanelOpen}
                onAssistantPanelOpenChange={setFlowAssistantPanelOpen}
                onFullscreenChange={setFlowFullscreen}
                onToggle={toggleTarget}
                onEdit={handleSourceEdit}
              />
            </div>
          )}
        </DistillSectionCard>
      </div>
      <KDialog
        open={clearConfirmOpen}
        onOpenChange={(open) => !open && setClearConfirmOpen(false)}
        title="清空前是否保存？"
        width={520}
        footer={
          <div className="flex flex-wrap justify-end gap-[8px]">
            <UIButton variant="outline" onClick={() => setClearConfirmOpen(false)}>取消</UIButton>
            <UIButton
              variant="outline"
              onClick={() => {
                setClearConfirmOpen(false);
                clearDistillWorkspace();
              }}
            >
              不保存清空
            </UIButton>
            <UIButton
              onClick={() => {
                setClearConfirmOpen(false);
                openSaveReview({ clearAfterSave: true });
              }}
            >
              保存并清空
            </UIButton>
          </div>
        }
      >
        <p className="m-0 text-[14px] leading-[22px] text-foreground">
          检测到当前 SOP 有未保存变更。你可以先保存当前内容；清空后会进入新的 SOP 草稿工作台，不会把原 SOP 替换为空。
        </p>
      </KDialog>
      <KDialog
        open={saveReviewOpen}
        onOpenChange={(open) => !open && closeSaveReview()}
        title="保存SOP版本"
        width={820}
        footer={
          <div className="flex flex-wrap justify-end gap-[8px]">
            <UIButton variant="outline" onClick={closeSaveReview}>取消</UIButton>
            <UIButton disabled={!saveReviewHasContentChanges} onClick={() => void saveDraft()}>保存</UIButton>
          </div>
        }
      >
        <div className={SAVE_REVIEW_FORM_CLASS}>
          <label className={SAVE_REVIEW_FORM_LABEL_CLASS}>
            <span>SOP名称</span>
            <Input value={saveName} onChange={(event) => setSaveName(event.target.value)} />
          </label>
          <label className={SAVE_REVIEW_FORM_LABEL_CLASS}>
            <span>业务域</span>
            <Input value={saveDomain} onChange={(event) => setSaveDomain(event.target.value)} />
          </label>
          <label className={SAVE_REVIEW_FORM_LABEL_CLASS}>
            <span>版本号</span>
            <Input value={saveVersion} disabled={!saveReviewHasContentChanges} onChange={(event) => setSaveVersion(event.target.value)} />
          </label>
        </div>
        <div className={SAVE_REVIEW_DIFF_CLASS}>
          <strong className="text-[13px] font-semibold text-foreground">本轮修改 diff</strong>
          {saveReviewDiffs.length === 0 ? (
            <EmptyState description="暂无结构差异" />
          ) : (
            saveReviewDiffs.map((diff) => (
              <div key={diff.key} className={SAVE_REVIEW_DIFF_ROW_CLASS}>
                <div className={SAVE_REVIEW_DIFF_PATH_CLASS}>{diffTargetLabel(diff.path, saveReviewDraft)} / {fieldLabel(diff.field)}</div>
                <SaveReviewDiffValue diff={diff} toolDescriptions={toolDescriptions} toolStatuses={toolStatuses} />
              </div>
            ))
          )}
        </div>
      </KDialog>
      <KDialog
        open={Boolean(toolDetail)}
        onOpenChange={(open) => !open && setToolDetail(null)}
        title="工具详情"
        width={1040}
        footer={
          <div className={cn(TOOL_SUGGESTION_DETAIL_FOOTER_CLASS, "flex flex-wrap justify-end gap-[8px]")}>
            <UIButton variant="outline" onClick={() => setToolDetail(null)}>关闭</UIButton>
            {toolDetail && toolSuggestionResolution(toolDetail) === 'new_candidate' && (
              <>
                <UIButton variant="outline" onClick={applyProbeArgumentsFromDetail}>应用样例参数</UIButton>
                <UIButton
                  disabled={toolDetail?.probeStatus === 'probing'}
                  onClick={probeToolDetail}
                >
                  {toolDetail?.probeStatus === 'probing' ? <LoadingOutlined className="animate-spin" /> : <ApiOutlined />}
                  {toolDetail?.probe_result ? '再次测试' : '测试接口'}
                </UIButton>
              </>
            )}
          </div>
        }
      >
        {toolDetail && (
          <div className={TOOL_SUGGESTION_DETAIL_CLASS}>
            <div><strong>解析状态：</strong>{toolSuggestionResolutionLabel(toolDetail)}</div>
            {toolDetail.matched_tool_name && (
              <div><strong>匹配工具：</strong>{toolDetail.matched_tool_display_name || toolDetail.matched_tool_name}</div>
            )}
            <div><strong>工具名：</strong>{toolDetail.name}</div>
            <div><strong>显示名：</strong>{toolDetail.display_name || '-'}</div>
            <div><strong>说明：</strong>{toolDetail.description || '-'}</div>
            <div><strong>方法：</strong>{toolDetail.method}</div>
            <div><strong>URL：</strong>{toolDetail.url}</div>
            {toolDetail.missing_reason && <div><strong>缺失原因：</strong>{toolDetail.missing_reason}</div>}
            <div><strong>原因：</strong>{toolDetail.reason || '-'}</div>
            <div><strong>来源：</strong>{toolDetail.source_excerpt || '-'}</div>
            <strong className="text-[13px] font-semibold text-foreground">样例参数</strong>
            <Textarea
              value={probeArgsText}
              rows={5}
              onChange={(event) => setProbeArgsText(event.target.value)}
            />
            <strong className="text-[13px] font-semibold text-foreground">输入 Schema</strong>
            <pre className={TOOL_SUGGESTION_DETAIL_PRE_CLASS}>{JSON.stringify(toolDetail.input_schema || {}, null, 2)}</pre>
            <strong className="text-[13px] font-semibold text-foreground">输出 Schema</strong>
            <pre className={TOOL_SUGGESTION_DETAIL_PRE_CLASS}>{JSON.stringify(toolDetail.output_schema || {}, null, 2)}</pre>
            {toolDetail.probe_result && (
              <>
                <strong className="text-[13px] font-semibold text-foreground">测试结果</strong>
                <pre className={TOOL_SUGGESTION_DETAIL_PRE_CLASS}>{JSON.stringify(toolDetail.probe_result, null, 2)}</pre>
              </>
            )}
          </div>
        )}
      </KDialog>
      {clearNewConfirm && (
        <ConfirmDialog
          open
          onOpenChange={(open) => !open && setClearNewConfirm(null)}
          title={clearNewConfirm.title}
          description={clearNewConfirm.description}
          confirmText="清空"
          destructive={false}
          onConfirm={() => {
            setClearNewConfirm(null);
            clearDistillWorkspace();
          }}
        />
      )}
      {rerunConfirm && (
        <ConfirmDialog
          open
          onOpenChange={(open) => !open && setRerunConfirm(null)}
          title="重新编辑这条消息？"
          confirmText="确认回退"
          destructive={false}
          description={
            <div>
              <p className="m-0 mb-[8px]">重新编辑会回到这条消息发送前的 SOP 草稿，并截断之后的推理记录。</p>
              <div className="rollback-operation-list flex flex-wrap gap-[6px]">
                {rerunConfirm.rollbackOperations.map((operation, operationIndex) => (
                  <DistillTag key={`${operation.kind}_${operationIndex}`}>{operation.label}</DistillTag>
                ))}
              </div>
            </div>
          }
          onConfirm={() => {
            const payload = rerunConfirm;
            setRerunConfirm(null);
            void rerunEditedMessage(
              payload.index,
              payload.snapshot,
              payload.rollbackOperations,
              payload.text,
              payload.outgoingText,
            );
          }}
        />
      )}
    </div>
  );
}

/** 蒸馏工作台通用分节卡片：标题栏加可滚动内容区，支持透传 div 属性。 */
function DistillSectionCard({
  className,
  bodyClassName,
  title,
  extra,
  children,
  ...rest
}: {
  className?: string;
  bodyClassName?: string;
  title?: ReactNode;
  extra?: ReactNode;
  children?: ReactNode;
} & Omit<HTMLAttributes<HTMLDivElement>, 'title'>) {
  return (
    <section className={cn(DISTILL_CARD_CLASS, 'h-full min-h-0', className)} {...rest}>
      {(title || extra) && (
        <div className={DISTILL_CARD_HEADER_CLASS}>
          <div className={cn('min-w-0', SECTION_CARD_TITLE_CLASS)}>{title}</div>
          {extra ? <div className="shrink-0">{extra}</div> : null}
        </div>
      )}
      <div className={cn(DISTILL_CARD_BODY_CLASS, bodyClassName)}>{children}</div>
    </section>
  );
}

/** 通用弹窗：统一圆角、最大高度、标题与底部操作区布局。 */
function KDialog({
  open,
  onOpenChange,
  title,
  width = 520,
  footer,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title?: ReactNode;
  width?: number;
  footer?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="max-h-[85vh] gap-0 overflow-y-auto rounded-[16px] p-0"
        style={{ width: `min(${width}px, calc(100vw - 32px))`, maxWidth: 'calc(100vw - 32px)' }}
      >
        {title != null && (
          <DialogTitle className="border-b border-border px-[24px] py-[16px] text-[16px] font-semibold text-foreground">
            {title}
          </DialogTitle>
        )}
        <div className="px-[24px] py-[20px]">{children}</div>
        {footer != null && (
          <DialogFooter>{footer}</DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  );
}

/** 轻量 Tooltip 包装：title 为空时直接渲染子节点，不挂载浮层。 */
function SimpleTooltip({ title, children }: { title?: ReactNode; children: ReactNode }) {
  if (!title) return <>{children}</>;
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>{children}</TooltipTrigger>
        <TooltipContent>{title}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

/** 空状态占位组件，居中显示说明文案。 */
function EmptyState({ description }: { description: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-[8px] py-[32px] text-center text-[13px] text-[#858b9c]">
      {description}
    </div>
  );
}

/** 小型灰色标签，用于展示回退操作等短文本。 */
function DistillTag({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex items-center rounded-[6px] bg-[#f2f3f5] px-[8px] py-px text-[12px] font-medium leading-[18px] text-[#5b6273]">
      {children}
    </span>
  );
}

/** SOP 源码编辑器内的单行文本输入框。 */
function SourceInput({
  className,
  ...rest
}: {
  className?: string;
  type?: HTMLInputTypeAttribute;
  value?: string;
  placeholder?: string;
  min?: number;
  max?: number;
  step?: number;
  disabled?: boolean;
  readOnly?: boolean;
  style?: CSSProperties;
  onChange?: ChangeEventHandler<HTMLInputElement>;
}) {
  return <Input className={cn(SOURCE_INPUT_CLASS, className)} {...rest} />;
}

/** 自适应高度的多行文本域，替代 Ant Design 的 `Input.TextArea autoSize`。 */
function AutoGrowTextarea({
  className,
  minRows = 1,
  maxRows,
  value,
  style,
  ...rest
}: {
  className?: string;
  minRows?: number;
  maxRows?: number;
  value?: string;
  placeholder?: string;
  disabled?: boolean;
  readOnly?: boolean;
  style?: CSSProperties;
  onChange?: ChangeEventHandler<HTMLTextAreaElement>;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const [autoSize, setAutoSize] = useState<{ height: number; overflowY: 'auto' | 'hidden' } | null>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    /** 按内容高度重新计算文本域高度与滚动策略，行高与内边距取自计算样式。 */
    const resize = () => {
      const computed = window.getComputedStyle(el);
      const lineHeight = Number.parseFloat(computed.lineHeight) || 20;
      const verticalPadding = (Number.parseFloat(computed.paddingTop) || 0)
        + (Number.parseFloat(computed.paddingBottom) || 0);
      const verticalBorder = (Number.parseFloat(computed.borderTopWidth) || 0)
        + (Number.parseFloat(computed.borderBottomWidth) || 0);
      const minHeight = lineHeight * minRows + verticalPadding + verticalBorder;
      const maxHeight = maxRows
        ? lineHeight * Math.max(minRows, maxRows) + verticalPadding + verticalBorder
        : Number.POSITIVE_INFINITY;
      const nextHeight = Math.max(minHeight, Math.min(el.scrollHeight, maxHeight));
      const nextOverflowY = maxRows || el.scrollHeight > maxHeight ? 'auto' : 'hidden';
      setAutoSize((current) => (
        current?.height === nextHeight && current.overflowY === nextOverflowY
          ? current
          : { height: nextHeight, overflowY: nextOverflowY }
      ));
    };
    resize();
    let previousWidth = el.clientWidth;
    const observer = new ResizeObserver((entries) => {
      const nextWidth = entries[0]?.contentRect.width ?? el.clientWidth;
      if (Math.abs(nextWidth - previousWidth) < 0.5) return;
      previousWidth = nextWidth;
      resize();
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [maxRows, minRows, value]);
  return (
    <Textarea
      ref={ref}
      rows={minRows}
      value={value}
      style={{ ...style, height: autoSize?.height, overflowY: autoSize?.overflowY }}
      className={cn(SOURCE_INPUT_CLASS, className)}
      {...rest}
    />
  );
}

/** 原生数字输入框，替代 Ant Design 的 `InputNumber`，空串会回传 null。 */
function SourceNumberInput({
  className,
  value,
  min,
  placeholder,
  onChange,
}: {
  className?: string;
  value: number | null;
  min?: number;
  placeholder?: string;
  onChange: (value: number | null) => void;
}) {
  return (
    <Input
      type="number"
      className={cn(SOURCE_INPUT_CLASS, className)}
      value={value ?? ''}
      min={min}
      placeholder={placeholder}
      onChange={(event) => {
        const raw = event.target.value;
        onChange(raw === '' ? null : Number(raw));
      }}
    />
  );
}

/**
 * 可搜索的动作选择器，替代 Ant Design 的 `Select showSearch`。
 *
 * - 输入框负责过滤，候选动作在 Popover 列表中展示。
 * - 提交空值表示删除该动作，对应原 `allowClear` 行为。
 */
function ActionCombobox({
  value,
  options,
  placeholder = '选择一个动作',
  onSelect,
}: {
  value?: string;
  options: SelectOption[];
  placeholder?: string;
  onSelect: (value: string) => void;
}) {
  const [open, setOpen] = useState(true);
  const [query, setQuery] = useState('');
  const normalizedQuery = query.trim().toLowerCase();
  const filtered = normalizedQuery
    ? options.filter(
        (option) =>
          option.label.toLowerCase().includes(normalizedQuery) ||
          String(option.value).toLowerCase().includes(normalizedQuery),
      )
    : options;
  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) onSelect(value || '');
      }}
    >
      <PopoverTrigger asChild>
        <input
          autoComplete="off"
          data-1p-ignore="true"
          data-lpignore="true"
          data-bwignore="true"
          className={SOURCE_ACTION_SELECT_CLASS}
          autoFocus
          value={query}
          placeholder={placeholder}
          onChange={(event) => {
            setQuery(event.target.value);
            setOpen(true);
          }}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault();
              if (filtered.length > 0) onSelect(String(filtered[0].value));
            } else if (event.key === 'Escape') {
              event.preventDefault();
              event.stopPropagation();
            }
          }}
        />
      </PopoverTrigger>
      <PopoverContent
        align="start"
        onOpenAutoFocus={(event) => event.preventDefault()}
        className="z-[130] max-h-[280px] w-[320px] overflow-y-auto p-[4px]"
      >
        {filtered.length === 0 ? (
          <div className="px-[10px] py-[12px] text-center text-[13px] text-[#858b9c]">无匹配动作</div>
        ) : (
          filtered.map((option) => (
            <button
              key={String(option.value)}
              type="button"
              className={cn(
                'flex w-full items-center rounded-[8px] px-[10px] py-[6px] text-left text-[13px] text-foreground hover:bg-muted',
                option.value === value && 'bg-muted',
              )}
              onClick={() => onSelect(String(option.value))}
            >
              {option.label}
            </button>
          ))
        )}
      </PopoverContent>
    </Popover>
  );
}

/** 适配 SOP 源码编辑器样式的 shadcn Select。 */
function SourceSelect({
  className,
  value,
  options,
  placeholder,
  onChange,
}: {
  className?: string;
  value?: string;
  options: SelectOption[];
  placeholder?: string;
  onChange: (value: string) => void;
}) {
  return (
    <UISelect value={value || undefined} onValueChange={onChange}>
      <SelectTrigger className={cn(SELECT_TRIGGER_CLASS, className)}>
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent className="z-[130]">
        {options.map((option) => (
          <SelectItem key={String(option.value)} value={String(option.value)}>
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </UISelect>
  );
}

/**
 * SOP 源码视图：以可编辑文本形式展示基础信息、节点与流转规则。
 *
 * - 编辑结果通过 onEdit 回传整份草稿与改动路径，由父组件写回状态。
 * - 选中、高亮、改写中与差异动画状态均由父组件传入。
 */
function SkillSource({
  skill,
  selectedPaths,
  highlightedPaths,
  updatingPaths,
  dirtyPaths,
  textDiffs,
  toolDescriptions,
  toolStatuses,
  generalSkills,
  sopSkills,
  tools,
  knowledgeBases,
  tenantUsers,
  containerRef,
  lockSkillId,
  onToggle,
  onEdit,
}: {
  skill: SkillCard;
  selectedPaths: string[];
  highlightedPaths: string[];
  updatingPaths: string[];
  dirtyPaths: string[];
  textDiffs: TextDiffAnimation[];
  toolDescriptions: ToolDescriptionMap;
  toolStatuses: ToolStatusMap;
  generalSkills: GeneralSkillRead[];
  sopSkills: SkillRead[];
  tools: ToolRead[];
  knowledgeBases: KnowledgeBaseRead[];
  tenantUsers: Array<{ id: string; username: string; display_name?: string; source?: string; channel_identities?: Array<{ channel: string; display_name?: string; external_user_id?: string }> }>;
  containerRef: RefObject<HTMLDivElement>;
  lockSkillId?: boolean;
  onToggle: (target: TargetSelection) => void;
  onEdit: (nextDraft: SkillCard, path: string) => void;
}) {
  const [deleteNodeIndex, setDeleteNodeIndex] = useState<number | null>(null);

  /** 修改基础信息字段，并按字段类型做列表拆分、数字与能力范围归一化。 */
  function editBasic(
    field: keyof SkillCard,
    value: string | string[] | number | null,
  ) {
    if (field === 'skill_id' && lockSkillId) return;
    const next = cloneSkill(skill);
    if (field === 'trigger_intents' || field === 'user_utterance_examples' || field === 'goal' || field === 'required_info' || field === 'response_rules') {
      next[field] = Array.isArray(value) ? value : splitEditableList(String(value ?? ''));
    } else if (field === 'step_timeout_seconds') {
      next.step_timeout_seconds = typeof value === 'number' ? value : null;
    } else if (field === 'capability_scope') {
      next.capability_scope = normalizeCapabilityScope(value);
    } else if (field === 'skill_id' || field === 'name' || field === 'version' || field === 'business_domain' || field === 'description') {
      next[field] = String(value);
    }
    onEdit(next, 'basic');
  }

  /**
   * 修改指定节点的单个字段，并处理改名、能力引用与节点类型联动。
   *
   * - 节点 ID 改名会同步更新关联边、起始节点与终止节点。
   * - 能力引用统一写入 capability_refs，并删除遗留的旧字段。
   */
  function editStep(index: number, field: string, value: string | string[] | boolean | Record<string, unknown>) {
    const next = cloneSkill(skill);
    const listValue = [
      'expected_user_info',
      'allowed_actions',
      'general_skill_ids',
      'tool_ids',
      'knowledge_base_ids',
      'required_general_skill_ids',
      'required_tool_ids',
      'required_knowledge_base_ids',
    ].includes(field)
      ? Array.isArray(value)
        ? value
        : splitEditableList(String(value))
      : value;
    next.nodes = Array.isArray(next.nodes) ? [...next.nodes] : [];
    const currentNode = { ...(next.nodes[index] || {}) };
    const nodeField = field === 'step_id' ? 'node_id' : field;
    if (nodeField === 'node_id') {
      const previousId = String(currentNode.node_id || currentNode.step_id || `node_${index + 1}`);
      const nextId = String(listValue || '').trim();
      if (!nextId) {
        notify.warning('节点 ID 不能为空');
        return;
      }
      const duplicated = next.nodes.some((node, nodeIndex) => (
        nodeIndex !== index && String(node?.node_id || node?.step_id || '') === nextId
      ));
      if (duplicated) {
        notify.warning(`节点 ID「${nextId}」已经存在`);
        return;
      }
      currentNode.node_id = nextId;
      next.edges = normalizeSkillEdges(next).map((edge) => ({
        ...edge,
        source_node_id: String(edge.source_node_id || '') === previousId ? nextId : edge.source_node_id,
        next_node_id: String(edge.next_node_id || '') === previousId ? nextId : edge.next_node_id,
      }));
      if (next.start_node_id === previousId) next.start_node_id = nextId;
      next.terminal_node_ids = asStringList(next.terminal_node_ids).map((nodeId) => (nodeId === previousId ? nextId : nodeId));
      next.nodes[index] = currentNode;
      onEdit(next, stepTargetPath(index));
      return;
    }
    if (nodeField === 'assignee_user_id') {
      const { userId, channel } = parseHandoffAssigneeValue(String(listValue));
      currentNode.assignee_user_id = userId || null;
      currentNode.assignee_notify_channel = userId ? channel : null;
    } else if (nodeField === 'type') {
      applyNodeTypeChange(currentNode, String(listValue));
    } else if (CAPABILITY_REFERENCE_FIELDS.includes(nodeField)) {
      const currentRefs = nodeCapabilityRefs(currentNode);
      currentNode.capability_refs = updateCapabilityRefs(
        currentRefs,
        nodeField,
        asStringList(listValue),
      );
      delete currentNode.general_skill_ids;
      delete currentNode.tool_ids;
      delete currentNode.knowledge_base_ids;
      delete currentNode.required_general_skill_ids;
      delete currentNode.required_tool_ids;
      delete currentNode.required_knowledge_base_ids;
    } else {
      currentNode[nodeField] = listValue;
    }
    next.nodes[index] = normalizeSubflowNode(currentNode);
    onEdit(next, stepTargetPath(index));
  }

  /** 更新某节点下第 edgeIndex 条出边，先按节点 ID 定位到全局边序号。 */
  function updateEdge(index: number, edgeIndex: number, patch: Record<string, unknown>) {
    const next = cloneSkill(skill);
    const sourceId = nodeIdAt(next, index);
    const edges = normalizeSkillEdges(next);
    const globalIndex = findSourceEdgeIndex(edges, sourceId, edgeIndex);
    if (globalIndex < 0) return;
    edges[globalIndex] = { ...edges[globalIndex], ...patch };
    next.edges = edges;
    onEdit(next, stepTargetPath(index));
  }

  /** 为指定节点新增一条出边，默认指向下一节点，优先级排在末尾。 */
  function addEdge(index: number) {
    const next = cloneSkill(skill);
    const sourceId = nodeIdAt(next, index);
    const nodes = normalizeSkillNodes(next);
    const fallbackTarget = nodes.find((node) => String(node.node_id || node.step_id || '') !== sourceId);
    const targetId = String(nodes[index + 1]?.node_id || nodes[index + 1]?.step_id || fallbackTarget?.node_id || fallbackTarget?.step_id || '');
    const sourceEdges = normalizeSkillEdges(next).filter((edge) => String(edge.source_node_id || '') === sourceId);
    const priority = sourceEdges.length > 0
      ? Math.max(...sourceEdges.map((edge, sourceIndex) => edgePriority(edge, sourceIndex))) + 1
      : 1;
    next.edges = [
      ...normalizeSkillEdges(next),
      {
        source_node_id: sourceId,
        next_node_id: targetId,
        condition: '',
        priority,
        label: targetId ? '新增流转' : '',
      },
    ];
    onEdit(next, stepTargetPath(index));
  }

  /** 删除指定节点下第 edgeIndex 条出边。 */
  function deleteEdge(index: number, edgeIndex: number) {
    const next = cloneSkill(skill);
    const sourceId = nodeIdAt(next, index);
    const edges = normalizeSkillEdges(next);
    const globalIndex = findSourceEdgeIndex(edges, sourceId, edgeIndex);
    if (globalIndex < 0) return;
    edges.splice(globalIndex, 1);
    next.edges = edges;
    onEdit(next, stepTargetPath(index));
  }

  /**
   * 在指定节点之后插入新节点，并自动串接前后的流转边。
   *
   * - 原有的直连边会改为指向新节点，再补一条新节点到后继的边。
   * - 空流程插入时会把新节点同时设为起始与终止节点。
   */
  function insertNodeBetween(index: number) {
    const next = cloneSkill(skill);
    const nodes = normalizeSkillNodes(next);
    const insertAt = nodes.length === 0 ? 0 : Math.max(0, Math.min(index + 1, nodes.length));
    const sourceNode = insertAt > 0 ? nodes[insertAt - 1] : null;
    const targetNode = insertAt < nodes.length ? nodes[insertAt] : null;
    const sourceId = sourceNode ? String(sourceNode.node_id || sourceNode.step_id || `node_${insertAt}`) : '';
    const targetId = targetNode ? String(targetNode.node_id || targetNode.step_id || `node_${insertAt + 1}`) : '';
    const newNodeId = uniqueNodeId(nodes, `node_${insertAt + 1}`);
    const newNode = {
      node_id: newNodeId,
      type: 'collect_info',
      name: '新增节点',
      instruction: '说明这个节点要完成的目标。',
      optional: false,
      condition: '',
      expected_user_info: [],
      allowed_actions: ['continue_flow'],
      capability_refs: {
        general_skill_ids: [],
        tool_ids: [],
        knowledge_base_ids: [],
        required_general_skill_ids: [],
        required_tool_ids: [],
        required_knowledge_base_ids: [],
      },
      knowledge_scope: {},
      retry_policy: {},
      metadata: {},
    };
    nodes.splice(insertAt, 0, newNode);
    next.nodes = nodes;

    const edges = normalizeSkillEdges(next);
    if (sourceId && targetId) {
      const directEdgeIndexes = edges
        .map((edge, edgeIndex) => ({ edge, edgeIndex }))
        .filter(({ edge }) => String(edge.source_node_id || '') === sourceId && String(edge.next_node_id || '') === targetId)
        .map(({ edgeIndex }) => edgeIndex);
      if (directEdgeIndexes.length > 0) {
        directEdgeIndexes.forEach((edgeIndex) => {
          edges[edgeIndex] = {
            ...edges[edgeIndex],
            next_node_id: newNodeId,
            label: String(edges[edgeIndex].label || '').trim() || '进入新增节点',
          };
        });
        const maxPriority = Math.max(...directEdgeIndexes.map((edgeIndex, localIndex) => edgePriority(edges[edgeIndex], localIndex)));
        edges.push({
          source_node_id: newNodeId,
          next_node_id: targetId,
          condition: '',
          priority: maxPriority + 1,
          label: `继续到 ${String(targetNode?.name || targetId)}`,
        });
      } else {
        const sourcePriority = edges
          .filter((edge) => String(edge.source_node_id || '') === sourceId)
          .reduce((max, edge, sourceIndex) => Math.max(max, edgePriority(edge, sourceIndex)), 0) + 1;
        edges.push({
          source_node_id: sourceId,
          next_node_id: newNodeId,
          condition: '',
          priority: sourcePriority,
          label: '进入新增节点',
        });
        edges.push({
          source_node_id: newNodeId,
          next_node_id: targetId,
          condition: '',
          priority: 1,
          label: `继续到 ${String(targetNode?.name || targetId)}`,
        });
      }
    } else if (!sourceId && targetId) {
      edges.push({
        source_node_id: newNodeId,
        next_node_id: targetId,
        condition: '',
        priority: 1,
        label: `继续到 ${String(targetNode?.name || targetId)}`,
      });
      next.start_node_id = newNodeId;
    } else if (sourceId && !targetId) {
      const sourcePriority = edges
        .filter((edge) => String(edge.source_node_id || '') === sourceId)
        .reduce((max, edge, sourceIndex) => Math.max(max, edgePriority(edge, sourceIndex)), 0) + 1;
      edges.push({
        source_node_id: sourceId,
        next_node_id: newNodeId,
        condition: '',
        priority: sourcePriority,
        label: '进入新增节点',
      });
      const previousTerminalIds = asStringList(next.terminal_node_ids);
      next.terminal_node_ids = previousTerminalIds.length > 0
        ? [...previousTerminalIds.filter((terminalId) => terminalId !== sourceId), newNodeId]
        : [newNodeId];
    } else {
      next.start_node_id = newNodeId;
      next.terminal_node_ids = [newNodeId];
    }
    next.edges = edges;
    if (!next.start_node_id) next.start_node_id = String(nodes[0]?.node_id || '');
    if (asStringList(next.terminal_node_ids).length === 0 && nodes.length > 0) {
      next.terminal_node_ids = [String(nodes[nodes.length - 1].node_id || '')];
    }
    onEdit(next, stepTargetPath(insertAt));
  }

  /** 请求删除节点：至少保留一个节点，满足条件时打开二次确认。 */
  function confirmDeleteNode(index: number) {
    const nodes = normalizeSkillNodes(skill);
    if (nodes.length <= 1) {
      notify.warning('至少需要保留一个节点');
      return;
    }
    setDeleteNodeIndex(index);
  }

  /** 执行删除节点，并清理相关边、起始节点与终止节点引用。 */
  function runDeleteNode(index: number) {
    const nodes = normalizeSkillNodes(skill);
    const node = nodes[index];
    if (!node) return;
    const nodeId = String(node.node_id || node.step_id || `node_${index + 1}`);
    const next = cloneSkill(skill);
    const nextNodes = normalizeSkillNodes(next).filter((_node, nodeIndex) => nodeIndex !== index);
    next.nodes = nextNodes;
    next.edges = normalizeSkillEdges(next).filter((edge) => (
      String(edge.source_node_id || '') !== nodeId && String(edge.next_node_id || '') !== nodeId
    ));
    if (next.start_node_id === nodeId) next.start_node_id = String(nextNodes[0]?.node_id || '');
    next.terminal_node_ids = asStringList(next.terminal_node_ids).filter((terminalId) => terminalId !== nodeId);
    if (next.terminal_node_ids.length === 0 && nextNodes.length > 0) {
      next.terminal_node_ids = [String(nextNodes[nextNodes.length - 1].node_id || '')];
    }
    onEdit(next, stepTargetPath(Math.min(index, Math.max(nextNodes.length - 1, 0))));
  }

  /** 渲染删除节点确认弹窗，列出会被一并移除的连接关系。 */
  function renderDeleteNodeConfirm() {
    if (deleteNodeIndex === null) return null;
    const nodes = normalizeSkillNodes(skill);
    const index = deleteNodeIndex;
    const node = nodes[index];
    if (!node) return null;
    const nodeId = String(node.node_id || node.step_id || `node_${index + 1}`);
    const edges = normalizeSkillEdges(skill);
    const incomingEdges = edges.filter((edge) => String(edge.next_node_id || '') === nodeId);
    const outgoingEdges = edges.filter((edge) => String(edge.source_node_id || '') === nodeId);
    const affected = [...incomingEdges, ...outgoingEdges];
    return (
      <ConfirmDialog
        open
        onOpenChange={(open) => !open && setDeleteNodeIndex(null)}
        title={`确认删除 Node ${index + 1}：${String(node.name || nodeId)}？`}
        confirmText="确认删除"
        description={
          <div className={NODE_DELETE_CONFIRM_CLASS}>
            <p>删除后会同时移除所有连接到这个节点、或从这个节点发出的流转规则。</p>
            <strong>将受影响的连接</strong>
            <ul>
              {affected.length > 0 ? (
                affected.map((edge, edgeIndex) => (
                  <li key={`${String(edge.source_node_id)}_${String(edge.next_node_id)}_${edgeIndex}`}>
                    {nodeDisplayNameById(nodes, String(edge.source_node_id || ''))}
                    {' -> '}
                    {nodeDisplayNameById(nodes, String(edge.next_node_id || ''))}
                    {String(edge.label || edge.condition || '').trim() ? `：${String(edge.label || edge.condition)}` : ''}
                  </li>
                ))
              ) : (
                <li>无直接连接关系</li>
              )}
            </ul>
          </div>
        }
        onConfirm={() => {
          runDeleteNode(index);
          setDeleteNodeIndex(null);
        }}
      />
    );
  }

  const steps = skillGraphSteps(skill);
  const nodeNameMap = steps.reduce<Record<string, string>>((acc, step, index) => {
    const nodeId = String(step.node_id || step.step_id || `node_${index + 1}`);
    acc[nodeId] = String(step.name || nodeId);
    return acc;
  }, {});
  const edgeMap = skillGraphEdgeMap(skill);
  const terminalNodeIds = new Set(asStringList(skill.terminal_node_ids));
  const startNodeId = String(skill.start_node_id || '');
  const nodeOptions = steps.map((step, index) => {
    const nodeId = String(step.node_id || step.step_id || `node_${index + 1}`);
    return {
      value: nodeId,
      label: `Node ${index + 1} · ${String(step.name || nodeId)}`,
    };
  });
  const actionOptions = buildActionOptions(toolDescriptions, toolStatuses, steps);
  const generalSkillOptions: CapabilityReferenceOption[] = generalSkills.map((item) => ({
    value: item.id,
    label: item.name || item.slug,
    description: item.description || item.slug,
    capabilityScope: item.capability_scope,
    unavailableReason: item.status === 'published' ? undefined : '技能未启用',
  }));
  const toolOptions: CapabilityReferenceOption[] = tools.map((item) => ({
    value: item.id,
    label: item.display_name || item.name,
    description: item.description || item.name,
    capabilityScope: item.capability_scope,
    unavailableReason: item.enabled ? undefined : '工具已停用',
  }));
  const knowledgeBaseOptions: CapabilityReferenceOption[] = knowledgeBases.map((item) => ({
    value: item.id,
    label: item.name,
    description: item.description,
    capabilityScope: item.capability_scope,
    unavailableReason: item.status === 'active' || item.status === 'published' ? undefined : '知识库已下线',
  }));
  const sopOptions: SelectOption[] = sopSkills
    .filter((item) => (
      item.status === 'published'
      && item.skill_id !== skill.skill_id
      && !wouldCreateSopNestingCycle(skill.skill_id, item.skill_id, sopSkills)
    ))
    .map((item) => ({
      value: item.skill_id,
      label: `${item.name} · ${item.skill_id}`,
    }));
  const tenantUserOptions: SelectOption[] = [
    { value: UNASSIGNED_USER_VALUE, label: '未指定（使用渠道默认）' },
    ...handoffAssigneeUserOptions(tenantUsers),
  ];

  return (
    <div className={SOURCE_MD_CLASS} ref={containerRef}>
      <div className={SOURCE_GROUP_TITLE_CLASS}>基础信息</div>
      <SelectableTarget
        className={distillSourceSectionClass('basic', selectedPaths, highlightedPaths, updatingPaths, dirtyPaths)}
        target={{ path: 'basic', label: '基础信息' }}
        onToggle={onToggle}
      >
        {selectedPaths.includes('basic') && <span className={SELECTION_MARK_CLASS}><CheckOutlined /></span>}
        <div className={SOURCE_RENDERED_CLASS}>
          <EditableSourceHeading value={skill.name} onChange={(value) => editBasic('name', value)} />
          <div className={SOURCE_META_LIST_CLASS}>
            <EditableSourceTextLine
              label={fieldLabel('skill_id')}
              value={skill.skill_id}
              readOnly={lockSkillId}
              onChange={(value) => editBasic('skill_id', value)}
            />
            <EditableSourceTextLine label={fieldLabel('version')} value={skill.version} onChange={(value) => editBasic('version', value)} />
            <EditableSourceTextLine label={fieldLabel('business_domain')} value={skill.business_domain || ''} onChange={(value) => editBasic('business_domain', value)} />
            <EditableSourceTextLine label={fieldLabel('description')} value={skill.description || ''} multiline onChange={(value) => editBasic('description', value)} />
            <CapabilityScopeControl
              compact
              resourceType="sop"
              value={normalizeCapabilityScope(skill.capability_scope)}
              onChange={(value) => editBasic('capability_scope', value)}
            />
            <EditableSourceNumberLine
              label={fieldLabel('step_timeout_seconds')}
              value={skill.step_timeout_seconds}
              min={1}
              max={3600}
              placeholder="不单独限制"
              onChange={(value) => editBasic('step_timeout_seconds', value)}
            />
            <EditableSourceListLine label={fieldLabel('trigger_intents')} values={skill.trigger_intents} onChange={(value) => editBasic('trigger_intents', value)} />
            <EditableSourceListLine label={fieldLabel('user_utterance_examples')} values={skill.user_utterance_examples} onChange={(value) => editBasic('user_utterance_examples', value)} />
            <EditableSourceListLine label={fieldLabel('goal')} values={skill.goal} onChange={(value) => editBasic('goal', value)} />
            <EditableSourceListLine label={fieldLabel('required_info')} values={skill.required_info} onChange={(value) => editBasic('required_info', value)} />
            <EditableSourceListLine label={fieldLabel('response_rules')} values={skill.response_rules} minRows={5} maxRows={14} onChange={(value) => editBasic('response_rules', value)} />
          </div>
        </div>
      </SelectableTarget>
      <div className={SOURCE_GROUP_TITLE_CLASS}>详细节点</div>
      <div className={SOURCE_STEPS_CLASS}>
        <div className={cn(NODE_INSERT_ROW_CLASS, NODE_INSERT_ROW_EDGE_CLASS)}>
          <UIButton variant="outline" size="sm" className={NODE_INSERT_BUTTON_CLASS} onClick={() => insertNodeBetween(-1)}>
            <PlusOutlined />
            {steps.length > 0 ? '在最前新增节点' : '新增第一个节点'}
          </UIButton>
        </div>
        {steps.map((step, index) => {
          const stepId = String(step.node_id || step.step_id || `node_${index + 1}`);
          const path = stepTargetPath(index);
          const outgoingEdges = edgeMap[stepId] || [];
          const isSubflow = String(step.type || '') === 'subflow';
          // 处理人仅对 handoff 节点有意义;转人工动作只允许出现在 handoff 节点上,
          // 回复/收集等其他节点一律不展示处理人与转人工动作。
          const isHandoffNode = String(step.type || '') === 'handoff';
          const nodeState = [
            stepId === startNodeId ? '起始节点' : '',
            Boolean(step.optional) ? '可选' : '必选',
            terminalNodeIds.has(stepId) ? '终止节点' : '流程节点',
          ].filter(Boolean).join(' · ');
          return (
            <div className={SOURCE_STEP_BLOCK_CLASS} key={path}>
              {index > 0 && (
                <div className={NODE_INSERT_ROW_CLASS}>
                  <UIButton variant="outline" size="sm" className={NODE_INSERT_BUTTON_CLASS} onClick={() => insertNodeBetween(index - 1)}>
                    <PlusOutlined />
                    在 Node {index} 和 Node {index + 1} 之间新增节点
                  </UIButton>
                </div>
              )}
              <SelectableTarget
                className={distillSourceSectionClass(path, selectedPaths, highlightedPaths, updatingPaths, dirtyPaths)}
                target={{ path, label: `节点 ${index + 1}：${step.name || stepId}` }}
                onToggle={onToggle}
              >
                {selectedPaths.includes(path) && <span className={SELECTION_MARK_CLASS}><CheckOutlined /></span>}
                <div className={SOURCE_RENDERED_CLASS}>
                  <div className={SOURCE_STEP_HEADER_CLASS}>
                    <EditableSourceStepHeading
                      index={index}
                      value={String(step.name || '')}
                      fallback={stepId}
                      onChange={(value) => editStep(index, 'name', value)}
                    />
                    <EditableSourceField>
                      <UIButton variant="destructive" size="sm" onClick={() => confirmDeleteNode(index)}>
                        <DeleteOutlined />
                        删除节点
                      </UIButton>
                    </EditableSourceField>
                  </div>
                  <div className={SOURCE_META_LIST_CLASS}>
                    <EditableSourceTextLine label={fieldLabel('step_id')} value={stepId} onChange={(value) => editStep(index, 'step_id', value)} />
                    <EditableSourceSelectLine
                      label={fieldLabel('type')}
                      value={String(step.type || 'collect_info')}
                      options={NODE_TYPE_OPTIONS}
                      onChange={(value) => editStep(index, 'type', value)}
                    />
                    {isSubflow && (
                      <EditableSourceSelectLine
                        label="调用子 SOP"
                        value={String(step.sub_sop_id || '')}
                        options={sopOptions}
                        onChange={(value) => editStep(index, 'sub_sop_id', value)}
                      />
                    )}
                    <SourceReadonlyLine label="节点状态" value={nodeState} />
                    {isSubflow ? (
                      <SourceReadonlyLine label="执行职责" value="仅进入所选子 SOP；父子流程共享当前 TaskFrame 字段。" />
                    ) : (
                      <>
                        <EditableSourceTextLine label={fieldLabel('instruction')} value={String(step.instruction || '')} multiline collapsible onChange={(value) => editStep(index, 'instruction', value)} />
                        <EditableSourceListLine label={fieldLabel('expected_user_info')} values={asStringList(step.expected_user_info)} onChange={(value) => editStep(index, 'expected_user_info', value)} />
                        <EditableSourceActionLine values={asStringList(step.allowed_actions)} options={filterActionOptionsForNodeType(actionOptions, String(step.type || ''))} toolDescriptions={toolDescriptions} toolStatuses={toolStatuses} onChange={(value) => editStep(index, 'allowed_actions', value)} />
                        <EditableCapabilityReferencesLine label="SOP 技能" values={asStringList(step.general_skill_ids)} requiredValues={asStringList(step.required_general_skill_ids)} options={generalSkillOptions} emptyText="未指定技能" onChange={(value) => editStep(index, 'general_skill_ids', value)} onRequiredChange={(value) => editStep(index, 'required_general_skill_ids', value)} />
                        <EditableCapabilityReferencesLine label="SOP 工具" values={asStringList(step.tool_ids)} requiredValues={asStringList(step.required_tool_ids)} options={toolOptions} emptyText="未指定工具" onChange={(value) => editStep(index, 'tool_ids', value)} onRequiredChange={(value) => editStep(index, 'required_tool_ids', value)} />
                        <EditableCapabilityReferencesLine label="SOP 知识库" values={asStringList(step.knowledge_base_ids)} requiredValues={asStringList(step.required_knowledge_base_ids)} options={knowledgeBaseOptions} emptyText="未指定知识库" onChange={(value) => editStep(index, 'knowledge_base_ids', value)} onRequiredChange={(value) => editStep(index, 'required_knowledge_base_ids', value)} />
                      </>
                    )}
                    {isHandoffNode && (
                      <EditableSourceSelectLine
                        label="处理人"
                        value={
                          step.assignee_user_id
                            ? formatHandoffAssigneeValue(
                              String(step.assignee_user_id),
                              String(step.assignee_notify_channel || ''),
                            )
                            : UNASSIGNED_USER_VALUE
                        }
                        options={tenantUserOptions}
                        onChange={(value) => editStep(
                          index,
                          'assignee_user_id',
                          value === UNASSIGNED_USER_VALUE ? '' : value,
                        )}
                      />
                    )}
                    <EditableFlowRulesLine
                      sourceNodeId={stepId}
                      edges={outgoingEdges}
                      nodes={steps}
                      nodeOptions={nodeOptions}
                      terminal={terminalNodeIds.has(stepId)}
                      onAdd={() => addEdge(index)}
                      onUpdate={(edgeIndex, patch) => updateEdge(index, edgeIndex, patch)}
                      onDelete={(edgeIndex) => deleteEdge(index, edgeIndex)}
                    />
                    {!isSubflow && <SourceJsonLine label="知识范围" value={step.knowledge_scope} />}
                    {!isSubflow && <EditableRetryPolicyLine value={step.retry_policy} onChange={(value) => editStep(index, 'retry_policy', value)} />}
                  </div>
                </div>
              </SelectableTarget>
            </div>
          );
        })}
        {steps.length > 0 && (
          <div className={cn(NODE_INSERT_ROW_CLASS, NODE_INSERT_ROW_EDGE_CLASS)}>
            <UIButton variant="outline" size="sm" className={NODE_INSERT_BUTTON_CLASS} onClick={() => insertNodeBetween(steps.length - 1)}>
              <PlusOutlined />
              在最后新增节点
            </UIButton>
          </div>
        )}
      </div>
      {renderDeleteNodeConfirm()}
    </div>
  );
}

/**
 * SOP 流程图视图：用节点画布展示草稿结构，支持选择、就地编辑与连线调整。
 *
 * - 节点位置可拖拽微调并以覆盖值叠加到原始布局；缩放与平移状态由本组件维护。
 * - 选择节点或基础信息时同步父组件的改写目标，编辑结果通过 onEdit 回传。
 * - 全屏下支持滚轮缩放、空白拖拽平移、Esc 退出与 Delete 删除选中连线。
 */
function SkillFlow({
  skill,
  selectedPaths,
  highlightedPaths,
  updatingPaths,
  dirtyPaths,
  textDiffs,
  toolDescriptions,
  toolStatuses,
  generalSkills,
  sopSkills,
  tools,
  knowledgeBases,
  tenantUsers,
  containerRef,
  lockSkillId,
  assistantPanelOpen,
  onAssistantPanelOpenChange,
  onFullscreenChange,
  onToggle,
  onEdit,
}: {
  skill: SkillCard;
  selectedPaths: string[];
  highlightedPaths: string[];
  updatingPaths: string[];
  dirtyPaths: string[];
  textDiffs: TextDiffAnimation[];
  toolDescriptions: ToolDescriptionMap;
  toolStatuses: ToolStatusMap;
  generalSkills: GeneralSkillRead[];
  sopSkills: SkillRead[];
  tools: ToolRead[];
  knowledgeBases: KnowledgeBaseRead[];
  tenantUsers: Array<{ id: string; username: string; display_name?: string; source?: string; channel_identities?: Array<{ channel: string; display_name?: string; external_user_id?: string }> }>;
  containerRef: RefObject<HTMLDivElement>;
  lockSkillId?: boolean;
  assistantPanelOpen: boolean;
  onAssistantPanelOpenChange: (open: boolean) => void;
  onFullscreenChange: (open: boolean) => void;
  onToggle: (target: TargetSelection) => void;
  onEdit: (nextDraft: SkillCard, path: string) => void;
}) {
  const [flowZoom, setFlowZoom] = useState(0.64);
  const [flowPreset, setFlowPreset] = useState<'fit' | '100' | null>('fit');
  const [flowPan, setFlowPan] = useState({ x: 0, y: 0 });
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [selectedNodeIndex, setSelectedNodeIndex] = useState<number | null>(null);
  const [basicInspectorOpen, setBasicInspectorOpen] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [flowViewportWidth, setFlowViewportWidth] = useState(0);
  const [armedConnectionSourceId, setArmedConnectionSourceId] = useState('');
  const [selectedEdgeId, setSelectedEdgeId] = useState('');
  const [hoveredEdgeId, setHoveredEdgeId] = useState('');
  const [nodePositionOverrides, setNodePositionOverrides] = useState<Record<string, SkillFlowPosition>>({});
  const [connectionDrag, setConnectionDrag] = useState<SkillFlowConnectionPreview | null>(null);
  const fullscreenButtonRef = useRef<HTMLButtonElement>(null);
  const connectionPointerRef = useRef<{ sourceNodeId: string; startX: number; startY: number } | null>(null);
  const connectionDragRef = useRef<typeof connectionDrag>(null);
  const nodeDragRef = useRef<{
    pointerId: number;
    nodeId: string;
    nodeIndex: number;
    pointerX: number;
    pointerY: number;
    startX: number;
    startY: number;
    moved: boolean;
    captureTarget: HTMLDivElement;
  } | null>(null);
  const suppressNodeClickRef = useRef('');
  const panStateRef = useRef<{
    pointerId: number;
    x: number;
    y: number;
    panX: number;
    panY: number;
  } | null>(null);
  const nodes = skillGraphSteps(skill);
  const edgeMap = skillGraphEdgeMap(skill);
  const terminalSet = new Set(asStringList(skill.terminal_node_ids));
  const nodeNameMap = Object.fromEntries(
    nodes.map((node, index) => {
      const nodeId = String(node.node_id || node.step_id || `node_${index + 1}`);
      return [nodeId, String(node.name || nodeId)];
    }),
  );
  const graphKey = `${skill.skill_id || 'skill'}:${skill.version || 'draft'}:${nodes.length}:${skill.start_node_id || ''}`;
  const centeredGraphKey = useRef('');
  const graphLayout = buildSkillFlowCanvasLayout(skill, nodes, nodeNameMap, nodePositionOverrides);
  const zoomedWidth = graphLayout.width * flowZoom;
  const zoomedHeight = graphLayout.height * flowZoom;
  const canvasWidth = Math.max(zoomedWidth, flowViewportWidth);
  const canvasOffsetX = Math.max(0, (canvasWidth - zoomedWidth) / 2);
  const selectedNode = selectedNodeIndex === null ? null : nodes[selectedNodeIndex] || null;
  const selectedNodeId = selectedNodeIndex === null
    ? ''
    : String(selectedNode?.node_id || selectedNode?.step_id || `node_${selectedNodeIndex + 1}`);
  const nodeOptions = nodes.map((node, index) => {
    const nodeId = String(node.node_id || node.step_id || `node_${index + 1}`);
    return { value: nodeId, label: `Node ${index + 1} · ${String(node.name || nodeId)}` };
  });
  const actionOptions = buildActionOptions(toolDescriptions, toolStatuses, nodes);
  // 流程视图当前选中的节点在非 handoff 类型下不提供转人工动作
  const selectedNodeType = selectedNodeIndex === null
    ? ''
    : String(nodes[selectedNodeIndex]?.type || '');
  const inspectorActionOptions = filterActionOptionsForNodeType(actionOptions, selectedNodeType);
  const generalSkillOptions: CapabilityReferenceOption[] = generalSkills.map((item) => ({
    value: item.id,
    label: item.name || item.slug,
    description: item.description || item.slug,
    capabilityScope: item.capability_scope,
    unavailableReason: item.status === 'published' ? undefined : '技能未启用',
  }));
  const toolOptions: CapabilityReferenceOption[] = tools.map((item) => ({
    value: item.id,
    label: item.display_name || item.name,
    description: item.description || item.name,
    capabilityScope: item.capability_scope,
    unavailableReason: item.enabled ? undefined : '工具已停用',
  }));
  const knowledgeBaseOptions: CapabilityReferenceOption[] = knowledgeBases.map((item) => ({
    value: item.id,
    label: item.name,
    description: item.description,
    capabilityScope: item.capability_scope,
    unavailableReason: item.status === 'active' || item.status === 'published' ? undefined : '知识库已下线',
  }));
  const sopOptions: SelectOption[] = sopSkills
    .filter((item) => (
      item.status === 'published'
      && item.skill_id !== skill.skill_id
      && !wouldCreateSopNestingCycle(skill.skill_id, item.skill_id, sopSkills)
    ))
    .map((item) => ({
      value: item.skill_id,
      label: `${item.name} · ${item.skill_id}`,
    }));
  const tenantUserOptions: SelectOption[] = [
    { value: UNASSIGNED_USER_VALUE, label: '未指定（使用渠道默认）' },
    ...handoffAssigneeUserOptions(tenantUsers),
  ];

  /** 修改流程图节点的单个字段，联动处理节点改名、能力引用与节点类型切换。 */
  const editFlowNode = (
    index: number,
    field: string,
    value: string | string[] | boolean | Record<string, unknown>,
  ) => {
    const next = cloneSkill(skill);
    const listValue = [
      'expected_user_info',
      'allowed_actions',
      'general_skill_ids',
      'tool_ids',
      'knowledge_base_ids',
      'required_general_skill_ids',
      'required_tool_ids',
      'required_knowledge_base_ids',
    ].includes(field)
      ? Array.isArray(value) ? value : splitEditableList(String(value))
      : value;
    next.nodes = Array.isArray(next.nodes) ? [...next.nodes] : [];
    const currentNode = { ...(next.nodes[index] || {}) };
    const nodeField = field === 'step_id' ? 'node_id' : field;
    if (nodeField === 'node_id') {
      const previousId = String(currentNode.node_id || currentNode.step_id || `node_${index + 1}`);
      const nextId = String(listValue || '').trim();
      if (!nextId) {
        notify.warning('节点 ID 不能为空');
        return;
      }
      const duplicated = next.nodes.some((node, nodeIndex) => (
        nodeIndex !== index && String(node?.node_id || node?.step_id || '') === nextId
      ));
      if (duplicated) {
        notify.warning(`节点 ID「${nextId}」已经存在`);
        return;
      }
      currentNode.node_id = nextId;
      next.edges = normalizeSkillEdges(next).map((edge) => ({
        ...edge,
        source_node_id: String(edge.source_node_id || '') === previousId ? nextId : edge.source_node_id,
        next_node_id: String(edge.next_node_id || '') === previousId ? nextId : edge.next_node_id,
      }));
      if (next.start_node_id === previousId) next.start_node_id = nextId;
      next.terminal_node_ids = asStringList(next.terminal_node_ids).map((nodeId) => (nodeId === previousId ? nextId : nodeId));
    } else if (nodeField === 'assignee_user_id') {
      const { userId, channel } = parseHandoffAssigneeValue(String(listValue));
      currentNode.assignee_user_id = userId || null;
      currentNode.assignee_notify_channel = userId ? channel : null;
    } else if (nodeField === 'type') {
      applyNodeTypeChange(currentNode, String(listValue));
    } else if (CAPABILITY_REFERENCE_FIELDS.includes(nodeField)) {
      const currentRefs = nodeCapabilityRefs(currentNode);
      currentNode.capability_refs = updateCapabilityRefs(
        currentRefs,
        nodeField,
        asStringList(listValue),
      );
      delete currentNode.general_skill_ids;
      delete currentNode.tool_ids;
      delete currentNode.knowledge_base_ids;
      delete currentNode.required_general_skill_ids;
      delete currentNode.required_tool_ids;
      delete currentNode.required_knowledge_base_ids;
    } else {
      currentNode[nodeField] = listValue;
    }
    next.nodes[index] = normalizeSubflowNode(currentNode);
    onEdit(next, stepTargetPath(index));
  };

  /** 修改流程图基础信息字段，并按字段类型做列表拆分与数值归一化。 */
  const editFlowBasic = (
    field: keyof SkillCard,
    value: string | string[] | number | null,
  ) => {
    if (field === 'skill_id' && lockSkillId) return;
    const next = cloneSkill(skill);
    if (field === 'trigger_intents' || field === 'user_utterance_examples' || field === 'goal' || field === 'required_info' || field === 'response_rules') {
      next[field] = Array.isArray(value) ? value : splitEditableList(String(value ?? ''));
    } else if (field === 'step_timeout_seconds') {
      next.step_timeout_seconds = typeof value === 'number' ? value : null;
    } else if (field === 'capability_scope') {
      next.capability_scope = normalizeCapabilityScope(value);
    } else if (field === 'skill_id' || field === 'name' || field === 'version' || field === 'business_domain' || field === 'description') {
      next[field] = String(value);
    }
    onEdit(next, 'basic');
  };

  /** 更新指定节点下第 edgeIndex 条出边。 */
  const updateFlowEdge = (index: number, edgeIndex: number, patch: Record<string, unknown>) => {
    const next = cloneSkill(skill);
    const sourceId = nodeIdAt(next, index);
    const edges = normalizeSkillEdges(next);
    const globalIndex = findSourceEdgeIndex(edges, sourceId, edgeIndex);
    if (globalIndex < 0) return;
    edges[globalIndex] = { ...edges[globalIndex], ...patch };
    next.edges = edges;
    onEdit(next, stepTargetPath(index));
  };

  /** 为指定节点新增一条出边，默认连接到另一个节点。 */
  const addFlowEdge = (index: number) => {
    const next = cloneSkill(skill);
    const sourceId = nodeIdAt(next, index);
    const target = nodes.find((node, nodeIndex) => nodeIndex !== index && String(node.node_id || node.step_id || ''));
    const targetId = String(target?.node_id || target?.step_id || '');
    const sourceEdges = normalizeSkillEdges(next).filter((edge) => String(edge.source_node_id || '') === sourceId);
    const priority = sourceEdges.length > 0
      ? Math.max(...sourceEdges.map((edge, sourceIndex) => edgePriority(edge, sourceIndex))) + 1
      : 1;
    next.edges = [...normalizeSkillEdges(next), {
      source_node_id: sourceId,
      next_node_id: targetId,
      condition: '',
      priority,
      label: targetId ? '新增流转' : '',
    }];
    onEdit(next, stepTargetPath(index));
  };

  /** 删除指定节点下第 edgeIndex 条出边。 */
  const deleteFlowEdge = (index: number, edgeIndex: number) => {
    const next = cloneSkill(skill);
    const sourceId = nodeIdAt(next, index);
    const edges = normalizeSkillEdges(next);
    const globalIndex = findSourceEdgeIndex(edges, sourceId, edgeIndex);
    if (globalIndex < 0) return;
    edges.splice(globalIndex, 1);
    next.edges = edges;
    onEdit(next, stepTargetPath(index));
  };

  /**
   * 在流程图中连接两个节点，自动去重并提示结果。
   *
   * - 已存在相同流转时只提示警告，不重复创建。
   * - 新建后选中源节点，并把整份草稿回传父组件。
   */
  const connectFlowNodes = (sourceNodeId: string, targetNodeId: string) => {
    if (!sourceNodeId || !targetNodeId || sourceNodeId === targetNodeId) return;
    const next = cloneSkill(skill);
    const edges = normalizeSkillEdges(next);
    if (edges.some((edge) => (
      String(edge.source_node_id || '') === sourceNodeId && String(edge.next_node_id || '') === targetNodeId
    ))) {
      notify.warning('这两个节点之间已经存在流转规则');
      return;
    }
    const sourceIndex = nodes.findIndex((node, index) => String(node.node_id || node.step_id || `node_${index + 1}`) === sourceNodeId);
    const targetName = nodeNameMap[targetNodeId] || targetNodeId;
    const sourceEdges = edges.filter((edge) => String(edge.source_node_id || '') === sourceNodeId);
    const priority = sourceEdges.length > 0
      ? Math.max(...sourceEdges.map((edge, edgeIndex) => edgePriority(edge, edgeIndex))) + 1
      : 1;
    next.edges = [...edges, {
      source_node_id: sourceNodeId,
      next_node_id: targetNodeId,
      condition: '',
      priority,
      label: `连接到 ${targetName}`,
    }];
    if (sourceIndex >= 0) setSelectedNodeIndex(sourceIndex);
    onEdit(next, stepTargetPath(Math.max(sourceIndex, 0)));
    notify.success(`已连接到「${targetName}」，右侧流转规则已同步`);
  };
  /** 删除流程图中指定下标的连线，并清空连线选中状态。 */
  const deleteFlowEdgeAt = (edgeIndex: number) => {
    const edge = normalizeSkillEdges(skill)[edgeIndex];
    if (!edge) return;
    const sourceNodeId = String(edge.source_node_id || '');
    const targetNodeId = String(edge.next_node_id || '');
    const sourceIndex = nodes.findIndex((node, index) => (
      String(node.node_id || node.step_id || `node_${index + 1}`) === sourceNodeId
    ));
    onEdit(withoutSkillEdgeAt(skill, edgeIndex), stepTargetPath(Math.max(sourceIndex, 0)));
    setSelectedEdgeId('');
    notify.success(`已删除「${nodeNameMap[sourceNodeId] || sourceNodeId} → ${nodeNameMap[targetNodeId] || targetNodeId}」流转`);
  };
  /** 开始拖拽节点：记录起始指针与节点坐标，点击交互控件时不触发。 */
  const startNodeDrag = (
    event: ReactPointerEvent<HTMLDivElement>,
    node: SkillFlowCanvasNode,
  ) => {
    if (event.button !== 0 || connectionDrag || armedConnectionSourceId) return;
    const target = event.target as HTMLElement;
    if (target.closest('button, input, textarea, select, [role="combobox"], [data-flow-connect-handle]')) return;
    nodeDragRef.current = {
      pointerId: event.pointerId,
      nodeId: node.nodeId,
      nodeIndex: node.index,
      pointerX: event.clientX,
      pointerY: event.clientY,
      startX: node.x,
      startY: node.y,
      moved: false,
      captureTarget: event.currentTarget,
    };
  };
  /** 拖拽节点移动：超过 4px 阈值后才更新节点位置覆盖值。 */
  const handleNodePointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = nodeDragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const deltaX = (event.clientX - drag.pointerX) / flowZoom;
    const deltaY = (event.clientY - drag.pointerY) / flowZoom;
    if (!drag.moved && Math.hypot(deltaX, deltaY) < 4) return;
    if (!drag.moved && !drag.captureTarget.hasPointerCapture(drag.pointerId)) {
      drag.captureTarget.setPointerCapture(drag.pointerId);
    }
    drag.moved = true;
    const position = {
      x: Math.max(32, drag.startX + deltaX),
      y: Math.max(32, drag.startY + deltaY),
    };
    setFlowPreset(null);
    setNodePositionOverrides((current) => ({ ...current, [drag.nodeId]: position }));
  };
  /** 结束节点拖拽：写回节点坐标，并抑制随后触发的点击事件。 */
  const finishNodeDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = nodeDragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    nodeDragRef.current = null;
    if (drag.captureTarget.hasPointerCapture(drag.pointerId)) {
      drag.captureTarget.releasePointerCapture(drag.pointerId);
    }
    if (!drag.moved) return;
    const position = {
      x: Math.max(32, drag.startX + (event.clientX - drag.pointerX) / flowZoom),
      y: Math.max(32, drag.startY + (event.clientY - drag.pointerY) / flowZoom),
    };
    setNodePositionOverrides((current) => ({ ...current, [drag.nodeId]: position }));
    suppressNodeClickRef.current = drag.nodeId;
    onEdit(withSkillNodeFlowPosition(skill, drag.nodeIndex, position), stepTargetPath(drag.nodeIndex));
  };
  /**
   * 调整画布缩放，并在全屏与内嵌模式下分别保持视觉中心不动。
   *
   * - preset 用于标记适配/100% 按钮的激活态。
   * - minimumZoom 控制允许的最小缩放比例。
   */
  const updateZoom = (
    nextZoom: number,
    preset: 'fit' | '100' | null = null,
    minimumZoom = isFullscreen ? 0.2 : 0.36,
  ) => {
    const next = Math.min(1.18, Math.max(minimumZoom, Math.round(nextZoom * 100) / 100));
    const container = containerRef.current;
    setFlowPreset(preset);
    if (!container) {
      setFlowZoom(next);
      return;
    }
    if (isFullscreen) {
      const centerX = container.clientWidth / 2;
      const centerY = container.clientHeight / 2;
      const graphX = (centerX - flowPan.x) / flowZoom;
      const graphY = (centerY - flowPan.y) / flowZoom;
      setFlowPreset(preset);
      setFlowZoom(next);
      setFlowPan({
        x: centerX - graphX * next,
        y: centerY - graphY * next,
      });
      return;
    }
    const centerX = (container.scrollLeft + container.clientWidth / 2) / flowZoom;
    const centerY = (container.scrollTop + container.clientHeight / 2) / flowZoom;
    setFlowZoom(next);
    window.requestAnimationFrame(() => {
      container.scrollLeft = Math.max(0, centerX * next - container.clientWidth / 2);
      container.scrollTop = Math.max(0, centerY * next - container.clientHeight / 2);
    });
  };
  /** 按视口尺寸把整张流程图缩放到可见范围，并回到起始节点位置。 */
  const fitFlowToViewport = (fullscreenMode = isFullscreen) => {
    const container = containerRef.current;
    if (!container) {
      updateZoom(0.64, 'fit');
      return;
    }
    const horizontalGutter = fullscreenMode ? 72 : 48;
    const verticalGutter = fullscreenMode ? 72 : 48;
    const availableWidth = Math.max(240, container.clientWidth - horizontalGutter);
    const availableHeight = Math.max(240, container.clientHeight - verticalGutter);
    const nextZoom = Math.min(
      1,
      availableWidth / graphLayout.width,
      availableHeight / graphLayout.height,
    );
    const normalizedZoom = Math.min(1, Math.max(0.2, Math.round(nextZoom * 100) / 100));
    setFlowPreset('fit');
    setFlowZoom(normalizedZoom);
    if (fullscreenMode) {
      setFlowPan({
        x: (container.clientWidth - graphLayout.width * normalizedZoom) / 2,
        y: (container.clientHeight - graphLayout.height * normalizedZoom) / 2,
      });
      return;
    }
    window.requestAnimationFrame(() => {
      const rootCenterX = (graphLayout.root.x + graphLayout.root.width / 2) * normalizedZoom;
      container.scrollLeft = Math.max(0, rootCenterX - container.clientWidth / 2);
      container.scrollTop = 0;
    });
  };
  /** 以 72% 缩放把视图聚焦到起始节点。 */
  const focusFlowStart = () => {
    const container = containerRef.current;
    if (!container) return;
    const nextZoom = 0.72;
    setFlowPreset(null);
    setFlowZoom(nextZoom);
    setFlowPan({
      x: container.clientWidth / 2 - (graphLayout.root.x + graphLayout.root.width / 2) * nextZoom,
      y: 44 - graphLayout.root.y * nextZoom,
    });
  };
  /** 切换全屏：同步父组件状态并重排画布，退出时清理连线拖拽状态。 */
  const toggleFullscreen = () => {
    const nextFullscreen = !isFullscreen;
    setIsFullscreen(nextFullscreen);
    onFullscreenChange(nextFullscreen);
    if (nextFullscreen) {
      onAssistantPanelOpenChange(true);
      setInspectorOpen(true);
    }
    if (nextFullscreen && selectedNodeIndex === null && nodes.length > 0) setSelectedNodeIndex(0);
    if (!nextFullscreen) {
      connectionPointerRef.current = null;
      connectionDragRef.current = null;
      setConnectionDrag(null);
      setArmedConnectionSourceId('');
    }
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => {
        if (nextFullscreen) focusFlowStart();
        else fitFlowToViewport(false);
        fullscreenButtonRef.current?.focus();
      });
    });
  };
  /** 全屏下按住连接手柄开始拖拽连线，记录起点并捕获指针。 */
  const startConnectionDrag = (event: ReactPointerEvent<HTMLButtonElement>, sourceNodeId: string) => {
    if (!isFullscreen) return;
    event.preventDefault();
    event.stopPropagation();
    const rect = event.currentTarget.getBoundingClientRect();
    const startX = rect.left + rect.width / 2;
    const startY = rect.top + rect.height / 2;
    setArmedConnectionSourceId('');
    connectionPointerRef.current = { sourceNodeId, startX, startY };
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  /** 点击连接手柄时先武装源节点，等待再点目标节点完成连接。 */
  const armConnectionClick = (event: MouseEvent<HTMLButtonElement>, sourceNodeId: string) => {
    event.preventDefault();
    event.stopPropagation();
    setArmedConnectionSourceId(sourceNodeId);
  };
  /** 拖拽连线过程中更新预览终点，悬停到其他节点时自动吸附。 */
  const handleConnectionPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const pending = connectionPointerRef.current;
    if (!pending) return;
    const moved = Math.hypot(event.clientX - pending.startX, event.clientY - pending.startY);
    if (!connectionDragRef.current && moved < 4) return;
    const hoveredNode = document.elementFromPoint(event.clientX, event.clientY)?.closest<HTMLElement>('[data-flow-node-id]');
    const hoveredNodeId = hoveredNode?.dataset.flowNodeId || '';
    const targetNodeId = hoveredNodeId && hoveredNodeId !== pending.sourceNodeId ? hoveredNodeId : '';
    const targetRect = targetNodeId ? hoveredNode?.getBoundingClientRect() : null;
    const nextDrag = {
      sourceNodeId: pending.sourceNodeId,
      targetNodeId,
      startX: pending.startX,
      startY: pending.startY,
      currentX: targetRect ? targetRect.left + targetRect.width / 2 : event.clientX,
      currentY: targetRect ? targetRect.top - 2 : event.clientY,
    };
    connectionDragRef.current = nextDrag;
    setConnectionDrag(nextDrag);
  };
  /** 结束连线拖拽：命中目标节点时创建流转，否则切换武装状态。 */
  const handleConnectionPointerUp = (event: ReactPointerEvent<HTMLDivElement>) => {
    const pending = connectionPointerRef.current;
    const drag = connectionDragRef.current;
    if (!pending) return;
    connectionPointerRef.current = null;
    connectionDragRef.current = null;
    setConnectionDrag(null);
    if (!drag) {
      setArmedConnectionSourceId((current) => current === pending.sourceNodeId ? '' : pending.sourceNodeId);
      return;
    }
    const target = document.elementFromPoint(event.clientX, event.clientY)?.closest<HTMLElement>('[data-flow-node-id]');
    const targetNodeId = drag.targetNodeId || target?.dataset.flowNodeId || '';
    if (targetNodeId) connectFlowNodes(drag.sourceNodeId, targetNodeId);
  };
  /** 全屏下按下画布空白区域开始平移，节点与交互控件不触发。 */
  const handlePanPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!isFullscreen || connectionDrag || event.button !== 0) return;
    const target = event.target as HTMLElement;
    if (target.closest('[data-flow-node-id], [data-flow-edge-hit], button, input, textarea, select, [role="button"], [role="combobox"]')) return;
    panStateRef.current = {
      pointerId: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      panX: flowPan.x,
      panY: flowPan.y,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  /** 平移画布：按指针位移更新平移量。 */
  const handlePanPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const pan = panStateRef.current;
    if (!pan || pan.pointerId !== event.pointerId) return;
    setFlowPan({
      x: pan.panX + event.clientX - pan.x,
      y: pan.panY + event.clientY - pan.y,
    });
  };
  /** 结束画布平移并释放指针捕获。 */
  const stopPan = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (panStateRef.current?.pointerId !== event.pointerId) return;
    panStateRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  };
  /** 布局变化后重新计算连线预览起点，保证预览与连接手柄对齐。 */
  const scheduleConnectionPreviewReanchor = () => {
    const pending = connectionPointerRef.current;
    if (!pending) return;
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => {
        const latestPending = connectionPointerRef.current;
        if (!latestPending) return;
        const container = containerRef.current;
        const nodeElements = container?.querySelectorAll<HTMLElement>('[data-flow-node-id]');
        /** 在已渲染的节点元素中按节点 ID 查找对应 DOM。 */
        const findNode = (nodeId: string) => Array.from(nodeElements || [])
          .find((node) => node.dataset.flowNodeId === nodeId);
        const sourceHandle = findNode(latestPending.sourceNodeId)
          ?.querySelector<HTMLElement>('[data-flow-connect-handle]');
        const sourceRect = sourceHandle?.getBoundingClientRect();
        if (!sourceRect) return;
        const sourcePoint = {
          x: sourceRect.left + sourceRect.width / 2,
          y: sourceRect.top + sourceRect.height / 2,
        };
        connectionPointerRef.current = {
          ...latestPending,
          startX: sourcePoint.x,
          startY: sourcePoint.y,
        };
        const current = connectionDragRef.current;
        if (!current) return;
        const targetRect = current.targetNodeId
          ? findNode(current.targetNodeId)?.getBoundingClientRect()
          : null;
        const next = reanchorSkillFlowConnection(
          current,
          sourcePoint,
          targetRect
            ? { x: targetRect.left + targetRect.width / 2, y: targetRect.top - 2 }
            : undefined,
        );
        connectionDragRef.current = next;
        setConnectionDrag(next);
      });
    });
  };
  /**
   * 全屏画布滚轮处理：Ctrl/Meta 组合为缩放，否则平移视图。
   *
   * - 缩放以指针位置为锚点，并同步重锚连线预览。
   */
  const handleCanvasWheel = (event: ReactWheelEvent<HTMLDivElement>) => {
    if (!isFullscreen) return;
    event.preventDefault();
    event.stopPropagation();
    if (event.ctrlKey || event.metaKey) {
      const rect = event.currentTarget.getBoundingClientRect();
      const pointerX = event.clientX - rect.left;
      const pointerY = event.clientY - rect.top;
      const graphX = (pointerX - flowPan.x) / flowZoom;
      const graphY = (pointerY - flowPan.y) / flowZoom;
      const multiplier = event.deltaY > 0 ? 0.9 : 1.1;
      const nextZoom = Math.min(1.18, Math.max(0.2, Math.round(flowZoom * multiplier * 100) / 100));
      setFlowPreset(null);
      setFlowZoom(nextZoom);
      setFlowPan({
        x: pointerX - graphX * nextZoom,
        y: pointerY - graphY * nextZoom,
      });
      scheduleConnectionPreviewReanchor();
      return;
    }
    const deltaX = normalizeSkillFlowWheelDelta(event.deltaX, event.deltaMode, event.currentTarget.clientWidth);
    const deltaY = normalizeSkillFlowWheelDelta(event.deltaY, event.deltaMode, event.currentTarget.clientHeight);
    setFlowPreset(null);
    setFlowPan((current) => ({
      x: current.x - deltaX,
      y: current.y - deltaY,
    }));
    scheduleConnectionPreviewReanchor();
  };
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return undefined;
    /** 记录画布可视宽度，供全屏居中等布局计算使用。 */
    const updateWidth = () => setFlowViewportWidth(container.clientWidth);
    updateWidth();
    const observer = new ResizeObserver(updateWidth);
    observer.observe(container);
    return () => observer.disconnect();
  }, [containerRef, isFullscreen]);
  useEffect(() => {
    if (nodes.length === 0) {
      if (selectedNodeIndex !== null) setSelectedNodeIndex(null);
      return;
    }
    if (selectedNodeIndex !== null && selectedNodeIndex >= nodes.length) setSelectedNodeIndex(nodes.length - 1);
  }, [nodes.length, selectedNodeIndex]);
  useEffect(() => {
    setNodePositionOverrides({});
    setSelectedEdgeId('');
  }, [skill.skill_id]);
  useEffect(() => {
    const visibleEdgeIds = new Set(graphLayout.edges.map((edge) => edge.id));
    if (selectedEdgeId && !visibleEdgeIds.has(selectedEdgeId)) setSelectedEdgeId('');
  }, [graphLayout.edges, selectedEdgeId]);
  useEffect(() => {
    if (!isFullscreen) return undefined;
    const previousOverflow = document.body.style.overflow;
    /** 全屏键盘处理：Delete 删除选中连线，Esc 依次取消连线、武装与全屏。 */
    const handleKeyDown = (event: globalThis.KeyboardEvent) => {
      if ((event.key === 'Delete' || event.key === 'Backspace') && selectedEdgeId) {
        const target = event.target as HTMLElement | null;
        if (target?.closest('input, textarea, select, [contenteditable="true"]')) return;
        const selectedEdge = graphLayout.edges.find((edge) => edge.id === selectedEdgeId);
        if (selectedEdge?.edgeIndex !== undefined) {
          event.preventDefault();
          deleteFlowEdgeAt(selectedEdge.edgeIndex);
        }
        return;
      }
      if (event.key !== 'Escape') return;
      event.preventDefault();
      if (connectionDrag) {
        connectionPointerRef.current = null;
        connectionDragRef.current = null;
        setConnectionDrag(null);
        return;
      }
      if (armedConnectionSourceId) {
        setArmedConnectionSourceId('');
        return;
      }
      setIsFullscreen(false);
      onFullscreenChange(false);
      window.requestAnimationFrame(() => fullscreenButtonRef.current?.focus());
    };
    document.body.style.overflow = 'hidden';
    window.addEventListener('keydown', handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [armedConnectionSourceId, connectionDrag, graphLayout.edges, isFullscreen, onFullscreenChange, selectedEdgeId]);
  useEffect(() => {
    const container = containerRef.current;
    if (!container || isFullscreen || centeredGraphKey.current === graphKey) return undefined;
    centeredGraphKey.current = graphKey;
    const frame = window.requestAnimationFrame(() => {
      const rootCenterX = (graphLayout.root.x + graphLayout.root.width / 2) * flowZoom;
      const targetScrollLeft = Math.max(0, rootCenterX - container.clientWidth / 2);
      container.scrollLeft = targetScrollLeft;
      container.scrollTop = 0;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [containerRef, flowZoom, graphKey, graphLayout.root.x, graphLayout.root.width, isFullscreen]);
  const isFitZoom = flowPreset === 'fit';
  const isFullZoom = flowPreset === '100' && Math.abs(flowZoom - 1) < 0.001;
  return (
    <div
      className={cn(FLOW_VIEWER_CLASS, isFullscreen && FLOW_VIEWER_FULLSCREEN_CLASS)}
      role={isFullscreen ? 'dialog' : undefined}
      aria-modal={isFullscreen || undefined}
      aria-label={isFullscreen ? 'SOP 流程图全屏查看' : undefined}
      onPointerMove={(event) => {
        handleConnectionPointerMove(event);
        handleNodePointerMove(event);
      }}
      onPointerUp={(event) => {
        handleConnectionPointerUp(event);
        finishNodeDrag(event);
      }}
      onPointerCancel={(event) => {
        handleConnectionPointerUp(event);
        finishNodeDrag(event);
      }}
    >
      <div className={cn(FLOW_VIEWER_CLASS, isFullscreen && FLOW_VIEWER_FULLSCREEN_PANEL_CLASS)}>
        <div className={cn(FLOW_ZOOM_TOOLBAR_CLASS, isFullscreen && FLOW_ZOOM_TOOLBAR_FULLSCREEN_CLASS)} aria-label="流程图控制">
          {isFullscreen && (
            <div className={FLOW_VIEWER_TITLE_CLASS}>
              <span className={FLOW_VIEWER_TITLE_MARK_CLASS}><BranchesOutlined /></span>
              <span className="grid min-w-0 gap-[2px]">
                <strong className="truncate text-[14px] font-medium text-[#18181a]">{skill.name || 'SOP 流程图'}</strong>
                <span className={FLOW_VIEWER_TITLE_META_CLASS}>
                  {nodes.length} 个节点 · {graphLayout.edges.length} 条连线 · {armedConnectionSourceId ? '点击目标节点完成连接' : '拖动节点调整排版，选择连线可删除'}
                </span>
              </span>
            </div>
          )}
          <div className={FLOW_ZOOM_CONTROLS_CLASS}>
            {isFullscreen && (
              <>
                <UIButton
                  variant="outline"
                  size="sm"
                  className={flowZoomPresetButtonClass(assistantPanelOpen)}
                  aria-pressed={assistantPanelOpen}
                  onClick={() => {
                    onAssistantPanelOpenChange(!assistantPanelOpen);
                  }}
                >
                  {assistantPanelOpen ? <PanelLeftClose /> : <PanelLeftOpen />}
                  AI 修改
                </UIButton>
                <UIButton
                  variant="outline"
                  size="sm"
                  className={flowZoomPresetButtonClass(inspectorOpen)}
                  aria-pressed={inspectorOpen}
                  onClick={() => {
                    setInspectorOpen((current) => !current);
                  }}
                >
                  {inspectorOpen ? <PanelRightClose /> : <PanelRightOpen />}
                  结构编辑
                </UIButton>
              </>
            )}
            <span className="shrink-0">缩放</span>
            <UIButton variant="outline" size="sm" className={FLOW_ZOOM_STEP_BUTTON_CLASS} onClick={() => updateZoom(flowZoom - 0.08)} aria-label="缩小">
              -
            </UIButton>
            <span className={FLOW_ZOOM_VALUE_CLASS}>{Math.round(flowZoom * 100)}%</span>
            <UIButton variant="outline" size="sm" className={FLOW_ZOOM_STEP_BUTTON_CLASS} onClick={() => updateZoom(flowZoom + 0.08)} aria-label="放大">
              +
            </UIButton>
            <UIButton
              variant="outline"
              size="sm"
              className={flowZoomPresetButtonClass(isFitZoom)}
              aria-pressed={isFitZoom}
              onClick={() => fitFlowToViewport()}
            >
              适配
            </UIButton>
            <UIButton
              variant="outline"
              size="sm"
              className={flowZoomPresetButtonClass(isFullZoom)}
              aria-pressed={isFullZoom}
              onClick={() => updateZoom(1, '100')}
            >
              100%
            </UIButton>
            <UIButton
              ref={fullscreenButtonRef}
              variant="outline"
              size="sm"
              className={CARD_OUTLINE_BUTTON_CLASS}
              onClick={toggleFullscreen}
              aria-label={isFullscreen ? '退出全屏' : '全屏查看流程图'}
            >
              {isFullscreen ? <Minimize2 /> : <Maximize2 />}
              {isFullscreen ? '退出全屏' : '全屏查看'}
            </UIButton>
          </div>
        </div>
        <div className={FLOW_VIEWER_BODY_CLASS}>
        <div
          className={cn(
            FLOW_CLASS,
            isFullscreen && FLOW_FULLSCREEN_CLASS,
            isFullscreen && FLOW_FULLSCREEN_PANNABLE_CLASS,
            isFullscreen && assistantPanelOpen && FLOW_FULLSCREEN_WITH_AI_CLASS,
          )}
          ref={containerRef}
          onPointerDown={handlePanPointerDown}
          onPointerMove={handlePanPointerMove}
          onPointerUp={stopPan}
          onPointerCancel={stopPan}
          onWheel={handleCanvasWheel}
          aria-label={isFullscreen ? 'SOP 流程图画布，可拖拽空白区域移动' : undefined}
          style={isFullscreen ? { backgroundPosition: `${flowPan.x}px ${flowPan.y}px` } : undefined}
        >
        <div
          className={cn(FLOW_ZOOM_SHELL_CLASS, isFullscreen && 'h-full! min-w-0! w-full!')}
          style={isFullscreen ? { width: '100%', height: '100%' } : { width: canvasWidth, height: zoomedHeight }}
        >
          <div
            className={cn(FLOW_GRAPH_CANVAS_CLASS, isFullscreen && 'rounded-none bg-none')}
            style={{
              top: isFullscreen ? flowPan.y : 0,
              left: isFullscreen ? flowPan.x : canvasOffsetX,
              width: graphLayout.width,
              height: graphLayout.height,
              transform: `scale(${flowZoom})`,
            }}
          >
            <svg
              className={cn(FLOW_EDGES_CLASS, 'pointer-events-auto')}
              width={graphLayout.width}
              height={graphLayout.height}
              viewBox={`0 0 ${graphLayout.width} ${graphLayout.height}`}
              aria-label="SOP 流程连线"
            >
              <defs>
                <marker id="skill-flow-arrow" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                  <path d="M 0 0 L 10 5 L 0 10 z" fill="#4e716d" />
                </marker>
              </defs>
              {graphLayout.edges.map((edge) => (
                <g key={edge.id}>
                  <path
                    className={cn(
                      FLOW_EDGE_PATH_CLASS,
                      'pointer-events-none',
                      hoveredEdgeId === edge.id && 'stroke-[#4f817c]! [stroke-width:2.15] opacity-100',
                      selectedEdgeId === edge.id && 'stroke-[#04756f]! [stroke-width:2.5] opacity-100',
                    )}
                    d={edge.path}
                    markerEnd="url(#skill-flow-arrow)"
                    aria-hidden="true"
                  >
                    <title>{edge.title}</title>
                  </path>
                  {edge.kind === 'edge' && (
                    <path
                      d={edge.path}
                      fill="none"
                      stroke="rgba(4, 117, 111, 0.001)"
                      strokeWidth="28"
                      vectorEffect="non-scaling-stroke"
                      pointerEvents="stroke"
                      data-flow-edge-hit={edge.id}
                      className="cursor-pointer [pointer-events:stroke]"
                      role="button"
                      tabIndex={0}
                      aria-label={`选择流转 ${edge.title}`}
                      onPointerDown={(event) => event.stopPropagation()}
                      onPointerEnter={() => setHoveredEdgeId(edge.id)}
                      onPointerLeave={() => setHoveredEdgeId((current) => current === edge.id ? '' : current)}
                      onFocus={() => setHoveredEdgeId(edge.id)}
                      onBlur={() => setHoveredEdgeId((current) => current === edge.id ? '' : current)}
                      onClick={(event) => {
                        event.stopPropagation();
                        setSelectedEdgeId(edge.id);
                      }}
                      onKeyDown={(event) => {
                        if (event.key !== 'Enter' && event.key !== ' ') return;
                        event.preventDefault();
                        event.stopPropagation();
                        setSelectedEdgeId(edge.id);
                      }}
                    />
                  )}
                </g>
              ))}
            </svg>
            {graphLayout.edges.map((edge) => (
              <div
                className={cn(
                  flowEdgeLabelClass(edge.labelTone || edge.kind),
                  edge.kind === 'edge' && 'pointer-events-auto! flex cursor-pointer items-center gap-[5px] overflow-visible!',
                  selectedEdgeId === edge.id && 'border-[#04756f]! bg-[#f3fbf8]! text-[#075f59]!',
                )}
                key={`${edge.id}_label`}
                data-flow-edge-label={edge.id}
                style={{
                  left: edge.labelX,
                  top: edge.labelY,
                  ...(isFullscreen
                    ? {
                        transform: `translate(-50%, -50%) scale(${1 / flowZoom})`,
                        transformOrigin: 'center',
                      }
                    : {}),
                }}
                title={edge.title}
                onPointerEnter={() => setHoveredEdgeId(edge.id)}
                onPointerLeave={() => setHoveredEdgeId((current) => current === edge.id ? '' : current)}
                onFocusCapture={() => setHoveredEdgeId(edge.id)}
                onBlurCapture={(event) => {
                  if (!event.currentTarget.contains(event.relatedTarget)) setHoveredEdgeId('');
                }}
              >
                {edge.kind === 'edge' ? (
                  <button
                    type="button"
                    className="min-w-0 cursor-pointer truncate border-0 bg-transparent p-0 text-inherit focus-visible:outline-none"
                    aria-label={`选择流转 ${edge.title}`}
                    onClick={() => setSelectedEdgeId(edge.id)}
                  >
                    {edge.label}
                  </button>
                ) : (
                  <span className="min-w-0 truncate">{edge.label}</span>
                )}
                {edge.kind === 'edge' && edge.edgeIndex !== undefined && (
                  <button
                    type="button"
                    className={cn(
                      'pointer-events-none absolute right-[-8px] top-[-9px] z-[2] grid size-[20px] place-items-center rounded-full border border-[#e3e7ec] bg-white p-0 text-[15px] leading-none text-[#858b9c] opacity-0 shadow-[0_4px_10px_rgba(24,31,45,0.12)] transition-[opacity,background,color,border-color] hover:border-[#fecaca] hover:bg-[#fee2e2] hover:text-[#b42318] focus-visible:pointer-events-auto focus-visible:opacity-100 focus-visible:outline-none',
                      (hoveredEdgeId === edge.id || selectedEdgeId === edge.id)
                        && 'pointer-events-auto opacity-100',
                    )}
                    aria-label={`删除流转 ${edge.title}`}
                    title="删除这条流转规则"
                    onClick={(event) => {
                      event.preventDefault();
                      event.stopPropagation();
                      deleteFlowEdgeAt(edge.edgeIndex as number);
                    }}
                  >
                    ×
                  </button>
                )}
              </div>
            ))}
            <div
              className={FLOW_ROOT_POSITION_CLASS}
              style={{ left: graphLayout.root.x, top: graphLayout.root.y, width: graphLayout.root.width, height: graphLayout.root.height }}
            >
              <SelectableTarget
                className={distillFlowNodeClass('basic', true, selectedPaths, highlightedPaths, updatingPaths, dirtyPaths)}
                target={{ path: 'basic', label: '基础信息' }}
                onToggle={(target) => {
                  setSelectedNodeIndex(null);
                  setBasicInspectorOpen(true);
                  onToggle(target);
                }}
              >
                {selectedPaths.includes('basic') && <span className={SELECTION_MARK_CLASS}><CheckOutlined /></span>}
                <span>基础信息</span>
                <strong><InlineDiffText path="basic" field="name" value={skill.name} diffs={textDiffs} /></strong>
                <small>{skill.skill_id}</small>
                <p><InlineDiffText path="basic" field="description" value={skill.description || '暂无描述'} diffs={textDiffs} /></p>
                <div className={FLOW_META_CLASS}>
                  <FlowMetaRow label="业务域">
                    <span className={FLOW_CHIP_CLASS}>{skill.business_domain || '-'}</span>
                  </FlowMetaRow>
                  <FlowMetaRow label="单步上限">
                    <span className={FLOW_CHIP_CLASS}>
                      {skill.step_timeout_seconds ? `${skill.step_timeout_seconds} 秒` : '未单独限制'}
                    </span>
                  </FlowMetaRow>
                  <FlowMetaRow label="必填信息">
                    <PlainChipList values={skill.required_info} />
                  </FlowMetaRow>
                  <FlowMetaRow label="触发意图">
                    <PlainChipList values={skill.trigger_intents} />
                  </FlowMetaRow>
                </div>
              </SelectableTarget>
            </div>
            {graphLayout.nodes.map((item) => (
              <div
                className={FLOW_NODE_POSITION_CLASS}
                data-flow-node-id={item.nodeId}
                key={item.nodeId}
                style={{ left: item.x, top: item.y, width: item.width, height: item.height }}
                onPointerDown={(event) => startNodeDrag(event, item)}
                onClickCapture={(event) => {
                  if (suppressNodeClickRef.current !== item.nodeId) return;
                  suppressNodeClickRef.current = '';
                  event.preventDefault();
                  event.stopPropagation();
                }}
              >
                <SkillFlowNodeCard
                  index={item.index}
                  step={item.step}
                  terminal={terminalSet.has(item.nodeId)}
                  outgoingEdges={edgeMap[item.nodeId] || []}
                  selectedPaths={selectedPaths}
                  highlightedPaths={highlightedPaths}
                  updatingPaths={updatingPaths}
                  dirtyPaths={dirtyPaths}
                  textDiffs={textDiffs}
                  toolDescriptions={toolDescriptions}
                  toolStatuses={toolStatuses}
                  sopSkills={sopSkills}
                  selected={selectedNodeId === item.nodeId}
                  connecting={connectionDrag?.sourceNodeId === item.nodeId || armedConnectionSourceId === item.nodeId}
                  dropTarget={connectionDrag?.targetNodeId === item.nodeId}
                  connectHandleScale={Math.min(2.4, 1 / flowZoom)}
                  showConnectHandle={isFullscreen}
                  onConnectClick={armConnectionClick}
                  onConnectStart={startConnectionDrag}
                  onSelect={() => {
                    if (armedConnectionSourceId) {
                      const sourceNodeId = armedConnectionSourceId;
                      setArmedConnectionSourceId('');
                      connectFlowNodes(sourceNodeId, item.nodeId);
                    }
                    setBasicInspectorOpen(false);
                    setSelectedNodeIndex(item.index);
                  }}
                  onToggle={onToggle}
                />
              </div>
            ))}
          </div>
        </div>
      </div>
      {isFullscreen && inspectorOpen && (
        basicInspectorOpen ? (
          <SkillFlowBasicInspector
            skill={skill}
            lockSkillId={lockSkillId}
            onEditBasic={editFlowBasic}
          />
        ) : (
          <SkillFlowInspector
            node={selectedNode}
            nodeIndex={selectedNodeIndex}
            nodeId={selectedNodeId}
            nodes={nodes}
            nodeOptions={nodeOptions}
            outgoingEdges={selectedNodeId ? edgeMap[selectedNodeId] || [] : []}
            terminal={selectedNodeId ? terminalSet.has(selectedNodeId) : false}
            actionOptions={inspectorActionOptions}
            toolDescriptions={toolDescriptions}
            toolStatuses={toolStatuses}
            generalSkillOptions={generalSkillOptions}
            toolOptions={toolOptions}
            knowledgeBaseOptions={knowledgeBaseOptions}
            sopOptions={sopOptions}
            tenantUserOptions={tenantUserOptions}
            onEditNode={editFlowNode}
            onAddEdge={addFlowEdge}
            onUpdateEdge={updateFlowEdge}
            onDeleteEdge={deleteFlowEdge}
          />
        )
      )}
      </div>
      </div>
      {connectionDrag && (
        <svg className={FLOW_CONNECTION_PREVIEW_CLASS} aria-hidden="true">
          <path
            d={connectionPreviewPath(connectionDrag)}
            fill="none"
            stroke="#04756f"
            strokeWidth="2.25"
            strokeLinecap="round"
          />
          <circle
            cx={connectionDrag.currentX}
            cy={connectionDrag.currentY}
            r={connectionDrag.targetNodeId ? 5 : 3.5}
            fill={connectionDrag.targetNodeId ? "white" : "#04756f"}
            stroke="#04756f"
            strokeWidth="2"
          />
        </svg>
      )}
    </div>
  );
}

/** 结构编辑面板中的分组区块：标题、说明与字段列表。 */
function FlowInspectorSection({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <section className="grid min-w-0 gap-[12px] rounded-[16px] border border-[#e4e8ee] bg-white p-[14px] shadow-[0_6px_18px_rgba(24,31,45,0.035)]">
      <div className="grid min-w-0 gap-[2px]">
        <strong className="text-[13px] font-semibold text-[#18181a]">{title}</strong>
        {description && <span className="text-[11px] leading-[1.5] text-[#858b9c]">{description}</span>}
      </div>
      <div className={SOURCE_META_LIST_CLASS}>{children}</div>
    </section>
  );
}

/** 流程图全屏时的基础信息编辑面板，与源码视图共用同一份草稿。 */
function SkillFlowBasicInspector({
  skill,
  lockSkillId,
  onEditBasic,
}: {
  skill: SkillCard;
  lockSkillId?: boolean;
  onEditBasic: (
    field: keyof SkillCard,
    value: string | string[] | number | null,
  ) => void;
}) {
  return (
    <aside className={FLOW_INSPECTOR_CLASS} aria-label="编辑 SOP 基础信息">
      <div className={FLOW_INSPECTOR_HEADER_CLASS}>
        <div className="grid min-w-0 gap-[3px]">
          <strong className="truncate text-[14px] font-semibold text-[#18181a]">基础信息 · {skill.name || '未命名 SOP'}</strong>
          <span className="text-[11px] leading-[1.45] text-[#858b9c]">这里与源码视图的基础信息使用同一份草稿，修改会实时同步。</span>
        </div>
        <span className="shrink-0 rounded-full bg-[#edf8f5] px-[8px] py-[4px] text-[11px] text-[#04756f]">实时同步</span>
      </div>
      <div className={FLOW_INSPECTOR_BODY_CLASS}>
        <div className="grid min-w-0 gap-[12px]">
          <FlowInspectorSection title="身份与版本" description="定义 SOP 的稳定标识、展示名称和所属业务域。">
            <EditableSourceTextLine label="SOP 名称" value={skill.name || ''} onChange={(value) => onEditBasic('name', value)} />
            <EditableSourceTextLine label={fieldLabel('skill_id')} value={skill.skill_id || ''} readOnly={lockSkillId} onChange={(value) => onEditBasic('skill_id', value)} />
            <EditableSourceTextLine label={fieldLabel('version')} value={skill.version || ''} onChange={(value) => onEditBasic('version', value)} />
            <EditableSourceTextLine label={fieldLabel('business_domain')} value={skill.business_domain || ''} onChange={(value) => onEditBasic('business_domain', value)} />
            <EditableSourceNumberLine
              label={fieldLabel('step_timeout_seconds')}
              value={skill.step_timeout_seconds}
              min={1}
              max={3600}
              placeholder="不单独限制"
              onChange={(value) => onEditBasic('step_timeout_seconds', value)}
            />
            <CapabilityScopeControl
              compact
              resourceType="sop"
              value={normalizeCapabilityScope(skill.capability_scope)}
              onChange={(value) => onEditBasic('capability_scope', value)}
            />
          </FlowInspectorSection>
          <FlowInspectorSection title="触发与目标" description="说明何时进入流程，以及模型需要完成什么。">
            <EditableSourceTextLine label={fieldLabel('description')} value={skill.description || ''} multiline onChange={(value) => onEditBasic('description', value)} />
            <EditableSourceListLine label={fieldLabel('trigger_intents')} values={skill.trigger_intents} onChange={(value) => onEditBasic('trigger_intents', value)} />
            <EditableSourceListLine label={fieldLabel('user_utterance_examples')} values={skill.user_utterance_examples} onChange={(value) => onEditBasic('user_utterance_examples', value)} />
            <EditableSourceListLine label={fieldLabel('goal')} values={skill.goal} onChange={(value) => onEditBasic('goal', value)} />
          </FlowInspectorSection>
          <FlowInspectorSection title="输入与回复约束" description="列出完成流程所需的信息和最终回复规则。">
            <EditableSourceListLine label={fieldLabel('required_info')} values={skill.required_info} onChange={(value) => onEditBasic('required_info', value)} />
            <EditableSourceListLine label={fieldLabel('response_rules')} values={skill.response_rules} minRows={5} maxRows={14} onChange={(value) => onEditBasic('response_rules', value)} />
          </FlowInspectorSection>
        </div>
      </div>
    </aside>
  );
}

/**
 * 流程图全屏时的节点编辑面板：节点定义、能力引用与流转规则。
 *
 * - 未选中节点时展示引导占位，不渲染编辑表单。
 * - 编辑结果通过 onEditNode/onAddEdge 等回调回传父组件。
 */
function SkillFlowInspector({
  node,
  nodeIndex,
  nodeId,
  nodes,
  nodeOptions,
  outgoingEdges,
  terminal,
  actionOptions,
  toolDescriptions,
  toolStatuses,
  generalSkillOptions,
  toolOptions,
  knowledgeBaseOptions,
  sopOptions,
  tenantUserOptions,
  onEditNode,
  onAddEdge,
  onUpdateEdge,
  onDeleteEdge,
}: {
  node: Record<string, unknown> | null;
  nodeIndex: number | null;
  nodeId: string;
  nodes: Array<Record<string, unknown>>;
  nodeOptions: SelectOption[];
  outgoingEdges: Array<Record<string, unknown>>;
  terminal: boolean;
  actionOptions: SelectOption[];
  toolDescriptions: ToolDescriptionMap;
  toolStatuses: ToolStatusMap;
  generalSkillOptions: CapabilityReferenceOption[];
  toolOptions: CapabilityReferenceOption[];
  knowledgeBaseOptions: CapabilityReferenceOption[];
  sopOptions: SelectOption[];
  tenantUserOptions: SelectOption[];
  onEditNode: (index: number, field: string, value: string | string[] | boolean | Record<string, unknown>) => void;
  onAddEdge: (index: number) => void;
  onUpdateEdge: (index: number, edgeIndex: number, patch: Record<string, unknown>) => void;
  onDeleteEdge: (index: number, edgeIndex: number) => void;
}) {
  if (!node || nodeIndex === null) {
    return (
      <aside className={FLOW_INSPECTOR_CLASS} aria-label="节点编辑器">
        <div className={FLOW_INSPECTOR_EMPTY_CLASS}>
          选择一个节点后，可在这里编辑对应的源码字段和流转规则。
        </div>
      </aside>
    );
  }
  const nodeState = [
    Boolean(node.optional) ? '可选' : '必选',
    terminal ? '终止节点' : '流程节点',
  ].join(' · ');
  const isSubflow = String(node.type || '') === 'subflow';
  // 处理人仅对 handoff 节点有意义;转人工动作只允许出现在 handoff 节点上,
  // 回复/收集等其他节点一律不展示处理人与转人工动作。
  const isHandoffNode = String(node.type || '') === 'handoff';
  return (
    <aside className={FLOW_INSPECTOR_CLASS} aria-label={`编辑节点 ${String(node.name || nodeId)}`}>
      <div className={FLOW_INSPECTOR_HEADER_CLASS}>
        <div className="grid min-w-0 gap-[3px]">
          <strong className="truncate text-[14px] font-semibold text-[#18181a]">Node {nodeIndex + 1} · {String(node.name || nodeId)}</strong>
          <span className="text-[11px] leading-[1.45] text-[#858b9c]">拖动左侧画布浏览；从节点右侧连接点拖到目标节点，可新增流转。</span>
        </div>
        <span className="shrink-0 rounded-full bg-[#edf8f5] px-[8px] py-[4px] text-[11px] text-[#04756f]">实时同步</span>
      </div>
      <div className={FLOW_INSPECTOR_BODY_CLASS}>
        <div className="grid min-w-0 gap-[12px]">
          <FlowInspectorSection title="节点定义" description="节点名称、类型和执行目标会直接进入 TaskFrame。">
            <EditableSourceTextLine label="节点名称" value={String(node.name || '')} onChange={(value) => onEditNode(nodeIndex, 'name', value)} />
            <EditableSourceTextLine label={fieldLabel('step_id')} value={nodeId} onChange={(value) => onEditNode(nodeIndex, 'step_id', value)} />
            <EditableSourceSelectLine label={fieldLabel('type')} value={String(node.type || 'collect_info')} options={NODE_TYPE_OPTIONS} onChange={(value) => onEditNode(nodeIndex, 'type', value)} />
            {String(node.type || '') === 'subflow' && (
              <EditableSourceSelectLine
                label="调用子 SOP"
                value={String(node.sub_sop_id || '')}
                options={sopOptions}
                onChange={(value) => onEditNode(nodeIndex, 'sub_sop_id', value)}
              />
            )}
            <SourceReadonlyLine label="节点状态" value={nodeState} />
            {isSubflow ? (
              <SourceReadonlyLine label="执行职责" value="仅进入所选子 SOP；字段、动作和能力由子 SOP 自己定义。" />
            ) : (
              <EditableSourceTextLine label={fieldLabel('instruction')} value={String(node.instruction || '')} multiline onChange={(value) => onEditNode(nodeIndex, 'instruction', value)} />
            )}
            {isHandoffNode && (
              <EditableSourceSelectLine
                label="处理人"
                value={
                  node.assignee_user_id
                    ? formatHandoffAssigneeValue(
                      String(node.assignee_user_id),
                      String(node.assignee_notify_channel || ''),
                    )
                    : UNASSIGNED_USER_VALUE
                }
                options={tenantUserOptions}
                onChange={(value) => onEditNode(
                  nodeIndex,
                  'assignee_user_id',
                  value === UNASSIGNED_USER_VALUE ? '' : value,
                )}
              />
            )}
          </FlowInspectorSection>
          {!isSubflow && (
            <>
              <FlowInspectorSection title="输入与允许动作" description="明确本节点需要收集的字段，以及模型可以自主选择的动作。">
                <EditableSourceListLine label={fieldLabel('expected_user_info')} values={asStringList(node.expected_user_info)} onChange={(value) => onEditNode(nodeIndex, 'expected_user_info', value)} />
                <EditableSourceActionLine values={asStringList(node.allowed_actions)} options={actionOptions} toolDescriptions={toolDescriptions} toolStatuses={toolStatuses} onChange={(value) => onEditNode(nodeIndex, 'allowed_actions', value)} />
              </FlowInspectorSection>
              <FlowInspectorSection title="节点专用能力" description="SOP-specific 能力只有在这里明确引用后，才会进入当前节点的 Harness 能力清单。">
                <EditableCapabilityReferencesLine label="SOP 技能" values={asStringList(node.general_skill_ids)} requiredValues={asStringList(node.required_general_skill_ids)} options={generalSkillOptions} emptyText="未指定技能" onChange={(value) => onEditNode(nodeIndex, 'general_skill_ids', value)} onRequiredChange={(value) => onEditNode(nodeIndex, 'required_general_skill_ids', value)} />
                <EditableCapabilityReferencesLine label="SOP 工具" values={asStringList(node.tool_ids)} requiredValues={asStringList(node.required_tool_ids)} options={toolOptions} emptyText="未指定工具" onChange={(value) => onEditNode(nodeIndex, 'tool_ids', value)} onRequiredChange={(value) => onEditNode(nodeIndex, 'required_tool_ids', value)} />
                <EditableCapabilityReferencesLine label="SOP 知识库" values={asStringList(node.knowledge_base_ids)} requiredValues={asStringList(node.required_knowledge_base_ids)} options={knowledgeBaseOptions} emptyText="未指定知识库" onChange={(value) => onEditNode(nodeIndex, 'knowledge_base_ids', value)} onRequiredChange={(value) => onEditNode(nodeIndex, 'required_knowledge_base_ids', value)} />
              </FlowInspectorSection>
            </>
          )}
          <FlowInspectorSection title="流转与失败处理" description="按优先级判断规则；未命中时使用重试策略或终止流程。">
            <EditableFlowRulesLine sourceNodeId={nodeId} edges={outgoingEdges} nodes={nodes} nodeOptions={nodeOptions} terminal={terminal} onAdd={() => onAddEdge(nodeIndex)} onUpdate={(edgeIndex, patch) => onUpdateEdge(nodeIndex, edgeIndex, patch)} onDelete={(edgeIndex) => onDeleteEdge(nodeIndex, edgeIndex)} />
            {!isSubflow && <SourceJsonLine label="知识范围" value={node.knowledge_scope} />}
            {!isSubflow && <EditableRetryPolicyLine value={node.retry_policy} onChange={(value) => onEditNode(nodeIndex, 'retry_policy', value)} />}
          </FlowInspectorSection>
        </div>
      </div>
    </aside>
  );
}

/**
 * 流程图节点卡片：展示节点摘要、能力标签与连线入口。
 *
 * - connecting/dropTarget 控制连线拖拽过程中的高亮反馈。
 * - 点击卡片会同时选中该节点并切换右侧改写目标。
 */
function SkillFlowNodeCard({
  index,
  step,
  terminal,
  outgoingEdges,
  selectedPaths,
  highlightedPaths,
  updatingPaths,
  dirtyPaths,
  textDiffs,
  toolDescriptions,
  toolStatuses,
  sopSkills,
  selected,
  connecting,
  dropTarget,
  connectHandleScale,
  showConnectHandle,
  onConnectClick,
  onConnectStart,
  onSelect,
  onToggle,
}: {
  index: number;
  step: Record<string, unknown>;
  terminal: boolean;
  outgoingEdges: Array<Record<string, unknown>>;
  selectedPaths: string[];
  highlightedPaths: string[];
  updatingPaths: string[];
  dirtyPaths: string[];
  textDiffs: TextDiffAnimation[];
  toolDescriptions: ToolDescriptionMap;
  toolStatuses: ToolStatusMap;
  sopSkills: SkillRead[];
  selected: boolean;
  connecting: boolean;
  dropTarget: boolean;
  connectHandleScale: number;
  showConnectHandle: boolean;
  onConnectClick: (event: MouseEvent<HTMLButtonElement>, sourceNodeId: string) => void;
  onConnectStart: (event: ReactPointerEvent<HTMLButtonElement>, sourceNodeId: string) => void;
  onSelect: () => void;
  onToggle: (target: TargetSelection) => void;
}) {
  const nodeId = String(step.node_id || step.step_id || `node_${index + 1}`);
  const path = stepTargetPath(index);
  const expectedInfo = asStringList(step.expected_user_info);
  const actionList = asStringList(step.allowed_actions);
  const instruction = String(step.instruction || '暂无说明');
  const isSubflow = String(step.type || '') === 'subflow';
  const childSop = isSubflow
    ? sopSkills.find((item) => item.skill_id === String(step.sub_sop_id || ''))
    : undefined;
  return (
    <div className={FLOW_NODE_SHELL_CLASS}>
      <SelectableTarget
        className={cn(
          distillFlowNodeClass(path, false, selectedPaths, highlightedPaths, updatingPaths, dirtyPaths),
          'cursor-grab! active:cursor-grabbing!',
          selected && 'border-[#04756f]! shadow-[0_0_0_3px_rgba(4,117,111,0.10),0_12px_30px_rgba(4,117,111,0.08)]',
          dropTarget && 'border-[#04756f]! shadow-[0_0_0_4px_rgba(4,117,111,0.12),0_12px_30px_rgba(4,117,111,0.10)]',
        )}
        target={{ path, label: `节点 ${index + 1}：${step.name || nodeId}` }}
        onToggle={(target) => {
          onSelect();
          onToggle(target);
        }}
      >
        {selectedPaths.includes(path) && <span className={SELECTION_MARK_CLASS}><CheckOutlined /></span>}
        <span>节点 {index + 1}</span>
        <strong><InlineDiffText path={path} field="name" value={String(step.name || nodeId)} diffs={textDiffs} /></strong>
        <small>{nodeId}</small>
        <div className={FLOW_NODE_BADGES_CLASS}>
          <span className={FLOW_CHIP_CLASS}>{nodeTypeLabel(String(step.type || 'collect_info'))}</span>
          {Boolean(step.optional) && <span className={FLOW_CHIP_CLASS}>可选</span>}
          {terminal && <span className={FLOW_CHIP_CLASS}>终止</span>}
        </div>
        {isSubflow ? (
          <NestedSopPreview childSop={childSop} subSopId={String(step.sub_sop_id || '')} />
        ) : (
          <p className={FLOW_NODE_SUMMARY_CLASS} title={instruction}>
            <InlineDiffText path={path} field="instruction" value={instruction} diffs={textDiffs} />
          </p>
        )}
        <div className={FLOW_COMPACT_META_CLASS}>
          {!isSubflow && expectedInfo.length > 0 && (
            <div className={FLOW_COMPACT_ROW_CLASS}>
              <span>字段</span>
              <PlainChipList values={expectedInfo} />
            </div>
          )}
          {!isSubflow && actionList.length > 0 && (
            <div className={FLOW_COMPACT_ROW_CLASS}>
              <span>动作</span>
              <FlowActionList actions={actionList} toolDescriptions={toolDescriptions} toolStatuses={toolStatuses} />
            </div>
          )}
          {outgoingEdges.length > 0 && <span className={FLOW_ROUTE_COUNT_CLASS}>{outgoingRouteCountLabel(outgoingEdges)}</span>}
        </div>
      </SelectableTarget>
      {showConnectHandle && (
        <button
          type="button"
          data-flow-connect-handle
          className={cn(FLOW_NODE_CONNECT_HANDLE_CLASS, connecting && FLOW_NODE_CONNECT_HANDLE_ACTIVE_CLASS)}
          style={{ transform: `translateX(-50%) scale(${connectHandleScale})` }}
          aria-label={`从「${String(step.name || nodeId)}」拖线连接节点`}
          title="拖到另一个节点以新增流转规则"
          onPointerDown={(event) => onConnectStart(event, nodeId)}
          onClick={(event) => onConnectClick(event, nodeId)}
        >
          +
        </button>
      )}
    </div>
  );
}

/** 子 SOP 节点的流程预览：展示前几个节点名与共享字段提示。 */
function NestedSopPreview({ childSop, subSopId }: { childSop?: SkillRead; subSopId: string }) {
  const childNodes = childSop ? skillGraphSteps(childSop.content) : [];
  const visibleNodes = childNodes.slice(0, 4);
  return (
    <div className="grid gap-[7px] rounded-[10px] border border-[#cfe5df] bg-[#f3faf7] p-[9px] text-[#315e59]">
      <div className="flex min-w-0 items-center justify-between gap-[8px]">
        <strong className="truncate text-[12px] font-semibold">{childSop?.name || '未找到子 SOP'}</strong>
        <span className="shrink-0 rounded-full bg-white px-[7px] py-[2px] text-[10px] text-[#04756f]">共享字段</span>
      </div>
      <span className="truncate font-mono text-[10px] text-[#66817d]">{subSopId || '尚未选择 SOP'}</span>
      {visibleNodes.length > 0 ? (
        <div className="flex min-w-0 items-center gap-[4px] overflow-hidden" aria-label="子 SOP 节点预览">
          {visibleNodes.map((node, index) => (
            <div className="contents" key={String(node.node_id || index)}>
              {index > 0 && <span className="shrink-0 text-[#8aa39f]">→</span>}
              <span className="min-w-0 truncate rounded-md border border-[#d8e9e4] bg-white px-[6px] py-[3px] text-[10px]">
                {String(node.name || node.node_id || `节点 ${index + 1}`)}
              </span>
            </div>
          ))}
          {childNodes.length > visibleNodes.length && (
            <span className="shrink-0 text-[10px] text-[#66817d]">+{childNodes.length - visibleNodes.length}</span>
          )}
        </div>
      ) : (
        <span className="text-[10px] text-[#8a9694]">发布子 SOP 后将在这里显示其流程节点</span>
      )}
    </div>
  );
}

/** 流程图元信息行：左侧标签加右侧内容。 */
function FlowMetaRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className={FLOW_META_ROW_CLASS}>
      <span className={FLOW_META_LABEL_CLASS}>{label}</span>
      {children}
    </div>
  );
}

/** 纯文本标签列表，空值时显示占位短横线。 */
function PlainChipList({ values }: { values: unknown }) {
  const items = asStringList(values);
  if (items.length === 0) return <span className={cn(FLOW_CHIP_CLASS, FLOW_CHIP_MUTED_CLASS)}>-</span>;
  return (
    <div className={FLOW_CHIP_LIST_CLASS}>
      {items.map((item, index) => (
        <span className={FLOW_CHIP_CLASS} key={`${item}_${index}`}>
          {item}
        </span>
      ))}
    </div>
  );
}

/** 动作标签列表，按动作逐个渲染带工具提示的 chip。 */
function FlowActionList({
  actions,
  toolDescriptions,
  toolStatuses,
}: {
  actions: string[];
  toolDescriptions: ToolDescriptionMap;
  toolStatuses: ToolStatusMap;
}) {
  return (
    <div className="flex min-w-0 flex-wrap gap-[6px]">
      {actions.map((action, index) => (
        <ActionChip action={action} toolDescriptions={toolDescriptions} toolStatuses={toolStatuses} key={`${action}_${index}`} />
      ))}
    </div>
  );
}

/** 把草稿节点归一化为流程图使用的步骤结构，并展开兼容旧字段的能力引用。 */
function skillGraphSteps(skill: SkillCard): Array<Record<string, unknown>> {
  if (Array.isArray(skill.nodes) && skill.nodes.length > 0) {
    return skill.nodes.map((node, index) => {
      const capabilityRefs = nodeCapabilityRefs(node);
      return {
        step_id: node.node_id || `node_${index + 1}`,
        node_id: node.node_id || `node_${index + 1}`,
        type: node.type || 'collect_info',
        name: node.name || node.node_id || `节点 ${index + 1}`,
        instruction: node.instruction || '',
        optional: Boolean(node.optional),
        condition: node.condition || '',
        expected_user_info: asStringList(node.expected_user_info),
        allowed_actions: asStringList(node.allowed_actions),
        general_skill_ids: capabilityRefs.general_skill_ids,
        tool_ids: capabilityRefs.tool_ids,
        knowledge_base_ids: capabilityRefs.knowledge_base_ids,
        required_general_skill_ids: capabilityRefs.required_general_skill_ids,
        required_tool_ids: capabilityRefs.required_tool_ids,
        required_knowledge_base_ids: capabilityRefs.required_knowledge_base_ids,
        knowledge_scope: isRecord(node.knowledge_scope) ? node.knowledge_scope : {},
        retry_policy: isRecord(node.retry_policy) ? node.retry_policy : {},
        metadata: isRecord(node.metadata) ? node.metadata : {},
        sub_sop_id: stringValue(node.sub_sop_id, ''),
        assignee_user_id: stringValue(node.assignee_user_id, ''),
        assignee_notify_channel: stringValue(node.assignee_notify_channel, ''),
      };
    });
  }
  return [];
}

/** 读取节点能力引用：兼容 capability_refs 与旧的扁平字段，并过滤失效的必选引用。 */
function nodeCapabilityRefs(node: Record<string, unknown>) {
  const refs = isRecord(node.capability_refs) ? node.capability_refs : {};
  const generalSkillIds = asStringList(refs.general_skill_ids ?? node.general_skill_ids);
  const toolIds = asStringList(refs.tool_ids ?? node.tool_ids);
  const knowledgeBaseIds = asStringList(refs.knowledge_base_ids ?? node.knowledge_base_ids);
  return {
    general_skill_ids: generalSkillIds,
    tool_ids: toolIds,
    knowledge_base_ids: knowledgeBaseIds,
    required_general_skill_ids: asStringList(
      refs.required_general_skill_ids ?? node.required_general_skill_ids,
    ).filter((value) => generalSkillIds.includes(value)),
    required_tool_ids: asStringList(
      refs.required_tool_ids ?? node.required_tool_ids,
    ).filter((value) => toolIds.includes(value)),
    required_knowledge_base_ids: asStringList(
      refs.required_knowledge_base_ids ?? node.required_knowledge_base_ids,
    ).filter((value) => knowledgeBaseIds.includes(value)),
  };
}

/** 更新某一类能力引用并自动去重，同时剔除已不在允许列表中的必选引用。 */
function updateCapabilityRefs(
  refs: ReturnType<typeof nodeCapabilityRefs>,
  field: string,
  values: string[],
): ReturnType<typeof nodeCapabilityRefs> {
  const next = { ...refs, [field]: Array.from(new Set(values)) };
  const requiredField = REQUIRED_CAPABILITY_FIELD_BY_ALLOWED[field];
  if (requiredField) {
    next[requiredField as keyof typeof next] = asStringList(
      next[requiredField as keyof typeof next],
    ).filter((value) => values.includes(value));
  }
  return next;
}

/** 按源节点 ID 归集流转边，便于流程图与源码视图按节点渲染出边。 */
function skillGraphEdgeMap(skill: SkillCard): Record<string, Array<Record<string, unknown>>> {
  const map: Record<string, Array<Record<string, unknown>>> = {};
  (Array.isArray(skill.edges) ? skill.edges : []).forEach((edge) => {
    const source = String(edge.source_node_id || '');
    if (!source) return;
    if (!map[source]) map[source] = [];
    map[source].push(edge);
  });
  return map;
}

/** 归一化节点数组：过滤非对象项并补齐 node_id。 */
function normalizeSkillNodes(skill: SkillCard): Array<Record<string, unknown>> {
  return Array.isArray(skill.nodes) ? skill.nodes.filter(isRecord).map((node, index) => ({
    ...node,
    node_id: String(node.node_id || node.step_id || `node_${index + 1}`),
  })) : [];
}

/** 归一化流转边数组：过滤非对象项并补齐缺省字段与优先级。 */
function normalizeSkillEdges(skill: SkillCard): Array<Record<string, unknown>> {
  return Array.isArray(skill.edges) ? skill.edges.filter(isRecord).map((edge, index) => ({
    source_node_id: String(edge.source_node_id || ''),
    next_node_id: String(edge.next_node_id || ''),
    condition: typeof edge.condition === 'string' ? edge.condition : '',
    priority: Number.isFinite(Number(edge.priority)) ? Number(edge.priority) : index,
    label: typeof edge.label === 'string' ? edge.label : '',
  })) : [];
}

/** 取指定下标节点的节点 ID，缺失时回退为 node_N。 */
function nodeIdAt(skill: SkillCard, index: number): string {
  const node = normalizeSkillNodes(skill)[index];
  return String(node?.node_id || node?.step_id || `node_${index + 1}`);
}

/** 把某节点内的局部出边下标记为全局边下标，找不到时返回 -1。 */
function findSourceEdgeIndex(edges: Array<Record<string, unknown>>, sourceId: string, localIndex: number): number {
  let seen = -1;
  return edges.findIndex((edge) => {
    if (String(edge.source_node_id || '') !== sourceId) return false;
    seen += 1;
    return seen === localIndex;
  });
}

/** 读取流转边优先级，非法值回退到给定默认值。 */
function edgePriority(edge: Record<string, unknown>, fallback = 0): number {
  const value = Number(edge.priority);
  return Number.isFinite(value) ? value : fallback;
}

/** 在现有节点集合内生成不重复的节点 ID，冲突时追加数字后缀。 */
function uniqueNodeId(nodes: Array<Record<string, unknown>>, preferred: string): string {
  const used = new Set(nodes.map((node) => String(node.node_id || node.step_id || '')).filter(Boolean));
  const base = preferred.replace(/\s+/g, '_').replace(/[^\w.-]/g, '') || 'node';
  if (!used.has(base)) return base;
  for (let suffix = 2; suffix < 1000; suffix += 1) {
    const candidate = `${base}_${suffix}`;
    if (!used.has(candidate)) return candidate;
  }
  return `${base}_${Date.now()}`;
}

/** 按节点 ID 生成「Node N · 名称」的展示名，缺失时回退为原始 ID。 */
function nodeDisplayNameById(nodes: Array<Record<string, unknown>>, nodeId: string): string {
  const index = nodes.findIndex((node) => String(node.node_id || node.step_id || '') === nodeId);
  if (index < 0) return nodeId || '未指定节点';
  const node = nodes[index];
  return `Node ${index + 1} · ${String(node.name || nodeId)}`;
}

type SkillFlowCanvasNode = {
  nodeId: string;
  step: Record<string, unknown>;
  index: number;
  rank: number;
  x: number;
  y: number;
  width: number;
  height: number;
};

type SkillFlowCanvasEdge = {
  id: string;
  kind: 'root' | 'edge';
  edgeIndex?: number;
  labelTone?: 'root' | 'branch' | 'parallel' | 'return';
  label: string;
  title: string;
  path: string;
  labelX: number;
  labelY: number;
};

/** 估算文本按给定每行字符数换行后占用的行数，用于节点高度预估。 */
function estimatedWrappedLines(value: unknown, charactersPerLine: number): number {
  const text = String(value || '').trim();
  if (!text) return 1;
  return text.split('\n').reduce((total, line) => (
    total + Math.max(1, Math.ceil(Array.from(line).length / charactersPerLine))
  ), 0);
}

/** 按说明、字段与动作数量估算流程图节点卡片高度，取所有节点的最大值。 */
function estimateSkillFlowNodeHeight(nodes: Array<Record<string, unknown>>): number {
  return Math.max(324, ...nodes.map((node) => {
    const instructionHeight = estimatedWrappedLines(node.instruction || '暂无说明', 24) * 21;
    const fieldRows = Math.ceil(asStringList(node.expected_user_info).length / 3) * 28;
    const actionRows = Math.ceil(asStringList(node.allowed_actions).length / 2) * 28;
    return 182 + instructionHeight + fieldRows + actionRows + 48;
  }));
}

/** 估算流程图根节点（基础信息）卡片高度。 */
function estimateSkillFlowRootHeight(skill: SkillCard): number {
  const descriptionHeight = estimatedWrappedLines(skill.description || '暂无描述', 38) * 21;
  const infoRows = Math.ceil(asStringList(skill.required_info).length / 4) * 28;
  const intentRows = Math.ceil(asStringList(skill.trigger_intents).length / 4) * 28;
  return Math.max(270, 154 + descriptionHeight + infoRows + intentRows + 72);
}

/**
 * 计算流程图完整画布布局：节点坐标、画布尺寸、连线路径与标签位置。
 *
 * - 节点坐标优先取手动覆盖值，其次取草稿内坐标，最后按层级自动排布。
 * - 回流边与遮挡边改走侧向路径，标签位置会做避让处理。
 */
function buildSkillFlowCanvasLayout(
  skill: SkillCard,
  nodes: Array<Record<string, unknown>>,
  nodeNameMap: Record<string, string>,
  positionOverrides: Record<string, SkillFlowPosition> = {},
) {
  const layerLayout = buildSkillFlowLayout(skill, nodes);
  const cardWidth = 360;
  const cardHeight = estimateSkillFlowNodeHeight(nodes);
  const rootWidth = 500;
  const rootHeight = estimateSkillFlowRootHeight(skill);
  const columnGap = 188;
  const rowGap = 236;
  const rootGap = 126;
  const paddingX = 144;
  const paddingY = 66;
  const layerWidths = layerLayout.layers.map((layer) => (
    layer.length * cardWidth + Math.max(0, layer.length - 1) * columnGap
  ));
  const maxContentWidth = Math.max(rootWidth, ...layerWidths, 0);
  const baseWidth = Math.max(1180, paddingX * 2 + maxContentWidth);
  const root = {
    x: (baseWidth - rootWidth) / 2,
    y: paddingY,
    width: rootWidth,
    height: rootHeight,
  };
  const positionedNodes: SkillFlowCanvasNode[] = [];
  const positionMap = new Map<string, SkillFlowCanvasNode>();

  layerLayout.layers.forEach((layer, layerIndex) => {
    const layerWidth = layer.length * cardWidth + Math.max(0, layer.length - 1) * columnGap;
    const layerStartX = Math.max(paddingX, (baseWidth - layerWidth) / 2);
    layer.forEach((item, itemIndex) => {
      const positioned = {
        ...item,
        rank: layerIndex,
        x: positionOverrides[item.nodeId]?.x
          ?? skillNodeFlowPosition(item.step)?.x
          ?? layerStartX + itemIndex * (cardWidth + columnGap),
        y: positionOverrides[item.nodeId]?.y
          ?? skillNodeFlowPosition(item.step)?.y
          ?? paddingY + rootHeight + rootGap + layerIndex * (cardHeight + rowGap),
        width: cardWidth,
        height: cardHeight,
      };
      positionedNodes.push(positioned);
      positionMap.set(item.nodeId, positioned);
    });
  });

  const width = Math.max(
    baseWidth,
    ...positionedNodes.map((node) => node.x + node.width + paddingX),
  );

  const rawEdges = Array.isArray(skill.edges) ? skill.edges : [];
  const edgeSiblingCounts = rawEdges.reduce<Record<string, number>>((acc, edge) => {
    const sourceId = String(edge.source_node_id || '');
    if (sourceId) acc[sourceId] = (acc[sourceId] || 0) + 1;
    return acc;
  }, {});
  const sourceEdgeLabelCounts = rawEdges.reduce<Record<string, Record<string, number>>>((acc, edge) => {
    const sourceId = String(edge.source_node_id || '').trim();
    if (!sourceId) return acc;
    const label = normalizedEdgeLabel(edge, nodeNameMap);
    if (!acc[sourceId]) acc[sourceId] = {};
    acc[sourceId][label] = (acc[sourceId][label] || 0) + 1;
    return acc;
  }, {});
  const incomingCounts = rawEdges.reduce<Record<string, number>>((acc, edge) => {
    const targetId = String(edge.next_node_id || '');
    if (targetId) acc[targetId] = (acc[targetId] || 0) + 1;
    return acc;
  }, {});
  const edgeSiblingIndexes: Record<string, number> = {};
  const incomingIndexes: Record<string, number> = {};
  const layoutEdges: SkillFlowCanvasEdge[] = [];
  const baseHeight = paddingY * 2 + rootHeight + rootGap + layerLayout.layers.length * cardHeight + Math.max(0, layerLayout.layers.length - 1) * rowGap;
  const height = Math.max(
    baseHeight,
    ...positionedNodes.map((node) => node.y + node.height + paddingY),
  );
  const startNode = positionMap.get(String(skill.start_node_id || positionedNodes[0]?.nodeId || ''));
  if (startNode) {
    const sourceX = root.x + root.width / 2;
    const sourceY = root.y + root.height + 8;
    const targetX = startNode.x + startNode.width / 2;
    const targetY = startNode.y - 8;
    const laneY = edgeLaneY(sourceY, targetY, 0, 1);
    const labelAnchor = avoidFlowLabelOverlap(
      { x: (sourceX + targetX) / 2, y: laneY },
      [...positionedNodes, { ...root, nodeId: '__root__' } as SkillFlowCanvasNode],
      width,
      height,
    );
    layoutEdges.push({
      id: `root_${startNode.nodeId}`,
      kind: 'root',
      labelTone: 'root',
      label: '开始',
      title: `开始 -> ${nodeNameMap[startNode.nodeId] || startNode.nodeId}`,
      path: forwardFlowPath(sourceX, sourceY, targetX, targetY, laneY),
      labelX: labelAnchor.x,
      labelY: labelAnchor.y,
    });
  }
  rawEdges.forEach((edge, index) => {
    const sourceId = String(edge.source_node_id || '');
    const targetId = String(edge.next_node_id || '');
    const source = positionMap.get(sourceId);
    const target = positionMap.get(targetId);
    if (!source || !target) return;
    const siblingCount = edgeSiblingCounts[sourceId] || 1;
    const baseLabel = normalizedEdgeLabel(edge, nodeNameMap);
    const hasDuplicateSourceLabel = (sourceEdgeLabelCounts[sourceId]?.[baseLabel] || 0) > 1;
    const isParallelFlow = hasDuplicateSourceLabel;
    const label = flowEdgeDisplayLabel(edge, nodeNameMap, siblingCount, hasDuplicateSourceLabel);
    const title = incomingEdgeLabel(edge, nodeNameMap);
    const siblingIndex = edgeSiblingIndexes[sourceId] || 0;
    edgeSiblingIndexes[sourceId] = siblingIndex + 1;
    const incomingCount = incomingCounts[targetId] || 1;
    const incomingIndex = incomingIndexes[targetId] || 0;
    incomingIndexes[targetId] = incomingIndex + 1;
    const sourceX = source.x + source.width / 2;
    const sourceY = source.y + source.height + 8;
    const targetX = target.x + target.width / 2;
    const targetY = target.y - 8;
    const isReturn = targetY <= sourceY;
    const laneY = edgeLaneY(sourceY, targetY, siblingIndex, siblingCount);
    const shouldAvoidNodes = !isReturn && forwardRouteHitsNode(source, target, positionedNodes, laneY);
    const path = isReturn
      ? sideReturnFlowPath(source, target, width, siblingIndex)
      : shouldAvoidNodes
        ? sideForwardFlowPath(source, target, positionedNodes, width, siblingIndex, incomingIndex)
        : forwardFlowPath(sourceX, sourceY, targetX, targetY, laneY);
    const labelAnchor = isReturn
      ? returnEdgeLabelPosition(source, target, width, siblingIndex)
      : shouldAvoidNodes
        ? sideForwardEdgeLabelPosition(source, target, positionedNodes, width, siblingIndex, incomingIndex)
        : forwardEdgeLabelPosition(sourceX, targetX, laneY, siblingIndex, siblingCount, incomingIndex, incomingCount);
    const safeLabelAnchor = avoidFlowLabelOverlap(labelAnchor, positionedNodes, width, height);
    layoutEdges.push({
      id: `${sourceId}_${targetId}_${index}`,
      kind: 'edge',
      edgeIndex: index,
      label: compactEdgeLabel(label),
      title,
      path,
      labelX: safeLabelAnchor.x,
      labelY: safeLabelAnchor.y,
      labelTone: isReturn ? 'return' : (isParallelFlow ? 'parallel' : (siblingCount > 1 ? 'branch' : 'root')),
    });
  });

  return { width, height, root, nodes: positionedNodes, edges: layoutEdges };
}

/**
 * 按可达性与流转优先级把节点分层，返回流程图的行（层）结构。
 *
 * - 从起始节点做广度优先遍历判断可达，不可达节点统一放到最后一层。
 */
function buildSkillFlowLayout(skill: SkillCard, nodes: Array<Record<string, unknown>>) {
  const byId = new Map(nodes.map((node, index) => [
    String(node.node_id || node.step_id || `node_${index + 1}`),
    { node, index },
  ]));
  const edgeMap = skillGraphEdgeMap(skill);
  const startId = String(skill.start_node_id || nodes[0]?.node_id || nodes[0]?.step_id || '');
  const start = startId && byId.has(startId)
    ? startId
    : nodes.length > 0
      ? String(nodes[0].node_id || nodes[0].step_id || 'node_1')
      : '';
  const reachable = new Set<string>();
  const queue = start ? [start] : [];
  while (queue.length > 0) {
    const nodeId = queue.shift() || '';
    if (!nodeId || reachable.has(nodeId) || !byId.has(nodeId)) continue;
    reachable.add(nodeId);
    (edgeMap[nodeId] || [])
      .slice()
      .sort((a, b) => {
        const priorityDelta = Number(a.priority || 0) - Number(b.priority || 0);
        if (priorityDelta !== 0) return priorityDelta;
        const aIndex = byId.get(String(a.next_node_id || ''))?.index ?? Number.MAX_SAFE_INTEGER;
        const bIndex = byId.get(String(b.next_node_id || ''))?.index ?? Number.MAX_SAFE_INTEGER;
        return aIndex - bIndex;
      })
      .forEach((edge) => {
        const nextId = String(edge.next_node_id || '');
        if (nextId && byId.has(nextId) && !reachable.has(nextId)) queue.push(nextId);
      });
  }

  const ranks = new Map<string, number>();
  if (start) ranks.set(start, 0);
  for (let pass = 0; pass < nodes.length + 2; pass += 1) {
    let changed = false;
    (Array.isArray(skill.edges) ? skill.edges : []).forEach((edge) => {
      const sourceId = String(edge.source_node_id || '');
      const targetId = String(edge.next_node_id || '');
      if (!reachable.has(sourceId) || !reachable.has(targetId)) return;
      const sourceMeta = byId.get(sourceId);
      const targetMeta = byId.get(targetId);
      if (!sourceMeta || !targetMeta) return;
      if (targetMeta.index <= sourceMeta.index) return;
      const sourceRank = ranks.get(sourceId);
      if (sourceRank === undefined) return;
      const nextRank = sourceRank + 1;
      if ((ranks.get(targetId) ?? -1) < nextRank) {
        ranks.set(targetId, nextRank);
        changed = true;
      }
    });
    if (!changed) break;
  }

  const layerMap = new Map<number, Array<{ nodeId: string; step: Record<string, unknown>; index: number }>>();
  const orderedReachable = nodes
    .map((node, index) => ({
      nodeId: String(node.node_id || node.step_id || `node_${index + 1}`),
      step: node,
      index,
    }))
    .filter((item) => reachable.has(item.nodeId));
  orderedReachable.forEach((item) => {
    const rank = Math.max(0, Math.min(ranks.get(item.nodeId) ?? item.index, item.index));
    if (!layerMap.has(rank)) layerMap.set(rank, []);
    layerMap.get(rank)?.push(item);
  });

  const layers = Array.from(layerMap.entries())
    .sort(([a], [b]) => a - b)
    .map(([, layer]) => layer.sort((a, b) => a.index - b.index));

  const remainder = nodes
    .map((node, index) => ({
      nodeId: String(node.node_id || node.step_id || `node_${index + 1}`),
      step: node,
      index,
    }))
    .filter((item) => !reachable.has(item.nodeId));
  if (remainder.length > 0) layers.push(remainder);
  return { layers };
}

/** 计算同一源节点多条出边所在水平通道的 Y 坐标，避免连线重叠。 */
function edgeLaneY(sourceY: number, targetY: number, siblingIndex: number, siblingCount: number): number {
  const safeTop = sourceY + 58;
  const safeBottom = targetY - 58;
  if (safeBottom <= safeTop) {
    return sourceY + Math.max(72, (targetY - sourceY) * 0.42);
  }
  const laneCount = Math.max(1, siblingCount);
  const maxSpread = Math.min(38, Math.max(24, (safeBottom - safeTop) / Math.max(1, laneCount - 1 || 1)));
  const start = (safeTop + safeBottom) / 2 - ((laneCount - 1) * maxSpread) / 2;
  return Math.max(safeTop, Math.min(safeBottom, start + siblingIndex * maxSpread));
}

/** 生成正向流转连线的 SVG 路径，先竖直下探到通道再水平横移。 */
function forwardFlowPath(sourceX: number, sourceY: number, targetX: number, targetY: number, laneY: number): string {
  const safeLaneY = Math.max(sourceY + 48, Math.min(targetY - 48, laneY));
  const verticalEase = Math.min(44, Math.max(22, (targetY - sourceY) * 0.18));
  const horizontalGap = Math.abs(targetX - sourceX);
  if (horizontalGap < 12) {
    return [
      `M ${sourceX} ${sourceY}`,
      `C ${sourceX} ${sourceY + verticalEase}, ${targetX} ${targetY - verticalEase}, ${targetX} ${targetY}`,
    ].join(' ');
  }
  const bend = Math.max(44, Math.min(120, horizontalGap * 0.28));
  return [
    `M ${sourceX} ${sourceY}`,
    `C ${sourceX} ${sourceY + verticalEase}, ${sourceX} ${safeLaneY - verticalEase}, ${sourceX} ${safeLaneY}`,
    `C ${sourceX + Math.sign(targetX - sourceX) * bend} ${safeLaneY}, ${targetX - Math.sign(targetX - sourceX) * bend} ${safeLaneY}, ${targetX} ${safeLaneY}`,
    `C ${targetX} ${safeLaneY + verticalEase}, ${targetX} ${targetY - verticalEase}, ${targetX} ${targetY}`,
  ].join(' ');
}

/** 生成连线拖拽预览的 SVG 路径，向下延伸或侧向绕出。 */
function connectionPreviewPath(connection: {
  startX: number;
  startY: number;
  currentX: number;
  currentY: number;
}): string {
  const { startX, startY, currentX, currentY } = connection;
  const horizontalDistance = Math.abs(currentX - startX);
  const verticalDistance = currentY - startY;
  if (verticalDistance >= 36) {
    const tension = Math.max(54, Math.min(180, verticalDistance * 0.42 + horizontalDistance * 0.08));
    return `M ${startX} ${startY} C ${startX} ${startY + tension}, ${currentX} ${currentY - tension}, ${currentX} ${currentY}`;
  }
  const direction = currentX >= startX ? 1 : -1;
  const sideX = direction > 0
    ? Math.max(startX, currentX) + Math.max(92, Math.min(150, horizontalDistance * 0.24 + 88))
    : Math.min(startX, currentX) - Math.max(92, Math.min(150, horizontalDistance * 0.24 + 88));
  const exitY = startY + 54;
  const entryY = currentY - 54;
  return [
    `M ${startX} ${startY}`,
    `C ${startX} ${startY + 30}, ${sideX} ${exitY - 20}, ${sideX} ${exitY}`,
    `C ${sideX} ${(exitY + entryY) / 2}, ${sideX} ${(exitY + entryY) / 2}, ${sideX} ${entryY}`,
    `C ${sideX} ${entryY + 20}, ${currentX} ${currentY - 30}, ${currentX} ${currentY}`,
  ].join(' ');
}

/** 判断两个矩形是否相交，边贴边视为相交。 */
function rectsOverlap(
  a: { left: number; right: number; top: number; bottom: number },
  b: { left: number; right: number; top: number; bottom: number },
) {
  return !(a.right < b.left || a.left > b.right || a.bottom < b.top || a.top > b.bottom);
}

/** 判断线段（带外扩边距）是否与流程图节点卡片相交。 */
function segmentHitsFlowNode(
  x1: number,
  y1: number,
  x2: number,
  y2: number,
  node: SkillFlowCanvasNode,
  margin = 18,
) {
  const segmentRect = {
    left: Math.min(x1, x2) - margin,
    right: Math.max(x1, x2) + margin,
    top: Math.min(y1, y2) - margin,
    bottom: Math.max(y1, y2) + margin,
  };
  const nodeRect = {
    left: node.x,
    right: node.x + node.width,
    top: node.y,
    bottom: node.y + node.height,
  };
  return rectsOverlap(segmentRect, nodeRect);
}

/** 判断正向连线路径是否会穿过其他节点，用于决定是否改走侧向。 */
function forwardRouteHitsNode(
  source: SkillFlowCanvasNode,
  target: SkillFlowCanvasNode,
  nodes: SkillFlowCanvasNode[],
  laneY: number,
) {
  const sourceX = source.x + source.width / 2;
  const sourceY = source.y + source.height + 8;
  const targetX = target.x + target.width / 2;
  const targetY = target.y - 8;
  const safeLaneY = Math.max(sourceY + 48, Math.min(targetY - 48, laneY));
  return nodes.some((node) => {
    if (node.nodeId === source.nodeId || node.nodeId === target.nodeId) return false;
    return segmentHitsFlowNode(sourceX, sourceY, sourceX, safeLaneY, node)
      || segmentHitsFlowNode(sourceX, safeLaneY, targetX, safeLaneY, node)
      || segmentHitsFlowNode(targetX, safeLaneY, targetX, targetY, node);
  });
}

/** 为绕行连线挑选纵向通道 X 坐标，优先选择不与节点相交的一侧。 */
function sideForwardLaneX(
  source: SkillFlowCanvasNode,
  target: SkillFlowCanvasNode,
  nodes: SkillFlowCanvasNode[],
  canvasWidth: number,
  siblingIndex: number,
  incomingIndex: number,
) {
  const sourceY = source.y + source.height + 8;
  const targetY = target.y - 8;
  const verticalTop = Math.min(sourceY, targetY);
  const verticalBottom = Math.max(sourceY, targetY);
  const relevantNodes = nodes.filter((node) => (
    node.nodeId !== source.nodeId
    && node.nodeId !== target.nodeId
    && node.y < verticalBottom + 80
    && node.y + node.height > verticalTop - 80
  ));
  const laneOffset = 76 + siblingIndex * 28 + incomingIndex * 18;
  const rightBoundary = Math.max(
    source.x + source.width,
    target.x + target.width,
    ...relevantNodes.map((node) => node.x + node.width),
  );
  const leftBoundary = Math.min(
    source.x,
    target.x,
    ...relevantNodes.map((node) => node.x),
  );
  const rightX = Math.min(canvasWidth - 74, rightBoundary + laneOffset);
  const leftX = Math.max(74, leftBoundary - laneOffset);
  const preferRight = target.x >= source.x;
  const candidates = preferRight ? [rightX, leftX] : [leftX, rightX];
  const clear = candidates.find((candidateX) => !relevantNodes.some((node) => (
    segmentHitsFlowNode(candidateX, sourceY + 34, candidateX, targetY - 34, node, 12)
  )));
  return clear ?? candidates[0];
}

/** 生成绕开节点遮挡的正向连线路径。 */
function sideForwardFlowPath(
  source: SkillFlowCanvasNode,
  target: SkillFlowCanvasNode,
  nodes: SkillFlowCanvasNode[],
  canvasWidth: number,
  siblingIndex: number,
  incomingIndex: number,
): string {
  const sourceX = source.x + source.width / 2;
  const sourceY = source.y + source.height + 8;
  const targetX = target.x + target.width / 2;
  const targetY = target.y - 8;
  const sideX = sideForwardLaneX(source, target, nodes, canvasWidth, siblingIndex, incomingIndex);
  const exitY = sourceY + 54 + (siblingIndex % 2) * 18;
  const entryY = targetY - 54 - (incomingIndex % 2) * 18;
  return [
    `M ${sourceX} ${sourceY}`,
    `C ${sourceX} ${exitY - 24}, ${sideX} ${exitY - 24}, ${sideX} ${exitY}`,
    `C ${sideX} ${(exitY + entryY) / 2}, ${sideX} ${(exitY + entryY) / 2}, ${sideX} ${entryY}`,
    `C ${sideX} ${entryY + 24}, ${targetX} ${entryY + 24}, ${targetX} ${targetY}`,
  ].join(' ');
}

/** 计算绕行连线标签的锚点位置。 */
function sideForwardEdgeLabelPosition(
  source: SkillFlowCanvasNode,
  target: SkillFlowCanvasNode,
  nodes: SkillFlowCanvasNode[],
  canvasWidth: number,
  siblingIndex: number,
  incomingIndex: number,
) {
  const sourceY = source.y + source.height + 8;
  const targetY = target.y - 8;
  const sideX = sideForwardLaneX(source, target, nodes, canvasWidth, siblingIndex, incomingIndex);
  return {
    x: sideX,
    y: (sourceY + targetY) / 2,
  };
}

/** 计算正向连线标签位置，按兄弟边与入边数量做偏移避让。 */
function forwardEdgeLabelPosition(
  sourceX: number,
  targetX: number,
  laneY: number,
  siblingIndex: number,
  siblingCount: number,
  incomingIndex: number,
  incomingCount: number,
) {
  const siblingOffset = siblingCount > 1 ? (siblingIndex - (siblingCount - 1) / 2) * 18 : 0;
  const incomingOffset = incomingCount > 1 ? (incomingIndex - (incomingCount - 1) / 2) * 28 : 0;
  const minX = Math.min(sourceX, targetX);
  const maxX = Math.max(sourceX, targetX);
  const midpoint = (sourceX + targetX) / 2;
  const hasHorizontalRoom = maxX - minX > 220;
  const x = hasHorizontalRoom
    ? Math.max(minX + 98, Math.min(maxX - 98, midpoint + siblingOffset + incomingOffset))
    : targetX + siblingOffset + incomingOffset;
  return { x, y: laneY };
}

/** 计算回流连线标签位置，放在目标节点右下方。 */
function returnEdgeLabelPosition(
  source: SkillFlowCanvasNode,
  target: SkillFlowCanvasNode,
  canvasWidth: number,
  siblingIndex: number,
) {
  const sideX = Math.min(canvasWidth - 96, Math.max(source.x + source.width + 96 + siblingIndex * 30, target.x + target.width + 96));
  return {
    x: sideX,
    y: Math.max(72, target.y - 84 - siblingIndex * 18),
  };
}

/** 把数值限制在给定的 [min, max] 区间内。 */
function clampNumber(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

/** 判断标签矩形（含外扩边距）是否与节点卡片重叠。 */
function flowLabelOverlapsNode(
  point: { x: number; y: number },
  node: Pick<SkillFlowCanvasNode, 'x' | 'y' | 'width' | 'height'>,
) {
  const labelWidth = 210;
  const labelHeight = 34;
  const margin = 22;
  const left = point.x - labelWidth / 2;
  const right = point.x + labelWidth / 2;
  const top = point.y - labelHeight / 2;
  const bottom = point.y + labelHeight / 2;
  return !(
    right < node.x - margin
    || left > node.x + node.width + margin
    || bottom < node.y - margin
    || top > node.y + node.height + margin
  );
}

/** 在候选位置中挑选不与节点重叠的标签锚点，必要时夹取到画布内。 */
function avoidFlowLabelOverlap(
  anchor: { x: number; y: number },
  nodes: SkillFlowCanvasNode[],
  canvasWidth: number,
  canvasHeight: number,
) {
  /** 把标签锚点夹取到画布可见范围内。 */
  const clampPoint = (point: { x: number; y: number }) => ({
    x: clampNumber(point.x, 112, canvasWidth - 112),
    y: clampNumber(point.y, 34, canvasHeight - 34),
  });
  /** 判断标签锚点是否不与任何节点重叠。 */
  const fits = (point: { x: number; y: number }) => !nodes.some((node) => flowLabelOverlapsNode(point, node));
  const base = clampPoint(anchor);
  if (fits(base)) return base;
  const candidates: Array<{ x: number; y: number }> = [];
  [48, 84, 122, 168, 216].forEach((offset) => {
    candidates.push(
      { x: anchor.x, y: anchor.y - offset },
      { x: anchor.x, y: anchor.y + offset },
      { x: anchor.x - offset * 1.4, y: anchor.y },
      { x: anchor.x + offset * 1.4, y: anchor.y },
      { x: anchor.x - offset, y: anchor.y - offset },
      { x: anchor.x + offset, y: anchor.y - offset },
      { x: anchor.x - offset, y: anchor.y + offset },
      { x: anchor.x + offset, y: anchor.y + offset },
    );
  });
  const found = candidates.map(clampPoint).find(fits);
  return found || clampPoint({ x: anchor.x, y: anchor.y - 76 });
}

/** 生成回流连线的 SVG 路径：从源节点底部绕到画布右侧再回到目标节点顶部。 */
function sideReturnFlowPath(
  source: SkillFlowCanvasNode,
  target: SkillFlowCanvasNode,
  canvasWidth: number,
  siblingIndex: number,
): string {
  const sourceX = source.x + source.width / 2;
  const sourceY = source.y + source.height + 8;
  const targetX = target.x + target.width / 2;
  const targetY = target.y - 8;
  const sideX = Math.min(canvasWidth - 54, Math.max(source.x + source.width + 70 + siblingIndex * 28, target.x + target.width + 70));
  const bottomY = sourceY + 64;
  const topY = Math.max(44, targetY - 64);
  return [
    `M ${sourceX} ${sourceY}`,
    `C ${sourceX} ${bottomY}, ${sideX} ${bottomY}, ${sideX} ${bottomY}`,
    `C ${sideX} ${bottomY}, ${sideX} ${topY}, ${sideX} ${topY}`,
    `C ${sideX} ${topY}, ${targetX} ${topY}, ${targetX} ${targetY}`,
  ].join(' ');
}

/** 生成流转关系的完整说明（源 -> 目标：标签或条件），用于连线悬浮提示。 */
function incomingEdgeLabel(edge: Record<string, unknown>, nodeNameMap: Record<string, string> = {}): string {
  const source = String(edge.source_node_id || '');
  const sourceName = source && nodeNameMap[source] ? nodeNameMap[source] : source;
  const targetName = edgeTargetName(edge, nodeNameMap);
  const label = String(edge.label || '');
  const condition = conditionNaturalText(String(edge.condition || ''));
  const route = [sourceName, targetName].filter(Boolean).join(' -> ');
  const detail = label && condition ? `${label}（${condition}）` : label || condition;
  if (route && detail) return `${route}：${detail}`;
  if (route) return route;
  return detail || '流转';
}

/** 取连线的展示标签，缺失时回退到条件或来源节点名。 */
function edgeDisplayLabel(edge: Record<string, unknown>, nodeNameMap: Record<string, string> = {}): string {
  const label = String(edge.label || '').trim();
  if (label) return label;
  const condition = conditionNaturalText(String(edge.condition || '')).trim();
  if (condition) return condition;
  const source = String(edge.source_node_id || '');
  const sourceName = source && nodeNameMap[source] ? nodeNameMap[source] : source;
  return sourceName ? `来自 ${sourceName}` : '流转';
}

/** 归一化连线标签中的空白字符，保证同一源节点的标签可比较。 */
function normalizedEdgeLabel(edge: Record<string, unknown>, nodeNameMap: Record<string, string> = {}): string {
  return edgeDisplayLabel(edge, nodeNameMap).replace(/\s+/g, ' ').trim() || '流转';
}

/** 取连线目标节点的展示名，缺失时回退为原始 ID。 */
function edgeTargetName(edge: Record<string, unknown>, nodeNameMap: Record<string, string> = {}): string {
  const targetId = String(edge.next_node_id || '').trim();
  return targetId ? nodeNameMap[targetId] || targetId : '';
}

/** 判断同一源节点下是否存在同名兄弟连线，用于标记并行流转。 */
function hasDuplicateSiblingEdgeLabel(
  edge: Record<string, unknown>,
  siblings: Array<Record<string, unknown>>,
  nodeNameMap: Record<string, string> = {},
): boolean {
  if (siblings.length <= 1) return false;
  const sourceId = String(edge.source_node_id || '').trim();
  const label = normalizedEdgeLabel(edge, nodeNameMap);
  return siblings.filter((item) => (
    String(item.source_node_id || '').trim() === sourceId
    && normalizedEdgeLabel(item, nodeNameMap) === label
  )).length > 1;
}

/** 判断一组出边中是否存在重复标签，用于标记并行流转。 */
function hasDuplicateOutgoingEdgeLabel(
  edges: Array<Record<string, unknown>>,
  nodeNameMap: Record<string, string> = {},
): boolean {
  if (edges.length <= 1) return false;
  const labelCounts = edges.reduce<Record<string, number>>((acc, edge) => {
    const label = normalizedEdgeLabel(edge, nodeNameMap);
    acc[label] = (acc[label] || 0) + 1;
    return acc;
  }, {});
  return Object.values(labelCounts).some((count) => count > 1);
}

/** 生成出边数量标签，存在并行分支时额外标注「并行」。 */
function outgoingRouteCountLabel(edges: Array<Record<string, unknown>>): string {
  return `${edges.length} 条${hasDuplicateOutgoingEdgeLabel(edges) ? '并行' : ''}流转`;
}

/** 生成流程图连线短标签：并行或同名分支会补充目标节点名。 */
function flowEdgeDisplayLabel(
  edge: Record<string, unknown>,
  nodeNameMap: Record<string, string> = {},
  siblingCount = 1,
  hasDuplicateSourceLabel = false,
): string {
  const label = normalizedEdgeLabel(edge, nodeNameMap);
  const targetName = edgeTargetName(edge, nodeNameMap);
  if (hasDuplicateSourceLabel && targetName) {
    return `并行执行 · ${targetName}`;
  }
  const hasExplicitLabel = Boolean(String(edge.label || '').trim() || String(edge.condition || '').trim());
  if (siblingCount > 1 && targetName && (hasDuplicateSourceLabel || !hasExplicitLabel)) {
    return `${label} · 到${targetName}`;
  }
  return label;
}

/** 生成源码视图中的流转摘要文本，包含并行标记、规则名与优先级。 */
function sourceEdgeSummary(
  edge: Record<string, unknown>,
  nodeNameMap: Record<string, string> = {},
  index = 0,
  siblingEdges: Array<Record<string, unknown>> = [],
): string {
  const targetName = edgeTargetName(edge, nodeNameMap) || '未指定节点';
  const label = String(edge.label || '').trim();
  const condition = conditionNaturalText(String(edge.condition || '')).trim();
  const hasPriority = edge.priority !== undefined && edge.priority !== null && String(edge.priority).trim() !== '';
  const priority = hasPriority && typeof edge.priority === 'number' ? edge.priority : hasPriority && Number.isFinite(Number(edge.priority)) ? Number(edge.priority) : index;
  const prefix = label || condition;
  const parallelText = hasDuplicateSiblingEdgeLabel(edge, siblingEdges, nodeNameMap) ? '并行执行 · ' : '';
  const priorityText = hasPriority && Number.isFinite(priority) ? ` · 优先级 ${priority}` : '';
  return `${parallelText}${prefix ? `${prefix} -> ` : ''}${targetName}${priorityText}`;
}

/** 压缩过长的连线标签，超过 24 字时截断并补省略号。 */
function compactEdgeLabel(value: string): string {
  const text = value.replace(/\s+/g, ' ').trim();
  if (text.length <= 24) return text;
  return `${text.slice(0, 21)}...`;
}

/** 取节点类型的中文展示名，未知类型原样返回。 */
function nodeTypeLabel(type: string): string {
  return NODE_TYPE_OPTIONS.find((item) => item.value === type)?.label || type || '节点';
}

/** 把知识范围对象展开为「键: 值」字符串数组。 */
function knowledgeScopeLabels(value: unknown): string[] {
  if (!isRecord(value) || Object.keys(value).length === 0) return [];
  return Object.entries(value).map(([key, item]) => `${key}: ${String(item)}`);
}

/** 按最长一行内容的显示宽度生成输入框宽度样式，并限制在给定区间内。 */
function compactInputStyle(value: string, minCh = 8, maxCh = 92): CSSProperties {
  const longestLine = String(value || '').split('\n').reduce((max, line) => Math.max(max, visualTextWidth(line)), 0);
  const width = Math.max(minCh, Math.min(maxCh, longestLine + 2));
  return { width: `min(${width}ch, 100%)` };
}

/** 计算文本的显示宽度，中日韩等全角字符按 2 个字符宽度计。 */
function visualTextWidth(value: string): number {
  return Array.from(value).reduce((total, char) => {
    if (/[\u2e80-\u9fff\uff00-\uffef]/.test(char)) return total + 2;
    return total + 1;
  }, 0);
}

/** 按内容长度生成源码输入区宽度样式，多行输入使用更宽的区间。 */
function sourceInputStyle(value: string, multiline = false): CSSProperties {
  const longestLine = String(value || '').split('\n').reduce((max, line) => Math.max(max, visualTextWidth(line)), 0);
  const minCh = multiline ? 34 : 18;
  const maxCh = multiline ? 96 : 72;
  const width = Math.max(minCh, Math.min(maxCh, longestLine + 4));
  return { width: `min(${width}ch, 100%)`, maxWidth: '100%' };
}

/** 生成节点说明的折叠预览文本，空白时返回占位文案，过长时截断。 */
function previewSourceText(value: string): string {
  const text = value.replace(/\s+/g, ' ').trim();
  if (!text) return '暂无节点说明';
  return text.length > 96 ? `${text.slice(0, 96)}...` : text;
}

/** 基础信息标题的就地编辑输入框。 */
function EditableSourceHeading({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  return (
    <EditableSourceField>
      <SourceInput
        className={SOURCE_TITLE_INPUT_CLASS}
        style={compactInputStyle(value, 10, 56)}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </EditableSourceField>
  );
}

/** 节点标题的就地编辑输入框，带「Node N」前缀与占位回退。 */
function EditableSourceStepHeading({
  index,
  value,
  fallback,
  onChange,
}: {
  index: number;
  value: string;
  fallback: string;
  onChange: (value: string) => void;
}) {
  return (
    <EditableSourceField>
      <div className={SOURCE_STEP_TITLE_EDIT_CLASS}>
        <span>Node {index + 1}:</span>
        <SourceInput
          value={value}
          placeholder={fallback}
          style={compactInputStyle(value || fallback, 10, 88)}
          onChange={(event) => onChange(event.target.value)}
        />
      </div>
    </EditableSourceField>
  );
}

/**
 * 源码视图中「标签加文本值」的一行编辑器，支持多行与折叠。
 *
 * - 可折叠且内容超过 90 字时默认收起，只显示预览文本。
 */
function EditableSourceTextLine({
  label,
  value,
  multiline = false,
  collapsible = false,
  readOnly = false,
  onChange,
}: {
  label: string;
  value: string;
  multiline?: boolean;
  collapsible?: boolean;
  readOnly?: boolean;
  onChange: (value: string) => void;
}) {
  const canCollapse = collapsible && multiline;
  const shouldStartCollapsed = canCollapse && value.trim().length > 90;
  const [collapsed, setCollapsed] = useState(shouldStartCollapsed);

  useEffect(() => {
    if (!canCollapse || value.trim().length <= 90) {
      setCollapsed(false);
    }
  }, [canCollapse, value]);

  return (
    <div className={cn(SOURCE_LINE_CLASS, canCollapse && "collapsible")}>
      <span className={SOURCE_KEY_CLASS}>{label}</span>
      <span className={SOURCE_VALUE_CLASS}>
        <EditableSourceField>
          {canCollapse ? (
            <div className={SOURCE_COLLAPSIBLE_EDITOR_CLASS}>
              <button
                type="button"
                className={SOURCE_COLLAPSIBLE_HEAD_CLASS}
                onClick={() => setCollapsed((current) => !current)}
              >
                <span className={cn(SOURCE_COLLAPSIBLE_PREVIEW_CLASS, !collapsed && SOURCE_COLLAPSIBLE_PREVIEW_MUTED_CLASS)}>
                  {collapsed ? previewSourceText(value) : '正在编辑节点说明'}
                </span>
                <span className={SOURCE_COLLAPSIBLE_TOGGLE_CLASS}>
                  {collapsed ? <RightOutlined /> : <DownOutlined />}
                  {collapsed ? '展开' : '收起'}
                </span>
              </button>
              {!collapsed && (
                <AutoGrowTextarea
                  className={SOURCE_EDIT_INPUT_CLASS}
                  value={value}
                  style={sourceInputStyle(value, true)}
                  minRows={3}
                  readOnly={readOnly}
                  onChange={(event) => {
                    if (!readOnly) onChange(event.target.value);
                  }}
                />
              )}
            </div>
          ) : multiline ? (
            <AutoGrowTextarea
              className={SOURCE_EDIT_INPUT_CLASS}
              value={value}
              style={sourceInputStyle(value, true)}
              minRows={2}
              readOnly={readOnly}
              onChange={(event) => {
                if (!readOnly) onChange(event.target.value);
              }}
            />
          ) : (
            <SourceInput
              className={SOURCE_EDIT_INPUT_CLASS}
              value={value}
              style={sourceInputStyle(value)}
              readOnly={readOnly}
              onChange={(event) => {
                if (!readOnly) onChange(event.target.value);
              }}
            />
          )}
        </EditableSourceField>
      </span>
    </div>
  );
}

/** 源码视图中「标签加数字值」的一行编辑器，越界值会被夹取到区间内。 */
function EditableSourceNumberLine({
  label,
  value,
  min,
  max,
  placeholder,
  onChange,
}: {
  label: string;
  value?: number | null;
  min: number;
  max: number;
  placeholder?: string;
  onChange: (value: number | null) => void;
}) {
  return (
    <div className={SOURCE_LINE_CLASS}>
      <span className={SOURCE_KEY_CLASS}>{label}</span>
      <span className={SOURCE_VALUE_CLASS}>
        <EditableSourceField>
          <SourceInput
            className={SOURCE_EDIT_INPUT_CLASS}
            type="number"
            min={min}
            max={max}
            step={1}
            value={value == null ? '' : String(value)}
            placeholder={placeholder}
            style={sourceInputStyle(value == null ? '' : String(value))}
            onChange={(event) => {
              if (!event.target.value.trim()) {
                onChange(null);
                return;
              }
              const next = Number(event.target.value);
              if (Number.isFinite(next)) {
                onChange(Math.max(min, Math.min(max, Math.round(next))));
              }
            }}
          />
        </EditableSourceField>
      </span>
    </div>
  );
}

/** 源码视图中「标签加多行列表」的一行编辑器，按行拆分为数组。 */
function EditableSourceListLine({
  label,
  values,
  minRows = 1,
  maxRows,
  onChange,
}: {
  label: string;
  values: string[];
  minRows?: number;
  maxRows?: number;
  onChange: (value: string) => void;
}) {
  return (
    <div className={SOURCE_LINE_CLASS}>
      <span className={SOURCE_KEY_CLASS}>{label}</span>
      <span className={SOURCE_VALUE_CLASS}>
        <EditableSourceField>
          <AutoGrowTextarea
            className={SOURCE_EDIT_INPUT_CLASS}
            value={values.join('\n')}
            style={sourceInputStyle(values.join('\n'), true)}
            minRows={minRows}
            maxRows={maxRows}
            onChange={(event) => onChange(event.target.value)}
          />
        </EditableSourceField>
      </span>
    </div>
  );
}

/** 源码视图中「标签加下拉选择」的一行编辑器，未知当前值会补进选项。 */
function EditableSourceSelectLine({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: SelectOption[];
  onChange: (value: string) => void;
}) {
  const mergedOptions = options.some((option) => option.value === value) || !value
    ? options
    : [...options, { value, label: value }];
  return (
    <div className={SOURCE_LINE_CLASS}>
      <span className={SOURCE_KEY_CLASS}>{label}</span>
      <span className={SOURCE_VALUE_CLASS}>
        <EditableSourceField>
          <SourceSelect
            className={cn(SOURCE_SELECT_CLASS, "w-[220px]")}
            value={value}
            options={mergedOptions}
            onChange={onChange}
          />
        </EditableSourceField>
      </span>
    </div>
  );
}

/** 进入条件编辑器：预设下拉加自然语言输入，并展示可读化结果。 */
function EditableConditionLine({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  const presetValue = conditionPresetValue(value);
  const naturalValue = conditionNaturalText(value);
  return (
    <div className={SOURCE_LINE_CLASS}>
      <span className={SOURCE_KEY_CLASS}>{fieldLabel('condition')}</span>
      <span className={SOURCE_VALUE_CLASS}>
        <EditableSourceField>
          <div className={CONDITION_EDITOR_CLASS}>
            <SourceSelect
              className={CONDITION_PRESET_CLASS}
              value={presetValue}
              options={CONDITION_PRESET_OPTIONS}
              onChange={(nextValue) => {
                if (nextValue === '__custom__') {
                  onChange(naturalValue);
                  return;
                }
                onChange(conditionFromPreset(nextValue));
              }}
            />
            <AutoGrowTextarea
              className={cn(SOURCE_EDIT_INPUT_CLASS, CONDITION_INPUT_CLASS)}
              value={naturalValue}
              placeholder="用一句话描述什么时候进入，例如：用户已经提供商品名称后进入"
              minRows={1}
              onChange={(event) => onChange(event.target.value)}
            />
            <span className={CONDITION_READABLE_CLASS}>{conditionReadableText(value)}</span>
          </div>
        </EditableSourceField>
      </span>
    </div>
  );
}

/** 只读的源码信息行，空值显示短横线。 */
function SourceReadonlyLine({ label, value }: { label: string; value: string }) {
  return (
    <div className={cn(SOURCE_LINE_CLASS, "readonly")}>
      <span className={SOURCE_KEY_CLASS}>{label}</span>
      <span className={cn(SOURCE_VALUE_CLASS, SOURCE_READONLY_VALUE_CLASS)}>{value || '-'}</span>
    </div>
  );
}

/** 只读的 JSON 信息行，空对象时不渲染。 */
function SourceJsonLine({ label, value }: { label: string; value: unknown }) {
  if (!hasReadableSourceObject(value)) return null;
  return (
    <div className={cn(SOURCE_LINE_CLASS, "readonly")}>
      <span className={SOURCE_KEY_CLASS}>{label}</span>
      <span className={SOURCE_VALUE_CLASS}>
        <pre className={SOURCE_JSON_INLINE_CLASS}>{JSON.stringify(value, null, 2)}</pre>
      </span>
    </div>
  );
}

/** 节点重试策略编辑器：重试次数、失败处理方式与追问文案。 */
function EditableRetryPolicyLine({
  value,
  onChange,
}: {
  value: unknown;
  onChange: (value: Record<string, unknown>) => void;
}) {
  const policy = isRecord(value) ? value : {};
  const attemptKey = Object.prototype.hasOwnProperty.call(policy, 'max_retries') ? 'max_retries' : 'max_attempts';
  const strategyKey = Object.prototype.hasOwnProperty.call(policy, 'strategy') ? 'strategy' : 'on_failure';
  const messageKey = Object.prototype.hasOwnProperty.call(policy, 'message') ? 'message' : 'retry_message';
  const maxAttempts = retryPolicyNumber(policy.max_retries ?? policy.max_attempts);
  const strategy = retryPolicyString(policy.strategy ?? policy.on_failure);
  const retryMessage = retryPolicyString(policy.retry_message ?? policy.message);
  const strategyOptions = mergeSelectOptions(
    RETRY_STRATEGY_OPTIONS,
    strategy ? [{ value: strategy, label: retryStrategyLabel(strategy) }] : [],
  );

  /** 写回重试策略补丁并清理空值字段，避免草稿留下空配置。 */
  function commit(patch: Record<string, unknown>) {
    const next = { ...policy, ...patch };
    Object.keys(next).forEach((key) => {
      if (next[key] === '' || next[key] === null || next[key] === undefined) delete next[key];
    });
    onChange(next);
  }

  /** 更新最大重试次数；非正数表示不限制，会移除该字段。 */
  function updateAttempts(nextValue: number | string | null) {
    const nextNumber = Number(nextValue);
    commit(Number.isFinite(nextNumber) && nextNumber > 0
      ? { [attemptKey]: Math.floor(nextNumber) }
      : { max_attempts: undefined, max_retries: undefined });
  }

  /** 更新失败后的处理方式，空值表示移除配置。 */
  function updateStrategy(nextValue?: string) {
    commit(nextValue ? { [strategyKey]: nextValue } : { on_failure: undefined, strategy: undefined });
  }

  /** 更新追问文案，空白文本会移除该字段。 */
  function updateMessage(nextValue: string) {
    commit(nextValue.trim() ? { [messageKey]: nextValue } : { retry_message: undefined, message: undefined });
  }

  return (
    <div className={SOURCE_LINE_CLASS}>
      <span className={SOURCE_KEY_CLASS}>重试策略</span>
      <span className={SOURCE_VALUE_CLASS}>
        <EditableSourceField>
          <div className={RETRY_POLICY_EDITOR_CLASS}>
            <label className={RETRY_POLICY_FIELD_CLASS}>
              <span>最多重试</span>
              <SourceNumberInput
                min={0}
                value={maxAttempts}
                placeholder="不限制"
                onChange={updateAttempts}
              />
            </label>
            <label className={RETRY_POLICY_FIELD_CLASS}>
              <span>失败后</span>
              <SourceSelect
                value={strategy || undefined}
                options={strategyOptions}
                placeholder="选择处理方式"
                onChange={(nextValue) => updateStrategy(nextValue)}
              />
            </label>
            <label className={RETRY_POLICY_FIELD_CLASS}>
              <span>追问文案</span>
              <SourceInput
                value={retryMessage}
                placeholder="例如：请补充需要校验的报文内容。"
                onChange={(event) => updateMessage(event.target.value)}
              />
            </label>
          </div>
        </EditableSourceField>
      </span>
    </div>
  );
}

/** 读取重试次数：仅接受正整数，其余返回 null。 */
function retryPolicyNumber(value: unknown): number | null {
  const numberValue = Number(value);
  return Number.isFinite(numberValue) && numberValue > 0 ? Math.floor(numberValue) : null;
}

/** 读取重试策略中的字符串字段，非字符串返回空串。 */
function retryPolicyString(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

/** 取重试策略的中文展示名，未知值原样返回。 */
function retryStrategyLabel(value: string): string {
  return RETRY_STRATEGY_OPTIONS.find((item) => item.value === value)?.label || value;
}

/** 判断值是否为可直接展示的非空普通对象。 */
function hasReadableSourceObject(value: unknown): boolean {
  if (!isRecord(value)) return false;
  return Object.keys(value).length > 0;
}

/** 允许动作行编辑器，内部复用可搜索的动作列表。 */
function EditableSourceActionLine({
  values,
  options,
  toolDescriptions,
  toolStatuses,
  onChange,
}: {
  values: string[];
  options: SelectOption[];
  toolDescriptions: ToolDescriptionMap;
  toolStatuses: ToolStatusMap;
  onChange: (value: string) => void;
}) {
  return (
    <div className={SOURCE_LINE_CLASS}>
      <span className={SOURCE_KEY_CLASS}>{fieldLabel('allowed_actions')}</span>
      <span className={SOURCE_VALUE_CLASS}>
        <EditableSourceField>
          <EditableActionList
            actions={values}
            options={options}
            toolDescriptions={toolDescriptions}
            toolStatuses={toolStatuses}
            onChange={onChange}
          />
        </EditableSourceField>
      </span>
    </div>
  );
}

/**
 * 能力引用行编辑器：搜索、勾选并按需标记为强制执行。
 *
 * - 已下线或不可用的资源仍保留展示，只允许取消引用。
 * - 变更通过 onChange / onRequiredChange 回传完整 ID 列表。
 */
export function EditableCapabilityReferencesLine({
  label,
  values,
  requiredValues,
  options,
  emptyText,
  onChange,
  onRequiredChange,
}: {
  label: string;
  values: string[];
  requiredValues: string[];
  options: CapabilityReferenceOption[];
  emptyText: string;
  onChange: (value: string[]) => void;
  onRequiredChange: (value: string[]) => void;
}) {
  const [query, setQuery] = useState('');
  const selectedValues = new Set(values);
  const mergedOptions = mergeCapabilityReferenceOptions(
    options.filter((option) => !option.unavailableReason || selectedValues.has(option.value)),
    values,
  );
  const selected = new Set(values);
  const required = new Set(requiredValues);
  const normalizedQuery = query.trim().toLowerCase();
  const filteredOptions = mergedOptions
    .filter((option) => {
      if (!normalizedQuery) return true;
      return [option.label, option.value, option.description]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
        .includes(normalizedQuery);
    })
    .sort((left, right) => Number(selected.has(right.value)) - Number(selected.has(left.value)));

  /** 勾选或取消勾选某个能力引用，新增时自动去重。 */
  function toggle(value: string, checked: boolean) {
    if (checked) {
      onChange(Array.from(new Set([...values, value])));
      return;
    }
    onChange(values.filter((item) => item !== value));
  }

  /** 把某个能力引用标记为强制或可选执行。 */
  function setRequired(value: string, checked: boolean) {
    if (checked) {
      onRequiredChange(Array.from(new Set([...requiredValues, value])));
      return;
    }
    onRequiredChange(requiredValues.filter((item) => item !== value));
  }

  return (
    <div className={SOURCE_LINE_CLASS}>
      <span className={SOURCE_KEY_CLASS}>{label}</span>
      <span className={SOURCE_VALUE_CLASS}>
        <EditableSourceField>
          <Popover>
            <PopoverTrigger asChild>
              <button
                type="button"
                className="flex min-h-[34px] w-full items-center justify-between gap-[10px] rounded-[8px] border border-[#dfe3eb] bg-white px-[10px] py-[6px] text-left outline-none transition-colors hover:border-[#c7cedc] focus-visible:border-[#1a71ff] focus-visible:ring-2 focus-visible:ring-[#1a71ff]/15"
              >
                <span className="flex min-w-0 flex-1 flex-wrap gap-[5px]">
                  {values.length === 0 ? (
                    <span className="text-[12px] text-[#a0a7b8]">{emptyText}</span>
                  ) : (
                    values.map((value) => {
                      const option = mergedOptions.find((item) => item.value === value);
                      return (
                        <span
                          key={value}
                          className={cn(
                            'inline-flex max-w-[200px] items-center gap-[5px] rounded-full px-[8px] py-[3px] text-[11px]',
                            required.has(value)
                              ? 'bg-[#fff1dc] text-[#9a5a00]'
                              : 'bg-[#eef3fb] text-[#464c5e]',
                          )}
                          title={option?.label || value}
                        >
                          <span className="truncate">{option?.label || value}</span>
                          <span className="shrink-0 text-[9px] opacity-75">
                            {required.has(value) ? '强制' : '可选'}
                          </span>
                        </span>
                      );
                    })
                  )}
                </span>
                <span className="shrink-0 text-[11px] text-[#1a71ff]">
                  {values.length > 0 ? `已选择 ${values.length} 个` : '选择'}
                </span>
              </button>
            </PopoverTrigger>
            <PopoverContent align="start" className="z-[130] w-[min(420px,calc(100vw-32px))] p-0">
              <div className="border-b border-[#eceef1] p-[10px]">
                <Input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder={`搜索${label}`}
                  className="h-[32px]"
                />
              </div>
              <div className="max-h-[300px] overflow-y-auto p-[6px]">
                {filteredOptions.length === 0 ? (
                  <div className="px-[10px] py-[24px] text-center text-[12px] text-[#858b9c]">暂无可选能力</div>
                ) : (
                  filteredOptions.map((option) => {
                    const checked = selected.has(option.value);
                    const disabled = Boolean(option.unavailableReason) && !checked;
                    return (
                      <div
                        key={option.value}
                        className={cn(
                          'flex items-start gap-[10px] rounded-[8px] px-[9px] py-[8px]',
                          disabled ? 'cursor-not-allowed opacity-55' : 'cursor-pointer hover:bg-[#f6f8fb]',
                        )}
                      >
                        <Checkbox
                          checked={checked}
                          disabled={disabled}
                          className="mt-[2px]"
                          onCheckedChange={(next) => toggle(option.value, next === true)}
                          aria-label={`${checked ? '取消选择' : '选择'}${option.label}`}
                        />
                        <span className="min-w-0 flex-1">
                          <span className="flex min-w-0 flex-wrap items-center gap-[6px]">
                            <strong className="truncate text-[12px] font-medium text-[#18181a]">{option.label}</strong>
                            <CapabilityScopeBadge value={option.capabilityScope} />
                            {option.unavailableReason && (
                              <span className="text-[10px] text-[#d20b0b]">{option.unavailableReason}</span>
                            )}
                          </span>
                          <span className="mt-[2px] block truncate text-[11px] text-[#858b9c]">
                            {option.description || option.value}
                          </span>
                        </span>
                        {checked && (
                          <span className="inline-flex shrink-0 rounded-[7px] bg-[#f0f2f6] p-[2px] text-[10px]">
                            <button
                              type="button"
                              className={cn(
                                'rounded-[5px] px-[7px] py-[3px] transition-colors',
                                !required.has(option.value)
                                  ? 'bg-white text-[#1a71ff] shadow-sm'
                                  : 'text-[#7c8495] hover:text-[#464c5e]',
                              )}
                              onClick={() => setRequired(option.value, false)}
                            >
                              可选执行
                            </button>
                            <button
                              type="button"
                              className={cn(
                                'rounded-[5px] px-[7px] py-[3px] transition-colors',
                                required.has(option.value)
                                  ? 'bg-[#fff7e8] text-[#a45f00] shadow-sm'
                                  : 'text-[#7c8495] hover:text-[#464c5e]',
                              )}
                              onClick={() => setRequired(option.value, true)}
                            >
                              强制执行
                            </button>
                          </span>
                        )}
                      </div>
                    );
                  })
                )}
              </div>
              <div className="border-t border-[#eceef1] px-[12px] py-[8px] text-[11px] leading-[1.45] text-[#858b9c]">
                通用能力始终可由模型自主选择；仅限 SOP 的能力需在当前节点选择。标记为强制执行后，成功调用才允许推进节点。
              </div>
            </PopoverContent>
          </Popover>
        </EditableSourceField>
      </span>
    </div>
  );
}

/** 合并能力选项：草稿中已引用但列表缺失的资源补一条不可用占位。 */
function mergeCapabilityReferenceOptions(
  options: CapabilityReferenceOption[],
  values: string[],
): CapabilityReferenceOption[] {
  const existing = new Set(options.map((option) => option.value));
  return [
    ...options,
    ...values
      .filter((value) => !existing.has(value))
      .map((value) => ({
        value,
        label: value,
        description: '资源当前不可用，可取消引用',
        unavailableReason: '资源不可用',
      })),
  ];
}

/** 可编辑的允许动作列表：新增、重选与删除动作。 */
function EditableActionList({
  actions,
  options,
  toolDescriptions,
  toolStatuses,
  onChange,
}: {
  actions: string[];
  options: SelectOption[];
  toolDescriptions: ToolDescriptionMap;
  toolStatuses: ToolStatusMap;
  onChange: (value: string) => void;
}) {
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const mergedOptions = mergeSelectOptions(options, actions.map((action) => ({
    value: action,
    label: actionLabel(action),
  })));

  /** 把动作数组写回父组件：过滤空值并按行拼接成字符串。 */
  function writeActions(nextActions: string[]) {
    onChange(nextActions.filter(Boolean).join('\n'));
  }

  /** 提交某个下标的动作：空值删除、重复提示、越界则追加。 */
  function commitAction(index: number, action: string) {
    const nextAction = action.trim();
    const next = [...actions];
    if (!nextAction) {
      if (index < next.length) next.splice(index, 1);
      writeActions(next);
      setEditingIndex(null);
      return;
    }
    const duplicateIndex = next.findIndex((item, itemIndex) => item === nextAction && itemIndex !== index);
    if (duplicateIndex >= 0) {
      notify.info('这个动作已经添加过了');
      setEditingIndex(null);
      return;
    }
    if (index >= next.length) {
      next.push(nextAction);
    } else {
      next[index] = nextAction;
    }
    writeActions(next);
    setEditingIndex(null);
  }

  /** 删除指定下标的动作并退出编辑态。 */
  function removeAction(index: number) {
    const next = [...actions];
    next.splice(index, 1);
    writeActions(next);
    if (editingIndex === index) setEditingIndex(null);
  }

  /** 渲染动作搜索选择器，供指定下标的新增或重选使用。 */
  function actionSelect(index: number, value?: string) {
    return (
      <ActionCombobox
        value={value || undefined}
        options={mergedOptions}
        placeholder="选择一个动作"
        onSelect={(nextValue) => commitAction(index, String(nextValue || ''))}
      />
    );
  }

  return (
    <div className={cn(SOURCE_ACTION_EDITOR_CLASS, "group/action-editor")}>
      <div className={cn(SOURCE_ACTION_LIST_CLASS, SOURCE_ACTION_LIST_EDITABLE_CLASS)}>
        {actions.map((action, index) => (
          editingIndex === index ? (
            <span className={SOURCE_ACTION_PICKER_CLASS} key={`editing_${index}`}>
              {actionSelect(index, action)}
            </span>
          ) : (
            <span className={cn(SOURCE_ACTION_TOKEN_CLASS, "group/token")} key={`${action}_${index}`}>
              <button
                type="button"
                className={SOURCE_ACTION_EDIT_BUTTON_CLASS}
                onClick={() => setEditingIndex(index)}
              >
                <ActionChip action={action} toolDescriptions={toolDescriptions} toolStatuses={toolStatuses} editable />
              </button>
              <button type="button" className={SOURCE_ACTION_REMOVE_CLASS} onClick={() => removeAction(index)} aria-label={`移除 ${actionLabel(action)}`}>
                <CloseOutlined />
              </button>
            </span>
          )
        ))}
        {editingIndex !== null && editingIndex >= actions.length && (
          <span className={SOURCE_ACTION_PICKER_CLASS}>
            {actionSelect(editingIndex)}
          </span>
        )}
        {actions.length === 0 && editingIndex === null && (
          <div className={SOURCE_ACTION_EMPTY_CLASS}>
            <span>当前节点还没有允许动作</span>
            <button type="button" className={SOURCE_ACTION_ADD_CLASS} onClick={() => setEditingIndex(0)}>
              <PlusOutlined />
              添加动作
            </button>
          </div>
        )}
        {actions.length > 0 && editingIndex === null && (
          <button type="button" className={SOURCE_ACTION_ADD_CLASS} onClick={() => setEditingIndex(actions.length)}>
            <PlusOutlined />
            新增动作
          </button>
        )}
      </div>
      <span className={SOURCE_EDIT_HINT_CLASS}>每次新增一个动作；点击已有动作可重新选择。</span>
    </div>
  );
}

/** 流转规则编辑器：按优先级排序展示出边，并编辑目标、条件与优先级。 */
function EditableFlowRulesLine({
  sourceNodeId,
  edges,
  nodes,
  nodeOptions,
  terminal,
  onAdd,
  onUpdate,
  onDelete,
}: {
  sourceNodeId: string;
  edges: Array<Record<string, unknown>>;
  nodes: Array<Record<string, unknown>>;
  nodeOptions: SelectOption[];
  terminal: boolean;
  onAdd: () => void;
  onUpdate: (edgeIndex: number, patch: Record<string, unknown>) => void;
  onDelete: (edgeIndex: number) => void;
}) {
  const orderedEdges = edges
    .map((edge, index) => ({ edge, index }))
    .sort((a, b) => edgePriority(a.edge, a.index) - edgePriority(b.edge, b.index));
  return (
    <div className={SOURCE_LINE_CLASS}>
      <span className={SOURCE_KEY_CLASS}>流转规则</span>
      <span className={SOURCE_VALUE_CLASS}>
        <EditableSourceField>
          <div className={FLOW_RULE_EDITOR_CLASS}>
            <div className={FLOW_RULE_HEAD_CLASS}>
              <span>从 {nodeDisplayNameById(nodes, sourceNodeId)} 出发</span>
              <UIButton variant="outline" size="sm" className={PILL_OUTLINE_BUTTON_CLASS} onClick={onAdd}>
                <PlusOutlined />
                新增规则
              </UIButton>
            </div>
            {orderedEdges.length === 0 ? (
              <div className={FLOW_RULE_EMPTY_CLASS}>{terminal ? '当前节点是终止节点，默认流程结束。' : '还没有后续节点，请新增流转规则。'}</div>
            ) : (
              <div className={FLOW_RULE_LIST_CLASS}>
                {orderedEdges.map(({ edge, index }) => (
                  <div className={FLOW_RULE_ITEM_CLASS} key={`${String(edge.next_node_id)}_${index}`}>
                    <label className={cn(FLOW_RULE_FIELD_CLASS, FLOW_RULE_FIELD_TARGET_CLASS)}>
                      <span>目标 Node</span>
                      <SourceSelect
                        className={FLOW_RULE_TARGET_CLASS}
                        value={String(edge.next_node_id || '') || undefined}
                        options={nodeOptions}
                        placeholder="选择目标 Node"
                        onChange={(value) => onUpdate(index, { next_node_id: value })}
                      />
                    </label>
                    <label className={cn(FLOW_RULE_FIELD_CLASS, FLOW_RULE_FIELD_LABEL_CLASS)}>
                      <span>规则名称</span>
                      <SourceInput
                        className={FLOW_RULE_LABEL_INPUT_CLASS}
                        value={String(edge.label || '')}
                        placeholder="例如：信息完整后继续"
                        onChange={(event) => onUpdate(index, { label: event.target.value })}
                      />
                    </label>
                    <div className={cn(FLOW_RULE_FIELD_CLASS, FLOW_RULE_FIELD_CONDITION_CLASS)}>
                      <span>进入条件</span>
                      <div className={FLOW_RULE_CONDITION_CONTROLS_CLASS}>
                        <SourceSelect
                          className={CONDITION_PRESET_CLASS}
                          value={conditionPresetValue(String(edge.condition || ''))}
                          options={CONDITION_PRESET_OPTIONS}
                          onChange={(nextValue) => {
                            if (nextValue === '__custom__') {
                              onUpdate(index, { condition: conditionNaturalText(String(edge.condition || '')) });
                              return;
                            }
                            onUpdate(index, { condition: conditionFromPreset(nextValue) });
                          }}
                        />
                        <AutoGrowTextarea
                          className={FLOW_RULE_CONDITION_INPUT_CLASS}
                          value={conditionNaturalText(String(edge.condition || ''))}
                          placeholder="用一句话描述，例如：报文已获取后进入"
                          minRows={1}
                          onChange={(event) => onUpdate(index, { condition: event.target.value })}
                        />
                      </div>
                      <em>{flowRuleConditionText(String(edge.condition || ''))}</em>
                    </div>
                    <label className={cn(FLOW_RULE_FIELD_CLASS, FLOW_RULE_FIELD_PRIORITY_CLASS)}>
                      <span>优先级</span>
                      <SourceNumberInput
                        className={FLOW_RULE_PRIORITY_CLASS}
                        min={0}
                        value={edgePriority(edge, index)}
                        onChange={(value) => onUpdate(index, { priority: Number(value ?? 0) })}
                      />
                    </label>
                    <UIButton variant="destructive" size="icon" className={FLOW_RULE_DELETE_CLASS} onClick={() => onDelete(index)}>
                      <DeleteOutlined />
                    </UIButton>
                  </div>
                ))}
              </div>
            )}
          </div>
        </EditableSourceField>
      </span>
    </div>
  );
}

/** 就地编辑字段容器：拦截鼠标与键盘事件，避免触发外层选中目标。 */
function EditableSourceField({ children }: { children: ReactNode }) {
  /** 阻止事件冒泡到外层可选中区块。 */
  function stop(event: MouseEvent<HTMLDivElement> | KeyboardEvent<HTMLDivElement>) {
    event.stopPropagation();
  }

  return (
    <div className={SOURCE_EDIT_FIELD_CLASS} onMouseDown={stop} onClick={stop} onDoubleClick={stop} onKeyDown={stop}>
      {children}
    </div>
  );
}

/** 可被选中为改写目标的可点击区块，支持点击与键盘切换。 */
function SelectableTarget({
  className,
  target,
  onToggle,
  children,
}: {
  className: string;
  target: TargetSelection;
  onToggle: (target: TargetSelection) => void;
  children: ReactNode;
}) {
  /** 点击选区：存在文本选中时只阻止默认行为，否则切换目标选中态。 */
  function handleClick(event: MouseEvent<HTMLDivElement>) {
    if (hasSelectedText()) {
      event.preventDefault();
      return;
    }
    onToggle(target);
  }

  /** 回车或空格切换目标选中态。 */
  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    onToggle(target);
  }

  return (
    <div role="button" tabIndex={0} className={className} onClick={handleClick} onKeyDown={handleKeyDown}>
      {children}
    </div>
  );
}

/** 动作差异列表：展示被删除的旧动作与新增动作的动画状态。 */
function ActionDiffList({
  diff,
  currentActions,
  toolDescriptions,
  toolStatuses,
}: {
  diff: TextDiffAnimation;
  currentActions: string[];
  toolDescriptions: ToolDescriptionMap;
  toolStatuses: ToolStatusMap;
}) {
  const oldActions = actionsFromDiffText(diffFullOldValue(diff));
  const newActions = actionsFromDiffText(diffFullNewValue(diff));
  const visibleActions = currentActions.length > 0 ? currentActions : newActions;
  const inserted = new Set(newActions.filter((action) => !oldActions.includes(action)));
  const removed = oldActions.filter((action) => !newActions.includes(action));
  const phaseClass = diff.phase === 'mark' ? 'marked' : diff.phase === 'type' ? 'typing' : 'settled';
  if (visibleActions.length === 0 && removed.length === 0) return <span className={ACTION_EMPTY_CLASS}>-</span>;
  return (
    <div className={ACTION_LIST_CLASS}>
      {removed.map((action, index) => (
        <ActionChip
          action={action}
          toolDescriptions={toolDescriptions}
          toolStatuses={toolStatuses}
          className="removed"
          key={`removed_${action}_${index}`}
        />
      ))}
      {visibleActions.map((action, index) => (
        <ActionChip
          action={action}
          toolDescriptions={toolDescriptions}
          toolStatuses={toolStatuses}
          className={inserted.has(action) ? `added ${phaseClass}` : ''}
          key={`${action}_${index}`}
        />
      ))}
    </div>
  );
}

/** 只读动作列表，空列表显示短横线。 */
function ActionList({
  actions,
  toolDescriptions,
  toolStatuses,
}: {
  actions: string[];
  toolDescriptions: ToolDescriptionMap;
  toolStatuses: ToolStatusMap;
}) {
  if (actions.length === 0) return <span className={ACTION_EMPTY_CLASS}>-</span>;
  return (
    <div className={ACTION_LIST_CLASS}>
      {actions.map((action, index) => (
        <ActionChip
          action={action}
          toolDescriptions={toolDescriptions}
          toolStatuses={toolStatuses}
          key={`${action}_${index}`}
        />
      ))}
    </div>
  );
}

/** 动作标签：展示动作中文名，工具动作附带工具状态与说明。 */
function ActionChip({
  action,
  toolDescriptions,
  toolStatuses,
  className = '',
  editable = false,
}: {
  action: string;
  toolDescriptions: ToolDescriptionMap;
  toolStatuses: ToolStatusMap;
  className?: string;
  editable?: boolean;
}) {
  const toolName = toolNameFromAction(action);
  const description = toolName ? toolDescriptions[toolName] || '当前工具配置中暂无描述' : '';
  const status = toolName ? toolStatuses[toolName] || 'incomplete' : '';
  const variant = className.includes('removed')
    ? 'removed'
    : className.includes('added')
      ? className.includes('typing')
        ? 'typing'
        : className.includes('settled')
          ? 'settled'
          : 'added'
      : undefined;

  return (
    <span
      className={cn(actionChipClass({ toolName: toolName || undefined, status, variant }), editable && 'pr-[26px]')}
      title={description || undefined}
    >
      {actionLabel(action)}
    </span>
  );
}

/** 保存弹窗的差异值：动作字段渲染增删列表，其他字段渲染文本行。 */
function SaveReviewDiffValue({
  diff,
  toolDescriptions,
  toolStatuses,
}: {
  diff: TextDiffAnimation;
  toolDescriptions: ToolDescriptionMap;
  toolStatuses: ToolStatusMap;
}) {
  if (diff.field === 'allowed_actions') {
    const removedActions = actionsFromDiffText(diffFullOldValue(diff));
    const insertedActions = actionsFromDiffText(diffFullNewValue(diff));
    return (
      <>
        {diff.removed && (
          <div className={cn(SAVE_REVIEW_ACTION_DIFF_CLASS, SAVE_REVIEW_ACTION_DIFF_OLD_CLASS)}>
            <span className={cn(SAVE_REVIEW_DIFF_SIGN_CLASS, SAVE_REVIEW_DIFF_SIGN_OLD_CLASS)}>-</span>
            <ActionList actions={removedActions} toolDescriptions={toolDescriptions} toolStatuses={toolStatuses} />
          </div>
        )}
        {diff.inserted && (
          <div className={cn(SAVE_REVIEW_ACTION_DIFF_CLASS, SAVE_REVIEW_ACTION_DIFF_NEW_CLASS)}>
            <span className={cn(SAVE_REVIEW_DIFF_SIGN_CLASS, SAVE_REVIEW_DIFF_SIGN_NEW_CLASS)}>+</span>
            <ActionList actions={insertedActions} toolDescriptions={toolDescriptions} toolStatuses={toolStatuses} />
          </div>
        )}
      </>
    );
  }
  return (
    <>
      {diff.removed && <div><span className={DIFF_OLD_CLASS}>- {diff.removed}</span></div>}
      {diff.inserted && <div><span className={DIFF_NEW_CLASS}>+ {diff.inserted}</span></div>}
    </>
  );
}

/** 就地差异文本：按动画阶段展示删除片段或逐字回放新增片段。 */
function InlineDiffText({
  path,
  field,
  value,
  diffs,
}: {
  path: string;
  field: string;
  value: string;
  diffs: TextDiffAnimation[];
}): ReactNode {
  const diff = diffs.find((item) => item.path === path && item.field === field);
  if (!diff) return value;
  if (diff.phase === 'mark') {
    return (
      <>
        {diff.prefix}
        {diff.removed ? <span className={INLINE_REMOVE_CLASS}>{diff.removed}</span> : null}
        {diff.suffix}
      </>
    );
  }
  const typedInsert = diff.inserted.slice(0, Math.ceil(diff.inserted.length * diff.progress));
  return (
    <>
      {diff.prefix}
      {typedInsert ? <span className={cn(INLINE_ADD_CLASS, diff.phase === 'settled' && INLINE_ADD_SETTLED_CLASS)}>{typedInsert}</span> : null}
      {diff.suffix}
    </>
  );
}

/** 还原差异条目对应的完整旧文本。 */
function diffFullOldValue(diff: TextDiffAnimation): string {
  return `${diff.prefix}${diff.removed}${diff.suffix}`;
}

/** 还原差异条目对应的完整新文本。 */
function diffFullNewValue(diff: TextDiffAnimation): string {
  return `${diff.prefix}${diff.inserted}${diff.suffix}`;
}

/** 把差异文本拆成动作数组，兼容顿号、逗号与换行分隔。 */
function actionsFromDiffText(value: string): string[] {
  const normalized = value.replace(/`/g, '').trim();
  if (!normalized || normalized === '-') return [];
  return normalized
    .split(/[、,\n]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

/** 从用户输入构造首次生成请求，标题用默认名称，正文取去空白文本。 */
function parseInitialSkillPrompt(text: string): { title: string; raw_content: string } {
  return { title: '新SOP', raw_content: text.trim() };
}

/** 生成流式解析期间使用的种子草稿，让预览区先有骨架。 */
function createStreamingDraftSeed(payload: { title: string; raw_content: string }): SkillCard {
  return {
    skill_id: `skill_${slugSegment(payload.title) || 'preview'}`,
    name: payload.title || '新SOP',
    version: '1.0.0',
    business_domain: '',
    description: payload.raw_content.slice(0, 120),
    capability_scope: 'general',
    trigger_intents: [],
    user_utterance_examples: [],
    goal: [],
    required_info: [],
    response_rules: [],
    nodes: [],
    edges: [],
    start_node_id: '',
    terminal_node_ids: [],
    interruption_policy: {},
  };
}

/**
 * 根据已接收的流式文本增量更新草稿预览。
 *
 * - 能完整解析成 JSON 时直接返回解析结果。
 * - 解析不完整时按字段与节点片段尽力还原，并补出线性流转边。
 */
function previewSkillFromStream(
  streamText: string,
  previous: SkillCard,
  payload: { title: string; raw_content: string },
): SkillCard {
  const parsed = parseCompleteStreamSkill(streamText);
  if (parsed) return parsed;
  const source = extractDraftSkillSource(streamText);
  const next = cloneSkill(previous || createStreamingDraftSeed(payload));
  applyStringPreview(next, source, 'skill_id');
  applyStringPreview(next, source, 'name');
  applyStringPreview(next, source, 'version');
  applyStringPreview(next, source, 'business_domain');
  applyStringPreview(next, source, 'description');
  applyArrayPreview(next, source, 'trigger_intents');
  applyArrayPreview(next, source, 'user_utterance_examples');
  applyArrayPreview(next, source, 'goal');
  applyArrayPreview(next, source, 'required_info');
  applyArrayPreview(next, source, 'response_rules');
  const nodes = extractNodePreview(source);
  if (nodes.length > 0) {
    next.nodes = nodes;
    next.start_node_id = String(nodes[0]?.node_id || '');
    next.terminal_node_ids = nodes.length > 0 ? [String(nodes[nodes.length - 1]?.node_id || '')].filter(Boolean) : [];
    next.edges = nodes.slice(0, -1).map((node, index) => ({
      source_node_id: String(node.node_id || ''),
      next_node_id: String(nodes[index + 1]?.node_id || ''),
      condition: '',
      priority: index,
      label: '',
    })).filter((edge) => edge.source_node_id && edge.next_node_id);
  }
  return next;
}

/** 尝试把流式文本解析为完整草稿，解析失败时返回 null。 */
function parseCompleteStreamSkill(streamText: string): SkillCard | null {
  try {
    const parsed = JSON.parse(extractJsonCandidate(streamText)) as Record<string, unknown>;
    const draft = isRecord(parsed.draft_skill) ? parsed.draft_skill : parsed;
    if (!isRecord(draft)) return null;
    return {
      skill_id: stringValue(draft.skill_id, 'skill_preview'),
      name: stringValue(draft.name, '新SOP'),
      version: stringValue(draft.version, '1.0.0'),
      business_domain: stringValue(draft.business_domain, ''),
      description: stringValue(draft.description, ''),
      capability_scope: normalizeCapabilityScope(draft.capability_scope),
      step_timeout_seconds:
        typeof draft.step_timeout_seconds === 'number'
          ? draft.step_timeout_seconds
          : null,
      trigger_intents: asStringList(draft.trigger_intents),
      user_utterance_examples: asStringList(draft.user_utterance_examples),
      goal: asStringList(draft.goal),
      required_info: asStringList(draft.required_info),
      response_rules: asStringList(draft.response_rules),
      nodes: Array.isArray(draft.nodes) ? draft.nodes.filter(isRecord).map(normalizeNodePreview) : [],
      edges: Array.isArray(draft.edges) ? draft.edges.filter(isRecord).map(normalizeEdgePreview) : [],
      start_node_id: stringValue(draft.start_node_id, ''),
      terminal_node_ids: asStringList(draft.terminal_node_ids),
      interruption_policy: isRecord(draft.interruption_policy) ? stringRecord(draft.interruption_policy) : {},
    };
  } catch {
    return null;
  }
}

/** 截取 draft_skill 对象起点之后的源码片段，便于逐字段预览。 */
function extractDraftSkillSource(streamText: string): string {
  const fieldIndex = streamText.indexOf('"draft_skill"');
  if (fieldIndex < 0) return streamText;
  const objectStart = streamText.indexOf('{', fieldIndex);
  if (objectStart < 0) return streamText.slice(fieldIndex);
  return streamText.slice(objectStart);
}

/** 截取文本中最外层的 JSON 对象片段。 */
function extractJsonCandidate(streamText: string): string {
  const stripped = streamText.trim();
  const start = stripped.indexOf('{');
  const end = stripped.lastIndexOf('}');
  return start >= 0 && end >= start ? stripped.slice(start, end + 1) : stripped;
}

/** 预览模式下把单个字符串字段写入草稿。 */
function applyStringPreview(skill: SkillCard, source: string, field: keyof SkillCard): void {
  const value = extractJsonStringField(source, String(field));
  if (value !== null) {
    (skill as unknown as Record<string, unknown>)[field] = value;
  }
}

/** 预览模式下把单个字符串数组字段写入草稿。 */
function applyArrayPreview(skill: SkillCard, source: string, field: keyof SkillCard): void {
  const value = extractJsonStringArrayField(source, String(field));
  if (value !== null) {
    (skill as unknown as Record<string, unknown>)[field] = value;
  }
}

/** 从流式源码的 nodes 字段中提取并解析节点片段。 */
function extractNodePreview(source: string): Array<Record<string, unknown>> {
  const fragments = extractObjectFragmentsFromArrayField(source, 'nodes');
  return fragments
    .map((fragment, index) => parseNodeFragment(fragment, index))
    .filter((node): node is Record<string, unknown> => Boolean(node));
}

/** 解析单个节点片段：优先整体按 JSON 解析，失败则逐字段提取。 */
function parseNodeFragment(fragment: string, index: number): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(fragment) as unknown;
    if (isRecord(parsed)) return normalizeNodePreview(parsed, index);
  } catch {
    // Partial object: fall through to field extraction.
  }
  const nodeId = extractJsonStringField(fragment, 'node_id') || '';
  const type = extractJsonStringField(fragment, 'type') || 'collect_info';
  const name = extractJsonStringField(fragment, 'name') || '';
  const instruction = extractJsonStringField(fragment, 'instruction') || '';
  const condition = extractJsonStringField(fragment, 'condition') || '';
  const subSopId = extractJsonStringField(fragment, 'sub_sop_id') || '';
  const assigneeUserId = extractJsonStringField(fragment, 'assignee_user_id') || '';
  const assigneeNotifyChannel = extractJsonStringField(fragment, 'assignee_notify_channel') || '';
  const expectedUserInfo = extractJsonStringArrayField(fragment, 'expected_user_info') || [];
  const allowedActions = extractJsonStringArrayField(fragment, 'allowed_actions') || [];
  const generalSkillIds = extractJsonStringArrayField(fragment, 'general_skill_ids') || [];
  const toolIds = extractJsonStringArrayField(fragment, 'tool_ids') || [];
  const knowledgeBaseIds = extractJsonStringArrayField(fragment, 'knowledge_base_ids') || [];
  const requiredGeneralSkillIds = extractJsonStringArrayField(fragment, 'required_general_skill_ids') || [];
  const requiredToolIds = extractJsonStringArrayField(fragment, 'required_tool_ids') || [];
  const requiredKnowledgeBaseIds = extractJsonStringArrayField(fragment, 'required_knowledge_base_ids') || [];
  if (
    !nodeId
    && !name
    && !instruction
    && expectedUserInfo.length === 0
    && allowedActions.length === 0
    && generalSkillIds.length === 0
    && toolIds.length === 0
    && knowledgeBaseIds.length === 0
  ) {
    return null;
  }
  return {
    node_id: nodeId || `node_${index + 1}`,
    type,
    name: name || nodeId || `节点 ${index + 1}`,
    instruction,
    optional: false,
    condition,
    sub_sop_id: subSopId,
    expected_user_info: expectedUserInfo,
    allowed_actions: allowedActions,
    capability_refs: {
      general_skill_ids: generalSkillIds,
      tool_ids: toolIds,
      knowledge_base_ids: knowledgeBaseIds,
      required_general_skill_ids: requiredGeneralSkillIds,
      required_tool_ids: requiredToolIds,
      required_knowledge_base_ids: requiredKnowledgeBaseIds,
    },
    knowledge_scope: {},
    retry_policy: {},
    metadata: {},
    assignee_user_id: assigneeUserId || null,
    assignee_notify_channel: assigneeNotifyChannel || null,
  };
}

/** 归一化预览节点：补齐默认值并统一能力引用结构。 */
function normalizeNodePreview(node: Record<string, unknown>, index = 0): Record<string, unknown> {
  const nodeId = stringValue(node.node_id, `node_${index + 1}`);
  const capabilityRefs = nodeCapabilityRefs(node);
  const assigneeUserId = stringValue(node.assignee_user_id, '');
  const assigneeNotifyChannel = stringValue(node.assignee_notify_channel, '');
  return {
    node_id: nodeId,
    type: stringValue(node.type, 'collect_info'),
    name: stringValue(node.name, nodeId),
    instruction: stringValue(node.instruction, ''),
    optional: Boolean(node.optional),
    condition: stringValue(node.condition, ''),
    expected_user_info: asStringList(node.expected_user_info),
    allowed_actions: asStringList(node.allowed_actions),
    capability_refs: capabilityRefs,
    knowledge_scope: isRecord(node.knowledge_scope) ? node.knowledge_scope : {},
    retry_policy: isRecord(node.retry_policy) ? node.retry_policy : {},
    metadata: isRecord(node.metadata) ? node.metadata : {},
    sub_sop_id: stringValue(node.sub_sop_id, ''),
    assignee_user_id: assigneeUserId || null,
    assignee_notify_channel: assigneeNotifyChannel || null,
  };
}

/** 归一化预览边：补齐字段与优先级默认值。 */
function normalizeEdgePreview(edge: Record<string, unknown>, index = 0): Record<string, unknown> {
  return {
    source_node_id: stringValue(edge.source_node_id, ''),
    next_node_id: stringValue(edge.next_node_id, ''),
    condition: stringValue(edge.condition, ''),
    priority: Number(edge.priority || index),
    label: stringValue(edge.label, ''),
  };
}

/** 从源码中提取指定字符串字段的值，字段缺失时返回 null。 */
function extractJsonStringField(source: string, field: string): string | null {
  const match = new RegExp(`"${escapeRegExp(field)}"\\s*:\\s*"((?:\\\\.|[^"\\\\])*)"`).exec(source);
  if (!match) return null;
  return decodeJsonString(match[1]);
}

/** 从源码中提取指定字符串数组字段，字段缺失时返回 null。 */
function extractJsonStringArrayField(source: string, field: string): string[] | null {
  const start = findFieldValueStart(source, field);
  if (start === null) return null;
  const arrayStart = skipWhitespace(source, start);
  if (source[arrayStart] !== '[') return null;
  const arrayEnd = findBalancedEnd(source, arrayStart, '[', ']');
  const arrayText = arrayEnd === null ? source.slice(arrayStart + 1) : source.slice(arrayStart, arrayEnd + 1);
  if (arrayEnd !== null) {
    try {
      const parsed = JSON.parse(arrayText) as unknown;
      return asStringList(parsed);
    } catch {
      return extractQuotedJsonStrings(arrayText);
    }
  }
  return extractQuotedJsonStrings(arrayText);
}

/** 从数组字段中按大括号配对截取对象片段，兼容未闭合的流式文本。 */
function extractObjectFragmentsFromArrayField(source: string, field: string): string[] {
  const start = findFieldValueStart(source, field);
  if (start === null) return [];
  const arrayStart = skipWhitespace(source, start);
  if (source[arrayStart] !== '[') return [];
  const fragments: string[] = [];
  let objectStart = -1;
  let depth = 0;
  let inString = false;
  let escaped = false;
  for (let index = arrayStart + 1; index < source.length; index += 1) {
    const char = source[index];
    if (inString) {
      if (escaped) {
        escaped = false;
      } else if (char === '\\') {
        escaped = true;
      } else if (char === '"') {
        inString = false;
      }
      continue;
    }
    if (char === '"') {
      inString = true;
      continue;
    }
    if (char === '{') {
      if (depth === 0) objectStart = index;
      depth += 1;
      continue;
    }
    if (char === '}') {
      depth -= 1;
      if (depth === 0 && objectStart >= 0) {
        fragments.push(source.slice(objectStart, index + 1));
        objectStart = -1;
      }
      continue;
    }
    if (char === ']' && depth === 0) break;
  }
  if (depth > 0 && objectStart >= 0) {
    fragments.push(source.slice(objectStart));
  }
  return fragments;
}

/** 定位字段值在源码中的起始下标，缺失时返回 null。 */
function findFieldValueStart(source: string, field: string): number | null {
  const match = new RegExp(`"${escapeRegExp(field)}"\\s*:`).exec(source);
  return match ? match.index + match[0].length : null;
}

/** 跳过空白字符，返回下一个非空白字符下标。 */
function skipWhitespace(source: string, start: number): number {
  let index = start;
  while (index < source.length && /\s/.test(source[index])) index += 1;
  return index;
}

/** 从起始括号开始查找配对的结束下标，找不到时返回 null。 */
function findBalancedEnd(source: string, start: number, openChar: string, closeChar: string): number | null {
  let depth = 0;
  let inString = false;
  let escaped = false;
  for (let index = start; index < source.length; index += 1) {
    const char = source[index];
    if (inString) {
      if (escaped) {
        escaped = false;
      } else if (char === '\\') {
        escaped = true;
      } else if (char === '"') {
        inString = false;
      }
      continue;
    }
    if (char === '"') {
      inString = true;
      continue;
    }
    if (char === openChar) depth += 1;
    if (char === closeChar) {
      depth -= 1;
      if (depth === 0) return index;
    }
  }
  return null;
}

/** 提取源码中所有已解码的 JSON 字符串字面量。 */
function extractQuotedJsonStrings(source: string): string[] {
  const values: string[] = [];
  const pattern = /"((?:\\.|[^"\\])*)"/g;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(source))) {
    const value = decodeJsonString(match[1]);
    if (value) values.push(value);
  }
  return values;
}

/** 解析 JSON 字符串转义，解析失败时原样返回。 */
function decodeJsonString(value: string): string {
  try {
    return JSON.parse(`"${value}"`) as string;
  } catch {
    return value;
  }
}

/** 读取字符串字段并去除首尾空白，空值返回默认值。 */
function stringValue(value: unknown, fallback = ''): string {
  return typeof value === 'string' && value.trim() ? value.trim() : fallback;
}

/** 把对象的值统一转成字符串记录。 */
function stringRecord(value: Record<string, unknown>): Record<string, string> {
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, String(item)]));
}

/** 判断值是否为普通对象（排除数组与 null）。 */
function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value));
}

/** 把文本解析为 JSON 对象，非对象或解析失败返回 null。 */
function parseJsonObject(value: string): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(value);
    return isRecord(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

/** 把文本转换为小写下划线形式的 ID 片段，最长 32 字符。 */
function slugSegment(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 32);
}

/** 转义正则元字符，供动态拼接正则使用。 */
function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/** 用反引号包裹列表项并以逗号连接，空值显示短横线。 */
function joinList(values: unknown): string {
  if (Array.isArray(values)) {
    const items = values.map(String).filter(Boolean);
    return items.length > 0 ? items.map((item) => `\`${item}\``).join(', ') : '-';
  }
  if (typeof values === 'string' && values.trim()) return values;
  return '-';
}

/** 用顿号连接列表项，空值显示短横线。 */
function joinPlain(values: unknown): string {
  if (Array.isArray(values)) {
    const items = values.map(String).filter(Boolean);
    return items.length > 0 ? items.join('、') : '-';
  }
  if (typeof values === 'string' && values.trim()) return values;
  return '-';
}

/** 把任意值归一化为字符串数组，空值返回空数组。 */
function asStringList(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(String);
  if (typeof value === 'string' && value.trim()) return [value];
  return [];
}

/** 切换节点类型；离开 handoff 类型时清理转人工动作与处理人配置。 */
export function applyNodeTypeChange(
  node: Record<string, unknown>,
  nextType: string,
): Record<string, unknown> {
  const type = String(nextType || 'collect_info');
  node.type = type;
  // 类型切换离开 handoff 时清理转人工专属配置:动作与处理人只属于 handoff 节点
  if (type !== 'handoff') {
    node.allowed_actions = asStringList(node.allowed_actions)
      .filter((action) => action !== 'handoff_human');
    node.assignee_user_id = null;
    node.assignee_notify_channel = null;
  }
  return node;
}

/** 按节点类型过滤动作选项：非 handoff 节点不提供转人工动作。 */
export function filterActionOptionsForNodeType(options: SelectOption[], type: string): SelectOption[] {
  // 转人工动作只允许出现在 handoff 节点上
  if (String(type || '') === 'handoff') return options;
  return options.filter((option) => option.value !== 'handoff_human');
}

/** 判断当前页面是否存在用户选中的文本。 */
function hasSelectedText(): boolean {
  return Boolean(window.getSelection()?.toString().trim());
}

/** 把文件内容读成 Base64 字符串，分块拼接避免参数超长。 */
async function fileToBase64(file: File): Promise<string> {
  const buffer = await file.arrayBuffer();
  let binary = '';
  const bytes = new Uint8Array(buffer);
  const chunkSize = 0x8000;
  for (let index = 0; index < bytes.length; index += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(index, index + chunkSize));
  }
  return window.btoa(binary);
}

/** 去掉扩展名后的文件名作为标题，空标题回退为「新SOP」。 */
function filenameTitle(filename: string): string {
  return filename.replace(/\.[^.]+$/, '').trim() || '新SOP';
}

const uploadContentMarker = '上传文档内容：';

/** 拼接发送给后端的文本：用户输入加上已就绪附件正文。 */
function buildOutgoingText(input: string, attachments: UploadAttachment[]): string {
  const text = input.trim();
  const attachmentText = attachments
    .filter((item) => item.status === 'ready' && item.text?.trim())
    .map((item) => `文件：${item.name}\n${item.text?.trim() || ''}`)
    .join('\n\n');
  return [text, attachmentText ? `上传文档内容：\n${attachmentText}` : ''].filter(Boolean).join('\n\n');
}

/** 取对话消息的可见文本，用户消息会去掉附件正文标记。 */
function visibleChatContent(item: ChatItem): string {
  if (item.role !== 'user') return item.content;
  return stripUploadContent(item.content || item.outgoingText || '');
}

/** 编辑用户消息时重建 outgoingText，保留原有附件正文。 */
function buildEditedOutgoingText(item: ChatItem, displayText: string): string {
  const source = item.outgoingText || item.content;
  const markerIndex = source.indexOf(uploadContentMarker);
  if (markerIndex < 0) return displayText.trim();
  const uploadContent = source.slice(markerIndex).trim();
  return [displayText.trim(), uploadContent].filter(Boolean).join('\n\n');
}

/** 去掉文本中「上传文档内容：」及其后的附件正文。 */
function stripUploadContent(text: string): string {
  const markerIndex = text.indexOf(uploadContentMarker);
  return (markerIndex >= 0 ? text.slice(0, markerIndex) : text).trim();
}

/** 把已就绪附件转换为用户消息上的附件展示项。 */
function buildDisplayAttachments(attachments: UploadAttachment[]): ChatAttachment[] {
  return attachments
    .filter((item) => item.status === 'ready')
    .map((item) => ({
      id: item.id,
      name: item.name,
      type: attachmentTypeLabel(item.name),
    }));
}

/** 取附件类型的展示标签（扩展名大写），无扩展名回退 FILE。 */
function attachmentTypeLabel(filename: string): string {
  const extension = filename.split('.').pop()?.trim().toUpperCase();
  return extension || 'FILE';
}

/** 把可编辑文本按换行、逗号、顿号拆分成列表。 */
function splitEditableList(value: string): string[] {
  return value
    .split(/\n|,|，|、/)
    .map((item) => item.trim())
    .filter(Boolean);
}

/** 把时间格式化为 HH:mm，非法或空值返回空串。 */
function formatMessageTime(value?: string): string {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
}

/** 复制图标。 */
function CopyGlyph() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <rect x="8" y="4" width="12" height="12" rx="3" />
      <rect x="4" y="8" width="12" height="12" rx="3" />
    </svg>
  );
}

/** 编辑（铅笔）图标。 */
function PencilGlyph() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M5 19l4.2-1 10-10a2.1 2.1 0 0 0-3-3l-10 10L5 19z" />
      <path d="M14.8 5.2l4 4" />
    </svg>
  );
}

/**
 * 归一化后端返回的工具建议列表，补齐默认值与初始状态。
 *
 * - 非数组或名称为空的条目会被丢弃。
 * - 已带探测结果的建议同步初始化探测状态。
 */
function normalizeToolSuggestions(value: unknown): ToolSuggestionItem[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item): item is Record<string, unknown> => isRecord(item))
    .map((item) => ({
      name: String(item.name || '').trim(),
      display_name: typeof item.display_name === 'string' ? item.display_name : undefined,
      description: typeof item.description === 'string' ? item.description : undefined,
      bucket: typeof item.bucket === 'string' && item.bucket.trim() ? item.bucket.trim() : '技能自发现工具',
      tool_type: item.tool_type === 'mcp' ? 'mcp' : item.tool_type === 'a2a' ? 'a2a' : 'http',
      method: typeof item.method === 'string' ? item.method : 'POST',
      url: typeof item.url === 'string' ? item.url : '',
      mcp_config: isRecord(item.mcp_config) ? item.mcp_config : {},
      input_schema: isRecord(item.input_schema) ? item.input_schema : {},
      output_schema: isRecord(item.output_schema) ? item.output_schema : {},
      sample_arguments: isRecord(item.sample_arguments) ? item.sample_arguments : {},
      source_excerpt: typeof item.source_excerpt === 'string' ? item.source_excerpt : undefined,
      probe_result: isRecord(item.probe_result) ? item.probe_result as ToolProbeResponse : undefined,
      reason: typeof item.reason === 'string' ? item.reason : '',
      resolution_status: toolSuggestionResolutionValue(item.resolution_status),
      matched_tool_id: typeof item.matched_tool_id === 'string' ? item.matched_tool_id : undefined,
      matched_tool_name: typeof item.matched_tool_name === 'string' ? item.matched_tool_name : undefined,
      matched_tool_display_name: typeof item.matched_tool_display_name === 'string' ? item.matched_tool_display_name : undefined,
      missing_reason: typeof item.missing_reason === 'string' ? item.missing_reason : undefined,
      status: 'pending' as const,
      probeStatus: isRecord(item.probe_result)
        ? Boolean(item.probe_result.success)
          ? 'success' as const
          : 'error' as const
        : 'idle' as const,
    }))
    .filter((item) => item.name);
}

/** 归一化工具建议的解析状态，未知值按可新增候选处理。 */
function toolSuggestionResolutionValue(value: unknown): ToolSuggestion['resolution_status'] {
  return value === 'existing' || value === 'incomplete' || value === 'new_candidate' ? value : 'new_candidate';
}

/** 读取工具建议的解析状态，缺省为可新增候选。 */
function toolSuggestionResolution(suggestion: ToolSuggestionItem): NonNullable<ToolSuggestion['resolution_status']> {
  return suggestion.resolution_status || 'new_candidate';
}

/** 生成工具建议标题，区分已匹配工具与建议新增工具。 */
function toolSuggestionTitle(suggestion: ToolSuggestionItem): string {
  const label = suggestion.display_name || suggestion.name;
  if (toolSuggestionResolution(suggestion) === 'existing') {
    return `已匹配工具：${suggestion.matched_tool_display_name || label}`;
  }
  return `建议新增工具：${label}`;
}

/** 取工具建议解析状态的中文说明。 */
function toolSuggestionResolutionLabel(suggestion: ToolSuggestionItem): string {
  const status = toolSuggestionResolution(suggestion);
  if (status === 'existing') return '已匹配现有工具';
  if (status === 'incomplete') return '工具信息不足';
  return '可新增候选';
}

/** 取工具建议当前处理状态的中文文案。 */
function toolSuggestionStatusText(suggestion: ToolSuggestionItem): string {
  if (suggestion.status === 'accepted') return '已确认';
  if (suggestion.status === 'created') return '已新增';
  if (suggestion.status === 'rejected') return '已拒绝';
  if (suggestion.probeStatus === 'probing') return '测试中';
  if (suggestion.probe_result?.success) return '测试通过';
  if (suggestion.probe_result && !suggestion.probe_result.success) return '测试失败';
  if (toolSuggestionResolution(suggestion) === 'existing') return '已存在';
  if (toolSuggestionResolution(suggestion) === 'incomplete') return '信息不足';
  return '待新增';
}

/** 取工具建议状态徽标的样式变体。 */
function toolSuggestionStatusClass(suggestion: ToolSuggestionItem): ToolStatusBadgeVariant {
  if (suggestion.status === 'accepted' || suggestion.status === 'created' || suggestion.probe_result?.success || toolSuggestionResolution(suggestion) === 'existing') {
    return 'success';
  }
  if (suggestion.status === 'rejected' || (suggestion.probe_result && !suggestion.probe_result.success)) {
    return 'error';
  }
  if (suggestion.probeStatus === 'probing') return 'running';
  if (toolSuggestionResolution(suggestion) === 'incomplete') return 'muted';
  return 'pending';
}

/** 判断所有可新增候选是否都已确认、创建或拒绝。 */
function toolSuggestionSelectionsComplete(suggestions: ToolSuggestionItem[]): boolean {
  const candidates = suggestions.filter((suggestion) => toolSuggestionResolution(suggestion) === 'new_candidate');
  return candidates.length > 0 && candidates.every((suggestion) =>
    suggestion.status === 'accepted' || suggestion.status === 'created' || suggestion.status === 'rejected',
  );
}

/** 合并重复的提示文案，重复项收进悬浮提示的完整标题。 */
function compactWarningItems(
  warnings: string[],
  _toolSuggestions: ToolSuggestionItem[] | undefined,
): Array<{ text: string; title: string }> {
  const items: Array<{ text: string; title: string }> = [];
  for (const warning of warnings) {
    const text = warning.trim();
    if (!text) continue;
    const existing = items.find((item) => item.text === text);
    if (existing) {
      existing.title = `${existing.title}\n${warning}`;
      continue;
    }
    items.push({ text, title: warning });
  }
  return items;
}

/**
 * 从 sessionStorage 读取蒸馏会话缓存，损坏时清除并返回 null。
 *
 * - 缺失字段按默认值补全，上传中的附件不会恢复。
 */
function readDistillCache(key: string): DistillCacheSnapshot | null {
  try {
    const raw = window.sessionStorage.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<DistillCacheSnapshot>;
    return {
      draft: parsed.draft || null,
      loadedSkill: parsed.loadedSkill || null,
      lastSavedDraft: parsed.lastSavedDraft || null,
      messages: Array.isArray(parsed.messages) ? parsed.messages : DEFAULT_DISTILL_MESSAGES,
      input: typeof parsed.input === 'string' ? parsed.input : '',
      selectedPaths: normalizeInitialSelectedPaths(
        Array.isArray(parsed.selectedPaths) ? parsed.selectedPaths.map(String) : DEFAULT_TARGET_PATHS,
      ),
      highlightedPaths: Array.isArray(parsed.highlightedPaths) ? parsed.highlightedPaths.map(String) : [],
      updatingPaths: Array.isArray(parsed.updatingPaths) ? parsed.updatingPaths.map(String) : [],
      dirtyPaths: Array.isArray(parsed.dirtyPaths) ? parsed.dirtyPaths.map(String) : [],
      textDiffs: Array.isArray(parsed.textDiffs) ? parsed.textDiffs : [],
      pendingChange: parsed.pendingChange || null,
      viewMode: parsed.viewMode === 'source' ? 'source' : 'flow',
      attachments: Array.isArray(parsed.attachments)
        ? parsed.attachments.filter((item): item is UploadAttachment => isRecord(item)).map((item) => ({
            id: String(item.id || `file_${Date.now()}_${Math.random().toString(16).slice(2)}`),
            name: String(item.name || '未命名文件'),
            status: item.status === 'error' ? 'error' : 'ready',
            text: typeof item.text === 'string' ? item.text : undefined,
            error: typeof item.error === 'string' ? item.error : undefined,
          }))
        : [],
      streamStatus: typeof parsed.streamStatus === 'string' ? parsed.streamStatus : '',
      activeJob: isRecord(parsed.activeJob)
        ? {
            jobId: String(parsed.activeJob.jobId || ''),
            kind: parsed.activeJob.kind === 'rewrite' ? 'rewrite' : 'distill',
            assistantId: String(parsed.activeJob.assistantId || ''),
            lastSeq: Number(parsed.activeJob.lastSeq || 0),
            status: typeof parsed.activeJob.status === 'string' ? parsed.activeJob.status : undefined,
            createPayload: isRecord(parsed.activeJob.createPayload)
              ? {
                  title: String(parsed.activeJob.createPayload.title || ''),
                  raw_content: String(parsed.activeJob.createPayload.raw_content || ''),
                }
              : undefined,
            previousDraft: isRecord(parsed.activeJob.previousDraft)
              ? (parsed.activeJob.previousDraft as SkillCard)
              : undefined,
            targets: Array.isArray(parsed.activeJob.targets)
              ? parsed.activeJob.targets.map(String)
              : undefined,
          }
        : null,
    };
  } catch {
    window.sessionStorage.removeItem(key);
    return null;
  }
}

/** 生成新的蒸馏工作区 ID，优先使用 crypto.randomUUID。 */
function createDistillWorkspaceId(): string {
  if (typeof window.crypto?.randomUUID === 'function') {
    return window.crypto.randomUUID();
  }
  return `${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
}

/** 写入蒸馏会话缓存；超出存储配额时静默失败。 */
function writeDistillCache(key: string, snapshot: DistillCacheSnapshot): void {
  try {
    window.sessionStorage.setItem(key, JSON.stringify(snapshot));
  } catch {
    // Cache is best-effort. Large uploaded documents can exceed browser quota.
  }
}

/** 删除蒸馏会话缓存，失败时忽略。 */
function removeDistillCache(key: string): void {
  try {
    window.sessionStorage.removeItem(key);
  } catch {
    // Cache cleanup is best-effort.
  }
}

/** 判断缓存快照是否为空工作区（没有任何草稿或技能）。 */
function isBlankDistillWorkspace(snapshot: DistillCacheSnapshot): boolean {
  return !snapshot.draft && !snapshot.loadedSkill && !snapshot.lastSavedDraft;
}

/** 归一化初始选中路径：只选中「basic」时视为未选择。 */
function normalizeInitialSelectedPaths(paths: string[]): string[] {
  if (paths.length === 1 && paths[0] === 'basic') return [];
  return paths;
}

/** 列出草稿的全部可改写目标路径：基础信息加各节点。 */
function allTargetPaths(skill: SkillCard): string[] {
  return [
    'basic',
    ...skillGraphSteps(skill).map((_step, index) => stepTargetPath(index)),
  ];
}

/** 用最新草稿校正选中路径，过滤已不存在的目标。 */
function reconcileSelectedPaths(paths: string[], skill: SkillCard): string[] {
  if (paths.length === 0) return [];
  const available = allTargetPaths(skill);
  const next = paths.filter((path) => available.includes(path));
  return next.length > 0 ? next : DEFAULT_TARGET_PATHS;
}


/** 合并两批路径并去重。 */
function mergePaths(current: string[], next: string[]): string[] {
  return Array.from(new Set([...current, ...next]));
}

/** 深拷贝草稿（JSON 序列化方式）。 */
function cloneSkill(skill: SkillCard): SkillCard {
  return JSON.parse(JSON.stringify(skill)) as SkillCard;
}

/** 归一化子流程节点：清空指令、字段、动作与能力配置。 */
function normalizeSubflowNode<T extends Record<string, unknown>>(node: T): T {
  if (String(node.type || '') !== 'subflow') return node;
  return {
    ...node,
    instruction: '',
    expected_user_info: [],
    allowed_actions: [],
    capability_refs: {
      general_skill_ids: [],
      tool_ids: [],
      knowledge_base_ids: [],
      required_general_skill_ids: [],
      required_tool_ids: [],
      required_knowledge_base_ids: [],
    },
    knowledge_scope: {},
    retry_policy: {},
  };
}

/** 归一化草稿内的全部子流程节点，返回新草稿。 */
function normalizeSubflowNodes(skill: SkillCard): SkillCard {
  const next = cloneSkill(skill);
  next.nodes = (Array.isArray(next.nodes) ? next.nodes : []).map((node) => (
    normalizeSubflowNode({ ...node })
  ));
  return next;
}

/**
 * 判断把候选 SOP 设为子流程后是否会形成嵌套环。
 *
 * - 沿子流程引用递归查找，命中父 SOP 即判定成环。
 */
function wouldCreateSopNestingCycle(
  parentSkillId: string,
  candidateSkillId: string,
  skills: SkillRead[],
): boolean {
  const byId = new Map(skills.map((item) => [item.skill_id, item]));
  const visiting = new Set<string>();
  /** 递归判断某个 SOP 的子流程链是否回到父 SOP。 */
  const reachesParent = (skillId: string): boolean => {
    if (skillId === parentSkillId) return true;
    if (visiting.has(skillId)) return false;
    visiting.add(skillId);
    const row = byId.get(skillId);
    const childIds = (row?.content.nodes || [])
      .filter((node) => String(node.type || '') === 'subflow')
      .map((node) => String(node.sub_sop_id || '').trim())
      .filter(Boolean);
    return childIds.some(reachesParent);
  };
  return reachesParent(candidateSkillId);
}

/** 把节点能力引用收敛到 capability_refs 并删除遗留的扁平字段。 */
function canonicalizeSkillCapabilityRefs(skill: SkillCard): SkillCard {
  const next = cloneSkill(skill);
  next.nodes = (Array.isArray(next.nodes) ? next.nodes : []).map((node) => {
    const normalized = { ...node, capability_refs: nodeCapabilityRefs(node) };
    const legacyFields = normalized as Record<string, unknown>;
    delete legacyFields.general_skill_ids;
    delete legacyFields.tool_ids;
    delete legacyFields.knowledge_base_ids;
    delete legacyFields.required_general_skill_ids;
    delete legacyFields.required_tool_ids;
    delete legacyFields.required_knowledge_base_ids;
    return normalized;
  });
  return next;
}

/** 生成不冲突的草稿技能 ID：清洗字符后追加时间戳。 */
function uniqueDraftSkillId(skillId: string): string {
  const normalized = (skillId || 'skill')
    .trim()
    .replace(/[^A-Za-z0-9_.-]+/g, '_')
    .replace(/^_+|_+$/g, '')
    || 'skill';
  return `${normalized}_${Date.now().toString(36)}`;
}

/** 生成用于比较的草稿副本，忽略版本号差异。 */
function comparableSkillContent(skill: SkillCard): SkillCard {
  const next = cloneSkill(skill);
  next.version = '';
  return next;
}

/** 判断草稿内容相对已保存内容是否变化（忽略版本号）。 */
function hasSkillContentChanges(targetDraft: SkillCard | null, baseDraft: SkillCard | null): boolean {
  if (!targetDraft) return false;
  if (!baseDraft) return true;
  return JSON.stringify(comparableSkillContent(targetDraft)) !== JSON.stringify(comparableSkillContent(baseDraft));
}

/** 从草稿所有节点中移除指定工具的调用动作。 */
function removeToolActionFromSkill(skill: SkillCard, toolName: string): SkillCard {
  const next = cloneSkill(skill);
  const targetAction = `call_tool:${toolName}`;
  next.nodes = (Array.isArray(next.nodes) ? next.nodes : []).map((node) => ({
    ...node,
    allowed_actions: asStringList(node.allowed_actions).filter((action) => action !== targetAction),
  }));
  return next;
}

/**
 * 把确认新增的工具动作合并进草稿对应节点。
 *
 * - 节点已引用该工具时保持不变，避免重复添加。
 * - 没有现成引用时按改写目标或唯一的工具节点决定落点。
 */
function integrateToolSuggestionsIntoDraft(
  skill: SkillCard,
  suggestions: ToolSuggestionItem[],
  fallbackPaths: string[] = [],
): SkillCard {
  const next = cloneSkill(skill);
  const nodes = normalizeSkillNodes(next);
  const fallbackIndexes = fallbackPaths
    .map(stepIndexFromPath)
    .filter((index): index is number => index !== null && index >= 0 && index < nodes.length);

  suggestions.forEach((suggestion) => {
    const toolName = suggestion.name.trim();
    if (!toolName) return;
    const action = `call_tool:${toolName}`;
    const existingIndexes = nodes
      .map((node, index) => (asStringList(node.allowed_actions).includes(action) ? index : -1))
      .filter((index) => index >= 0);
    const targetIndexes = existingIndexes.length > 0
      ? existingIndexes
      : toolSuggestionTargetIndexes(nodes, suggestion, fallbackIndexes);

    targetIndexes.forEach((nodeIndex) => {
      const node = nodes[nodeIndex];
      const actions = asStringList(node.allowed_actions);
      if (actions.includes(action)) return;
      node.allowed_actions = [...actions, action];
    });
  });

  next.nodes = nodes;
  return next;
}

/** 决定工具动作写入哪些节点：优先改写目标，其次唯一的工具节点。 */
function toolSuggestionTargetIndexes(
  nodes: Array<Record<string, unknown>>,
  _suggestion: ToolSuggestionItem,
  fallbackIndexes: number[],
): number[] {
  const uniqueFallbacks = Array.from(new Set(fallbackIndexes));
  if (uniqueFallbacks.length === 1) return uniqueFallbacks;
  const toolNodeIndexes = nodes
    .map((node, index) => (String(node.type || '') === 'tool_call' ? index : -1))
    .filter((index) => index >= 0);
  if (toolNodeIndexes.length === 1) return toolNodeIndexes;
  return [];
}

/** 深拷贝服务端返回的技能详情。 */
function cloneSkillRead(skill: SkillRead): SkillRead {
  return JSON.parse(JSON.stringify(skill)) as SkillRead;
}

/** 收集历史消息中的回退操作并按关键字段去重。 */
function collectRollbackOperations(messages: ChatItem[]): DistillHistoryOperation[] {
  const operations = messages.flatMap((item) => item.operations || []);
  const relevant = operations.filter((operation) =>
    ['skill_change', 'version_save', 'tool_add'].includes(operation.kind),
  );
  const seen = new Set<string>();
  return relevant.filter((operation) => {
    const key = `${operation.kind}:${operation.skillId || ''}:${operation.version || ''}:${operation.toolId || operation.toolName || ''}:${operation.label}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

/** 构造空草稿骨架，保留节点数量与类型，用于差异动画的首帧对比。 */
function blankSkillForAnimation(skill: SkillCard): SkillCard {
  const blank = cloneSkill(skill);
  blank.skill_id = '';
  blank.name = '';
  blank.version = '';
  blank.business_domain = '';
  blank.description = '';
  blank.capability_scope = 'general';
  blank.trigger_intents = [];
  blank.user_utterance_examples = [];
  blank.goal = [];
  blank.required_info = [];
  blank.response_rules = [];
  blank.nodes = skillGraphSteps(skill).map((step) => ({
    node_id: '',
    type: String(step.type || 'collect_info'),
    name: '',
    instruction: '',
    sub_sop_id: '',
    optional: Boolean(step.optional),
    condition: '',
    expected_user_info: [],
    allowed_actions: [],
    capability_refs: {
      general_skill_ids: [],
      tool_ids: [],
      knowledge_base_ids: [],
      required_general_skill_ids: [],
      required_tool_ids: [],
      required_knowledge_base_ids: [],
    },
    knowledge_scope: {},
    retry_policy: {},
    metadata: {},
  }));
  blank.edges = [];
  blank.start_node_id = '';
  blank.terminal_node_ids = [];
  return blank;
}

/** 计算两次草稿之间发生变化的路径（含 basic 与 graph）。 */
function diffTargetPaths(previousDraft: SkillCard, nextDraft: SkillCard, targetPaths: string[]): string[] {
  const candidates = Array.from(new Set([
    ...targetPaths,
    ...allTargetPaths(previousDraft),
    ...allTargetPaths(nextDraft),
    'graph',
  ]));
  return candidates.filter((path) => sectionSignature(previousDraft, path) !== sectionSignature(nextDraft, path));
}

/** 生成路径对应区块的内容签名，用于判断该区块是否变化。 */
function sectionSignature(skill: SkillCard, path: string): string {
  if (path === 'basic') {
    return JSON.stringify({
      skill_id: skill.skill_id,
      name: skill.name,
      version: skill.version,
      business_domain: skill.business_domain || '',
      description: skill.description,
      step_timeout_seconds: skill.step_timeout_seconds ?? null,
      trigger_intents: skill.trigger_intents || [],
      user_utterance_examples: skill.user_utterance_examples || [],
      goal: skill.goal || [],
      required_info: skill.required_info || [],
      interruption_policy: skill.interruption_policy || {},
      response_rules: skill.response_rules || [],
    });
  }
  if (path === 'graph') {
    return JSON.stringify({
      edges: skill.edges || [],
      start_node_id: skill.start_node_id,
      terminal_node_ids: skill.terminal_node_ids || [],
    });
  }
  const stepIndex = stepIndexFromPath(path);
  if (stepIndex === null) return '';
  return JSON.stringify(skillGraphSteps(skill)[stepIndex] || null);
}

/**
 * 收集指定路径上的文本差异条目，供差异动画与保存弹窗展示。
 *
 * - changedPaths 含 all 时扫描全部目标路径。
 * - 无变化的字段不会生成差异条目。
 */
function collectTextDiffs(previousDraft: SkillCard, nextDraft: SkillCard, changedPaths: string[]): TextDiffAnimation[] {
  const diffs: TextDiffAnimation[] = [];
  const paths = changedPaths.includes('all') ? allTargetPaths(nextDraft) : changedPaths;
  paths.forEach((path) => {
    if (path === 'basic') {
      [
        'skill_id',
        'name',
        'version',
        'business_domain',
        'description',
        'trigger_intents',
        'user_utterance_examples',
        'goal',
        'required_info',
        'response_rules',
      ].forEach((field) => {
        const diff = makeTextDiff(
          path,
          field,
          getDisplayField(previousDraft, path, field),
          getDisplayField(nextDraft, path, field),
        );
        if (diff) diffs.push(diff);
      });
      return;
    }
    const stepIndex = stepIndexFromPath(path);
    if (stepIndex === null) return;
    [
      'step_id',
      'type',
      'condition',
      'name',
      'instruction',
      'expected_user_info',
      'allowed_actions',
      'general_skill_ids',
      'tool_ids',
      'knowledge_base_ids',
    ].forEach((field) => {
      const diff = makeTextDiff(
        path,
        field,
        getDisplayField(previousDraft, path, field),
        getDisplayField(nextDraft, path, field),
      );
      if (diff) diffs.push(diff);
    });
  });
  return diffs;
}

/**
 * 计算单字段的差异片段：截取公共前后缀，中间部分作为删除与新增。
 *
 * - 新旧文本完全相同时返回 null。
 */
function makeTextDiff(path: string, field: string, oldText: string, newText: string): TextDiffAnimation | null {
  if (oldText === newText) return null;
  let prefixLength = 0;
  const maxPrefix = Math.min(oldText.length, newText.length);
  while (prefixLength < maxPrefix && oldText[prefixLength] === newText[prefixLength]) {
    prefixLength += 1;
  }
  let suffixLength = 0;
  const maxSuffix = Math.min(oldText.length - prefixLength, newText.length - prefixLength);
  while (
    suffixLength < maxSuffix &&
    oldText[oldText.length - 1 - suffixLength] === newText[newText.length - 1 - suffixLength]
  ) {
    suffixLength += 1;
  }
  return {
    key: `${path}:${field}`,
    path,
    field,
    prefix: newText.slice(0, prefixLength),
    removed: oldText.slice(prefixLength, oldText.length - suffixLength),
    inserted: newText.slice(prefixLength, newText.length - suffixLength),
    suffix: newText.slice(newText.length - suffixLength),
    phase: 'mark',
    progress: 0,
  };
}

/** 取路径与字段对应的展示文本，数组字段用反引号列表拼接。 */
function getDisplayField(skill: SkillCard, path: string, field: string): string {
  const value =
    path === 'basic'
      ? (skill as unknown as Record<string, unknown>)[field]
      : skillGraphSteps(skill)[stepIndexFromPath(path) ?? -1]?.[field];
  if (Array.isArray(value)) return joinList(value.map(String));
  if (typeof value === 'string') return value;
  return '';
}

/** 把差异动画的中间文本写回草稿，列表字段会被跳过。 */
function setTextField(skill: SkillCard, path: string, field: string, value: string): void {
  if (isListField(field)) return;
  if (path === 'basic') {
    (skill as unknown as Record<string, unknown>)[field] = value;
    return;
  }
  const stepIndex = stepIndexFromPath(path);
  if (stepIndex === null) return;
  if (Array.isArray(skill.nodes) && skill.nodes[stepIndex]) {
    const nodeField = field === 'step_id' ? 'node_id' : field;
    skill.nodes[stepIndex][nodeField] = value;
  }
}

/** 判断字段是否为列表类型（差异动画不逐字回放这类字段）。 */
function isListField(field: string): boolean {
  return [
    'trigger_intents',
    'user_utterance_examples',
    'goal',
    'required_info',
    'response_rules',
    'expected_user_info',
    'allowed_actions',
    'general_skill_ids',
    'tool_ids',
    'knowledge_base_ids',
  ].includes(field);
}

/** 按进度生成逐字回放的中间草稿，进度到 1 时直接返回目标草稿。 */
function typedDraft(previousDraft: SkillCard, nextDraft: SkillCard, diffs: TextDiffAnimation[], progress: number): SkillCard {
  const output = cloneSkill(previousDraft);
  diffs.forEach((diff) => {
    const typedInsert = diff.inserted.slice(0, Math.ceil(diff.inserted.length * progress));
    setTextField(output, diff.path, diff.field, `${diff.prefix}${typedInsert}${diff.suffix}`);
  });
  if (progress >= 1) return cloneSkill(nextDraft);
  return output;
}

/** 递增次版本号并重置修订号，非法版本按 1.0.0 处理。 */
function bumpSkillVersion(version: string): string {
  const parts = version.split('.').map((item) => Number.parseInt(item, 10));
  const major = Number.isFinite(parts[0]) ? parts[0] : 1;
  const minor = Number.isFinite(parts[1]) ? parts[1] : 0;
  return `${major}.${minor + 1}.0`;
}

/** 汇总工具名称到描述的映射，并补充技能建议里提到的工具。 */
function buildToolDescriptionMap(tools: ToolRead[], messages: ChatItem[] = []): ToolDescriptionMap {
  const descriptions = tools.reduce<ToolDescriptionMap>((acc, tool) => {
    acc[tool.name] = [tool.display_name, tool.description].filter(Boolean).join('：') || tool.name;
    return acc;
  }, {});
  messages.flatMap((item) => item.toolSuggestions || []).forEach((suggestion) => {
    const label = suggestion.display_name || suggestion.name;
    descriptions[suggestion.name] = [label, suggestion.description || suggestion.reason].filter(Boolean).join('：') || suggestion.name;
    if (suggestion.matched_tool_name) {
      descriptions[suggestion.matched_tool_name] = descriptions[suggestion.name];
    }
  });
  return descriptions;
}

/** 汇总工具名称到处理状态的映射，并补充技能建议里的新工具。 */
function buildToolStatusMap(tools: ToolRead[], messages: ChatItem[]): ToolStatusMap {
  const statuses = tools.reduce<ToolStatusMap>((acc, tool) => {
    acc[tool.name] = 'existing';
    return acc;
  }, {});
  messages.flatMap((item) => item.toolSuggestions || []).forEach((suggestion) => {
    const resolution = toolSuggestionResolution(suggestion);
    const status: ToolActionStatus =
      resolution === 'existing'
        ? 'existing'
        : resolution === 'incomplete'
          ? 'incomplete'
          : suggestion.status === 'accepted' || suggestion.status === 'created' || suggestion.status === 'rejected'
            ? suggestion.status
            : 'pending';
    statuses[suggestion.name] = status;
    if (suggestion.matched_tool_name) statuses[suggestion.matched_tool_name] = status;
  });
  return statuses;
}

/** 由工具建议构造创建工具的请求体，优先使用探测得到的输出 Schema。 */
function toolPayloadFromSuggestion(suggestion: ToolSuggestionItem, skillId?: string): Record<string, unknown> {
  const outputSchema = suggestion.probe_result?.success && suggestion.probe_result.inferred_output_schema
    ? suggestion.probe_result.inferred_output_schema
    : suggestion.output_schema || {};
  return {
    tenant_id: TENANT_ID,
    name: suggestion.name,
    display_name: suggestion.display_name || suggestion.name,
    description: suggestion.description || suggestion.reason || '',
    bucket: suggestion.bucket || '技能自发现工具',
    tool_type: suggestion.tool_type || 'http',
    method: suggestion.method || 'POST',
    url: suggestion.url || `/api/mock/${suggestion.name.replace(/\./g, '/')}`,
    headers: {},
    auth: {},
    mcp_config: suggestion.tool_type === 'mcp' || suggestion.tool_type === 'a2a' ? suggestion.mcp_config || {} : {},
    input_schema: suggestion.input_schema || {},
    output_schema: outputSchema,
    execution_policy: { timeout_seconds: 8 },
    allowed_skills: skillId ? [skillId] : [],
    enabled: true,
  };
}

/** 由工具建议构造本地工具对象，用于 ID 冲突时复用已有工具。 */
function toolReadFromSuggestion(suggestion: ToolSuggestionItem, skillId?: string): ToolRead {
  const outputSchema = suggestion.probe_result?.success && suggestion.probe_result.inferred_output_schema
    ? suggestion.probe_result.inferred_output_schema
    : suggestion.output_schema || {};
  return {
    id: suggestion.name,
    tenant_id: TENANT_ID,
    name: suggestion.name,
    display_name: suggestion.display_name || suggestion.name,
    description: suggestion.description || suggestion.reason || '',
    bucket: suggestion.bucket || '技能自发现工具',
    tool_type: suggestion.tool_type || 'http',
    method: suggestion.method || 'POST',
    url: suggestion.url || `/api/mock/${suggestion.name.replace(/\./g, '/')}`,
    headers: {},
    auth: {},
    mcp_config: suggestion.tool_type === 'mcp' || suggestion.tool_type === 'a2a' ? suggestion.mcp_config || {} : {},
    input_schema: suggestion.input_schema || {},
    output_schema: outputSchema,
    execution_policy: { timeout_seconds: 8 },
    allowed_skills: skillId ? [skillId] : [],
    enabled: true,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };
}

/** 按工具名新增或更新工具列表项。 */
function upsertToolRead(current: ToolRead[], nextTool: ToolRead): ToolRead[] {
  const exists = current.some((tool) => tool.name === nextTool.name);
  return exists
    ? current.map((tool) => (tool.name === nextTool.name ? { ...tool, ...nextTool, id: nextTool.id || tool.id } : tool))
    : [...current, nextTool];
}

/** 取字段的中文展示名，未登记字段原样返回。 */
function fieldLabel(field: string): string {
  const labels: Record<string, string> = {
    skill_id: '技能 ID',
    name: '名称',
    version: '版本',
    business_domain: '业务域',
    description: '描述',
    capability_scope: '使用范围',
    step_timeout_seconds: '单步运行上限（秒）',
    trigger_intents: '触发意图',
    user_utterance_examples: '示例话术',
    goal: '目标',
    required_info: '必填信息',
    response_rules: '回复规则',
    step_id: '节点 ID',
    type: '节点类型',
    condition: '条件',
    instruction: '节点说明',
    expected_user_info: '期望字段',
    allowed_actions: '允许动作',
    general_skill_ids: 'SOP 技能',
    tool_ids: 'SOP 工具',
    knowledge_base_ids: 'SOP 知识库',
    sub_sop_id: '调用子 SOP',
  };
  return labels[field] || field;
}

/** 从 call_tool: 动作中取出工具名，非工具动作返回空串。 */
function toolNameFromAction(action: string): string {
  return action.startsWith('call_tool:') ? action.replace(/^call_tool:/, '').trim() : '';
}

/** 取动作的展示文案，工具动作前缀为「调用工具：」。 */
function actionLabel(action: string): string {
  const toolName = toolNameFromAction(action);
  if (toolName) return `调用工具：${toolName}`;
  return BASE_ACTION_OPTIONS.find((item) => item.value === action)?.label || action;
}

/** 汇总可用动作选项：基础动作、已有工具与流程中出现的动作去重合并。 */
function buildActionOptions(
  toolDescriptions: ToolDescriptionMap,
  toolStatuses: ToolStatusMap,
  steps: Array<Record<string, unknown>>,
): SelectOption[] {
  const toolNames = Array.from(new Set([
    ...Object.keys(toolDescriptions),
    ...Object.keys(toolStatuses),
  ])).filter(Boolean).sort((a, b) => a.localeCompare(b));
  const toolOptions = toolNames.map((toolName) => ({
    value: `call_tool:${toolName}`,
    label: `调用工具：${toolName}`,
  }));
  const currentActionOptions = steps
    .flatMap((step) => asStringList(step.allowed_actions))
    .filter(Boolean)
    .map((action) => ({ value: action, label: actionLabel(action) }));
  return mergeSelectOptions(BASE_ACTION_OPTIONS, toolOptions, currentActionOptions);
}

/** 依次合并多组选项并按 value 去重，空值忽略。 */
function mergeSelectOptions(...groups: SelectOption[][]): SelectOption[] {
  const seen = new Set<string>();
  const output: SelectOption[] = [];
  groups.flat().forEach((option) => {
    if (!option.value || seen.has(option.value)) return;
    seen.add(option.value);
    output.push(option);
  });
  return output;
}

/** 推断条件编辑器的预设值，空条件视为总是进入，无法匹配时返回自定义。 */
function conditionPresetValue(value: string): string {
  const trimmed = value.trim();
  if (!trimmed || trimmed === 'always' || trimmed === 'true') return '__always__';
  if (CONDITION_PRESET_OPTIONS.some((option) => option.value === trimmed)) return trimmed;
  const naturalMatch = Object.entries(CONDITION_PRESET_TEXT).find(([, text]) => text === trimmed);
  if (naturalMatch) return naturalMatch[0];
  return '__custom__';
}

/** 由预设值生成实际保存的进入条件，总是进入保存为空串。 */
function conditionFromPreset(value: string): string {
  if (value === '__always__') return '';
  return CONDITION_PRESET_TEXT[value] || '';
}

/** 把条件转换为自然语言描述，空值表示总是进入。 */
function conditionNaturalText(value: string): string {
  const trimmed = value.trim();
  if (!trimmed || trimmed === 'always' || trimmed === 'true') return '';
  if (CONDITION_PRESET_TEXT[trimmed]) return CONDITION_PRESET_TEXT[trimmed];
  const presetMatch = Object.entries(CONDITION_PRESET_TEXT).find(([, text]) => text === trimmed);
  if (presetMatch) return presetMatch[1];
  return trimmed;
}

/** 生成条件编辑器的可读说明文案。 */
function conditionReadableText(value: string): string {
  const natural = conditionNaturalText(value);
  return natural ? `模型理解：${natural}。` : '模型理解：没有额外限制，流程可以从这里继续。';
}

/** 生成流转规则下方的进入条件说明。 */
function flowRuleConditionText(value: string): string {
  const natural = conditionNaturalText(value);
  return natural ? `进入条件：${natural}。` : '进入条件：总是进入。';
}

/** 取差异路径的展示名，草稿缺失时直接返回路径。 */
function diffTargetLabel(path: string, skill: SkillCard | null): string {
  if (!skill) return path;
  return targetLabel([path], skill);
}

/** 把若干改写路径转换为可读的区块名称。 */
function targetLabel(paths: string[], skill: SkillCard): string {
  const labels = paths.map((path) => {
    if (path === 'basic') return '基础信息';
    if (path === 'graph') return '流程连线';
    const stepIndex = stepIndexFromPath(path);
    if (stepIndex !== null) {
      const index = stepIndex;
      const step = index >= 0 ? skillGraphSteps(skill)[index] : null;
      return step ? `节点 ${index + 1}：${step.name || step.step_id || path}` : path;
    }
    return path;
  });
  return labels.join('、');
}

/** 由节点下标生成改写目标路径。 */
function stepTargetPath(index: number): string {
  return `nodes[${index}]`;
}

/** 从改写目标路径解析节点下标，非节点路径返回 null。 */
function stepIndexFromPath(path: string): number | null {
  const match = path.match(/^nodes\[(\d+)\]$/);
  return match ? Number(match[1]) : null;
}
