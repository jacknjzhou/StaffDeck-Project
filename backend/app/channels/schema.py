from __future__ import annotations

from typing import Optional

from pydantic import BaseModel
from sqlmodel import Session, select

from app.db.models import ChannelBinding, ChannelDelivery, Team, User


class ChannelBindingCreate(BaseModel):
    """创建渠道绑定的请求体:员工挂载与团队绑定二选一,渠道默认 wechat。"""

    tenant_id: str
    # 与 team_id 互斥:挂员工集(现状)或绑一个团队,二选一
    agent_id: Optional[str] = None
    team_id: Optional[str] = None
    channel: str = "wechat"
    # 接入显示名;缺省时后端按「渠道名+YYYYMMDDHHMM」生成默认名
    name: Optional[str] = None


class ChannelBindingAgentRead(BaseModel):
    """绑定上挂载的员工条目(只读):员工 ID、名称与是否该渠道默认员工。"""

    agent_id: str
    name: Optional[str] = None
    is_default: bool = False


class ChannelBindingAgentInput(BaseModel):
    """更新挂载员工集合时的单个员工入参。"""

    agent_id: str
    is_default: bool = False


class WeChatKfAccountRead(BaseModel):
    """微信客服账号的只读视图:客服号、归属员工或团队、同步游标与错误。"""

    open_kfid: str
    name: str = ""
    agent_id: str | None = None
    team_id: str | None = None
    status: str = "active"
    sync_cursor: str = ""
    last_error: str | None = None


class ChannelBindingAgentsUpdate(BaseModel):
    """更新绑定挂载员工与分发开关的请求体;字段缺省(None)表示不改动该项。"""

    # 为 None 时跳过挂载集替换(仅更新开关);为 [] 时报 400 不允许空列表
    agents: Optional[list[ChannelBindingAgentInput]] = None
    # 智能分发开关:不传不动,传则写 config_json.auto_route
    auto_route: Optional[bool] = None
    # 渠道默认人工处理人:不传不动;传 None 清空,传 user_id 写入。
    # SOP 节点未指定 assignee 时回退到此值,再回退到数字员工负责人/管理员。
    default_handoff_assignee_user_id: str | None = "unchanged"
    # 处理人通知渠道:不传不动;None/"web"=网页端收件箱;"feishu" 等绑定渠道=按该渠道转接。
    # 仅在 default_handoff_assignee_user_id 非 unchanged 时生效。
    default_handoff_assignee_channel: str | None = "unchanged"
    # 接入显示名(重命名):不传不动;传非空字符串则更新
    name: Optional[str] = None


class ChannelBindingRead(BaseModel):
    """绑定信息对外视图：只暴露配置元数据，绝不回传凭证明文。"""

    id: str
    tenant_id: str
    agent_id: str
    channel: str
    # 用户可编辑的接入显示名;为空时前端回退展示渠道类型名
    name: Optional[str] = None
    # 团队绑定:非空表示接入某团队(与员工挂载互斥)
    team_id: Optional[str] = None
    team_name: Optional[str] = None
    status: str
    connected: bool
    ilink_bot_id: Optional[str] = None
    baseurl: Optional[str] = None
    bot_id: Optional[str] = None
    corp_id: Optional[str] = None
    open_kfid: Optional[str] = None
    callback_ready: bool = False
    app_id: Optional[str] = None
    client_id: Optional[str] = None
    bot_open_id: Optional[str] = None
    bot_name: Optional[str] = None
    provider_tenant_key: Optional[str] = None
    config_revision: int = 0
    session_expired: bool = False
    bound_at: Optional[str] = None
    created_by_user_id: Optional[str] = None
    created_by_name: Optional[str] = None
    agents: list[ChannelBindingAgentRead] = []
    wechat_kf_accounts: list[WeChatKfAccountRead] = []
    auto_route: bool = True
    # 渠道默认人工处理人(SOP 节点未指定 assignee 时回退到此值)。
    default_handoff_assignee_user_id: Optional[str] = None
    default_handoff_assignee_name: Optional[str] = None
    # 处理人通知渠道:None=默认投递;"web"=仅网页端;"feishu" 等绑定渠道=按该渠道转接。
    default_handoff_assignee_channel: Optional[str] = None
    identity_scope_key: Optional[str] = None
    # 当前请求者对该绑定的管理角色:admin/owner/collaborator;无管理关系时为 None
    my_role: Optional[str] = None
    created_at: str
    updated_at: str


