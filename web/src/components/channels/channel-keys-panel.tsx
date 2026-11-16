import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, KeyRound, ListChecks, Pencil, Plus, Trash2, X } from "lucide-react";
import { useMemo, useState } from "react";


import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { FieldError } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { notify } from "@/lib/notify";
import { ApiError } from "@/lib/api";
import {
  addChannelKey,
  deleteChannelKey,
  describeApiError,
  maskSecret,
  testChannelDraft,
  updateChannelKey,
  type ChannelDiscoverInput,
  type ChannelKey,
} from "@/lib/channels";
import { cn } from "@/lib/utils";

export const MAX_CHANNEL_KEYS = 20;

export interface PendingChannelKey {
  local_id: string;
  label: string;
  masked: string;
  api_key: string;
}

interface ChannelKeysPanelProps {
  channelId?: string;
  existingKeys: ChannelKey[];
  pendingKeys: PendingChannelKey[];
  onAddPending: (key: PendingChannelKey) => void;
  onRemovePending: (localId: string) => void;
  /** Draft connection values so unsaved channels can also batch-test keys. */
  testInput?: ChannelDiscoverInput | null;
}

interface BatchItem {
  api_key: string;
  status: "pending" | "testing" | "ok" | "fail";
  message: string;
}

function isDuplicateKeyError(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 409 || error.code === "CONFLICT");
}

function describeKeyAddError(error: unknown): string {
  if (isDuplicateKeyError(error)) return "该密钥已存在，未重复添加。";
  return describeApiError(error);
}

