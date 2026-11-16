import "@testing-library/jest-dom/vitest";
import { configure } from "@testing-library/react";

// findBy*/waitFor 默认只等 1s。时区选择器这类「上千个 option 的 cmdk 列表」
// 在 jsdom 里单条渲染就要几秒，全量套件并行时 1s 会稳定超时（不是断言错误）。
// 放到 8s：慢机器上仍能通过，真失败也仍在 testTimeout(30s) 内暴露。
configure({ asyncUtilTimeout: 8_000 });

// jsdom 4.x 缺这三个浏览器 API，而 Radix 的 Select/Popover/Dialog 与 cmdk 都会用到：
// - ResizeObserver：SelectContent 测量高度、cmdk 列表尺寸
// - 指针捕获：SelectTrigger 的 pointerdown 处理器会调用 hasPointerCapture
// - scrollIntoView：SelectItem 聚焦时调用
// 不补的话测试会以 "ResizeObserver is not defined" 或
// "target.hasPointerCapture is not a function" 直接抛错，掩盖真正的断言结果。
if (typeof globalThis.ResizeObserver === "undefined") {
  class ResizeObserverStub {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  }
  globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;
}

if (typeof Element !== "undefined") {
  if (!Element.prototype.hasPointerCapture) {
    Element.prototype.hasPointerCapture = function hasPointerCapture() {
      return false;
    };
  }
  if (!Element.prototype.setPointerCapture) {
    Element.prototype.setPointerCapture = function setPointerCapture() {};
  }
  if (!Element.prototype.releasePointerCapture) {
    Element.prototype.releasePointerCapture = function releasePointerCapture() {};
  }
  if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = function scrollIntoView() {};
  }
}
if (typeof window !== "undefined" && !window.matchMedia) {
  // jsdom 没有 matchMedia，主题检测（lib/theme.ts）与 sonner 的 Toaster 都会调用。
  // 返回一个「永不匹配」的 MediaQueryList，让受测组件走浅色/默认分支。
  window.matchMedia = function matchMedia(query: string) {
    const list = {
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    };
    return list as unknown as MediaQueryList;
  };
}
