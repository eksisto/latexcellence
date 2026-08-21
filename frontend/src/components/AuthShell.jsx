import Wordmark from "@/components/Wordmark";

/**
 * 多用户模式认证页公共外壳（登录 / 注册 / 待审批）。
 * 居中布局 + 品牌字标，风格对齐欢迎页。
 */
export default function AuthShell({ children }) {
  return (
    <div className="flex min-h-screen items-center justify-center px-6 py-10 animate-tab-enter">
      <div className="flex w-full max-w-md flex-col items-center">
        <div className="mb-8 text-center">
          <Wordmark className="text-3xl font-medium tracking-[-0.01em]" />
        </div>
        {children}
      </div>
    </div>
  );
}