export function ChannelKeysPanel({
  channelId,
  existingKeys,
  pendingKeys,
  onAddPending,
  onRemovePending,
  testInput,
}: ChannelKeysPanelProps) {
  const queryClient = useQueryClient();
  const [label, setLabel] = useState("");
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<{ id: string; label: string } | null>(null);
  const [batchOpen, setBatchOpen] = useState(false);
  const [batchText, setBatchText] = useState("");
  const [batchItems, setBatchItems] = useState<BatchItem[]>([]);
  const [batchError, setBatchError] = useState<string | null>(null);
  const [batchBusy, setBatchBusy] = useState(false);
  const [batchSaving, setBatchSaving] = useState(false);
  const [confirmClearOpen, setConfirmClearOpen] = useState(false);
  const [editingKeyId, setEditingKeyId] = useState<string | null>(null);
  const [labelDraft, setLabelDraft] = useState("");
  const [labelError, setLabelError] = useState<string | null>(null);

  const count = existingKeys.length + pendingKeys.length;
  const atLimit = count >= MAX_CHANNEL_KEYS;
  const pastedLines = useMemo(
    () =>
      batchText
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter(Boolean),
    [batchText],
  );
  const duplicateInBatch = pastedLines.length - new Set(pastedLines).size;

  const refresh = async () => {
    if (!channelId) return;
    await queryClient.invalidateQueries({ queryKey: ["channel", channelId] });
    await queryClient.invalidateQueries({ queryKey: ["channels"] });
  };

  const addMutation = useMutation({
    mutationFn: (payload: { label: string; api_key: string }) =>
      addChannelKey(channelId ?? "", payload),
    onSuccess: async () => {
      setLabel("");
      setValue("");
      setError(null);
      notify.success("密钥已添加");
      await refresh();
    },
    onError: (mutationError) => setError(describeKeyAddError(mutationError)),
  });

  const deleteMutation = useMutation({
    mutationFn: (keyId: string) => deleteChannelKey(channelId ?? "", keyId),
    onSuccess: async () => {
      setDeleteTarget(null);
      notify.success("密钥已删除");
      await refresh();
    },
    onError: (mutationError) => {
      setDeleteTarget(null);
      notify.error("删除密钥失败", { description: describeApiError(mutationError) });
    },
  });

  const labelMutation = useMutation({
    mutationFn: ({ keyId, label: nextLabel }: { keyId: string; label: string }) =>
      updateChannelKey(channelId ?? "", keyId, { label: nextLabel }),
    onSuccess: async () => {
      setEditingKeyId(null);
      setLabelDraft("");
      setLabelError(null);
      notify.success("备注已更新");
      await refresh();
    },
    onError: (mutationError) => {
      if (mutationError instanceof ApiError && mutationError.status === 404) {
        setLabelError("当前后端尚未支持修改备注，请升级后端后再试。");
        notify.warning("修改备注不可用", { description: "后端尚未提供 PATCH 密钥接口。" });
        return;
      }
      setLabelError(describeApiError(mutationError));
    },
  });

  const persistBatch = async (items: BatchItem[]) => {
    const passed = items.filter((item) => item.status === "ok");
    if (passed.length === 0) {
      setConfirmClearOpen(false);
      setBatchError("没有检测通过的密钥可保存");
      return;
    }
    setBatchSaving(true);
    setBatchError(null);
    try {
      if (channelId) {
        const failures: string[] = [];
        let savedCount = 0;
        let duplicateCount = 0;
        for (const [index, item] of passed.entries()) {
          try {
            await addChannelKey(channelId, {
              label: `批次 ${index + 1}`,
              api_key: item.api_key,
            });
            savedCount += 1;
          } catch (mutationError) {
            if (isDuplicateKeyError(mutationError)) duplicateCount += 1;
            else failures.push(describeApiError(mutationError));
          }
        }
        await refresh();
        if (failures.length > 0) {
          setBatchError(failures.join("；"));
          notify.warning("部分密钥保存失败", { description: failures.join("；") });
          return;
        }
        notify.success("批量密钥已保存", {
          description:
            duplicateCount > 0
              ? `成功保存 ${savedCount} 个密钥，已忽略 ${duplicateCount} 个已存在的密钥`
              : `成功保存 ${savedCount} 个密钥`,
        });
      } else {
        for (const [index, item] of passed.entries()) {
          onAddPending({
            local_id: `pending-batch-${Date.now()}-${index}`,
            label: `批次 ${index + 1}`,
            masked: maskSecret(item.api_key),
            api_key: item.api_key,
          });
        }
        notify.success("批量密钥已加入待保存列表", { description: "保存渠道后一并写入" });
      }
      setConfirmClearOpen(false);
      setBatchOpen(false);
      setBatchItems([]);
      setBatchText("");
    } finally {
      setBatchSaving(false);
    }
  };

  const runBatch = async () => {
    const keys = Array.from(
      new Set(
        batchText
          .split(/\r?\n/)
          .map((line) => line.trim())
          .filter(Boolean),
      ),
    );
    if (keys.length === 0) {
      setBatchError("请按一行一个密钥的格式粘贴内容");
      return;
    }
    if (count + keys.length > MAX_CHANNEL_KEYS) {
      setBatchError(`最多只能保存 ${MAX_CHANNEL_KEYS} 个密钥，当前已有 ${count} 个`);
      return;
    }
    if (!channelId && !testInput?.connection_url?.trim()) {
      setBatchError("请先填写连接地址再检测密钥");
      return;
    }
    setBatchError(null);
    setBatchBusy(true);
    const next: BatchItem[] = keys.map((api_key) => ({
      api_key,
      status: "pending",
      message: "等待检测",
    }));
    setBatchItems(next);
    try {
      for (let index = 0; index < next.length; index += 1) {
        next[index] = { ...next[index], status: "testing", message: "检测中…" };
        setBatchItems([...next]);
        try {
          const result = await testChannelDraft({
            ...(channelId ? { channel_id: channelId } : {}),
            ...(!channelId && testInput ? testInput : {}),
            api_key: next[index].api_key,
          });
          next[index] = {
            ...next[index],
            status: result.status === "ok" ? "ok" : "fail",
            message: result.message || (result.status === "ok" ? "连接成功" : "检测失败"),
          };
        } catch (mutationError) {
          next[index] = {
            ...next[index],
            status: "fail",
            message: describeApiError(mutationError),
          };
        }
        setBatchItems([...next]);
      }
      const failed = next.filter((item) => item.status !== "ok");
      if (failed.length > 0) {
        setConfirmClearOpen(true);
      } else {
        await persistBatch(next);
      }
    } finally {
      setBatchBusy(false);
    }
  };

  const submit = () => {
    const trimmed = value.trim();
    if (!trimmed) {
      setError("请输入密钥内容");
      return;
    }
    if (atLimit) {
      setError(`最多只能保存 ${MAX_CHANNEL_KEYS} 个密钥`);
      return;
    }
    if (pendingKeys.some((key) => key.api_key === trimmed)) {
      setError("该密钥已在待保存列表中，未重复添加。");
      return;
    }
    setError(null);
    if (channelId) {
      addMutation.mutate({ label: label.trim(), api_key: trimmed });
    } else {
      onAddPending({
        local_id: `pending-${Date.now()}-${count}`,
        label: label.trim(),
        masked: maskSecret(trimmed),
        api_key: trimmed,
      });
      setLabel("");
      setValue("");
    }
  };

  const startEditingLabel = (key: ChannelKey) => {
    setEditingKeyId(key.id);
    setLabelDraft(key.label);
    setLabelError(null);
  };

  const saveLabel = (keyId: string) => {
    labelMutation.mutate({ keyId, label: labelDraft.trim() });
  };

  const busy =
    addMutation.isPending || deleteMutation.isPending || labelMutation.isPending;

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Label htmlFor="channel-key">渠道密钥</Label>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-8 gap-1 px-2 text-2xs"
            aria-pressed={batchOpen}
            onClick={() => {
              setBatchOpen((current) => !current);
              setBatchError(null);
            }}
          >
            <ListChecks className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
            批量模式
          </Button>
        </div>
        <span
          className={cn(
            "inline-flex items-center gap-1 rounded-badge px-2 py-0.5 text-2xs font-medium",
            count > 0 ? "bg-primary-soft text-primary" : "bg-surface-2 text-muted-foreground",
          )}
        >
          <KeyRound className="h-3 w-3" strokeWidth={1.75} aria-hidden="true" />
          共 {count} 个
        </span>
      </div>

      <div className="mt-1.5 rounded-control border border-border bg-surface-2/60 p-2.5">
        {count === 0 ? (
          <p className="px-1 py-1 text-2xs text-muted-foreground">
            尚未设置密钥。匿名可访问的上游可以留空；否则请至少添加一个。
          </p>
        ) : (
          <ul className="space-y-1">
            {existingKeys.map((key, index) => (
              <li
                key={key.id}
                className="flex items-center gap-2 rounded-md bg-surface px-2.5 py-1.5"
              >
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-surface-2 text-muted-foreground">
                  <KeyRound className="h-3 w-3" strokeWidth={1.75} aria-hidden="true" />
                </span>
                {editingKeyId === key.id ? (
                  <div className="flex min-w-0 flex-1 items-center gap-1.5">
                    <Input
                      aria-label="编辑密钥备注"
                      className="h-8 min-w-0 flex-1 text-xs"
                      placeholder="备注（可选）"
                      maxLength={64}
                      autoFocus
                      value={labelDraft}
                      disabled={labelMutation.isPending}
                      onChange={(event) => setLabelDraft(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") saveLabel(key.id);
                        if (event.key === "Escape") {
                          setEditingKeyId(null);
                          setLabelError(null);
                        }
                      }}
                    />
                    <Button
                      size="sm"
                      className="h-8 shrink-0 px-2"
                      disabled={labelMutation.isPending}
                      onClick={() => saveLabel(key.id)}
                    >
                      {labelMutation.isPending ? (
                        <Spinner size="sm" aria-hidden="true" />
                      ) : (
                        "保存"
                      )}
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-8 shrink-0 px-2"
                      disabled={labelMutation.isPending}
                      onClick={() => {
                        setEditingKeyId(null);
                        setLabelError(null);
                      }}
                    >
                      取消
                    </Button>
                  </div>
                ) : (
                  <>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-xs text-title">
                        {key.label || `密钥 #${index + 1}`}
                      </span>
                      <span className="block font-mono text-2xs text-muted-foreground">{key.masked}</span>
                    </span>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-8 w-8 shrink-0 p-0 text-muted-foreground hover:text-title"
                      aria-label={`修改密钥备注 ${key.label || `密钥 #${index + 1}`}`}
                      disabled={busy}
                      onClick={() => startEditingLabel(key)}
                    >
                      <Pencil className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-8 w-8 shrink-0 p-0 text-danger-text hover:bg-danger-soft hover:text-danger-text"
                      aria-label={`删除密钥 ${key.label || index + 1}`}
                      disabled={busy}
                      onClick={() => {
                        setDeleteTarget({ id: key.id, label: key.label || `密钥 #${index + 1}` });
                      }}
                    >
                      <Trash2 className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                    </Button>
                  </>
                )}
              </li>
            ))}
            {pendingKeys.map((key, index) => (
              <li
                key={key.local_id}
                className="flex items-center gap-2 rounded-md border border-dashed border-border-strong bg-surface px-2.5 py-1.5"
              >
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-surface-2 text-muted-foreground">
                  <KeyRound className="h-3 w-3" strokeWidth={1.75} aria-hidden="true" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-xs text-title">
                    {key.label || `新密钥 #${existingKeys.length + index + 1}`}
                  </span>
                  <span className="block font-mono text-2xs text-muted-foreground">{key.masked}</span>
                </span>
                <span className="shrink-0 rounded-badge bg-warning-soft px-1.5 py-0.5 text-2xs text-warning-text">
                  待保存
                </span>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-8 w-8 shrink-0 p-0 text-danger-text hover:bg-danger-soft hover:text-danger-text"
                  aria-label={`移除待保存密钥 ${index + 1}`}
                  onClick={() => onRemovePending(key.local_id)}
                >
                  <Trash2 className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                </Button>
              </li>
            ))}
          </ul>
        )}

        {batchOpen ? (
          <div className="mt-2 space-y-2 border-t border-border pt-2.5">
            <p className="text-2xs text-muted-foreground">
              一行一个密钥，粘贴后逐条检测；全部通过会自动保存。
            </p>
            <Textarea
              aria-label="批量密钥"
              className="min-h-32 resize-y rounded-control border-border-strong bg-surface font-mono text-xs leading-6 text-title md:text-xs"
              placeholder={"sk-...\nsk-...\nsk-..."}
              value={batchText}
              disabled={batchBusy || batchSaving}
              onChange={(event) => setBatchText(event.target.value)}
            />
            {duplicateInBatch > 0 ? (
              <p className="text-2xs text-warning-text">
                已忽略 {duplicateInBatch} 个重复密钥
              </p>
            ) : null}
            {batchItems.length > 0 ? (
              <ul className="max-h-56 space-y-1 overflow-y-auto">
                {batchItems.map((item, index) => (
                  <li
                    key={`${item.api_key}-${index}`}
                    className="flex items-center gap-2 rounded-md border border-border bg-surface px-2.5 py-1.5"
                  >
                    <span className="w-4 shrink-0">
                      {item.status === "ok" ? (
                        <Check className="h-3.5 w-3.5 text-success" strokeWidth={2.25} aria-label="检测通过" />
                      ) : item.status === "fail" ? (
                        <X className="h-3.5 w-3.5 text-danger-text" strokeWidth={2.25} aria-label="检测失败" />
                      ) : item.status === "testing" ? (
                        <Spinner size="sm" label="检测中" className="text-muted-foreground" />
                      ) : (
                        <span className="block h-3.5 w-3.5 rounded-full border border-border" aria-hidden="true" />
                      )}
                    </span>
                    <span className="min-w-0 flex-1 truncate font-mono text-2xs text-body">
                      {maskSecret(item.api_key)}
                    </span>
                    <span className="max-w-[48%] truncate text-2xs text-muted-foreground" title={item.message}>
                      {item.message}
                    </span>
                  </li>
                ))}
              </ul>
            ) : null}
            <div className="flex flex-wrap items-center justify-end gap-2">
              <Button
                variant="ghost"
                size="md"
                disabled={batchBusy || batchSaving}
                onClick={() => {
                  setBatchOpen(false);
                  setBatchItems([]);
                  setBatchError(null);
                }}
              >
                取消
              </Button>
              <Button
                variant="outline"
                size="md"
                disabled={batchBusy || batchSaving || atLimit}
                onClick={() => void runBatch()}
              >
                {batchBusy || batchSaving ? (
                  <Spinner size="sm" aria-hidden="true" />
                ) : (
                  <ListChecks className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                )}
                检测并保存
              </Button>
            </div>
          </div>
        ) : (
          <div className="mt-2 grid gap-2 border-t border-border pt-2.5 sm:grid-cols-[minmax(0,0.75fr)_minmax(0,1fr)_auto]">
            <Input
              aria-label="密钥备注"
              className="h-9 text-xs"
              placeholder="备注（可选）"
              maxLength={64}
              value={label}
              onChange={(event) => setLabel(event.target.value)}
            />
            <Input
              id="channel-key"
              aria-label="新密钥"
              type="password"
              autoComplete="new-password"
              className="h-9 font-mono text-xs"
              placeholder={count > 0 ? "输入要追加的密钥" : "输入渠道密钥"}
              value={value}
              onChange={(event) => setValue(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") submit();
              }}
            />
            <Button
              variant="outline"
              size="md"
              className="shrink-0 justify-center"
              disabled={busy || atLimit}
              onClick={submit}
            >
              {addMutation.isPending ? (
                <Spinner size="sm" aria-hidden="true" />
              ) : (
                <Plus className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
              )}
              添加
            </Button>
          </div>
        )}
        {error ? <FieldError className="mt-1.5 text-2xs text-danger-text">{error}</FieldError> : null}
        {batchError ? (
          <FieldError className="mt-1.5 text-2xs text-danger-text">{batchError}</FieldError>
        ) : null}
        {labelError ? (
          <FieldError className="mt-1.5 text-2xs text-danger-text">{labelError}</FieldError>
        ) : null}
        <p className="mt-1.5 text-2xs leading-5 text-muted-foreground">
          最多 {MAX_CHANNEL_KEYS} 个。只写入、不以明文返回；调用时按请求编号分散使用，悬停列表可看到尾部 4 位。
        </p>
      </div>

      <ConfirmDialog
        open={confirmClearOpen}
        onOpenChange={(next) => {
          if (!next && batchSaving) return;
          setConfirmClearOpen(next);
        }}
        title="清除检测失败的密钥？"
        description={`共有 ${
          batchItems.filter((item) => item.status === "fail").length
        } 个密钥检测失败，${batchItems.filter((item) => item.status === "ok").length} 个通过。失败项不会写入渠道；通过项会立即保存。你可以返回修改后重新检测。`}
        confirmLabel="清除失败并保存通过项"
        cancelLabel="返回修改"
        destructive
        onConfirm={() => void persistBatch(batchItems)}
      />

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        onOpenChange={(next) => {
          if (!next) setDeleteTarget(null);
        }}
        title="删除渠道密钥"
        description={
          deleteTarget
            ? `确定删除「${deleteTarget.label}」吗？删除后该密钥立即停止参与轮换。若这是最后一个密钥，渠道将以无密钥方式请求上游。`
            : undefined
        }
        confirmLabel="确认删除"
        destructive
        onConfirm={() => {
          if (deleteTarget) deleteMutation.mutate(deleteTarget.id);
        }}
      />
    </div>
  );
}
