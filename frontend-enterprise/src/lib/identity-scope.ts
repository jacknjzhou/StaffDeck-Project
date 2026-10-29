// 飞书身份 scope 由后端 feishu_identity_scope 生成:
// app:{appId长度}:{appId}:tenant:{tenantKey长度}:{tenantKey}
// 长度前缀保证 appId/tenantKey 含冒号时仍可无歧义解析。
/**
 * 从飞书身份 scope 字符串中解析出 appId。
 *
 * scope 由后端 `feishu_identity_scope` 生成，格式为
 * `app:{appId长度}:{appId}:tenant:{tenantKey长度}:{tenantKey}`；
 * 长度前缀保证 appId 含冒号时仍可无歧义解析。
 * @param scope 待解析的身份 scope 字符串。
 * @returns 解析成功时返回 appId；前缀、长度或 tenant 段不合法时返回 null。
 */
export function feishuAppIdFromIdentityScope(scope: string): string | null {
  if (!scope.startsWith('app:')) return null;
  const lengthEnd = scope.indexOf(':', 'app:'.length);
  if (lengthEnd < 0) return null;
  const appIdLength = Number(scope.slice('app:'.length, lengthEnd));
  if (!Number.isInteger(appIdLength) || appIdLength <= 0) return null;
  const appIdStart = lengthEnd + 1;
  const appId = scope.slice(appIdStart, appIdStart + appIdLength);
  if (!appId) return null;
  if (!scope.slice(appIdStart + appIdLength).startsWith(':tenant:')) return null;
  return appId;
}
