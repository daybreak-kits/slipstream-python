import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  availabilityPlan,
  ModelAvailabilityButton,
} from "@/components/models/model-availability";

describe("availabilityPlan", () => {
  it("bans an enabled healthy model", () => {
    expect(availabilityPlan({ enabled: true, health: "normal" })).toEqual({
      action: "ban",
      enabled: false,
      reset: false,
    });
  });

  it("treats a missing health as normal", () => {
    expect(availabilityPlan({ enabled: true })).toEqual({
      action: "ban",
      enabled: false,
      reset: false,
    });
    expect(availabilityPlan({ enabled: false })).toEqual({
      action: "enable",
      enabled: true,
      reset: false,
    });
  });

  it("resets an enabled model whose breaker is open, half-open or faulted", () => {
    for (const health of ["open", "half_open", "fault"] as const) {
      expect(availabilityPlan({ enabled: true, health })).toEqual({
        action: "reset",
        reset: true,
      });
    }
  });

  it("enables a disabled model and clears leftover breaker state", () => {
    expect(availabilityPlan({ enabled: false, health: "open" })).toEqual({
      action: "enable",
      enabled: true,
      reset: true,
    });
    expect(availabilityPlan({ enabled: false, health: "normal" })).toEqual({
      action: "enable",
      enabled: true,
      reset: false,
    });
  });
});

describe("ModelAvailabilityButton", () => {
  it("asks for confirmation before banning", async () => {
    const onConfirm = vi.fn();
    const user = userEvent.setup();
    render(
      <ModelAvailabilityButton
        subject="alpha-model"
        enabled={true}
        health="normal"
        onConfirm={onConfirm}
      />,
    );

    await user.click(screen.getByRole("button", { name: "封禁 alpha-model" }));
    expect(screen.getByRole("heading", { name: "封禁模型" })).toBeInTheDocument();
    expect(onConfirm).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "确认封禁" }));
    expect(onConfirm).toHaveBeenCalledWith({ action: "ban", enabled: false, reset: false });
  });

  it("labels the broken-state action 启用 but still only resets", async () => {
    const onConfirm = vi.fn();
    const user = userEvent.setup();
    render(
      <ModelAvailabilityButton
        subject="fault-model"
        enabled={true}
        health="fault"
        onConfirm={onConfirm}
      />,
    );

    await user.click(screen.getByRole("button", { name: "启用 fault-model" }));
    expect(screen.getByRole("heading", { name: "启用模型" })).toBeInTheDocument();
    expect(screen.getByText(/启用后.*清除冷却状态/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认启用" }));
    expect(onConfirm).toHaveBeenCalledWith({ action: "reset", reset: true });
  });

  it("enables a disabled model and carries the chained reset", async () => {
    const onConfirm = vi.fn();
    const user = userEvent.setup();
    render(
      <ModelAvailabilityButton
        subject="off-model"
        enabled={false}
        health="open"
        onConfirm={onConfirm}
      />,
    );

    await user.click(screen.getByRole("button", { name: "启用 off-model" }));
    expect(screen.getByRole("heading", { name: "启用模型" })).toBeInTheDocument();
    expect(screen.getByText(/清除其冷却状态/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认启用" }));
    expect(onConfirm).toHaveBeenCalledWith({ action: "enable", enabled: true, reset: true });
  });

  it("does nothing when the confirmation is cancelled", async () => {
    const onConfirm = vi.fn();
    const user = userEvent.setup();
    render(
      <ModelAvailabilityButton
        subject="alpha-model"
        enabled={true}
        health="normal"
        onConfirm={onConfirm}
      />,
    );

    await user.click(screen.getByRole("button", { name: "封禁 alpha-model" }));
    await user.click(screen.getByRole("button", { name: "取消" }));
    expect(onConfirm).not.toHaveBeenCalled();
    expect(screen.queryByRole("heading", { name: "封禁模型" })).not.toBeInTheDocument();
  });

  it("disables the trigger while a request is in flight", () => {
    render(
      <ModelAvailabilityButton
        subject="alpha-model"
        enabled={true}
        health="normal"
        busy={true}
        onConfirm={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "封禁 alpha-model" })).toBeDisabled();
  });
});