class ChannelQRCodeRead(BaseModel):
    """渠道扫码登录二维码:二维码内容与可选的图片数据。"""

    qrcode: str
    qrcode_img_content: Optional[str] = None


class ChannelQRCodeStatusRead(BaseModel):
    """扫码登录状态的轮询结果;登录成功后附带绑定详情。"""

    status: str
    binding: Optional[ChannelBindingRead] = None


class ChannelBindCodeRead(BaseModel):
    """渠道身份绑定码:一次性绑定码及其过期时间。"""

    code: str
    expires_at: str


class ChannelIdentityBindCodeCreate(BaseModel):
    """为指定用户生成渠道身份绑定码的请求体。"""

    user_id: str


class ChannelBindingManagerCreate(BaseModel):
    """新增绑定协作管理员的请求体:被授权用户 ID。"""

    user_id: str


class ChannelBindingManagerRead(BaseModel):
    """绑定协作管理员的只读视图:被授权用户、授权时间与授权人。"""

    user_id: str
    name: Optional[str] = None
    granted_at: str
    granted_by_user_id: Optional[str] = None
    granted_by_name: Optional[str] = None


class MyIdentityBindingRead(BaseModel):
    """当前用户已绑定的外部渠道身份条目。"""

    channel: str
    external_user_id: str
    display_name: Optional[str] = None
    bound_at: str
    # 渠道账号作用域(前端 scope 标签;wechat 为空串)
    external_account_scope: str = ""


class WeComCredentialsRequest(BaseModel):
    """企业微信凭据提交:企业 ID、Bot ID 与密钥。"""

    tenant_id: str
    bot_id: str
    secret: str
    # 企业 ID 是企微 userid 的真实唯一边界,首次激活即必须提供
    corp_id: str


class WeChatKfCredentialsRequest(BaseModel):
    """微信客服凭据提交:企业 ID、密钥与回调加解密参数。"""

    tenant_id: str
    corp_id: str
    secret: str
    callback_token: str = ""
    encoding_aes_key: str = ""


class WeChatKfCallbackConfigRequest(BaseModel):
    """微信客服回调配置请求:只需企业 ID,用于先生成回调地址与校验凭证。"""

    tenant_id: str
    corp_id: str


class WeChatKfAccountCreateRequest(BaseModel):
    """创建微信客服账号:用上传素材换来的 media_id 向企业申请客服号。"""

    tenant_id: str
    name: str
    media_id: str


class WeChatKfAccountSelectRequest(BaseModel):
    """选用已有微信客服账号(open_kfid)接入当前绑定。"""

    tenant_id: str
    open_kfid: str


class WeChatKfAccountUpdateRequest(BaseModel):
    """修改微信客服账号名称,可选用新素材替换头像。"""

    tenant_id: str
    open_kfid: str
    name: str
    media_id: str | None = None


class FeishuCredentialsRequest(BaseModel):
    """飞书应用凭据:app_id 与 app_secret。"""

    tenant_id: str
    app_id: str
    app_secret: str


class DingTalkCredentialsRequest(BaseModel):
    """钉钉应用凭据:client_id 与 client_secret。"""

    tenant_id: str
    client_id: str
    client_secret: str


class ChannelCredentialFieldRead(BaseModel):
    """渠道凭据字段的表单描述:键、标签、占位符及是否密文/可选。"""

    key: str
    label: str
    placeholder: Optional[str] = None
    secret: bool = False
    optional: bool = False


class ChannelMetaRead(BaseModel):
    """渠道类型元数据:展示名、配置方式、凭据字段与能力清单。"""

    channel: str
    name: str
    setup: str
    credential_fields: list[ChannelCredentialFieldRead] = []
    capabilities: list[str] = []


