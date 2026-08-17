// Reviewed records often carry JSON inside otherwise ordinary prose, either
// fenced or bare. Splitting a field into prose and parsed JSON lets the viewer
// format only the parts that really are JSON and leave the rest untouched.
export type Segment = { text: string; json?: unknown };

const FENCE = /```[a-zA-Z]*[ \t]*\r?\n?([\s\S]*?)```/g;

/** Parsed value, but only for objects and arrays: a bare number or quoted word
 *  parses as JSON too, and reformatting those would just add noise. */
function parseStructure(candidate: string): unknown | undefined {
  try {
    const parsed = JSON.parse(candidate);
    return parsed !== null && typeof parsed === "object" ? parsed : undefined;
  } catch {
    return undefined;
  }
}

/** Index of the bracket closing the one at `start`, or -1. Brackets inside
 *  strings are skipped, so a brace in a value cannot end the scan early. */
function matchBalanced(text: string, start: number): number {
  const open = text[start];
  const close = open === "{" ? "}" : "]";
  let depth = 0;
  let inString = false;
  let escaped = false;
  for (let i = start; i < text.length; i++) {
    const ch = text[i];
    if (inString) {
      if (escaped) escaped = false;
      else if (ch === "\\") escaped = true;
      else if (ch === '"') inString = false;
      continue;
    }
    if (ch === '"') inString = true;
    else if (ch === open) depth++;
    else if (ch === close && --depth === 0) return i;
  }
  return -1;
}

function scan(chunk: string, out: Segment[]): void {
  let plain = "";
  let i = 0;
  const flush = () => { if (plain) { out.push({ text: plain }); plain = ""; } };
  while (i < chunk.length) {
    const ch = chunk[i];
    if (ch === "{" || ch === "[") {
      const end = matchBalanced(chunk, i);
      if (end > i) {
        const source = chunk.slice(i, end + 1);
        const parsed = parseStructure(source);
        if (parsed !== undefined) {
          flush();
          out.push({ text: source, json: parsed });
          i = end + 1;
          continue;
        }
      }
    }
    // Anything that does not parse stays prose, which is what keeps a
    // template placeholder such as {input} rendering as written.
    plain += ch;
    i++;
  }
  flush();
}

export function extractJson(text: string): Segment[] {
  const out: Segment[] = [];
  let cursor = 0;
  for (const match of text.matchAll(FENCE)) {
    const body = match[1].trim();
    const parsed = parseStructure(body);
    if (parsed === undefined) continue;
    if (match.index > cursor) scan(text.slice(cursor, match.index), out);
    out.push({ text: body, json: parsed });
    cursor = match.index + match[0].length;
  }
  scan(text.slice(cursor), out);
  return out.length ? out : [{ text }];
}

export function containsJson(segments: Segment[]): boolean {
  return segments.some((segment) => segment.json !== undefined);
}
