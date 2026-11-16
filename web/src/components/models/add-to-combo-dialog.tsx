import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import type { Combo } from "@/lib/combos";

/** 可以被加入组合的对象：直连渠道模型或另一个组合（子组合）。 */
export interface ComboTargetRef {
  kind: "direct" | "combo";
  id: string;
  label: string;
}

function alreadyInside(combo: Combo, target: ComboTargetRef): boolean {
  return combo.members.some((member) =>
    target.kind === "direct"
      ? member.channel_model_id === target.id
      : member.child_combo_id === target.id,
  );
}

export interface AddToComboDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  targets: readonly ComboTargetRef[];
  combos: readonly Combo[];
  busy?: boolean;
  /** 加入已有组合：page 负责逐组合写 members。 */
  onAdd: (comboIds: string[]) => void;
  /** 新建组合并把这些对象作为成员。 */
  onCreate: (name: string, targets: readonly ComboTargetRef[]) => void;
}

const MAX_TARGET_LABELS = 3;

/**
 * 「加入组合」弹框：列出手动组合（可多选）+ 新建组合入口。
 * 不做「移出组合」——移出是组合编辑器的职责（口径已定）。
 */
export function AddToComboDialog({
  open,
  onOpenChange,
  targets,
  combos,
  busy = false,
  onAdd,
  onCreate,
}: AddToComboDialogProps) {
  const [picked, setPicked] = useState<string[]>([]);
  const [createName, setCreateName] = useState("");

  useEffect(() => {
    if (!open) return;
    setPicked([]);
    setCreateName("");
  }, [open]);

  const labels = targets.map((target) => target.label);
  const summary =
    labels.length <= MAX_TARGET_LABELS
      ? labels.join("、")
      : labels.slice(0, MAX_TARGET_LABELS).join("、") + " 等 " + labels.length + " 项";

  const allInside = (combo: Combo) => targets.every((target) => alreadyInside(combo, target));
  const insideCount = (combo: Combo) => targets.filter((target) => alreadyInside(combo, target)).length;

  const creating = createName.trim().length > 0;
  const canSubmit = !busy && (creating || picked.length > 0);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>加入组合</DialogTitle>
          <DialogDescription>
            把 {summary} 加进一个或多个手动组合；已经在组合里的对象会自动跳过。移出组合请到组合编辑器里做。
          </DialogDescription>
        </DialogHeader>

        <div className="flex max-h-64 flex-col gap-1 overflow-y-auto">
          {combos.length === 0 ? (
            <p className="text-xs text-muted-foreground">还没有手动组合，可以在下面直接新建一个。</p>
          ) : (
            combos.map((combo) => {
              const inside = insideCount(combo);
              const full = allInside(combo);
              const id = "add-to-combo-" + combo.id;
              return (
                <label
                  key={combo.id}
                  htmlFor={id}
                  className="flex cursor-pointer items-center gap-2 rounded-control border border-transparent px-2 py-1.5 hover:bg-surface-2"
                >
                  <Checkbox
                    id={id}
                    checked={picked.includes(combo.id)}
                    disabled={full}
                    onCheckedChange={(checked) =>
                      setPicked((current) =>
                        checked === true
                          ? [...current, combo.id]
                          : current.filter((item) => item !== combo.id),
                      )
                    }
                    aria-label={"选择组合 " + combo.name}
                  />
                  <span className="font-mono text-xs text-title">{combo.name}</span>
                  <Badge variant="secondary" className="ml-auto">
                    {full ? "已全部包含" : "已包含 " + inside + "/" + targets.length}
                  </Badge>
                </label>
              );
            })
          )}
        </div>

        <Field>
          <FieldLabel htmlFor="add-to-combo-new">新建组合</FieldLabel>
          <Input
            id="add-to-combo-new"
            className="h-9"
            placeholder="输入组合名，直接把这些对象建成新组合"
            value={createName}
            onChange={(event) => setCreateName(event.target.value)}
          />
          <FieldDescription>
            {creating
              ? "将新建「" + createName.trim() + "」并把 " + targets.length + " 个对象作为成员（优先级 1）。"
              : "填了名字就只新建组合，不再写入上面勾选的组合。"}
          </FieldDescription>
        </Field>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            disabled={!canSubmit}
            onClick={() => {
              if (creating) onCreate(createName.trim(), targets);
              else onAdd(picked);
            }}
          >
            {busy ? "提交中…" : creating ? "创建并加入" : "加入组合"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
