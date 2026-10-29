import * as React from "react"

import { useI18n } from "@/i18n"
import { cn } from "@/lib/utils"

/**
 * 通用文本输入框,统一尺寸与边框样式,并把 placeholder、title、aria-label 交给 i18n 翻译。
 *
 * - 关闭浏览器与密码管理器的自动填充提示(autoComplete="off" 及相关 ignore 标记);
 * - 其余原生 input 属性透传,自定义 className 通过 cn 与内置样式合并。
 */
function Input({ className, type, ...props }: React.ComponentProps<"input">) {
  const { t } = useI18n()
  const localizedProps = {
    ...props,
    placeholder: typeof props.placeholder === "string" ? t(props.placeholder) : props.placeholder,
    title: typeof props.title === "string" ? t(props.title) : props.title,
    "aria-label": typeof props["aria-label"] === "string" ? t(props["aria-label"]) : props["aria-label"],
  }

  return (
    <input
      type={type}
      data-slot="input"
      autoComplete="off"
      data-1p-ignore="true"
      data-lpignore="true"
      data-bwignore="true"
      className={cn(
        "h-8 w-full min-w-0 rounded-lg border border-input bg-transparent px-2.5 py-1 text-base transition-colors outline-none file:inline-flex file:h-6 file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-foreground placeholder:text-muted-foreground disabled:pointer-events-none disabled:cursor-not-allowed disabled:bg-input/50 disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 md:text-sm",
        className
      )}
      {...localizedProps}
    />
  )
}

export { Input }
