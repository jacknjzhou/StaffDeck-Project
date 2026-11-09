import { useState, type KeyboardEvent } from 'react';

import { api, ApiError, TENANT_ID } from '../api/client';
import { setEnterpriseAuthSession, type EnterpriseAuthSession } from '../auth';
import AppHeader from '../components/AppHeader';
import BrandLogo from '../components/BrandLogo';
import IconFieldClear from '../assets/icons/field-clear.svg?react';
import IconFieldEye from '../assets/icons/field-eye.svg?react';
import IconFieldEyeOn from '../assets/icons/field-eye-on.svg?react';
import loginPreview from '../assets/staffdeck/login-preview.png';

export type LoginPageProps = {
  onLogin: (session: EnterpriseAuthSession) => void;
};

/** 未展开态的两个入口按钮共用的尺寸与圆角样式。 */
const entryButtonClass =
  'flex items-center justify-center rounded-[10px] px-[36px] py-[10px] text-[16px] font-normal transition-colors';

/**
 * 未登录态落地/登录页，对应 Figma 节点 68:201（`Login_light`）：
 * 全幅 hero 区展示 StaffDeck 字标，底部固定一张产品预览占位图。
 *
 * - 点击「登录」/「注册」后，对应表单（节点 68:1563）以滑动动画在原 CTA 按钮位置展开。
 * - 组件只负责收集并校验凭据，成功后通过 onLogin 回调上抛会话，不在此处做路由跳转。
 * - 账号状态类 403（待审核/已拒绝）走表单上方的提示条，不塞进字段级「密码错误」。
 */
