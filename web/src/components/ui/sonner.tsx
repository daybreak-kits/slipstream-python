"use client"

import { Toaster as Sonner } from "sonner"

import { useThemeMode } from "@/lib/theme"

type ToasterProps = React.ComponentProps<typeof Sonner>

/**
 * shadcn 的 Toaster 依赖 next-themes 取主题；本仓主题由 src/lib/theme.ts 管理
 * （system/light/dark + .dark class），所以这里直接读本仓的 useThemeMode，
 * 不引入 next-themes，避免两套主题状态机。
 */
const Toaster = ({ ...props }: ToasterProps) => {
  const { mode } = useThemeMode()

  return (
    <Sonner
      theme={mode}
      className="toaster group"
      visibleToasts={4}
      toastOptions={{
        duration: 5200,
        classNames: {
          toast:
            "group toast group-[.toaster]:bg-background group-[.toaster]:text-foreground group-[.toaster]:border-border group-[.toaster]:shadow-lg",
          description: "group-[.toast]:text-muted-foreground",
          actionButton:
            "group-[.toast]:bg-primary group-[.toast]:text-primary-foreground",
          cancelButton:
            "group-[.toast]:bg-muted group-[.toast]:text-muted-foreground",
        },
      }}
      {...props}
    />
  )
}

export { Toaster }
