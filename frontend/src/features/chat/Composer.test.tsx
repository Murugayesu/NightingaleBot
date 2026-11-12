import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Composer } from "./Composer";

describe("Composer", () => {
  it("sends on Enter, adds a line on Shift+Enter, and clears", async () => {
    const onSend = vi.fn(async () => true);
    render(<Composer busy={false} maxChars={100} onSend={onSend} onStop={() => {}} />);
    const box = screen.getByRole("textbox");
    await userEvent.type(box, "line one{Shift>}{Enter}{/Shift}line two");
    expect(onSend).not.toHaveBeenCalled();
    await userEvent.type(box, "{Enter}");
    expect(onSend).toHaveBeenCalledWith("line one\nline two");
    expect(box).toHaveValue("");
  });

  it("gives the question back when it is rejected", async () => {
    const onSend = vi.fn(async () => false);
    render(<Composer busy={false} maxChars={100} onSend={onSend} onStop={() => {}} />);
    await userEvent.type(screen.getByRole("textbox"), "What is the fee?{Enter}");
    expect(screen.getByRole("textbox")).toHaveValue("What is the fee?");
  });

  it("blocks questions that are too long and explains why", async () => {
    render(<Composer busy={false} maxChars={5} onSend={vi.fn()} onStop={() => {}} />);
    await userEvent.type(screen.getByRole("textbox"), "123456");
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    expect(screen.getByText("Questions can be at most 5 characters.")).toBeInTheDocument();
  });

  it("shows Stop while an answer is streaming", async () => {
    const onStop = vi.fn();
    render(<Composer busy maxChars={100} onSend={vi.fn()} onStop={onStop} />);
    await userEvent.click(screen.getByRole("button", { name: "Stop" }));
    expect(onStop).toHaveBeenCalled();
  });
});
