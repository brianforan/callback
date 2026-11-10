// A small Markdown reader for the fact sheet: headings, paragraphs, bullet, numbered and task
// lists (nested), tables, block quotes, fenced code, rules, and inline code, bold, italic,
// strikethrough and links. It returns plain data for a renderer to draw, and never produces HTML,
// so nothing in the text can inject markup. Images are shown as their alt text, so nothing is
// loaded from another site, and only http, https and mailto links become links.
//
// Left out on purpose: indented code blocks, reference links, footnotes and raw HTML, which show
// as written.

export type Inline =
  | { t: 'text'; v: string }
  | { t: 'code'; v: string }
  | { t: 'strong' | 'em' | 'del'; c: Inline[] }
  | { t: 'link'; href: string; c: Inline[] }
  | { t: 'br' };

export type Align = 'left' | 'center' | 'right' | null;

export interface ListItem {
  // null for a plain item, otherwise whether the task box is ticked.
  task: boolean | null;
  blocks: MdBlock[];
}

export type MdBlock =
  | { t: 'heading'; level: 1 | 2 | 3 | 4 | 5 | 6; c: Inline[] }
  | { t: 'paragraph'; c: Inline[] }
  | { t: 'list'; ordered: boolean; start: number; items: ListItem[] }
  | { t: 'quote'; c: MdBlock[] }
  | { t: 'code'; lang: string; v: string }
  | { t: 'rule' }
  | { t: 'table'; align: Align[]; head: Inline[][]; rows: Inline[][][] };

const MAX_DEPTH = 8;
// Emphasis and links must close within this many characters. It keeps a run of stray markers from
// making every one of them search the rest of a long paragraph.
const INLINE_WINDOW = 600;
// A paragraph longer than this is shown without inline formatting.
const MAX_FORMATTED = 20_000;
const BREAK = String.fromCharCode(1);

const HEADING = /^ {0,3}(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$/;
const FENCE = /^ {0,3}(`{3,}|~{3,})[ \t]*([^\s`]*)/;
const RULE = /^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$/;
const QUOTE = /^ {0,3}>[ \t]?(.*)$/;
const ITEM = /^( *)([-*+]|\d{1,9}[.)])(?:[ \t]+(.*))?$/;
const TABLE_SEPARATOR = /^[ \t]*\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*$/;
const TASK = /^\[([ xX])\][ \t]+(.*)$/;

function indentOf(line: string): number {
  return line.length - line.trimStart().length;
}

function isBlank(line: string): boolean {
  return line.trim() === '';
}

function splitCells(line: string): string[] {
  let text = line.trim();
  if (text.startsWith('|')) text = text.slice(1);
  if (text.endsWith('|') && !text.endsWith('\\|')) text = text.slice(0, -1);
  const cells: string[] = [];
  let cell = '';
  for (let i = 0; i < text.length; i++) {
    if (text[i] === '\\' && text[i + 1] === '|') {
      cell += '|';
      i++;
    } else if (text[i] === '|') {
      cells.push(cell.trim());
      cell = '';
    } else {
      cell += text[i];
    }
  }
  cells.push(cell.trim());
  return cells;
}

function startsTable(lines: string[], i: number): boolean {
  const header = lines[i];
  const separator = lines[i + 1];
  if (separator === undefined || !header.includes('|') || !separator.includes('-')) return false;
  if (!TABLE_SEPARATOR.test(separator)) return false;
  return splitCells(header).length === splitCells(separator).length;
}

// Whether a line starts a block, so it can end the paragraph above it.
function interrupts(lines: string[], i: number): boolean {
  const line = lines[i];
  if (HEADING.test(line) || FENCE.test(line) || QUOTE.test(line) || RULE.test(line)) return true;
  const item = ITEM.exec(line);
  // A numbered item only interrupts when it starts a list at 1.
  if (item && (/^[-*+]$/.test(item[2]) || /^1[.)]$/.test(item[2]))) return true;
  return startsTable(lines, i);
}

