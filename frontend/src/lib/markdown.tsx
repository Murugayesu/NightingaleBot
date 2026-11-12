/**
 * A deliberately small Markdown renderer for answers.
 *
 * Supports paragraphs, line breaks, "-"/"*" and "1." lists, "#" headings,
 * **bold**, *italic*, `code` and [Source N] citation buttons. Everything
 * becomes React elements and text nodes; no HTML string is ever injected,
 * so markup inside an answer is shown literally and can never run. Links are
 * not rendered (answers must not send users to unverified addresses).
 */
import type { ReactNode } from "react";

// **bold** | *italic* or _italic_ | `code` | [Source N]
const INLINE =
  /\*\*(.+?)\*\*|(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])|(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)|`([^`]+)`|\[Source\s+(\d+)\]/g;

export interface MarkdownProps {
  text: string;
  /** Citation numbers that exist; [Source N] for others stays plain text. */
  sources?: ReadonlySet<number>;
  onCite?: (sourceNumber: number) => void;
}

function renderInline(
  text: string,
  keyPrefix: string,
  sources: ReadonlySet<number>,
  onCite?: (n: number) => void,
): ReactNode[] {
  const nodes: ReactNode[] = [];
  let last = 0;
  let index = 0;
  for (const match of text.matchAll(INLINE)) {
    const start = match.index ?? 0;
    if (start > last) nodes.push(text.slice(last, start));
    const key = `${keyPrefix}-${index++}`;
    const [, bold, italic, italicUnderscore, code, source] = match;
    if (bold !== undefined) {
      nodes.push(<strong key={key}>{renderInline(bold, key, sources, onCite)}</strong>);
    } else if (italic !== undefined || italicUnderscore !== undefined) {
      const inner = italic ?? italicUnderscore ?? "";
      nodes.push(<em key={key}>{renderInline(inner, key, sources, onCite)}</em>);
    } else if (code !== undefined) {
      nodes.push(<code key={key}>{code}</code>);
    } else if (source !== undefined) {
      const number = Number(source);
      nodes.push(
        sources.has(number) ? (
          <sup key={key}>
            <button
              type="button"
              className="cite-ref"
              aria-label={`Show source ${number}`}
              onClick={() => onCite?.(number)}
            >
              {number}
            </button>
          </sup>
        ) : (
          match[0]
        ),
      );
    }
    last = start + match[0].length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

type Block =
  | { kind: "p"; lines: string[] }
  | { kind: "h"; level: number; text: string }
  | { kind: "ul" | "ol"; start: number; items: string[] };

export function parseBlocks(text: string): Block[] {
  const blocks: Block[] = [];
  const paragraphs = text.replace(/\r\n?/g, "\n").trim().split(/\n\s*\n/);
  for (const paragraph of paragraphs) {
    let current: Block | null = null;
    for (const line of paragraph.split("\n")) {
      const heading = line.match(/^\s{0,3}(#{1,4})\s+(.+?)\s*#*\s*$/);
      const bullet = line.match(/^\s*[-*•]\s+(.*)$/);
      const numbered = line.match(/^\s*(\d+)[.)]\s+(.*)$/);
      if (heading) {
        blocks.push({ kind: "h", level: heading[1]!.length, text: heading[2]! });
        current = null;
      } else if (bullet || numbered) {
        const kind = bullet ? "ul" : "ol";
        if (!current || current.kind !== kind) {
          current = { kind, start: numbered ? Number(numbered[1]) : 1, items: [] };
          blocks.push(current);
        }
        current.items.push(bullet ? bullet[1]! : numbered![2]!);
      } else if (line.trim() === "") {
        continue;
      } else if (current && current.kind !== "p" && /^\s+\S/.test(line)) {
        // An indented line right after a list item continues that item.
        const last = current.items.length - 1;
        current.items[last] = `${current.items[last]}\n${line.trim()}`;
      } else {
        if (!current || current.kind !== "p") {
          current = { kind: "p", lines: [] };
          blocks.push(current);
        }
        current.lines.push(line);
      }
    }
  }
  return blocks;
}

function withBreaks(
  text: string,
  key: string,
  sources: ReadonlySet<number>,
  onCite?: (n: number) => void,
): ReactNode[] {
  return text.split("\n").flatMap((line, i) => {
    const parts = renderInline(line, `${key}-${i}`, sources, onCite);
    return i === 0 ? parts : [<br key={`${key}-br-${i}`} />, ...parts];
  });
}

const EMPTY = new Set<number>();

export function Markdown({ text, sources = EMPTY, onCite }: MarkdownProps) {
  return (
    <>
      {parseBlocks(text).map((block, i) => {
        const key = `b${i}`;
        switch (block.kind) {
          case "h": {
            const content = renderInline(block.text, key, sources, onCite);
            return block.level <= 2 ? (
              <h3 key={key}>{content}</h3>
            ) : (
              <h4 key={key}>{content}</h4>
            );
          }
          case "ul":
            return (
              <ul key={key}>
                {block.items.map((item, j) => (
                  <li key={j}>{withBreaks(item, `${key}-${j}`, sources, onCite)}</li>
                ))}
              </ul>
            );
          case "ol":
            return (
              <ol key={key} start={block.start}>
                {block.items.map((item, j) => (
                  <li key={j}>{withBreaks(item, `${key}-${j}`, sources, onCite)}</li>
                ))}
              </ol>
            );
          default:
            return <p key={key}>{withBreaks(block.lines.join("\n"), key, sources, onCite)}</p>;
        }
      })}
    </>
  );
}
