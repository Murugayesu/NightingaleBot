import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Markdown, parseBlocks } from "./markdown";

describe("Markdown", () => {
  it("renders paragraphs, lists, headings, bold, italic and code", () => {
    const { container } = render(
      <Markdown
        text={"## Steps\n\nIntro **bold** and *soft* and `code`.\n\n1. One\n2. Two\n\n- a\n- b"}
      />,
    );
    expect(container.querySelector("h3")?.textContent).toBe("Steps");
    expect(container.querySelector("strong")?.textContent).toBe("bold");
    expect(container.querySelector("em")?.textContent).toBe("soft");
    expect(container.querySelector("code")?.textContent).toBe("code");
    expect(container.querySelectorAll("ol li")).toHaveLength(2);
    expect(container.querySelectorAll("ul li")).toHaveLength(2);
  });

  it("never turns answer text into HTML", () => {
    const { container } = render(
      <Markdown text={'<img src=x onerror="alert(1)"> <script>alert(2)</script> [link](javascript:x)'} />,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("a")).toBeNull();
    expect(container.textContent).toContain("<script>alert(2)</script>");
  });

  it("makes known [Source N] tags clickable and leaves unknown ones as text", () => {
    const onCite = vi.fn();
    render(<Markdown text="Fee is Rs. 365 [Source 1]. Also [Source 7]." sources={new Set([1])} onCite={onCite} />);
    fireEvent.click(screen.getByRole("button", { name: "Show source 1" }));
    expect(onCite).toHaveBeenCalledWith(1);
    expect(screen.queryByRole("button", { name: "Show source 7" })).toBeNull();
    expect(screen.getByText(/\[Source 7\]/)).toBeInTheDocument();
  });

  it("keeps indented continuation lines inside their list item", () => {
    expect(parseBlocks("- first\n  more\n- second")).toEqual([
      { kind: "ul", start: 1, items: ["first\nmore", "second"] },
    ]);
  });

  it("does not treat multiplication or snake_case as emphasis", () => {
    const { container } = render(<Markdown text="Use 2*3*4 and file_name_here." />);
    expect(container.querySelector("em")).toBeNull();
  });
});
