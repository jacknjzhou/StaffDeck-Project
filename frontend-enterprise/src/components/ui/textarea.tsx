import * as React from "react"

import { useI18n } from "@/i18n"
import { cn } from "@/lib/utils"

/**
 * 多行文本输入框组件，基于原生 textarea 封装统一样式。
 *
 * - 触发时机：由使用方在表单、对话等场景中渲染，受控与非受控模式均可用。
 * - 副作用：通过 useI18n 对 placeholder、title、aria-label 文本做 i18n 翻译。
 * - 返回语义：返回带 data-slot="textarea" 的元素，className 与内置样式合并后生效。
 */
function Textarea({ className, ...props }: React.ComponentProps<"textarea">) {
  const { t } = useI18n()
  const localizedProps = {
    ...props,
    placeholder: typeof props.placeholder === "string" ? t(props.placeholder) : props.placeholder,
    title: typeof props.title === "string" ? t(props.title) : props.title,
    "aria-label": typeof props["aria-label"] === "string" ? t(props["aria-label"]) : props["aria-label"],
  }

  return (
    <textarea
      data-slot="textarea"
      className={cn(
        "flex field-sizing-fixed min-h-16 w-full overflow-y-auto rounded-lg border border-input bg-transparent px-2.5 py-2 text-base transition-colors outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:bg-input/50 disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 md:text-sm",
        className
      )}
      {...localizedProps}
    />
  )
}

export { Textarea }
