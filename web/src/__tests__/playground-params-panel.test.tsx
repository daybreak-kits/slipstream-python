import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ChatParamsPanel } from "@/components/playground/chat-params-panel";
import type { ChatParamInput } from "@/lib/playground";

/**
 * 参数面板的布局契约（issue 12）：
 * - 四个折叠分组标题存在，默认展开；
 * - 同质控件一行：textarea 类整行独占，短输入走共享网格（lg 3 列 / xl 6 列，窄屏 1→2 列退化）；
 * - 标签体系：中文业务名主标 + 参数名等宽副注，取值与用途走 ui/tooltip；
 * - 组间 16px（space-y-4）、组内 12px（gap-y-3），保持 4px 网格。
 */
function renderPanel(params: ChatParamInput = {}) {
  const onParamsChange = vi.fn();
  const view = render(
    <ChatParamsPanel
      systemPrompt=""
      onSystemPromptChange={vi.fn()}
      apiKey=""
      onApiKeyChange={vi.fn()}
      params={params}
      onParamsChange={onParamsChange}
      streamMode
      onStreamModeChange={vi.fn()}
    />,
  );
  return { onParamsChange, view };
}

describe("chat params panel layout contract", () => {
  it("renders the four collapsible group headers, all expanded by default", () => {
    renderPanel();
    for (const name of ["系统", "采样", "输出", "高级参数"]) {
      const header = screen.getByRole("button", { name });
      expect(header).toHaveAttribute("aria-expanded", "true");
      expect(header.querySelector("svg")).not.toBeNull();
    }
  });

  it("exposes every field through its composed accessible label", () => {
    renderPanel({ logprobs: true });
    const labels = [
      "系统提示词",
      "API 密钥",
      "流式（SSE）",
      "渠道思考等级（只读）",
      "温度 temperature",
      "核采样 top_p",
      "候选词数 top_k",
      "随机种子 seed",
      "频率惩罚 frequency_penalty",
      "存在惩罚 presence_penalty",
      "最大输出 max_tokens",
      "补全上限 max_completion_tokens",
      "回复份数 n",
      "回复格式 response_format",
      "停止序列 stop",
      "词概率 logprobs",
      "词概率条数 top_logprobs",
      "用户标识 user",
      "思考",
      "额外请求字段（JSON）",
    ];
    for (const label of labels) {
      expect(screen.getByLabelText(label)).toBeInTheDocument();
    }
  });

  it("keeps textarea-class fields on their own full-width rows", () => {
    renderPanel({ stop: "", extraFields: "" });
    for (const label of ["系统提示词", "停止序列 stop", "额外请求字段（JSON）"]) {
      expect(screen.getByLabelText(label).closest("[data-slot=field]")).toHaveClass(
        "col-span-full",
      );
    }
  });

  it("lays short inputs out on the shared homogeneous grid with narrow-screen fallback", () => {
    renderPanel();
    const grid = screen.getByLabelText("温度 temperature").closest(".grid");
    expect(grid).toHaveClass("sm:grid-cols-2", "lg:grid-cols-3", "xl:grid-cols-6");
    expect(grid).toHaveClass("gap-x-3", "gap-y-3");
    // 开关这类无标签行的控件靠底部对齐与同行输入框齐平。
    expect(grid).toHaveClass("items-end");

    // 六个采样输入在同一网格容器里（同质同行等高等宽）。
    const samplingInputs = [
      "playground-temperature",
      "playground-top-p",
      "playground-top-k",
      "playground-seed",
      "playground-frequency-penalty",
      "playground-presence-penalty",
    ].map((id) => document.getElementById(id));
    const grids = samplingInputs.map((input) => input?.closest(".grid"));
    for (const cell of grids) {
      expect(cell).toBe(grids[0]);
    }
  });

  it("pairs a Chinese main label with a mono param subnote and a tooltip hint", async () => {
    const user = userEvent.setup();
    renderPanel();

    const temperatureLabel = screen.getByText("温度").closest("label")!;
    expect(temperatureLabel).toHaveTextContent("温度");
    const subnote = screen.getByText("temperature");
    expect(subnote).toHaveClass("font-mono", "text-2xs");
    expect(subnote.closest("label")).toBe(temperatureLabel);

    // 取值与用途走 ui/tooltip：悬停标签出现；不用 title= 属性。
    expect(temperatureLabel).not.toHaveAttribute("title");
    await user.hover(temperatureLabel);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "0.0–2.0 · 越高输出越随机，越低越确定",
    );
  });

  it("keeps group separation on the 16px rhythm of the 4px grid", () => {
    const { view } = renderPanel();
    expect(view.container.firstElementChild).toHaveClass("space-y-4");
  });

  it("still emits param updates through the inputs", async () => {
    const { onParamsChange } = renderPanel();
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("温度 temperature"), "1");
    expect(onParamsChange).toHaveBeenCalledWith(expect.objectContaining({ temperature: "1" }));
  });
});
