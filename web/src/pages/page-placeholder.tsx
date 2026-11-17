import type { LucideIcon } from "lucide-react";

import { Card } from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import { cn } from "@/lib/utils";

interface PagePlaceholderProps {
  title: string;
  description: string;
  icon: LucideIcon;
  accent?: "indigo" | "teal" | "amber";
}

const accents = {
  indigo: "bg-primary-soft text-primary",
  teal: "bg-success-soft text-success-text",
  amber: "bg-warning-soft text-warning-text",
};

export function PagePlaceholder({
  title,
  description,
  icon: Icon,
  accent = "indigo",
}: PagePlaceholderProps) {
  return (
    <div className="animate-fade-up">
      <PageHeader title={title} description={description} />

      <Card className="mt-7 flex min-h-80 flex-col items-center justify-center border-dashed bg-surface px-6 py-14 text-center">
        <span className={cn("flex h-12 w-12 items-center justify-center rounded-xl", accents[accent])}>
          <Icon className="h-5 w-5" strokeWidth={1.75} aria-hidden="true" />
        </span>
        <h2 className="mt-4 text-sm font-semibold text-title">模块骨架已就位</h2>
        <p className="mt-1.5 max-w-md text-2sm leading-6 text-muted-foreground">
          登录、受保护路由与应用外壳已经可用。{title} 的数据与交互会在对应的后续工单中接入，此处暂不提供操作入口。
        </p>
        <span className="mt-5 rounded-badge border border-border bg-surface px-2.5 py-1 text-2xs font-medium text-muted-foreground">
          即将上线
        </span>
      </Card>
    </div>
  );
}