function parseTable(lines: string[], start: number): { block: MdBlock; next: number } {
  const head = splitCells(lines[start]);
  const align: Align[] = splitCells(lines[start + 1]).map((cell) => {
    const left = cell.startsWith(':');
    const right = cell.endsWith(':');
    if (left && right) return 'center';
    if (right) return 'right';
    return left ? 'left' : null;
  });
  const rows: Inline[][][] = [];
  let i = start + 2;
  while (i < lines.length && !isBlank(lines[i]) && lines[i].includes('|')) {
    const cells = splitCells(lines[i]);
    // Every row has as many cells as the header: extras are dropped and gaps left empty.
    rows.push(head.map((_, column) => inline(cells[column] ?? '')));
    i++;
  }
  return { block: { t: 'table', align, head: head.map((cell) => inline(cell)), rows }, next: i };
}

function parseList(
  lines: string[],
  start: number,
  depth: number,
): { block: MdBlock; next: number } {
  const first = ITEM.exec(lines[start]) as RegExpExecArray;
  const ordered = /\d/.test(first[2]);
  const baseIndent = first[1].length;
  const items: ListItem[] = [];
  let i = start;

  while (i < lines.length) {
    const match = ITEM.exec(lines[i]);
    if (!match || match[1].length > baseIndent + 3 || /\d/.test(match[2]) !== ordered) break;
    if (RULE.test(lines[i])) break;
    const markerEnd = match[1].length + match[2].length + 1;
    const body: string[] = [match[3] ?? ''];
    i++;
    while (i < lines.length) {
      const line = lines[i];
      if (isBlank(line)) {
        // A blank line stays in the item only when indented text follows it.
        let ahead = i + 1;
        while (ahead < lines.length && isBlank(lines[ahead])) ahead++;
        if (ahead < lines.length && indentOf(lines[ahead]) > baseIndent) {
          body.push('');
          i++;
          continue;
        }
        break;
      }
      if (indentOf(line) > baseIndent) {
        body.push(line);
        i++;
        continue;
      }
      // Text that wraps onto the next line without being indented belongs to the item.
      const previousBlank = isBlank(body[body.length - 1]);
      if (!previousBlank && !ITEM.test(line) && !interrupts(lines, i)) {
        body.push(line.trim());
        i++;
        continue;
      }
      break;
    }
    const rest = body.slice(1).filter((line) => !isBlank(line));
    const strip = Math.min(markerEnd, ...rest.map(indentOf));
    const content = [body[0], ...body.slice(1).map((line) => line.slice(Math.min(strip, indentOf(line))))];
    const task = TASK.exec(content[0]);
    if (task) content[0] = task[2];
    items.push({
      task: task ? task[1] !== ' ' : null,
      blocks: parseBlocks(content, depth + 1),
    });
    // Blank lines between items don't end the list, unless something else follows them.
    if (i < lines.length && isBlank(lines[i])) {
      let ahead = i;
      while (ahead < lines.length && isBlank(lines[ahead])) ahead++;
      const next = ahead < lines.length ? ITEM.exec(lines[ahead]) : null;
      if (next && next[1].length <= baseIndent + 3 && /\d/.test(next[2]) === ordered) i = ahead;
    }
  }
  const number = ordered ? Number.parseInt(first[2], 10) : 1;
  return { block: { t: 'list', ordered, start: number, items }, next: i };
}