class ChannelDeliveryRead(BaseModel):
    """渠道消息投递记录视图:投递状态、重试次数与最近错误。"""

    id: str
    binding_id: str
    session_id: str
    message_id: Optional[str] = None
    kind: str
    text: str
    status: str
    attempts: int
    last_error: Optional[str] = None
    delivered_at: Optional[str] = None
    created_at: str


class ChannelConversationRead(BaseModel):
    """渠道会话列表条目:会话归属、消息量与最近一条消息摘要。"""

    session_id: str
    external_conv_id: Optional[str] = None
    display_name: Optional[str] = None
    is_group: bool = False
    agent_id: Optional[str] = None
    agent_name: Optional[str] = None
    message_count: int = 0
    last_message_preview: Optional[str] = None
    updated_at: str


class ChannelConversationAttachmentRead(BaseModel):
    """会话消息附件的精简元数据视图。

    仅暴露展示所需的字段，避免把 data_url(base64)、sandbox_path 等
    内部字段或大体量内容塞进会话列表响应。
    """

    id: str
    filename: str
    content_type: str
    size: int
    kind: str


class ChannelConversationMessageRead(BaseModel):
    """会话内的单条消息:角色、正文与附件元数据。"""

    id: str
    role: str
    content: str
    created_at: str
    attachments: list[ChannelConversationAttachmentRead] | None = None


class ChannelConversationPage(BaseModel):
    """渠道会话分页结果。"""

    items: list[ChannelConversationRead]
    total: int
    offset: int
    limit: int


class ChannelDeliveryPage(BaseModel):
    """渠道投递记录分页结果。"""

    items: list[ChannelDeliveryRead]
    total: int
    offset: int
    limit: int


class ChannelDeliveryDay(BaseModel):
    """按天聚合的投递记录:日期、当日数量与明细。"""

    date: str
    count: int
    items: list[ChannelDeliveryRead]


class ChannelDeliveryDayPage(BaseModel):
    """投递记录按天分页结果。"""

    days: list[ChannelDeliveryDay]
    total_days: int
    offset: int
    limit: int


def channel_binding_agents_read(db: Session, binding: ChannelBinding) -> list[ChannelBindingAgentRead]:
    """挂载员工列表(含存量绑定 legacy 回退),join agent_profiles 取名称。"""
    from app.channels.service_routing import agent_names, mounted_agents

    mounts = mounted_agents(db, binding)
    names = agent_names(db, binding.tenant_id, [mount.agent_id for mount in mounts])
    return [
        ChannelBindingAgentRead(
            agent_id=mount.agent_id,
            name=names.get(mount.agent_id),
            is_default=mount.is_default,
        )
        for mount in mounts
    ]


def channel_binding_creator_name(db: Session, binding: ChannelBinding) -> Optional[str]:
    """创建者展示名;用户已删除或存量绑定无 created_by_user_id 时返回 None。"""
    if not binding.created_by_user_id:
        return None
    user = db.get(User, binding.created_by_user_id)
    if not user:
        return None
    return user.display_name or user.username


def _default_handoff_assignee_name(db: Session, binding: ChannelBinding) -> Optional[str]:
    """渠道默认人工处理人展示名;未配置或用户已删除时返回 None。"""
    config = binding.config_json if isinstance(binding.config_json, dict) else {}
    user_id = str(config.get("default_handoff_assignee_user_id") or "").strip() or None
    if not user_id:
        return None
    user = db.get(User, user_id)
    if not user or user.tenant_id != binding.tenant_id:
        return None
    return user.display_name or user.username


def channel_binding_my_role(
    db: Session, binding: ChannelBinding, current_user: Optional[User]
) -> Optional[str]:
    """当前用户对该绑定的管理角色:admin/owner/collaborator;无管理关系时返回 None。"""
    if current_user is None:
        return None
    from app.db.models import ChannelBindingManager
    from app.security.permissions import is_admin_user

    if is_admin_user(current_user):
        return "admin"
    if binding.created_by_user_id == current_user.id:
        return "owner"
    row = db.exec(
        select(ChannelBindingManager).where(
            ChannelBindingManager.binding_id == binding.id,
            ChannelBindingManager.user_id == current_user.id,
            ChannelBindingManager.revoked_at.is_(None),
        )
    ).first()
    return "collaborator" if row else None


