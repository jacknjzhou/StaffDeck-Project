import { UnderlineTabs, type UnderlineTabItem } from '@/components/ui';
import { notify } from '@/components/ui/app-toast';

import IconSearch from '../assets/icons/search.svg?react';

import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { api, TENANT_ID } from '../api/client';
import { isGalleryEmployee, type EnterpriseAuthUser } from '../auth';

import AppHeader from '../components/AppHeader';
import { ConfirmDialog } from '../components/ConfirmDialog';
import EmployeeAvatarEditor from '../components/EmployeeAvatarEditor';
import EmployeeCard from '../components/EmployeeCard';
import EmployeeProfileEditor from '../components/EmployeeProfileEditor';
import TeamCard, { teamLeader } from '../components/TeamCard';
import {
  canManageEmployeeAgent,
  employeeDisplayName,
  employeeDisplayNameWithCreator,
  employeeProfile,
  isMyEmployeeAgent,
  visibleEmployeeAgents,
} from '../employee';
import type { AgentProfileRead, TeamRead } from '../types';

const ENTERPRISE_AGENT_STORAGE_KEY = 'ultrarag_enterprise_agent_scope';

type GalleryScope = 'all' | 'mine' | 'teams' | 'gallery';

/**
 * 数字员工目录页：按「所有员工 / 我的数字员工 / 团队对话 / 数字员工广场」分类浏览并搜索。
 *
 * 挂载时并行拉取员工与团队列表；搜索与分类筛选均在客户端计算，不额外请求后端。
 * 删除当前选中的员工时会改写本地存储并广播作用域变更事件。
 */