function parseBlocks(lines: string[], depth: number): MdBlock[] {
  const blocks: MdBlock[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (isBlank(line)) {
      i++;
      continue;
    }
    const fence = FENCE.exec(line);
    if (fence) {
      const marker = fence[1];
      const code: string[] = [];
      i++;
      while (i < lines.length) {
        const close = lines[i].trim();
        if (close.startsWith(marker[0].repeat(marker.length)) && close === marker[0].repeat(close.length)) break;
        code.push(lines[i]);
        i++;
      }
      i++;
      blocks.push({ t: 'code', lang: fence[2], v: code.join('\n') });
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      blocks.push({
        t: 'heading',
        level: heading[1].length as 1 | 2 | 3 | 4 | 5 | 6,
        c: inline(heading[2]),
      });
      i++;
      continue;
    }
    if (RULE.test(line)) {
      blocks.push({ t: 'rule' });
      i++;
      continue;
    }
    if (QUOTE.test(line)) {
      const quoted: string[] = [];
      while (i < lines.length && QUOTE.test(lines[i])) {
        quoted.push((QUOTE.exec(lines[i]) as RegExpExecArray)[1]);
        i++;
      }
      blocks.push({ t: 'quote', c: depth < MAX_DEPTH ? parseBlocks(quoted, depth + 1) : [] });
      continue;
    }
    if (startsTable(lines, i)) {
      const table = parseTable(lines, i);
      blocks.push(table.block);
      i = table.next;
      continue;
    }
    if (ITEM.test(line) && depth < MAX_DEPTH) {
      const list = parseList(lines, i, depth);
      blocks.push(list.block);
      i = list.next;
      continue;
    }
    const paragraph: string[] = [line];
    i++;
    while (i < lines.length && !isBlank(lines[i]) && !interrupts(lines, i)) {
      paragraph.push(lines[i]);
      i++;
    }
    blocks.push({ t: 'paragraph', c: inline(paragraphText(paragraph)) });
  }
  return blocks;
}

// Lines of one paragraph as a single string. A line ending in two spaces or a backslash is a hard
// break, which is marked so the inline reader can turn it into one.
function paragraphText(lines: string[]): string {
  return lines
    .map((line, index) => {
      const clean = line.split(BREAK).join('');
      if (index === lines.length - 1) return clean.trim();
      if (/( {2,}|\\)$/.test(clean)) return clean.replace(/( {2,}|\\)$/, '').trim() + BREAK;
      return `${clean.trim()} `;
    })
    .join('');
}

