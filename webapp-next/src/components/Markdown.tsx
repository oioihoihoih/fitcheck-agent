import type { JSX } from "react";

// Minimal, dependency-free markdown renderer for agent answers. Handles the
// subset the FitCheck agent actually emits: ### headings, **bold**, `code`,
// bullet/numbered lists, autolinked URLs, and paragraphs. Everything is escaped
// via React text nodes (no dangerouslySetInnerHTML), so it's injection-safe.

export function Markdown({ text }: { text: string }) {
  const blocks = parseBlocks(text);
  return <div className="md">{blocks.map((b, i) => renderBlock(b, i))}</div>;
}

type Block =
  | { kind: "h"; level: number; text: string }
  | { kind: "ul"; items: string[] }
  | { kind: "ol"; items: string[] }
  | { kind: "p"; text: string };

function parseBlocks(src: string): Block[] {
  const lines = src.replace(/\r\n/g, "\n").split("\n");
  const blocks: Block[] = [];
  let para: string[] = [];
  let list: { kind: "ul" | "ol"; items: string[] } | null = null;

  const flushPara = () => {
    if (para.length) {
      blocks.push({ kind: "p", text: para.join("\n") });
      para = [];
    }
  };
  const flushList = () => {
    if (list) {
      blocks.push(list);
      list = null;
    }
  };

  for (const raw of lines) {
    const line = raw.replace(/\s+$/, "");
    const h = /^(#{1,4})\s+(.*)$/.exec(line);
    const ol = /^\s*(\d+)[.)]\s+(.*)$/.exec(line);
    const ul = /^\s*[-*•]\s+(.*)$/.exec(line);
    if (h) {
      flushPara();
      flushList();
      blocks.push({ kind: "h", level: h[1].length, text: h[2] });
    } else if (ol) {
      flushPara();
      if (!list || list.kind !== "ol") {
        flushList();
        list = { kind: "ol", items: [] };
      }
      list.items.push(ol[2]);
    } else if (ul) {
      flushPara();
      if (!list || list.kind !== "ul") {
        flushList();
        list = { kind: "ul", items: [] };
      }
      list.items.push(ul[1]);
    } else if (line.trim() === "") {
      flushPara();
      flushList();
    } else {
      flushList();
      para.push(line);
    }
  }
  flushPara();
  flushList();
  return blocks;
}

function renderBlock(b: Block, key: number): JSX.Element {
  switch (b.kind) {
    case "h": {
      const cls = `md-h md-h${b.level}`;
      return (
        <div className={cls} key={key}>
          {renderInline(b.text)}
        </div>
      );
    }
    case "ul":
      return (
        <ul className="md-ul" key={key}>
          {b.items.map((it, i) => (
            <li key={i}>{renderInline(it)}</li>
          ))}
        </ul>
      );
    case "ol":
      return (
        <ol className="md-ol" key={key}>
          {b.items.map((it, i) => (
            <li key={i}>{renderInline(it)}</li>
          ))}
        </ol>
      );
    default:
      return (
        <p className="md-p" key={key}>
          {renderInline(b.text)}
        </p>
      );
  }
}

// Inline: **bold**, `code`, and autolinked URLs.
function renderInline(text: string): (JSX.Element | string)[] {
  const out: (JSX.Element | string)[] = [];
  // Tokenize on the three inline patterns, preserving order.
  const re = /(\*\*[^*]+\*\*|`[^`]+`|https?:\/\/[^\s)]+)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let k = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("**")) {
      out.push(<strong key={k++}>{tok.slice(2, -2)}</strong>);
    } else if (tok.startsWith("`")) {
      out.push(
        <code className="md-code" key={k++}>
          {tok.slice(1, -1)}
        </code>,
      );
    } else {
      out.push(
        <a className="md-link" href={tok} target="_blank" rel="noreferrer" key={k++}>
          {shortenUrl(tok)}
        </a>,
      );
    }
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function shortenUrl(url: string): string {
  try {
    const u = new URL(url);
    const path = u.pathname.length > 24 ? u.pathname.slice(0, 22) + "…" : u.pathname;
    return u.host + path;
  } catch {
    return url;
  }
}