export default function LoginPage({ onLogin }: LoginPageProps) {
  const [showForm, setShowForm] = useState(false);
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [usernameError, setUsernameError] = useState('');
  const [passwordError, setPasswordError] = useState('');
  const [registerUsername, setRegisterUsername] = useState('');
  const [registerDisplayName, setRegisterDisplayName] = useState('');
  const [registerPassword, setRegisterPassword] = useState('');
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  /** 校验账号密码并调用登录接口，成功后写入本地会话并回调 onLogin；失败时写入字段级错误提示。 */
  async function login() {
    const trimmedUsername = username.trim();
    const trimmedPassword = password.trim();
    setUsernameError(trimmedUsername ? '' : '请输入账号');
    setPasswordError(trimmedPassword ? '' : '请输入密码');
    if (!trimmedUsername || !trimmedPassword) return;

    setLoading(true);
    try {
      const session = await api.post<EnterpriseAuthSession>('/api/auth/login', {
        tenant_id: TENANT_ID,
        username: trimmedUsername,
        password: trimmedPassword,
      });
      setEnterpriseAuthSession(session);
      onLogin(session);
    } catch (error) {
      const code = error instanceof ApiError ? error.code : null;
      // 账号状态不是凭据错误：用提示条说明，字段级错误保持空白。
      if (code === 'ACCOUNT_PENDING_APPROVAL') {
        setUsernameError('');
        setPasswordError('');
        setNotice('账号正在等待管理员审核');
        return;
      }
      if (code === 'ACCOUNT_REGISTRATION_REJECTED') {
        setUsernameError('');
        setPasswordError('');
        setNotice('注册申请未通过，请联系管理员');
        return;
      }
      const messageText = error instanceof Error ? error.message : '登录失败';
      setUsernameError('账号输入错误');
      setPasswordError(messageText || '密码输入错误');
    } finally {
      setLoading(false);
    }
  }

  /** 提交注册申请：成功后切回登录态并提示等待审核，失败时把服务端文案放进提示条。 */
  async function register() {
    const trimmedUsername = registerUsername.trim();
    const trimmedPassword = registerPassword.trim();
    setUsernameError(trimmedUsername ? '' : '请输入账号');
    setPasswordError(trimmedPassword ? '' : '请输入密码');
    if (!trimmedUsername || !trimmedPassword) return;

    setLoading(true);
    try {
      await api.post('/api/auth/register', {
        tenant_id: TENANT_ID,
        username: trimmedUsername,
        display_name: registerDisplayName.trim(),
        password: trimmedPassword,
      });
      setRegisterUsername('');
      setRegisterDisplayName('');
      setRegisterPassword('');
      setMode('login');
      setNotice('注册申请已提交，请等待管理员审核');
    } catch (error) {
      const messageText = error instanceof Error ? error.message : '';
      setNotice(messageText || '操作失败');
    } finally {
      setLoading(false);
    }
  }

  /** 输入框回车快捷提交：仅当按键为 Enter 时复用当前表单的提交逻辑。 */
  function onFieldKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'Enter') void (mode === 'register' ? register() : login());
  }

  /** 切换登录/注册表单，并清掉上一条提示，避免旧文案残留。 */
  function switchMode(next: 'login' | 'register') {
    setShowForm(true);
    setMode(next);
    setUsernameError('');
    setPasswordError('');
    setNotice(null);
  }

  const inputBaseClass =
    'flex h-[44px] w-full items-center gap-[8px] rounded-[10px] border bg-white px-[16px] transition-colors';

  return (
    <div className="relative flex min-h-screen flex-col bg-white">
      <AppHeader
        className="h-[60px] shrink-0 px-[32px]"
        left={<BrandLogo markSize={28} />}
        right={null}
      />

      <main className="flex flex-1 flex-col items-center px-[32px]">
        <div className="flex flex-col items-center pt-[60px]">
          <span className="flex items-center justify-center rounded-[10px] border-[0.5px] border-[#e3e7f1] bg-[#f6f6f6] px-[20px] py-[6px] text-[14px] text-[#464c5e]">
            我们来做什么？
          </span>
          <h1 className="mt-[6px] text-center text-[54px] font-semibold leading-[80px] tracking-[1.08px] text-[#18181a]">
            StaffDeck
            <br />
            数字员工运营平台
          </h1>

          {!showForm ? (
            <div className="mt-[24px] flex items-center gap-[12px]">
              <button
                type="button"
                onClick={() => switchMode('login')}
                className={`${entryButtonClass} bg-[#18181a] text-white hover:bg-[#18181a]/90`}
              >
                登录
              </button>
              <button
                type="button"
                onClick={() => switchMode('register')}
                className={`${entryButtonClass} border-[0.5px] border-[#e3e7f1] bg-white text-[#18181a] hover:border-[#cbd3e6]`}
              >
                注册
              </button>
            </div>
          ) : (
            <div className="mt-[24px] flex w-[320px] flex-col duration-300 ease-out animate-in fade-in slide-in-from-top-4">
              {notice && (
                <div
                  role="status"
                  className="mb-[16px] rounded-[10px] border-[0.5px] border-[#e3e7f1] bg-[#f6f6f6] px-[12px] py-[8px] text-[13px] leading-[18px] text-[#464c5e]"
                >
                  {notice}
                </div>
              )}

              {mode === 'register' ? (
                <form
                  className="flex w-full flex-col"
                  onSubmit={(event) => {
                    event.preventDefault();
                    void register();
                  }}
                >
                  <div
                    className={`${inputBaseClass} ${usernameError ? 'border-[#f54a45]' : registerUsername ? 'border-[#18181a]' : 'border-[#e3e7f1]'}`}
                  >
                    <input
                      value={registerUsername}
                      autoComplete="username"
                      placeholder="请输入账号"
                      aria-label="账号"
                      onChange={(event) => {
                        setRegisterUsername(event.target.value);
                        if (usernameError) setUsernameError('');
                        if (notice) setNotice(null);
                      }}
                      onKeyDown={onFieldKeyDown}
                      className="min-w-0 flex-1 border-0 bg-transparent text-[14px] text-[#18181a] outline-none placeholder:text-[#757f9c]"
                    />
                  </div>

                  <div
                    className={`mt-[16px] ${inputBaseClass} ${registerDisplayName ? 'border-[#18181a]' : 'border-[#e3e7f1]'}`}
                  >
                    <input
                      value={registerDisplayName}
                      autoComplete="name"
                      placeholder="显示名"
                      aria-label="显示名"
                      onChange={(event) => {
                        setRegisterDisplayName(event.target.value);
                        if (notice) setNotice(null);
                      }}
                      onKeyDown={onFieldKeyDown}
                      className="min-w-0 flex-1 border-0 bg-transparent text-[14px] text-[#18181a] outline-none placeholder:text-[#757f9c]"
                    />
                  </div>

                  <div
                    className={`mt-[16px] ${inputBaseClass} ${passwordError ? 'border-[#f54a45]' : registerPassword ? 'border-[#18181a]' : 'border-[#e3e7f1]'}`}
                  >
                    <input
                      value={registerPassword}
                      type="password"
                      autoComplete="new-password"
                      placeholder="请输入密码"
                      aria-label="密码"
                      onChange={(event) => {
                        setRegisterPassword(event.target.value);
                        if (passwordError) setPasswordError('');
                        if (notice) setNotice(null);
                      }}
                      onKeyDown={onFieldKeyDown}
                      className="min-w-0 flex-1 border-0 bg-transparent text-[14px] text-[#18181a] outline-none placeholder:text-[#757f9c]"
                    />
                  </div>

                  <button
                    type="submit"
                    disabled={loading}
                    className="mt-[24px] flex h-[40px] items-center justify-center self-center rounded-[10px] bg-[#18181a] px-[24px] text-[16px] font-normal text-white transition-colors hover:bg-[#18181a]/90 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {loading ? '提交中…' : '提交注册申请'}
                  </button>

                  <button
                    type="button"
                    onClick={() => switchMode('login')}
                    className="mt-[16px] self-center text-[14px] text-[#757f9c] outline-none transition-colors hover:text-[#18181a]"
                  >
                    已有账号？去登录
                  </button>
                </form>
              ) : (
                <form
                  className="flex w-full flex-col"
                  onSubmit={(event) => {
                    event.preventDefault();
                    void login();
                  }}
                >
                  <div
                    className={`${inputBaseClass} ${usernameError ? 'border-[#f54a45]' : username ? 'border-[#18181a]' : 'border-[#e3e7f1]'}`}
                  >
                    <input
                      value={username}
                      autoComplete="username"
                      placeholder="请输入账号（首次使用请输入admin）"
                      aria-label="账号"
                      onChange={(event) => {
                        setUsername(event.target.value);
                        if (usernameError) setUsernameError('');
                        if (notice) setNotice(null);
                      }}
                      onKeyDown={onFieldKeyDown}
                      className="min-w-0 flex-1 border-0 bg-transparent text-[14px] text-[#18181a] outline-none placeholder:text-[#757f9c]"
                    />
                    {username && (
                      <button
                        type="button"
                        aria-label="清空账号"
                        onClick={() => {
                          setUsername('');
                          setUsernameError('');
                        }}
                        className="grid size-[18px] shrink-0 place-items-center text-[#667085] outline-none transition-colors hover:text-[#464c5e]"
                      >
                        <IconFieldClear className="size-[18px]" />
                      </button>
                    )}
                  </div>

                  <div
                    className={`mt-[24px] ${inputBaseClass} ${passwordError ? 'border-[#f54a45]' : password ? 'border-[#18181a]' : 'border-[#e3e7f1]'}`}
                  >
                    <input
                      value={password}
                      type={showPassword ? 'text' : 'password'}
                      autoComplete="current-password"
                      placeholder="请输入密码（首次使用请输入admin）"
                      aria-label="密码"
                      onChange={(event) => {
                        setPassword(event.target.value);
                        if (passwordError) setPasswordError('');
                        if (notice) setNotice(null);
                      }}
                      onKeyDown={onFieldKeyDown}
                      className="min-w-0 flex-1 border-0 bg-transparent text-[14px] text-[#18181a] outline-none placeholder:text-[#757f9c]"
                    />
                    <button
                      type="button"
                      aria-label={showPassword ? '隐藏密码' : '显示密码'}
                      onClick={() => setShowPassword((prev) => !prev)}
                      className="grid size-[18px] shrink-0 place-items-center text-[#677185] outline-none transition-colors hover:text-[#464c5e]"
                    >
                      {showPassword ? (
                        <IconFieldEyeOn className="size-[18px]" />
                      ) : (
                        <IconFieldEye className="size-[18px]" />
                      )}
                    </button>
                  </div>

                  <button
                    type="submit"
                    disabled={loading}
                    className="mt-[24px] flex h-[40px] w-[120px] items-center justify-center self-center rounded-[10px] bg-[#18181a] text-[16px] font-normal text-white transition-colors hover:bg-[#18181a]/90 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {loading ? '登录中…' : '登录'}
                  </button>
                </form>
              )}
            </div>
          )}
        </div>

        <div className="mt-[32px] flex w-full justify-center">
          <img
            src={loginPreview}
            alt="StaffDeck 产品预览"
            className="h-auto w-full max-w-[1200px] select-none object-contain"
            draggable={false}
          />
        </div>
      </main>
    </div>
  );
}
