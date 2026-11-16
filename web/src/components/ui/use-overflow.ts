import { useLayoutEffect, useRef, useState } from "react";

/**
 * 判断一段被 `truncate` 包住的文本是否真的溢出。
 *
 * jsdom 没有布局，clientWidth 恒为 0；此时按「溢出」处理，让测试仍能触发
 * tooltip，真实浏览器在 layout effect 里会立刻按实际宽度纠正。
 */
export function useOverflow<T extends HTMLElement>(text: string) {
  const ref = useRef<T>(null);
  const [overflowing, setOverflowing] = useState(true);

  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;

    const measure = () => {
      setOverflowing(element.clientWidth === 0 || element.scrollWidth > element.clientWidth + 1);
    };
    measure();

    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    window.addEventListener("resize", measure);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [text]);

  return { ref, overflowing };
}
