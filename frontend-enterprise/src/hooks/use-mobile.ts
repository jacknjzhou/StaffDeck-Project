import * as React from "react"

const MOBILE_BREAKPOINT = 768

/**
 * 判断当前视口是否为移动端尺寸（宽度小于 768px）的 Hook。
 *
 * 挂载时监听 `matchMedia` 断点变化并同步一次当前宽度，卸载时移除监听；
 * 首次渲染返回 false，避免初始值为 undefined 造成布局抖动。
 * @returns 视口宽度小于 768px 时返回 true，否则返回 false。
 */
export function useIsMobile() {
  const [isMobile, setIsMobile] = React.useState<boolean | undefined>(undefined)

  React.useEffect(() => {
    const mql = window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT - 1}px)`)
    const onChange = () => {
      setIsMobile(window.innerWidth < MOBILE_BREAKPOINT)
    }
    mql.addEventListener("change", onChange)
    setIsMobile(window.innerWidth < MOBILE_BREAKPOINT)
    return () => mql.removeEventListener("change", onChange)
  }, [])

  return !!isMobile
}
