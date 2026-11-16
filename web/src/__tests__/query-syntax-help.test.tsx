import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import {
  QUERY_EXAMPLES,
  QUERY_FIELDS,
  QUERY_FIELD_GROUPS,
  QUERY_KEYWORD_HINT,
  QUERY_OPERATORS,
  QuerySyntaxHelp,
} from "@/components/logs/query-syntax-help";

describe("query syntax reference data", () => {
  it("keeps the field registry in sync with the backend field table", () => {
    // 这串名字抄自 l03_domain/query_lang/fields.py 的注册表；改后端字段表必须同步改这里。
    expect(QUERY_FIELDS.map((field) => field.name)).toEqual([
      "model",
      "upstream",
      "channel",
      "channel_model",
      "error",
      "msg",
      "key",
      "user",
      "source",
      "status",
      "latency",
      "ttft",
      "ttat",
      "tokens",
      "cost",
      "time",
      "stream",
    ]);
  });

  it("partitions every field into exactly one group", () => {
    const grouped = QUERY_FIELD_GROUPS.flatMap((group) => group.fields.map((field) => field.name));
    expect(grouped.sort()).toEqual(QUERY_FIELDS.map((field) => field.name).sort());
    expect(QUERY_FIELD_GROUPS.map((group) => group.title)).toEqual(["文本", "数值与时间", "布尔"]);
  });

  it("documents every operator the compiler accepts", () => {
    expect(QUERY_OPERATORS.map((item) => item.op)).toEqual([
      "=",
      "==",
      "!=",
      "*=",
      ">",
      ">=",
      "<",
      "<=",
    ]);
  });
});

describe("query syntax help", () => {
  it("opens the reference dialog from the trigger", async () => {
    const user = userEvent.setup();
    render(<QuerySyntaxHelp />);

    expect(screen.queryByRole("dialog")).toBeNull();
    await user.click(screen.getByRole("button", { name: "语法参考" }));

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("查询语法");
    expect(dialog).toHaveTextContent("操作符");
    expect(dialog).toHaveTextContent("字段");
    expect(dialog).toHaveTextContent("示例");
  });

  it("lists the operators users type most", async () => {
    const user = userEvent.setup();
    render(<QuerySyntaxHelp />);
    await user.click(screen.getByRole("button", { name: "语法参考" }));
    const dialog = await screen.findByRole("dialog");

    expect(dialog).toHaveTextContent("包含");
    expect(dialog).toHaveTextContent("完全相等");
    expect(dialog).toHaveTextContent("通配");
    for (const op of ["*=", "!=", ">=", "<="]) {
      expect(dialog).toHaveTextContent(op);
    }
  });

  it("shows all three field groups with their hints", async () => {
    const user = userEvent.setup();
    render(<QuerySyntaxHelp />);
    await user.click(screen.getByRole("button", { name: "语法参考" }));
    const dialog = await screen.findByRole("dialog");

    expect(dialog).toHaveTextContent("数值与时间");
    expect(dialog).toHaveTextContent("布尔");
    expect(dialog).toHaveTextContent("% 和 _ 是普通字符");
    for (const field of QUERY_FIELDS) {
      expect(dialog).toHaveTextContent(field.name);
    }
  });

  it("renders the shared examples and the keyword fallback note", async () => {
    const user = userEvent.setup();
    render(<QuerySyntaxHelp />);
    await user.click(screen.getByRole("button", { name: "语法参考" }));
    const dialog = await screen.findByRole("dialog");

    for (const example of QUERY_EXAMPLES) {
      expect(dialog).toHaveTextContent(example.query);
    }
    expect(dialog).toHaveTextContent("upstream==MiniMax-M3.1-Flash");
    expect(dialog).toHaveTextContent("status>=400 && model*=flash");
    expect(dialog).toHaveTextContent("latency>3s");
    expect(dialog).toHaveTextContent("msg*=timeout");
    expect(dialog).toHaveTextContent(QUERY_KEYWORD_HINT);
  });

  it("can render inline for embedding without a dialog", () => {
    render(<QuerySyntaxHelp inline />);
    expect(screen.getByText("操作符")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("keeps an accessible name when collapsed to an icon", () => {
    render(<QuerySyntaxHelp iconOnly />);
    expect(screen.getByRole("button", { name: "语法参考" })).toBeInTheDocument();
  });
});