def channel_binding_read(
    db: Session, binding: ChannelBinding, current_user: Optional[User] = None
) -> ChannelBindingRead:
    """组装绑定对外视图:合并 config_json、团队名、客服账号、挂载员工与当前用户角色。"""
    config = dict(binding.config_json or {})
    bound_at = config.get("bound_at")
    team_name: Optional[str] = None
    if binding.team_id:
        team = db.get(Team, binding.team_id)
        team_name = team.name if team else None
    wechat_kf_accounts: list[WeChatKfAccountRead] = []
    if binding.channel == "wechat_kf":
        from app.db.models import WeChatKfAccount

        wechat_kf_accounts = [
            WeChatKfAccountRead(
                open_kfid=row.open_kfid,
                name=row.name,
                agent_id=binding.agent_id if not binding.team_id else None,
                team_id=binding.team_id,
                status=row.status,
                sync_cursor=row.sync_cursor,
                last_error=row.last_error,
            )
            for row in db.exec(
                select(WeChatKfAccount).where(WeChatKfAccount.binding_id == binding.id)
            ).all()
        ]
    identity_scope_key = binding.identity_scope_key
    if not identity_scope_key and binding.channel == "feishu":
        app_id = str(config.get("app_id") or "").strip()
        tenant_key = str(binding.provider_tenant_key or "").strip()
        if app_id and tenant_key:
            from app.channels.service_feishu_inbox import feishu_identity_scope

            identity_scope_key = feishu_identity_scope(app_id, tenant_key)
    return ChannelBindingRead(
        id=binding.id,
        tenant_id=binding.tenant_id,
        agent_id=binding.agent_id,
        channel=binding.channel,
        name=binding.name,
        team_id=binding.team_id,
        team_name=team_name,
        status=binding.status,
        connected=binding.connected,
        ilink_bot_id=config.get("ilink_bot_id"),
        baseurl=config.get("baseurl"),
        bot_id=config.get("bot_id"),
        corp_id=config.get("corp_id"),
        open_kfid=config.get("open_kfid"),
        callback_ready=bool(config.get("callback_ready")),
        app_id=config.get("app_id"),
        client_id=config.get("client_id"),
        bot_open_id=config.get("bot_open_id"),
        bot_name=config.get("bot_name"),
        provider_tenant_key=binding.provider_tenant_key,
        config_revision=binding.config_revision,
        session_expired=bool(config.get("session_expired")),
        bound_at=str(bound_at) if bound_at else None,
        created_by_user_id=binding.created_by_user_id,
        created_by_name=channel_binding_creator_name(db, binding),
        agents=channel_binding_agents_read(db, binding),
        wechat_kf_accounts=wechat_kf_accounts,
        auto_route=(binding.config_json or {}).get("auto_route") is not False,
        default_handoff_assignee_user_id=(binding.config_json or {}).get(
            "default_handoff_assignee_user_id"
        ),
        default_handoff_assignee_name=_default_handoff_assignee_name(db, binding),
        default_handoff_assignee_channel=(
            (binding.config_json or {}).get("default_handoff_assignee_channel")
            if (binding.config_json or {}).get("default_handoff_assignee_user_id")
            else None
        ),
        identity_scope_key=identity_scope_key,
        my_role=channel_binding_my_role(db, binding, current_user),
        created_at=binding.created_at.isoformat(),
        updated_at=binding.updated_at.isoformat(),
    )


def channel_delivery_read(delivery: ChannelDelivery) -> ChannelDeliveryRead:
    """把投递记录行转成对外视图,时间字段统一转 ISO 字符串。"""
    return ChannelDeliveryRead(
        id=delivery.id,
        binding_id=delivery.binding_id,
        session_id=delivery.session_id,
        message_id=delivery.message_id,
        kind=delivery.kind,
        text=delivery.text,
        status=delivery.status,
        attempts=delivery.attempts,
        last_error=delivery.last_error,
        delivered_at=delivery.delivered_at.isoformat() if delivery.delivered_at else None,
        created_at=delivery.created_at.isoformat(),
    )
