import {
  FlaskConical,
  KeyRound,
  Layers,
  LayoutDashboard,
  LayoutGrid,
  Network,
  ScrollText,
  Settings,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  label: string;
  to: string;
  icon: LucideIcon;
  description: string;
}

export interface NavGroup {
  label: string;
  items: NavItem[];
}

export const navGroups: NavGroup[] = [
  {
    label: "常规",
    items: [
      {
        label: "全局概览",
        to: "/overview",
        icon: LayoutGrid,
        description: "连接地址、接入示例与运行概况",
      },
      {
        label: "数据看板",
        to: "/",
        icon: LayoutDashboard,
        description: "请求量、词元、成本与延迟概览",
      },
      {
        label: "使用日志",
        to: "/logs",
        icon: ScrollText,
        description: "逐条请求的模型、渠道、耗时与费用",
      },
    ],
  },
  {
    label: "配置",
    items: [
      { label: "渠道管理", to: "/channels", icon: Network, description: "上游连接、密钥与模型映射" },
      { label: "模型管理", to: "/catalog", icon: Layers, description: "可调用模型清单与组合编排" },
    ],
  },
  {
    label: "工具",
    items: [
      { label: "模型试验", to: "/playground", icon: FlaskConical, description: "多轮流式调试与用量回显" },
    ],
  },
  {
    label: "系统",
    items: [
      { label: "用户密钥", to: "/keys", icon: KeyRound, description: "调用方密钥的签发与封禁" },
      { label: "系统设置", to: "/settings", icon: Settings, description: "管理员账号与日志策略" },
    ],
  },
];

export const navItems: NavItem[] = navGroups.flatMap((group) => group.items);

export function findNavItem(pathname: string): NavItem | undefined {
  return (
    navItems.find((item) => item.to === pathname) ??
    navItems.find((item) => item.to !== "/" && pathname.startsWith(item.to))
  );
}

export function findNavGroup(pathname: string): NavGroup | undefined {
  const item = findNavItem(pathname);
  return navGroups.find((group) => group.items.includes(item as NavItem));
}
