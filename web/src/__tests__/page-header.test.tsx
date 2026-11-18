import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { PageHeader } from "@/components/ui/page-header";

describe("PageHeader", () => {
  it("渲染 title 为页面唯一 h1", () => {
    render(<PageHeader title="数据看板" />);
    const headings = screen.getAllByRole("heading", { level: 1 });
    expect(headings).toHaveLength(1);
    expect(headings[0]).toHaveTextContent("数据看板");
  });

  it("渲染 description", () => {
    render(<PageHeader title="数据看板" description="按小时分桶。" />);
    expect(screen.getByText("按小时分桶。")).toBeInTheDocument();
  });

  it("未传 description 时不渲染说明段落", () => {
    const { container } = render(<PageHeader title="数据看板" />);
    expect(container.querySelector("p")).toBeNull();
  });

  it("actions 可点击", async () => {
    const onClick = vi.fn();
    render(
      <PageHeader
        title="渠道管理"
        actions={
          <button type="button" onClick={onClick}>
            新建渠道
          </button>
        }
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "新建渠道" }));
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("badge 渲染在标题旁", () => {
    render(
      <PageHeader
        title="新建渠道"
        badge={<span data-testid="header-badge">有未保存修改</span>}
      />,
    );
    expect(screen.getByTestId("header-badge")).toBeInTheDocument();
    const h1 = screen.getByRole("heading", { level: 1 });
    expect(h1.parentElement).toContainElement(screen.getByTestId("header-badge"));
  });

  it("className 透传到 header 根元素", () => {
    const { container } = render(<PageHeader title="系统设置" className="mt-2 items-end" />);
    const header = container.firstElementChild;
    expect(header).toHaveClass("mt-2", "items-end");
    expect(header?.tagName).toBe("HEADER");
  });

  it("title/h1 使用 text-h1 字阶与规范字距", () => {
    render(<PageHeader title="全局概览" />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveClass(
      "text-h1",
      "font-semibold",
      "tracking-[-0.01em]",
      "text-title",
    );
  });
});
