import * as React from "react";

const DialogOpenerContext = React.createContext<React.RefObject<HTMLElement | null> | null>(null);

const captureOpener = () => {
  if (typeof document === "undefined") return null;
  const active = document.activeElement;
  return active instanceof HTMLElement && active !== document.body ? active : null;
};

/**
 * A 类基元的受控行为（ADR-063 约束 5 的例外，见 ui-standards §3.0）。
 *
 * Radix 的 onCloseAutoFocus 只会把焦点还给 DialogTrigger，而本仓弹窗都是状态驱动
 * 打开（没有 Trigger），关闭后焦点落到 body。在 Root 侧于"打开那一刻"记下当前焦点
 * 元素：此时 Radix 还没把焦点移进弹窗，读到的就是触发控件。必须在渲染阶段读，
 * 因为 Radix 把弹窗内容常驻渲染（从页面加载起就在），挂载期读到的永远是 body。
 */
export function useDialogOpener(open: boolean | undefined) {
  const openerRef = React.useRef<HTMLElement | null>(null);
  const wasOpenRef = React.useRef(false);
  if (open && !wasOpenRef.current) openerRef.current = captureOpener();
  wasOpenRef.current = Boolean(open);
  return openerRef;
}

export const DialogOpenerProvider = DialogOpenerContext.Provider;

/** Content 侧：关闭后若焦点确实丢了，就还给打开它的控件；有 Trigger 时 Radix 自己处理。 */
export function useDialogOpenerRestore(onCloseAutoFocus?: (event: Event) => void) {
  const openerRef = React.useContext(DialogOpenerContext);
  return React.useCallback(
    (event: Event) => {
      onCloseAutoFocus?.(event);
      // 调用方自己接管了焦点（preventDefault），不要再抢。
      if (event.defaultPrevented) return;
      // Radix 会在本处理器之后同步把焦点还给 trigger；没有 trigger 时它什么也不做，
      // 所以下一帧再检查焦点是不是真的丢了。
      requestAnimationFrame(() => {
        const active = document.activeElement;
        if (active && active !== document.body) return;
        const opener = openerRef?.current;
        if (opener?.isConnected) opener.focus({ preventScroll: true });
      });
    },
    [onCloseAutoFocus, openerRef],
  );
}
