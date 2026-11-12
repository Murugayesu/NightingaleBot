import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { MessageView } from "./MessageView";
import type { ChatMessage } from "./useChat";

function answer(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    key: "m1",
    id: "m1",
    role: "assistant",
    content: "The fee is Rs. 365 [Source 1].",
    answerMode: "grounded",
    label: "From official records",
    citations: [
      {
        source_index: 1,
        chunk_id: "c1",
        document_id: "d1",
        title: "FAQ.pdf",
        page_number: 2,
        score: 0.6,
        snippet: "The online registration fee is Rs. 365.",
      },
    ],
    status: "complete",
    isImported: false,
    feedback: null,
    ...overrides,
  };
}

function view(message: ChatMessage, onRate = vi.fn(async () => {})) {
  render(
    <MessageView
      message={message}
      isLast
      busy={false}
      contactUrl="https://example.org/contact"
      canRate
      onRegenerate={() => {}}
      onRate={onRate}
    />,
  );
  return onRate;
}

describe("MessageView", () => {
  it("labels official answers and opens the cited source", async () => {
    view(answer());
    expect(screen.getByText("From official records")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Show source 1" }));
    expect(screen.getByText("FAQ.pdf")).toBeInTheDocument();
    expect(screen.getByText("page 2")).toBeInTheDocument();
    expect(screen.getByText("The online registration fee is Rs. 365.")).toBeInTheDocument();
  });

  it("always shows the general-knowledge label", () => {
    view(
      answer({
        content: "Hand hygiene prevents infection.",
        answerMode: "general",
        label: "General nursing knowledge: not from official TNNMC records.",
        citations: [],
      }),
    );
    expect(screen.getByRole("note")).toHaveTextContent("not from official TNNMC records");
    expect(screen.queryByRole("button", { name: /Sources/ })).toBeNull();
  });

  it("offers the council contact on declined answers", () => {
    view(answer({ answerMode: "declined", citations: [], content: "Not in the records." }));
    expect(screen.getByRole("link", { name: /Contact the council/ })).toHaveAttribute(
      "href",
      "https://example.org/contact",
    );
  });

  it("marks stopped answers as unchecked", () => {
    view(answer({ status: "stopped", answerMode: null, citations: [] }));
    expect(screen.getByText(/partial answer was not checked/)).toBeInTheDocument();
  });

  it("asks for a comment after a thumbs-down", async () => {
    const onRate = view(answer());
    await userEvent.click(screen.getByRole("button", { name: "Not helpful" }));
    expect(onRate).toHaveBeenCalledWith(-1);
    await userEvent.type(screen.getByRole("textbox"), "Missing the late fee");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onRate).toHaveBeenLastCalledWith(-1, "Missing the late fee");
  });
});
