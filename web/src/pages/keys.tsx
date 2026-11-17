import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Ban,
  Check,
  CheckCircle2,
  Copy,
  FlaskConical,
  KeyRound,
  Pencil,
  Plus,
  RefreshCw,
  RotateCcw,
  Trash2,
  X,
} from "lucide-react";
import { Spinner } from "@/components/ui/spinner";
import { useEffect, useState, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageHeader } from "@/components/ui/page-header";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { cn } from "@/lib/utils";
import { describeApiError } from "@/lib/channels";
import { copyText } from "@/lib/clipboard";
import { notify } from "@/lib/notify";
import { formatDateTime, useTimezone } from "@/lib/time";
import { createKey, deleteKey, listKeys, resetKey, revealKey, updateKey, type ApiKey, type ApiKeyCreated } from "@/lib/keys";

/**
 * keys 页的表单弹窗外壳。用 shadcn Dialog 原子件复刻旧 Modal 的四段式布局
 * （标题 / 描述 / 可滚动正文 / 底部按钮），免得每个弹窗再抄一遍壳子。
 */
function KeyDialog({
  open,
  onClose,
  title,
  description,
  footer,
  className,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description: string;
  footer: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <DialogContent
        showCloseButton={false}
        className={cn(
          "flex max-h-[calc(100dvh-5rem)] w-full flex-col gap-0 overflow-hidden rounded-card border-border bg-surface p-0 shadow-pop sm:rounded-card",
          className,
        )}
      >
        <DialogHeader className="relative shrink-0 gap-0.5 space-y-0.5 border-b border-border px-5 py-4 text-left">
          <DialogTitle className="text-sm font-semibold text-title">{title}</DialogTitle>
          <DialogDescription className="text-xs text-muted-foreground">{description}</DialogDescription>
          <DialogClose asChild>
            <button
              type="button"
              aria-label="关闭"
              className="absolute right-4 top-4 flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-surface-2 hover:text-title"
            >
              <X className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
            </button>
          </DialogClose>
        </DialogHeader>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
        <DialogFooter className="flex shrink-0 flex-col gap-2 border-t border-border bg-surface-2 px-5 py-3.5 sm:flex-row sm:flex-wrap sm:items-center sm:justify-end sm:space-x-0 [&>*]:w-full sm:[&>*]:w-auto">
          {footer}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function KeysPage() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  const { mode } = useTimezone();
  const [formOpen, setFormOpen] = useState(false);
  const [label, setLabel] = useState("");
  const [created, setCreated] = useState<ApiKeyCreated | null>(null);
  const [createdKind, setCreatedKind] = useState<"create" | "reset">("create");
  const [resetting, setResetting] = useState<ApiKey | null>(null);
  const [copied, setCopied] = useState(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [createError, setCreateError] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<ApiKey | null>(null);
  const [renameLabel, setRenameLabel] = useState("");
  const [deleting, setDeleting] = useState<ApiKey | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    if (!(location.state as { create?: boolean } | null)?.create) return;
    setCreated(null);
    setCreateError(null);
    setCopied(false);
    setFormOpen(true);
    navigate(location.pathname, { replace: true, state: null });
  }, [location.pathname, location.state, navigate]);

  const keysQuery = useQuery({ queryKey: ["keys"], queryFn: listKeys });

  const createMutation = useMutation({
    mutationFn: (nextLabel: string) => createKey(nextLabel),
    onSuccess: async (key) => {
      setCreated(key);
      setCreatedKind("create");
      setFormOpen(false);
      setLabel("");
      setCreateError(null);
      await queryClient.invalidateQueries({ queryKey: ["keys"] });
    },
    onError: (error) => setCreateError(describeApiError(error)),
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: { label?: string; enabled?: boolean } }) =>
      updateKey(id, payload),
    onSuccess: async () => {
      setRenaming(null);
      setActionError(null);
      await queryClient.invalidateQueries({ queryKey: ["keys"] });
    },
    onError: (error) => setActionError(describeApiError(error)),
  });

  const resetMutation = useMutation({
    mutationFn: (id: string) => resetKey(id),
    onSuccess: async (key) => {
      setCreated(key);
      setCreatedKind("reset");
      setCopied(false);
      setActionError(null);
      await queryClient.invalidateQueries({ queryKey: ["keys"] });
    },
    // ConfirmDialog 点确认即关闭，错误改走 toast（与 channel-keys-panel 同范式）
    onError: (error) => notify.error("重置密钥失败", { description: describeApiError(error) }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteKey(id),
    onSuccess: async () => {
      setActionError(null);
      await queryClient.invalidateQueries({ queryKey: ["keys"] });
    },
    onError: (error) => notify.error("删除密钥失败", { description: describeApiError(error) }),
  });

  const copyRowMutation = useMutation({
    mutationFn: async (key: ApiKey) => {
      const { key: plaintext } = await revealKey(key.id);
      return { id: key.id, plaintext };
    },
    onSuccess: async ({ id, plaintext }) => {
      try {
        await copyText(plaintext);
      } catch {
        notify.error("复制失败，请手动选择文本");
        return;
      }
      setCopiedId(id);
      setActionError(null);
      notify.success("已复制密钥");
      window.setTimeout(() => {
        setCopiedId((current) => (current === id ? null : current));
      }, 2000);
    },
    onError: (error) => notify.error(describeApiError(error)),
  });

  const items = keysQuery.data?.items ?? [];
  const total = keysQuery.data?.total ?? 0;

  const openCreate = () => {
    setCreateError(null);
    setCopied(false);
    setLabel("");
    setFormOpen(true);
  };

  const closePlaintext = () => {
    setCreated(null);
    setCopied(false);
  };

  const copyKey = async () => {
    if (!created) return;
    try {
      await copyText(created.key);
      setCopied(true);
      notify.success("已复制密钥");
    } catch {
      setCopied(false);
      notify.error("复制失败，请手动选择文本");
    }
  };

  const useInPlayground = () => {
    if (!created) return;
    closePlaintext();
    navigate("/playground");
  };

  return (
    <div className="animate-fade-up">
      <PageHeader
        title="用户密钥"
        description={
          <>
            签发用于调用 <span className="font-mono text-xs">/v1/chat/completions</span> 的
            Bearer 密钥。完整密钥加密存放于服务端，可随时在列表中复制；怀疑泄露时请立即重置。
          </>
        }
        actions={
          <Button onClick={openCreate}>
            <Plus className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
            新建密钥
          </Button>
        }
      />

      {keysQuery.isError ? (
        <Alert variant="destructive" className="mt-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span>{describeApiError(keysQuery.error)}</span>
            <Button variant="outline" size="sm" onClick={() => keysQuery.refetch()}>
              <RefreshCw className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
              重试
            </Button>
          </div>
        </Alert>
      ) : null}

      <Card className="mt-6 overflow-hidden">
        {keysQuery.isLoading ? (
          <div className="flex min-h-60 items-center justify-center gap-2 text-2sm text-muted-foreground">
            <Spinner aria-hidden="true" />
            正在加载密钥…
          </div>
        ) : items.length === 0 && !keysQuery.isError ? (
          <div className="flex min-h-80 flex-col items-center justify-center px-6 py-14 text-center">
            <span className="flex h-12 w-12 items-center justify-center rounded-xl bg-primary-soft text-primary">
              <KeyRound className="h-5 w-5" strokeWidth={1.75} aria-hidden="true" />
            </span>
            <h2 className="mt-4 text-sm font-semibold text-title">还没有用户密钥</h2>
            <p className="mt-1.5 max-w-md text-2sm leading-6 text-muted-foreground">
              创建一个密钥后即可在任意 OpenAI 兼容客户端或模型试验页调用网关。
            </p>
            <Button className="mt-5" onClick={openCreate}>
              <Plus className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
              创建第一个密钥
            </Button>
          </div>
        ) : (
          <Table className="min-w-180 text-left">
            <TableHeader>
              <TableRow className="border-b border-border bg-surface-2 text-2xs uppercase tracking-[0.08em] text-muted-foreground hover:bg-transparent">
                <TableHead className="h-auto px-5 py-3 font-semibold">标识</TableHead>
                <TableHead className="h-auto px-3 py-3 font-semibold">前缀</TableHead>
                <TableHead className="h-auto px-3 py-3 font-semibold">状态</TableHead>
                <TableHead className="h-auto px-3 py-3 font-semibold">最近使用</TableHead>
                <TableHead className="h-auto px-5 py-3 font-semibold">创建时间</TableHead>
                <TableHead className="h-auto px-5 py-3 text-right font-semibold">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((key) => (
                <TableRow key={key.id} className="hover:bg-surface-2">
                  <TableCell className="whitespace-nowrap px-5 py-3.5">
                    <span className="inline-block max-w-70 truncate align-bottom text-2sm font-medium text-title" title={key.label}>
                      {key.label}
                    </span>
                  </TableCell>
                  <TableCell className="whitespace-nowrap px-3 py-3.5">
                    <code className="rounded-badge bg-surface-2 px-2 py-1 font-mono text-2xs text-body">
                      {key.prefix}…
                    </code>
                  </TableCell>
                  <TableCell className="whitespace-nowrap px-3 py-3.5">
                    <span
                      className={cn(
                        "inline-flex items-center gap-1.5 rounded-badge px-2 py-0.5 text-2xs font-medium",
                        key.enabled
                          ? "bg-success-soft text-success-text"
                          : "bg-surface-2 text-muted-foreground",
                      )}
                    >
                      <span
                        className={cn(
                          "h-1.5 w-1.5 rounded-full",
                          // neutral dot: bg-faint stays visible on the new surface-2 muted
                          key.enabled ? "bg-success" : "bg-faint",
                        )}
                        aria-hidden="true"
                      />
                      {key.enabled ? "启用" : "封禁"}
                    </span>
                  </TableCell>
                  <TableCell className="whitespace-nowrap px-3 py-3.5 text-xs text-muted-foreground">
                    {key.last_used_at ? formatDateTime(key.last_used_at, mode) : "从未使用"}
                  </TableCell>
                  <TableCell className="whitespace-nowrap px-5 py-3.5 text-xs text-muted-foreground">
                    {formatDateTime(key.created_at, mode)}
                  </TableCell>
                  <TableCell className="whitespace-nowrap px-5 py-3.5">
                    <div className="flex items-center justify-end gap-0.5">
                      <Button
                        variant="ghost"
                        size="sm"
                        // 行级 pending：只禁用正在复制的那一行，其余行仍可操作
                        disabled={
                          copyRowMutation.isPending && copyRowMutation.variables?.id === key.id
                        }
                        onClick={() => copyRowMutation.mutate(key)}
                      >
                        {copiedId === key.id ? (
                          <Check className="h-3.5 w-3.5 text-success" strokeWidth={1.75} aria-hidden="true" />
                        ) : copyRowMutation.isPending && copyRowMutation.variables?.id === key.id ? (
                          <Spinner size="sm" aria-hidden="true" />
                        ) : (
                          <Copy className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                        )}
                        {copiedId === key.id ? "已复制" : "复制"}
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                          setRenaming(key);
                          setRenameLabel(key.label);
                          setActionError(null);
                        }}
                      >
                        <Pencil className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                        重命名
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={updateMutation.isPending}
                        onClick={() =>
                          updateMutation.mutate({ id: key.id, payload: { enabled: !key.enabled } })
                        }
                      >
                        {key.enabled ? (
                          <Ban className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                        ) : (
                          <CheckCircle2 className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                        )}
                        {key.enabled ? "封禁" : "启用"}
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                          setResetting(key);
                          setActionError(null);
                        }}
                      >
                        <RotateCcw className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                        重置
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        // 危险前景统一 --danger-text：同一控件不混用 --danger 与 --danger-text（ui.md §2.3）
                        className="text-danger-text"
                        onClick={() => {
                          setDeleting(key);
                          setActionError(null);
                        }}
                      >
                        <Trash2 className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                        删除
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Card>

      {keysQuery.data ? (
        <p className="mt-3 text-xs text-muted-foreground">共 {total} 个密钥</p>
      ) : null}

      <KeyDialog
        open={formOpen}
        onClose={() => {
          if (!createMutation.isPending) setFormOpen(false);
        }}
        title="新建用户密钥"
        description="标识用于在日志里区分调用方，可重复创建不同标识的密钥。"
        footer={
          <>
            <Button
              variant="ghost"
              disabled={createMutation.isPending}
              onClick={() => setFormOpen(false)}
            >
              取消
            </Button>
            <Button
              disabled={createMutation.isPending || !label.trim()}
              onClick={() => createMutation.mutate(label.trim())}
            >
              {createMutation.isPending ? (
                <Spinner aria-hidden="true" />
              ) : (
                <KeyRound className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
              )}
              创建密钥
            </Button>
          </>
        }
      >
        <div className="space-y-2">
          <Label htmlFor="key-label">密钥标识</Label>
          <Input
            id="key-label"
            autoFocus
            value={label}
            maxLength={64}
            placeholder="例如 laptop、ci、mobile"
            onChange={(event) => setLabel(event.target.value)}
          />
          <p className="text-xs text-muted-foreground">最长 64 位；同一用户下不能重名。</p>
        </div>
        {createError ? (
          <Alert variant="destructive" className="mt-3">
            {createError}
          </Alert>
        ) : null}
      </KeyDialog>

      <KeyDialog
        open={Boolean(renaming)}
        onClose={() => setRenaming(null)}
        title="重命名密钥"
        description="重命名只改变列表里的展示标识，不会重新签发密钥。"
        footer={
          <>
            <Button variant="ghost" onClick={() => setRenaming(null)}>取消</Button>
            <Button
              disabled={updateMutation.isPending || !renameLabel.trim()}
              onClick={() =>
                renaming && updateMutation.mutate({ id: renaming.id, payload: { label: renameLabel.trim() } })
              }
            >
              <Check className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
              保存
            </Button>
          </>
        }
      >
        <Label htmlFor="rename-key">密钥标识</Label>
        <Input
          id="rename-key"
          autoFocus
          className="mt-2"
          value={renameLabel}
          maxLength={64}
          onChange={(event) => setRenameLabel(event.target.value)}
        />
        {actionError ? <Alert variant="destructive" className="mt-3">{actionError}</Alert> : null}
      </KeyDialog>

      {/* 危险确认统一走 ui/confirm-dialog（ui.md §2.3）：alertdialog 语义 + 问句标题。 */}
      <ConfirmDialog
        open={Boolean(deleting)}
        onOpenChange={(next) => {
          if (!next) setDeleting(null);
        }}
        title={deleting ? `删除密钥 ${deleting.label}？` : ""}
        description={
          deleting
            ? `删除后，使用 ${deleting.label} 的客户端会立即收到 401，此操作不可撤销。`
            : "删除后，使用该密钥的客户端会立即收到 401，此操作不可撤销。"
        }
        confirmLabel="删除"
        destructive
        onConfirm={() => {
          if (deleting) deleteMutation.mutate(deleting.id);
        }}
      />

      <ConfirmDialog
        open={Boolean(resetting)}
        onOpenChange={(next) => {
          if (!next) setResetting(null);
        }}
        title={resetting ? `重置密钥 ${resetting.label}？` : ""}
        description={
          resetting
            ? `重置后，使用 ${resetting.label} 的旧密钥立即失效，客户端会收到 401。将生成新的密钥明文（之后也可在列表中复制），旧密钥立即作废。`
            : "重置后，使用该密钥的旧客户端会立即收到 401。将生成新的密钥明文（之后也可在列表中复制），旧密钥立即作废。"
        }
        confirmLabel="重置密钥"
        destructive
        onConfirm={() => {
          if (resetting) resetMutation.mutate(resetting.id);
        }}
      />

      <KeyDialog
        open={Boolean(created)}
        onClose={closePlaintext}
        title={createdKind === "reset" ? "密钥已重置" : "密钥已创建"}
        description="完整密钥已加密保存，之后也可在列表中随时复制；请先复制到客户端使用。"
        className="max-w-xl"
        footer={
          <>
            <Button variant="ghost" onClick={closePlaintext}>
              <Check className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
              我已保存
            </Button>
            <Button onClick={useInPlayground}>
              <FlaskConical className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
              去模型试验使用
            </Button>
          </>
        }
      >
        <div className="rounded-control border border-border bg-surface-2 p-3">
          <code className="block break-all font-mono text-xs leading-6 text-title">
            {created?.key}
          </code>
        </div>
        <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
          <span className="text-xs text-muted-foreground">
            标识：{created?.label} · 前缀：{created?.prefix}
          </span>
          <Button variant="outline" size="sm" onClick={copyKey}>
            {copied ? (
              <Check className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
            ) : (
              <Copy className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
            )}
            {copied ? "已复制" : "复制密钥"}
          </Button>
        </div>
        <Alert variant="warning" className="mt-3">
          不要把密钥写进仓库或日志；需要轮换时可直接重置密钥，旧密钥立即失效。
        </Alert>
      </KeyDialog>
    </div>
  );
}
