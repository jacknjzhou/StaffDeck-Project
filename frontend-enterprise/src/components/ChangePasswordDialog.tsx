import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
  Label,
  notify,
} from '@/components/ui';
import { KeyRound, LoaderCircle } from 'lucide-react';
import { useEffect, useState, type FormEvent } from 'react';

import { api } from '../api/client';

/** 从 ApiError 风格的异常里取 HTTP 状态码,非该类异常返回 undefined。 */
function errorStatus(error: unknown): number | undefined {
  if (typeof error !== 'object' || error === null || !('status' in error)) return undefined;
  const status = Number((error as { status?: unknown }).status);
  return Number.isFinite(status) ? status : undefined;
}

/** 把改密失败翻译成可内联展示的中文文案,未知错误回退到原始 message。 */
function errorMessage(error: unknown): string {
  switch (errorStatus(error)) {
    case 403:
      return '当前密码不正确，请重试';
    case 400:
      return error instanceof Error && error.message.includes('no local password')
        ? '账户未设置本地密码，请联系管理员或使用单点登录'
        : '新密码不能与当前密码相同';
    case 422:
      return '新密码需为 8-128 位';
    case 429:
      return '尝试过于频繁，请稍后再试';
    default:
      return error instanceof Error && error.message
        ? error.message
        : '修改密码失败，请稍后重试';
  }
}

/**
 * 自助改密弹窗：校验当前口令后提交新口令，错误就地展示，成功后关闭。
 *
 * 新口令长度先在本地校验（至少 8 位），避免无谓请求；403/400/422 由服务端
 * 错误码映射为中文提示。提交中禁止关闭，防止重复提交或状态错乱。
 */
export default function ChangePasswordDialog({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) return;
    setCurrentPassword('');
    setNewPassword('');
    setError('');
    setSubmitting(false);
  }, [open]);

  /** 本地校验新口令长度后提交改密请求。 */
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) return;
    setError('');
    const trimmedPassword = newPassword.trim();
    if (trimmedPassword.length < 8) {
      setError('新密码至少 8 位');
      return;
    }
    if (trimmedPassword.length > 128) {
      setError('新密码最多 128 位');
      return;
    }
    setSubmitting(true);
    try {
      await api.post('/api/auth/change-password', {
        current_password: currentPassword,
        new_password: trimmedPassword,
      });
      notify.success('密码已修改');
      onClose();
    } catch (submitError) {
      setError(errorMessage(submitError));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && !submitting) onClose();
      }}
    >
      <DialogContent
        aria-describedby="change-password-description"
        className="w-[calc(100%-2rem)] gap-0 overflow-hidden rounded-[18px] border-0 bg-white p-0 shadow-[0_28px_80px_rgba(24,31,46,0.20)] sm:max-w-[420px]"
      >
        <DialogHeader className="border-b border-[#e9ecf2] px-[26px] py-[22px]">
          <div className="flex items-center gap-[12px]">
            <span className="grid size-[38px] place-items-center rounded-[12px] bg-[#18181a] text-white">
              <KeyRound className="size-[18px]" />
            </span>
            <div>
              <DialogTitle className="text-[16px] font-semibold text-[#18181a]">修改密码</DialogTitle>
              <DialogDescription id="change-password-description" className="mt-[5px] text-[12px] text-[#757f9c]">
                请输入当前密码以确认身份，新密码至少 8 位。
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>

        <form onSubmit={(event) => void submit(event)}>
          <div className="space-y-[16px] px-[26px] py-[22px]">
            <div className="space-y-[7px]">
              <Label htmlFor="change-password-current" className="text-[12px] text-[#464c5e]">
                当前密码
              </Label>
              <Input
                id="change-password-current"
                type="password"
                autoComplete="current-password"
                value={currentPassword}
                onChange={(event) => setCurrentPassword(event.target.value)}
                disabled={submitting}
                aria-invalid={Boolean(error) && !currentPassword}
                className="h-[38px] rounded-[10px] text-[13px]"
              />
            </div>
            <div className="space-y-[7px]">
              <Label htmlFor="change-password-new" className="text-[12px] text-[#464c5e]">
                新密码
              </Label>
              <Input
                id="change-password-new"
                type="password"
                autoComplete="new-password"
                value={newPassword}
                onChange={(event) => setNewPassword(event.target.value)}
                disabled={submitting}
                aria-invalid={Boolean(error)}
                className="h-[38px] rounded-[10px] text-[13px]"
              />
            </div>
            {error && (
              <p role="alert" className="rounded-[10px] bg-[#fff1f1] px-[12px] py-[9px] text-[12px] text-[#bd4141]">
                {error}
              </p>
            )}
          </div>
          <DialogFooter className="border-t border-[#e9ecf2] px-[26px] py-[16px]">
            <Button
              type="button"
              variant="outline"
              disabled={submitting}
              onClick={onClose}
              className="h-[34px] rounded-[10px] px-[14px] text-[12px]"
            >
              取消
            </Button>
            <Button
              type="submit"
              disabled={submitting}
              className="h-[34px] rounded-[10px] bg-[#18181a] px-[14px] text-[12px] text-white hover:bg-[#2b2b2e]"
            >
              {submitting && <LoaderCircle className="size-[14px] animate-spin" />}
              确认修改
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