export default function EmployeeGalleryPage({
  currentUser,
  isAdmin = false,
  onStartChat,
  onLogout,
}: {
  currentUser?: EnterpriseAuthUser;
  isAdmin?: boolean;
  onStartChat?: (agent: AgentProfileRead) => void | Promise<void>;
  onLogout?: () => void;
}) {
  const [agents, setAgents] = useState<AgentProfileRead[]>([]);
  const [teams, setTeams] = useState<TeamRead[]>([]);
  const [teamsLoadFailed, setTeamsLoadFailed] = useState(false);
  const [loading, setLoading] = useState(false);
  const [avatarAgent, setAvatarAgent] = useState<AgentProfileRead | null>(null);
  const [profileAgent, setProfileAgent] = useState<AgentProfileRead | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<AgentProfileRead | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [startingAgentId, setStartingAgentId] = useState<string | null>(null);
  const [startingTeamId, setStartingTeamId] = useState<string | null>(null);
  const [searchTerm, setSearchTerm] = useState('');
  const [scope, setScope] = useState<GalleryScope>('all');
  const navigate = useNavigate();

  /** 拉取当前租户的员工列表并写入 agents；请求期间维护 loading，失败时弹出错误提示。 */
  async function load() {
    setLoading(true);
    try {
      const rows = await api.get<AgentProfileRead[]>(`/api/enterprise/agents?tenant_id=${TENANT_ID}`);
      setAgents(rows);
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '加载员工失败');
    } finally {
      setLoading(false);
    }
  }

  /**
   * 拉取当前租户的团队列表并写入 teams。
   *
   * 失败时把 teamsLoadFailed 置为 true，供切换到团队标签时重试；成功后清除该标记。
   */
  async function loadTeams() {
    try {
      const rows = await api.get<TeamRead[]>(`/api/enterprise/teams?tenant_id=${TENANT_ID}`);
      setTeams(rows);
      setTeamsLoadFailed(false);
    } catch (error) {
      setTeamsLoadFailed(true);
      notify.error(error instanceof Error ? error.message : '加载团队失败');
    }
  }

  useEffect(() => {
    void load();
    void loadTeams();
  }, []);

  // Keep these tabs aligned with the rest of the app:
  // - 所有员工: employees the current user can access and chat with
  // - 我的数字员工: employees the current user can manage/edit
  // - 团队对话: teams the backend has authorized for the current tenant
  // - 数字员工广场: public employees not already listed as mine
  const availableAgents = useMemo(
    () => visibleEmployeeAgents(agents, currentUser, { activeOnly: true }),
    [agents, currentUser],
  );
  const myEmployees = useMemo(
    () => availableAgents.filter((item) => isMyEmployeeAgent(item, currentUser)),
    [availableAgents, currentUser],
  );
  const galleryEmployees = useMemo(() => {
    const myIds = new Set(myEmployees.map((item) => item.id));
    return availableAgents.filter((item) => isGalleryEmployee(item) && !myIds.has(item.id));
  }, [availableAgents, myEmployees]);

  const scopedEmployees = scope === 'mine'
    ? myEmployees
    : scope === 'gallery'
      ? galleryEmployees
      : availableAgents;

  const filteredEmployees = scopedEmployees.filter((item) => {
    const profile = employeeProfile(item);
    const keyword = searchTerm.trim().toLowerCase();
    if (!keyword) return true;
    return [
      employeeDisplayName(item),
      employeeDisplayNameWithCreator(item),
      profile.roleName,
      item.description || '',
      profile.workStyles.join(' '),
      profile.expertiseTags.join(' '),
    ].some((value) => value.toLowerCase().includes(keyword));
  });

  const filteredTeams = teams.filter((team) => {
    const keyword = searchTerm.trim().toLowerCase();
    if (!keyword) return true;
    return [
      team.name,
      team.description || '',
      teamLeader(team)?.agent_name || '',
    ].some((value) => value.toLowerCase().includes(keyword));
  });

  /**
   * 发起与单个员工的对话：优先调用外部传入的 onStartChat，否则跳转到草稿会话路由。
   *
   * 请求进行中以 startingAgentId 去重，避免重复点击；失败时提示错误并复位状态。
   */
  async function startEmployeeChat(row: AgentProfileRead) {
    if (startingAgentId) return;
    setStartingAgentId(row.id);
    try {
      if (onStartChat) {
        await onStartChat(row);
        return;
      }
      navigate(`/workspace/chat/draft/${row.id}`);
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '发起对话失败');
    } finally {
      setStartingAgentId(null);
    }
  }

  /**
   * 为指定团队创建群聊会话并跳转到该会话。
   *
   * 创建期间以 startingTeamId 去重；后端未返回 session_id 时按失败处理并提示错误。
   */
  async function startTeamChat(team: TeamRead) {
    if (startingTeamId) return;
    setStartingTeamId(team.id);
    try {
      const result = await api.post<{ session_id: string }>(
        `/api/enterprise/teams/${team.id}/tl/session`,
        { tenant_id: TENANT_ID },
      );
      if (!result.session_id) throw new Error('未返回团队群聊');
      navigate(`/workspace/chat/${result.session_id}`);
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '发起团队对话失败');
    } finally {
      setStartingTeamId(null);
    }
  }

  /**
   * 上线 / 下线员工：PUT 更新 status 后重新拉取列表，并广播作用域刷新事件。
   *
   * 只提交 status 与原有 metadata，不改动其它字段；失败时提示错误。
   */
  async function updateStatus(row: AgentProfileRead, status: 'active' | 'archived') {
    try {
      await api.put<AgentProfileRead>(`/api/enterprise/agents/${row.id}`, {
        tenant_id: TENANT_ID,
        status,
        metadata: row.metadata || {},
      });
      notify.success(status === 'active' ? '员工已上线' : '员工已下线');
      await load();
      window.dispatchEvent(new Event('ultrarag-enterprise-agent-scope-refresh'));
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '更新员工状态失败');
    }
  }

  /**
   * 发布 / 下架数字员工广场：只改 metadata 中与广场相关的字段。
   *
   * 发布时写入发布时间与操作人并清除下架痕迹；下架时把两者置空（序列化时被丢弃）。
   * 成功后重新拉取列表并广播作用域刷新事件。
   */
  async function updateGalleryState(row: AgentProfileRead, published: boolean) {
    try {
      const metadata: Record<string, unknown> = {
        ...(row.metadata || {}),
        published_to_gallery: published,
        gallery_published_at: published ? new Date().toISOString() : undefined,
        gallery_published_by: published ? currentUser?.username : undefined,
      };
      if (published) {
        delete metadata.gallery_unpublished_at;
        delete metadata.gallery_unpublished_by;
      }
      await api.put<AgentProfileRead>(`/api/enterprise/agents/${row.id}`, {
        tenant_id: TENANT_ID,
        metadata,
      });
      notify.success(published ? '已发布到广场' : '已从广场下架');
      await load();
      window.dispatchEvent(new Event('ultrarag-enterprise-agent-scope-refresh'));
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '更新广场状态失败');
    }
  }

  /**
   * 确认删除员工：DELETE 成功后刷新列表并广播作用域刷新事件。
   *
   * 若删除的正是本地当前选中的员工，会自动切换到其它在册员工（优先在线）并广播变更；
   * 删除期间以 deleting 标记对话框 loading，失败时保留对话框并提示错误。
   */
  async function confirmDelete() {
    const row = deleteTarget;
    if (!row) return;
    setDeleting(true);
    try {
      await api.delete(`/api/enterprise/agents/${row.id}?tenant_id=${TENANT_ID}`);
      if (window.localStorage.getItem(ENTERPRISE_AGENT_STORAGE_KEY) === row.id) {
        const nextAgent = availableAgents.find((item) => item.id !== row.id && item.status === 'active')
          || availableAgents.find((item) => item.id !== row.id);
        if (nextAgent) {
          window.localStorage.setItem(ENTERPRISE_AGENT_STORAGE_KEY, nextAgent.id);
          window.dispatchEvent(new CustomEvent('ultrarag-enterprise-agent-scope-change', { detail: { agentId: nextAgent.id } }));
        } else {
          window.localStorage.removeItem(ENTERPRISE_AGENT_STORAGE_KEY);
          window.dispatchEvent(new CustomEvent('ultrarag-enterprise-agent-scope-change', { detail: { agentId: '' } }));
        }
      }
      notify.success('员工已删除');
      setDeleteTarget(null);
      await load();
      window.dispatchEvent(new Event('ultrarag-enterprise-agent-scope-refresh'));
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '删除员工失败');
    } finally {
      setDeleting(false);
    }
  }

  /** 用保存后的员工数据替换列表中同 id 项，供头像 / 档案编辑回调使用，避免整表重拉。 */
  function updateAgentInList(row: AgentProfileRead) {
    setAgents((current) => current.map((item) => (item.id === row.id ? row : item)));
  }

  const galleryTabs: UnderlineTabItem<GalleryScope>[] = [
    { value: 'all', label: '所有员工' },
    { value: 'mine', label: '我的数字员工' },
    { value: 'teams', label: '团队对话' },
    { value: 'gallery', label: '数字员工广场' },
  ];

  /** 切换员工分类；若团队列表此前加载失败，则在切到团队标签时重试一次。 */
  function changeScope(nextScope: GalleryScope) {
    setScope(nextScope);
    if (nextScope === 'teams' && teamsLoadFailed) {
      void loadTeams();
    }
  }

  const hasSearchTerm = Boolean(searchTerm.trim());
  const emptyText = hasSearchTerm ? '没有匹配的数字员工' : '暂无数字员工';
  const emptyDescription = hasSearchTerm
    ? '换个关键词，或切换员工分类再试试'
    : '当前分类还没有可用员工';
  const teamsEmptyText = hasSearchTerm ? '没有匹配的团队' : '暂无团队';
  const teamsEmptyDescription = hasSearchTerm
    ? '换个关键词再试试'
    : '请先在管理端创建团队并设置项目领导';

  return (
    <div className="min-h-full box-border px-[48px] pt-[32px] pb-[43px] max-[900px]:px-[16px]" aria-busy={loading}>
      <AppHeader
        onLogout={onLogout}
        userName={currentUser?.username}
        left={(
          <div className="flex h-[50px] w-full items-center gap-[6px] rounded-[20px] bg-white px-[20px] text-[#757F9C] shadow-[0_0_6px_rgba(0,0,0,0.05)]">
            <IconSearch className="size-[20px] shrink-0" />
            <input
              autoComplete="off"
              data-1p-ignore="true"
              data-lpignore="true"
              data-bwignore="true"
              value={searchTerm}
              onChange={(event) => setSearchTerm(event.target.value)}
              placeholder="搜索"
              aria-label="搜索数字员工"
              className="min-w-0 flex-1 border-0 bg-transparent text-[14px] text-[#18181A] outline-none placeholder:text-[#757F9C]"
            />
          </div>
        )}
      />

      <UnderlineTabs
        className="mt-[36px] mb-[16px] max-[560px]:w-full"
        aria-label="数字员工分类"
        value={scope}
        onChange={changeScope}
        items={galleryTabs}
        tabClassName="max-[560px]:min-h-[54px] max-[560px]:w-auto max-[560px]:flex-1 max-[560px]:px-[6px] max-[560px]:text-[12px] max-[560px]:leading-[16px]"
      />

      {scope === 'teams' ? (
        <section aria-label="团队">
          <div className="grid grid-cols-1 content-start gap-[32px] sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4 max-[900px]:gap-[18px]">
            {filteredTeams.map((team) => (
              <TeamCard
                key={team.id}
                team={team}
                agents={agents}
                busy={startingTeamId === team.id}
                onOpen={() => void startTeamChat(team)}
              />
            ))}
            {!filteredTeams.length && (
              <EmployeeGalleryEmptyState title={teamsEmptyText} description={teamsEmptyDescription} />
            )}
          </div>
        </section>
      ) : (
        <div className="grid auto-rows-[minmax(262px,auto)] grid-cols-1 content-start gap-[32px] sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4 max-[900px]:gap-[18px]">
          {filteredEmployees.map((employee) => (
            <EmployeeCard
              key={employee.id}
              employee={employee}
              busy={startingAgentId === employee.id}
              canManage={canManageEmployeeAgent(employee, currentUser)}
              showMenu={false}
              onOpen={() => void startEmployeeChat(employee)}
              onStatus={(status) => void updateStatus(employee, status)}
              onGallery={(published) => void updateGalleryState(employee, published)}
              onDelete={() => setDeleteTarget(employee)}
              onAvatar={() => setAvatarAgent(employee)}
              onEdit={() => setProfileAgent(employee)}
              onChat={() => void startEmployeeChat(employee)}
            />
          ))}
          {!filteredEmployees.length && (
            <EmployeeGalleryEmptyState title={emptyText} description={emptyDescription} />
          )}
        </div>
      )}

      <EmployeeAvatarEditor
        agent={avatarAgent}
        open={Boolean(avatarAgent)}
        onClose={() => setAvatarAgent(null)}
        onSaved={updateAgentInList}
      />
      <EmployeeProfileEditor
        agent={profileAgent}
        open={Boolean(profileAgent)}
        currentUser={currentUser}
        onClose={() => setProfileAgent(null)}
        onSaved={updateAgentInList}
      />
      <ConfirmDialog
        open={Boolean(deleteTarget)}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null);
        }}
        loading={deleting}
        title={`删除员工「${deleteTarget ? employeeDisplayName(deleteTarget) : ''}」？`}
        description="删除后该员工的所有配置将一并移除，操作不可撤销。"
        onConfirm={() => void confirmDelete()}
      />
    </div>
  );
}

/** 员工 / 团队列表的空态占位：渲染搜索图标、标题与补充说明。 */
function EmployeeGalleryEmptyState({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <div className="flex h-[262px] w-full items-center justify-center rounded-[20px] border border-dashed border-[#e4e9f2] bg-[#fbfcfe] px-[24px] text-center">
      <div className="flex max-w-[210px] flex-col items-center">
        <span className="grid size-[34px] place-items-center rounded-[12px] bg-white text-[#98a2b3] shadow-[0_1px_8px_rgba(70,76,94,0.06)] ring-1 ring-[#edf1f6]">
          <IconSearch className="size-[16px] shrink-0" />
        </span>
        <p className="mt-[12px] text-[14px] font-medium leading-[20px] text-[#7f879a]">
          {title}
        </p>
        <p className="mt-[4px] text-[11px] leading-[17px] text-[#a7adbb]">
          {description}
        </p>
      </div>
    </div>
  );
}
