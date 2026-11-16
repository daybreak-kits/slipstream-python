import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { QueryError } from "@/components/logs/query-error";
import { caretIndex, caretLine, queryErrorTitle } from "@/lib/query-error";

describe("caret helpers", () => {
  it("maps a 1-based column onto a 0-based caret offset", () => {
    expect(caretIndex(1, "model=x")).toBe(0);
    expect(caretIndex(7, "model=x")).toBe(6);
    expect(caretLine("model=x", 7)).toBe("      ^");
    expect(caretLine("model=x", 1)).toBe("^");
  });

  it("gives up when the column is missing or past the end", () => {
    expect(caretIndex(undefined, "model=x")).toBeNull();
    expect(caretIndex(0, "model=x")).toBeNull();
    expect(caretIndex(-3, "model=x")).toBeNull();
    expect(caretIndex(99, "model=x")).toBeNull();
    expect(caretLine("model=x", 99)).toBe("");
  });

  it("counts CJK characters as one column each", () => {
    // 后端按 Python 字符下标报列号，中文也只有一个字符。
    expect(caretLine("msg=错误", 5)).toBe("    ^");
  });

  it("expands tabs so the caret still points at the right glyph", () => {
    expect(caretLine("\tmodel=x", 2)).toBe("    ^");
  });

  it("prefixes the title with the column only when it is usable", () => {
    expect(queryErrorTitle({ message: "未知字段：mode", column: 4 })).toBe("第 4 列：未知字段：mode");
    expect(queryErrorTitle({ message: "未知字段：mode" })).toBe("未知字段：mode");
    expect(queryErrorTitle({ message: "未知字段：mode", column: 0 })).toBe("未知字段：mode");
  });
});

describe("query error banner", () => {
  it("announces the message, column, caret line and suggestion", () => {
    render(
      <QueryError
        query={'msg=="timeout'}
        error={{
          message: "字符串没有闭合的引号",
          column: 7,
          suggestion: '值需要引号闭合：在结尾补一个 "',
        }}
      />,
    );

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("第 7 列：字符串没有闭合的引号");
    expect(alert).toHaveTextContent('建议：值需要引号闭合：在结尾补一个 "');
    // ^ 行是纯视觉辅助：对读屏软件隐藏，但仍要在 DOM 里供人眼对齐。
    expect(alert.querySelector("pre")?.textContent).toBe('msg=="timeout\n      ^');
  });

  it("renders nothing without an error", () => {
    const { container } = render(<QueryError error={null} query="model=x" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("skips the caret line when the error carries no column", () => {
    render(<QueryError error={{ message: "查询过长：最多 512 个字符" }} query="model=x" />);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("查询过长：最多 512 个字符");
    expect(alert.querySelector("pre")).toBeNull();
  });

  it("keeps the suggestion when the backend sends one", () => {
    render(
      <QueryError
        query="statu=500"
        error={{ message: "未知字段：statu", column: 1, suggestion: "是否想用 status？" }}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("建议：是否想用 status？");
  });
});