const PUNCTUATION = /^[!-/:-@[-`{-~]$/;
const SPECIAL = /[\\`*_~[!<\u0001]/g;

function isWordChar(char: string | undefined): boolean {
  return char !== undefined && /[\p{L}\p{N}]/u.test(char);
}

function safeHref(url: string): string | null {
  return /^(https?:|mailto:)/i.test(url) ? url : null;
}

function emphasisPatterns(char: string): { triple: RegExp; strong: RegExp; em: RegExp } {
  const c = `\\${char}`;
  return {
    triple: new RegExp(`^${c}${c}${c}(?=\\S)([\\s\\S]*?\\S)${c}${c}${c}`),
    strong: new RegExp(`^${c}${c}(?=\\S)([\\s\\S]*?\\S)${c}${c}`),
    em: new RegExp(`^${c}(?=[^\\s${c}])([\\s\\S]*?[^\\s${c}])${c}(?!${c})`),
  };
}

const EMPHASIS: Record<string, { triple: RegExp; strong: RegExp; em: RegExp }> = {
  '*': emphasisPatterns('*'),
  _: emphasisPatterns('_'),
};

const LINK = /^\[((?:[^[\]\\]|\\.)*)\]\(\s*([^\s)]*)(?:\s+(?:"[^"]*"|'[^']*'))?\s*\)/;
const IMAGE = /^!\[((?:[^[\]\\]|\\.)*)\]\(\s*([^\s)]*)(?:\s+(?:"[^"]*"|'[^']*'))?\s*\)/;

export function inline(text: string, depth = 0): Inline[] {
  if (text.length > MAX_FORMATTED || depth > 6) {
    return [{ t: 'text', v: text.split(BREAK).join(' ') }];
  }
  const out: Inline[] = [];
  let buffer = '';
  const flush = () => {
    if (buffer) out.push({ t: 'text', v: buffer });
    buffer = '';
  };

  let i = 0;
  while (i < text.length) {
    SPECIAL.lastIndex = i;
    const found = SPECIAL.exec(text);
    const next = found ? found.index : text.length;
    buffer += text.slice(i, next);
    i = next;
    if (i >= text.length) break;

    const char = text[i];
    const rest = text.slice(i, i + INLINE_WINDOW);
    const before = text[i - 1];

    if (char === BREAK) {
      flush();
      out.push({ t: 'br' });
      i++;
    } else if (char === '\\') {
      if (text[i + 1] !== undefined && PUNCTUATION.test(text[i + 1])) {
        buffer += text[i + 1];
        i += 2;
      } else {
        buffer += char;
        i++;
      }
    } else if (char === '`') {
      const code = /^(`+)(?!`)([\s\S]*?[^`])\1(?!`)/.exec(rest);
      if (code) {
        flush();
        const value = /^ .* $/s.test(code[2]) && code[2].trim() ? code[2].slice(1, -1) : code[2];
        out.push({ t: 'code', v: value });
        i += code[0].length;
      } else {
        const run = /^`+/.exec(rest) as RegExpExecArray;
        buffer += run[0];
        i += run[0].length;
      }
    } else if (char === '!' && text[i + 1] === '[') {
      const image = IMAGE.exec(rest);
      if (image) {
        // Not loaded: the alt text stands in for the picture.
        flush();
        out.push(...inline(image[1], depth + 1));
        i += image[0].length;
      } else {
        buffer += char;
        i++;
      }
    } else if (char === '[') {
      const link = LINK.exec(rest);
      const href = link ? safeHref(link[2]) : null;
      if (link) {
        flush();
        const children = inline(link[1], depth + 1);
        if (href) out.push({ t: 'link', href, c: children });
        else out.push(...children);
        i += link[0].length;
      } else {
        buffer += char;
        i++;
      }
    } else if (char === '<') {
      const auto = /^<((?:https?:\/\/|mailto:)[^\s<>]+)>/i.exec(rest);
      if (auto) {
        flush();
        out.push({ t: 'link', href: auto[1], c: [{ t: 'text', v: auto[1] }] });
        i += auto[0].length;
      } else {
        buffer += char;
        i++;
      }
    } else if (char === '*' || char === '_') {
      const { triple: triplePattern, strong: strongPattern, em: emPattern } = EMPHASIS[char];
      const triple = triplePattern.exec(rest);
      const strong = strongPattern.exec(rest);
      const em = emPattern.exec(rest);
      // Underscores inside a word, as in snake_case, are not emphasis.
      const wordy = (match: RegExpExecArray) =>
        char === '_' && (isWordChar(before) || isWordChar(text[i + match[0].length]));
      if (triple && !wordy(triple)) {
        flush();
        out.push({ t: 'strong', c: [{ t: 'em', c: inline(triple[1], depth + 1) }] });
        i += triple[0].length;
      } else if (strong && !wordy(strong)) {
        flush();
        out.push({ t: 'strong', c: inline(strong[1], depth + 1) });
        i += strong[0].length;
      } else if (em && !wordy(em)) {
        flush();
        out.push({ t: 'em', c: inline(em[1], depth + 1) });
        i += em[0].length;
      } else {
        buffer += char;
        i++;
      }
    } else if (char === '~') {
      const del = /^~~(?=\S)([\s\S]*?\S)~~/.exec(rest);
      if (del) {
        flush();
        out.push({ t: 'del', c: inline(del[1], depth + 1) });
        i += del[0].length;
      } else {
        buffer += char;
        i++;
      }
    } else {
      buffer += char;
      i++;
    }
  }
  flush();
  return out;
}

export function parseMarkdown(source: string): MdBlock[] {
  // Leading tabs count as four spaces, so nested lists indent the same however they were typed.
  const lines = source
    .replace(/\r\n?/g, '\n')
    .split('\n')
    .map((line) => line.replace(/^\t+/, (tabs) => '    '.repeat(tabs.length)));
  try {
    return parseBlocks(lines, 0);
  } catch (error) {
    if (!(error instanceof RangeError)) throw error;
    return [{ t: 'paragraph', c: [{ t: 'text', v: 'This is too deeply nested to preview.' }] }];
  }
}
