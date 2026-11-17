import { toast, type ExternalToast } from "sonner";

/**
 * 通知入口：把设计规范 TOAST.policy 的分级时长固化在这里。
 * 为什么需要这层：sonner 的 toast 默认 4000ms，直接调用会丢失本仓
 * 「success/error/info 5200ms、warning 6500ms」的规范（见 design-system §8）。
 * 调用方可以在 options 里传 duration 覆盖。
 */
type ToastMessage = Parameters<typeof toast.success>[0];

const STANDARD_DURATION_MS = 5200;
const WARNING_DURATION_MS = 6500;

function withPolicyDuration(
  options: ExternalToast | undefined,
  duration: number,
): ExternalToast {
  return { duration, ...options };
}

function success(message: ToastMessage, options?: ExternalToast) {
  return toast.success(message, withPolicyDuration(options, STANDARD_DURATION_MS));
}

function error(message: ToastMessage, options?: ExternalToast) {
  return toast.error(message, withPolicyDuration(options, STANDARD_DURATION_MS));
}

function info(message: ToastMessage, options?: ExternalToast) {
  return toast.info(message, withPolicyDuration(options, STANDARD_DURATION_MS));
}

function warning(message: ToastMessage, options?: ExternalToast) {
  return toast.warning(message, withPolicyDuration(options, WARNING_DURATION_MS));
}

export const notify = { success, error, info, warning };
