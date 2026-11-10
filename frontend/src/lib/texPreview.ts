// A small previewer for LaTeX resumes: it reads the subset resumes use and returns a page of
// blocks to draw, with no compile and no dependencies. It is an approximation. Fonts and line
// breaks differ from the real PDF, so the compiled PDF stays the exact version.
//
// Supported: \newcommand macros, \definecolor, geometry margins, titlesec section styles and
// rules, enumitem list spacing, sections, center and flush environments, itemize and enumerate,
// text styles and sizes, \href and \url, \\ and \hfill, \vspace, and common symbols. Anything else
// is skipped and listed in `unsupported`. Nothing is evaluated: no file is read and no link is
// followed, and the output is plain data.

export type Align = 'left' | 'center' | 'right';

export interface RunStyle {
  bold: boolean;
  italic: boolean;
  mono: boolean;
  underline: boolean;
  smallCaps: boolean;
  // Points.
  size: number;
  color: string | null;
  href: string | null;
}

export type Run =
  | { k: 'text'; text: string; style: RunStyle }
  | { k: 'hfill' }
  | { k: 'br'; gap: number }
  | { k: 'space'; width: number };

export interface ParagraphBlock {
  kind: 'paragraph';
  runs: Run[];
  align: Align;
  before: number;
  after: number;
  indent: number;
}

export interface SectionBlock {
  kind: 'section';
  runs: Run[];
  before: number;
  after: number;
  rule: string | null;
}

export interface ListItem {
  label: Run[] | null;
  blocks: Block[];
}

export interface ListBlock {
  kind: 'list';
  ordered: boolean;
  depth: number;
  leftMargin: number;
  before: number;
  itemGap: number;
  items: ListItem[];
}

export interface RuleBlock {
  kind: 'rule';
  color: string;
}

export interface PageBreakBlock {
  kind: 'pagebreak';
}

export type Block = ParagraphBlock | SectionBlock | ListBlock | RuleBlock | PageBreakBlock;

export interface TexDocument {
  // Points. Margins are the text area's distance from each edge.
  page: {
    width: number;
    height: number;
    margin: { top: number; right: number; bottom: number; left: number };
    fontSize: number;
  };
  blocks: Block[];
  // Commands and environments the preview skipped, so the page can say what it left out.
  unsupported: string[];
}

type Token =
  | { k: 'text'; v: string }
  | { k: 'cmd'; name: string }
  | { k: 'open' }
  | { k: 'close' }
  | { k: 'param'; n: number }
  | { k: 'par' };

const MAX_EXPANSIONS = 5_000;
const MAX_EXPANSION_DEPTH = 50;
const MAX_UNSUPPORTED = 20;

const PAPERS: Record<string, [number, number]> = {
  letter: [612, 792],
  a4: [595, 842],
  a5: [420, 595],
  legal: [612, 1008],
  executive: [522, 756],
};

const SIZES: Record<number, Record<string, number>> = {
  10: { tiny: 5, scriptsize: 7, footnotesize: 8, small: 9, normalsize: 10, large: 12, Large: 14.4, LARGE: 17.28, huge: 20.74, Huge: 24.88 },
  11: { tiny: 6, scriptsize: 8, footnotesize: 9, small: 10, normalsize: 10.95, large: 12, Large: 14.4, LARGE: 17.28, huge: 20.74, Huge: 24.88 },
  12: { tiny: 6, scriptsize: 8, footnotesize: 10, small: 10.95, normalsize: 12, large: 14.4, Large: 17.28, LARGE: 20.74, huge: 24.88, Huge: 24.88 },
};

const NAMED_COLORS: Record<string, string> = {
  black: '#000000',
  white: '#ffffff',
  red: '#ff0000',
  green: '#00ff00',
  blue: '#0000ff',
  cyan: '#00ffff',
  magenta: '#ff00ff',
  yellow: '#ffff00',
  gray: '#808080',
  darkgray: '#404040',
  lightgray: '#bfbfbf',
  orange: '#ff8000',
  purple: '#bf0040',
  brown: '#bf8040',
  olive: '#808000',
  teal: '#008080',
  violet: '#800080',
  pink: '#ffbfbf',
  lime: '#bfff00',
};

// Symbols that are a character whatever surrounds them.
const SYMBOLS: Record<string, string> = {
  '&': '&',
  '%': '%',
  $: '$',
  '#': '#',
  _: '_',
  '{': '{',
  '}': '}',
  ' ': ' ',
  ',': '\u2009',
  ';': '\u2005',
  ':': '\u2005',
  '!': '',
  '/': '',
  '-': '',
  '@': '',
  quad: '\u2003',
  qquad: '\u2003\u2003',
  enspace: '\u2002',
  thinspace: '\u2009',
  textbar: '|',
  textasciitilde: '~',
  textasciicircum: '^',
  textbackslash: '\\',
  textless: '<',
  textgreater: '>',
  textendash: '–',
  textemdash: '—',
  textbullet: '•',
  textperiodcentered: '·',
  textquotesingle: "'",
  textdegree: '°',
  textregistered: '®',
  texttrademark: '™',
  copyright: '©',
  pounds: '£',
  euro: '€',
  S: '§',
  P: '¶',
  ss: 'ß',
  ae: 'æ',
  AE: 'Æ',
  oe: 'œ',
  OE: 'Œ',
  o: 'ø',
  O: 'Ø',
  aa: 'å',
  AA: 'Å',
  ldots: '…',
  dots: '…',
  textellipsis: '…',
  LaTeX: 'LaTeX',
  TeX: 'TeX',
  bullet: '•',
  cdot: '·',
  times: '×',
};

const ACCENTS: Record<string, string> = {
  "'": '́',
  '`': '̀',
  '^': '̂',
  '"': '̈',
  '~': '̃',
  '=': '̄',
  '.': '̇',
};

const STYLE_ARGS: Record<string, Partial<RunStyle>> = {
  textbf: { bold: true },
  textmd: { bold: false },
  textit: { italic: true },
  textsl: { italic: true },
  textup: { italic: false },
  texttt: { mono: true },
  textrm: { mono: false },
  textsf: { mono: false },
  textsc: { smallCaps: true },
  underline: { underline: true },
  uline: { underline: true },
  textnormal: { bold: false, italic: false, mono: false, smallCaps: false },
  mbox: {},
  hbox: {},
  text: {},
  textsuperscript: {},
  textsubscript: {},
};

const STYLE_DECLARATIONS: Record<string, Partial<RunStyle>> = {
  bfseries: { bold: true },
  mdseries: { bold: false },
  itshape: { italic: true },
  slshape: { italic: true },
  upshape: { italic: false },
  ttfamily: { mono: true },
  rmfamily: { mono: false },
  sffamily: { mono: false },
  scshape: { smallCaps: true },
  normalfont: { bold: false, italic: false, mono: false, smallCaps: false },
};

// Commands that take this many arguments and do nothing visible.
const SWALLOWED: Record<string, number> = {
  label: 1,
  thispagestyle: 1,
  pagestyle: 1,
  hypersetup: 1,
  input: 1,
  include: 1,
  usepackage: 1,
  setcounter: 2,
  addtolength: 2,
  phantom: 1,
  vphantom: 1,
  hphantom: 1,
  footnote: 1,
  index: 1,
  cite: 1,
  ref: 1,
  pdfgentounicode: 0,
};

// Commands with no visible effect and no arguments.
const IGNORED = new Set([
  'noindent',
  'indent',
  'relax',
  'null',
  'ignorespaces',
  'protect',
  'strut',
  'leavevmode',
  'raggedbottom',
  'flushbottom',
  'sloppy',
  'fussy',
  'nobreak',
  'allowbreak',
  'linebreak',
  'nolinebreak',
  'hline',
  'maketitle',
  'tableofcontents',
  'clubpenalty',
  'widowpenalty',
  'pdfgentounicode',
]);

const DEFINITIONS = new Set(['newcommand', 'renewcommand', 'providecommand', 'definecolor', 'setlength']);

const SECTION_LEVELS: Record<string, number> = { section: 1, subsection: 2, subsubsection: 3 };

type Stop = { kind: 'eof' } | { kind: 'close' } | { kind: 'bracket' } | { kind: 'env'; name: string };

interface Macro {
  args: number;
  body: Token[];
}

interface ListLayout {
  leftmargin?: number;
  topsep?: number;
  itemsep?: number;
  parsep?: number;
}

interface TitleFormat {
  size: number | null;
  bold: boolean;
  color: string | null;
  rule: string | null;
  before: number | null;
  after: number | null;
}

const UNITS: Record<string, number> = {
  pt: 1,
  bp: 1,
  pc: 12,
  in: 72,
  cm: 72 / 2.54,
  mm: 72 / 25.4,
};

// Names come from the source, so lookups must not reach the prototype ("constructor" and so on).
function own<T>(table: Record<string, T>, name: string): T | undefined {
  return Object.hasOwn(table, name) ? table[name] : undefined;
}

function tokenize(src: string): Token[] {
  const out: Token[] = [];
  const n = src.length;
  let i = 0;
  // A comment ends its line's newline, which still counts toward a blank line after it.
  let carry = 0;
  const push = (token: Token) => {
    out.push(token);
    carry = 0;
  };
  const isSpace = (c: string) => c === ' ' || c === '\t' || c === '\n' || c === '\r';
  const isLetter = (c: string) => (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z');
  const isSpecial = (c: string) => '% \t\n\r\\{}#~$[]'.includes(c);

  while (i < n) {
    const c = src[i];
    if (c === '%') {
      while (i < n && src[i] !== '\n') i++;
      if (i < n) {
        i++;
        carry = 1;
      }
      while (i < n && (src[i] === ' ' || src[i] === '\t')) i++;
    } else if (isSpace(c)) {
      let newlines = carry;
      while (i < n && isSpace(src[i])) {
        if (src[i] === '\n') newlines++;
        i++;
      }
      push(newlines >= 2 ? { k: 'par' } : { k: 'text', v: ' ' });
    } else if (c === '\\') {
      i++;
      if (i >= n) break;
      if (isLetter(src[i])) {
        let j = i;
        while (j < n && isLetter(src[j])) j++;
        const name = src.slice(i, j);
        let newlines = 0;
        while (j < n && isSpace(src[j])) {
          if (src[j] === '\n') newlines++;
          j++;
        }
        push({ k: 'cmd', name });
        if (newlines >= 2) push({ k: 'par' });
        i = j;
      } else {
        push({ k: 'cmd', name: src[i] });
        i++;
      }
    } else if (c === '{') {
      push({ k: 'open' });
      i++;
    } else if (c === '}') {
      push({ k: 'close' });
      i++;
    } else if (c === '#') {
      const digit = src[i + 1];
      if (digit >= '1' && digit <= '9') {
        push({ k: 'param', n: Number(digit) });
        i += 2;
      } else {
        push({ k: 'text', v: '#' });
        i++;
      }
    } else if (c === '~') {
      push({ k: 'text', v: '\u00a0' });
      i++;
    } else if (c === '$') {
      // Math isn't drawn: its contents show as written.
      i++;
    } else if (c === '[' || c === ']') {
      push({ k: 'text', v: c });
      i++;
    } else {
      let j = i;
      while (j < n && !isSpecial(src[j])) j++;
      const text = src
        .slice(i, j)
        .replace(/---/g, '—')
        .replace(/--/g, '–')
        .replace(/``/g, '“')
        .replace(/''/g, '”')
        .replace(/`/g, '‘')
        .replace(/'/g, '’');
      push({ k: 'text', v: text });
      i = j;
    }
  }
  return out;
}

// Tokens come from the source, and from macro bodies pushed on top as they expand.
class Reader {
  private frames: { tokens: Token[]; index: number }[];

  constructor(tokens: Token[]) {
    this.frames = [{ tokens, index: 0 }];
  }

  get depth(): number {
    return this.frames.length;
  }

  peek(): Token | undefined {
    for (;;) {
      const frame = this.frames[this.frames.length - 1];
      if (frame.index < frame.tokens.length) return frame.tokens[frame.index];
      if (this.frames.length === 1) return undefined;
      this.frames.pop();
    }
  }

  next(): Token | undefined {
    const token = this.peek();
    if (token) this.frames[this.frames.length - 1].index++;
    return token;
  }

  push(tokens: Token[]): void {
    if (tokens.length) this.frames.push({ tokens, index: 0 });
  }
}

function plain(tokens: Token[], url = false): string {
  let text = '';
  for (const token of tokens) {
    if (token.k === 'text') text += url ? token.v.replace(/\u00a0/g, '~') : token.v;
    else if (token.k === 'cmd') {
      const symbol = own(SYMBOLS, token.name);
      if (symbol?.length === 1) text += symbol;
    }
  }
  return text;
}

function toPoints(length: string, em: number): number | null {
  const match = /^\s*(-?\d*\.?\d+)\s*(pt|bp|pc|in|cm|mm|em|ex)\s*$/.exec(length);
  if (!match) return null;
  const value = Number(match[1]);
  if (match[2] === 'em') return value * em;
  if (match[2] === 'ex') return value * em * 0.45;
  return value * UNITS[match[2]];
}

function safeHref(url: string): string | null {
  return /^(https?:|mailto:)/i.test(url.trim()) ? url.trim() : null;
}

function hex(value: number): string {
  return Math.max(0, Math.min(255, Math.round(value))).toString(16).padStart(2, '0');
}

class Parser {
  private reader: Reader;
  private macros = new Map<string, Macro>();
  private expansions = 0;
  private colors = new Map<string, string>();
  private inDocument: boolean;
  sawDocument = false;
  private ended = false;

  private fontSize = 10;
  private paper: [number, number] = PAPERS.letter;
  private margin: { top?: number; right?: number; bottom?: number; left?: number } = {};
  private parindent = 15;
  private parskip = 0;
  private listLayout = new Map<string, ListLayout>();
  private titleFormats = new Map<string, TitleFormat>();
  private titleSpacing = new Map<string, { before: number; after: number }>();

  private style: RunStyle = baseStyle(10);
  private align: Align = 'left';
  private para: ParagraphBlock | null = null;
  private blocks: Block[] = [];
  private lists: ListBlock[] = [];
  private pending = 0;
  // Whether the current paragraph's last run ends in a space, kept so a long paragraph isn't rescanned.
  private spaceEnds = false;
  readonly unsupported: string[] = [];

  constructor(
    tokens: Token[],
    startInDocument: boolean,
  ) {
    this.reader = new Reader(tokens);
    this.inDocument = startInDocument;
  }

  run(): TexDocument {
    this.process({ kind: 'eof' });
    this.endParagraph();
    const [width, height] = this.paper;
    const wide = (width - 345) / 2;
    return {
      page: {
        width,
        height,
        margin: {
          top: this.margin.top ?? 0.9 * 72,
          right: this.margin.right ?? wide,
          bottom: this.margin.bottom ?? 0.9 * 72,
          left: this.margin.left ?? wide,
        },
        fontSize: SIZES[this.fontSize].normalsize,
      },
      blocks: this.blocks,
      unsupported: this.unsupported,
    };
  }

  // ---- Reading arguments --------------------------------------------------------------

  private skipSpaces(): Token | undefined {
    for (;;) {
      const token = this.reader.peek();
      if (token && token.k === 'text' && token.v === ' ') this.reader.next();
      else return token;
    }
  }

  // One argument's tokens, without its braces: a group, or a single token.
  private readGroup(): Token[] | null {
    const first = this.skipSpaces();
    if (!first || first.k === 'close' || first.k === 'par') return null;
    this.reader.next();
    if (first.k === 'open') {
      const inner: Token[] = [];
      let depth = 1;
      for (let token = this.reader.next(); token; token = this.reader.next()) {
        if (token.k === 'open') depth++;
        if (token.k === 'close' && --depth === 0) break;
        inner.push(token);
      }
      return inner;
    }
    if (first.k === 'text' && first.v.length > 1) {
      const [char] = first.v;
      this.reader.push([{ k: 'text', v: first.v.slice(char.length) }]);
      return [{ k: 'text', v: char }];
    }
    return [first];
  }

  private readText(url = false): string {
    return plain(this.readGroup() ?? [], url);
  }

  // [ ... ] if one is next, as its tokens.
  private readOptional(skipSpaces: boolean): Token[] | null {
    const first = skipSpaces ? this.skipSpaces() : this.reader.peek();
    if (!first || first.k !== 'text' || first.v !== '[') return null;
    this.reader.next();
    const inner: Token[] = [];
    let depth = 0;
    for (let token = this.reader.next(); token; token = this.reader.next()) {
      if (token.k === 'open') depth++;
      if (token.k === 'close') depth--;
      if (token.k === 'text' && token.v === ']' && depth <= 0) break;
      inner.push(token);
    }
    return inner;
  }

  private takeStar(): boolean {
    const first = this.reader.peek();
    if (!first || first.k !== 'text' || first.v[0] !== '*') return false;
    this.reader.next();
    if (first.v.length > 1) this.reader.push([{ k: 'text', v: first.v.slice(1) }]);
    return true;
  }

  private length(tokens: Token[] | null): number | null {
    return tokens ? toPoints(plain(tokens), this.style.size) : null;
  }

  // ---- Text and paragraphs ------------------------------------------------------------

  private takePending(): number {
    const value = this.pending;
    this.pending = 0;
    return value;
  }

  private startParagraph(): ParagraphBlock {
    if (!this.para) {
      const previous = this.blocks[this.blocks.length - 1];
      this.para = {
        kind: 'paragraph',
        runs: [],
        align: this.align,
        before: this.takePending() + (previous ? this.parskip : 0),
        after: 0,
        indent: previous?.kind === 'paragraph' ? this.parindent : 0,
      };
    }
    return this.para;
  }

  private addText(text: string): void {
    if (!text) return;
    const onlySpaces = !text.replace(/ /g, '');
    if (!this.para && onlySpaces) return;
    const para = this.startParagraph();
    const last = para.runs[para.runs.length - 1];
    // A space doesn't start a line, and two don't sit together.
    const atLineStart = !last || last.k === 'br' || last.k === 'hfill';
    if (atLineStart || (last.k === 'text' && this.spaceEnds)) text = text.replace(/^ +/, '');
    if (!text) return;
    if (last && last.k === 'text' && last.style === this.style) last.text += text;
    else para.runs.push({ k: 'text', text, style: this.style });
    this.spaceEnds = text.endsWith(' ');
  }

  private addRun(run: Run): void {
    const para = this.startParagraph();
    this.trimEnd(para.runs);
    para.runs.push(run);
    this.spaceEnds = false;
  }

  private trimEnd(runs: Run[]): void {
    const last = runs[runs.length - 1];
    if (last && last.k === 'text') {
      last.text = last.text.replace(/ +$/, '');
      if (!last.text) runs.pop();
    }
  }

  private endParagraph(): void {
    const para = this.para;
    this.para = null;
    if (!para) return;
    while (para.runs.length && para.runs[para.runs.length - 1].k === 'br') para.runs.pop();
    this.trimEnd(para.runs);
    if (!para.runs.length) {
      this.pending += para.before + para.after;
      return;
    }
    this.pending += para.after;
    para.after = 0;
    this.blocks.push(para);
  }

  private addBlock(block: Block): void {
    this.endParagraph();
    this.blocks.push(block);
  }

  private vspace(points: number | null): void {
    if (points === null) return;
    if (this.para) this.para.after += points;
    else this.pending += points;
  }

  private currentRuns(): Run[] {
    return this.para ? this.para.runs : [];
  }

  // Run `fn` with a fresh paragraph and scratch blocks, and return the runs it made.
  private capture(fn: () => void): Run[] {
    const saved = { para: this.para, blocks: this.blocks, pending: this.pending };
    this.para = null;
    this.blocks = [];
    this.pending = 0;
    fn();
    const runs = this.currentRuns();
    this.para = saved.para;
    this.blocks = saved.blocks;
    this.pending = saved.pending;
    this.trimEnd(runs);
    this.spaceEnds = false;
    return runs;
  }

  // ---- The main loop ------------------------------------------------------------------

  private group(fn: () => void): void {
    const style = this.style;
    const align = this.align;
    fn();
    this.style = style;
    this.align = align;
  }

  private parseArg(patch: Partial<RunStyle> = {}): void {
    const first = this.skipSpaces();
    if (!first) return;
    this.group(() => {
      this.style = { ...this.style, ...patch };
      if (first.k === 'open') {
        this.reader.next();
        this.process({ kind: 'close' });
      } else if (first.k !== 'close' && first.k !== 'par') {
        this.processSingle();
      }
    });
  }

  private processSingle(): void {
    const token = this.reader.next();
    if (!token) return;
    if (token.k === 'text') {
      const char = String.fromCodePoint(token.v.codePointAt(0) ?? 32);
      if (token.v.length > char.length) {
        this.reader.push([{ k: 'text', v: token.v.slice(char.length) }]);
      }
      this.addText(char);
    } else if (token.k === 'cmd') {
      this.command(token.name, { kind: 'eof' });
    }
  }

  // Returns when the stop is reached, or at the end of the document.
  private process(stop: Stop): void {
    for (let token = this.reader.next(); token; token = this.reader.next()) {
      if (this.ended) return;
      if (token.k === 'text') {
        if (stop.kind === 'bracket' && token.v === ']') return;
        if (this.inDocument) this.addText(token.v);
      } else if (token.k === 'par') {
        if (this.inDocument) this.endParagraph();
      } else if (token.k === 'open') {
        this.group(() => this.process({ kind: 'close' }));
      } else if (token.k === 'close') {
        if (stop.kind === 'close') return;
      } else if (token.k === 'cmd') {
        if (this.command(token.name, stop)) return;
      }
    }
  }

  private noteUnsupported(name: string): void {
    if (this.inDocument && !this.unsupported.includes(name) && this.unsupported.length < MAX_UNSUPPORTED) {
      this.unsupported.push(name);
    }
  }

  // Returns true when this command ends the current stop.
  private command(name: string, stop: Stop): boolean {
    if (DEFINITIONS.has(name)) {
      this.definition(name);
      return false;
    }
    if (name === 'begin') {
      this.begin(this.readText().trim());
      return false;
    }
    if (name === 'end') {
      const env = this.readText().trim();
      if (env === 'document') {
        this.ended = true;
        return true;
      }
      return stop.kind === 'env' && stop.name === env;
    }
    if (!this.inDocument) {
      this.preamble(name);
      return false;
    }

    const macro = this.macros.get(name);
    if (macro) {
      this.expand(name, macro);
      return false;
    }
    const styleArg = own(STYLE_ARGS, name);
    if (styleArg) {
      this.parseArg(styleArg);
      return false;
    }
    const declaration = own(STYLE_DECLARATIONS, name);
    if (declaration) {
      this.style = { ...this.style, ...declaration };
      return false;
    }
    const size = own(SIZES[this.fontSize], name);
    if (size !== undefined) {
      this.style = { ...this.style, size };
      return false;
    }
    const symbol = own(SYMBOLS, name);
    if (symbol !== undefined) {
      this.addText(symbol);
      return false;
    }
    const accent = own(ACCENTS, name);
    if (accent) {
      const target = plain(this.readGroup() ?? []);
      this.addText(`${target[0] ?? ''}${accent}`.normalize('NFC') + target.slice(1));
      return false;
    }
    const level = own(SECTION_LEVELS, name);
    if (level !== undefined) {
      this.section(level, name);
      return false;
    }
    const swallowed = own(SWALLOWED, name);
    if (swallowed !== undefined) {
      this.readOptional(true);
      for (let count = 0; count < swallowed; count++) this.readGroup();
      return false;
    }
    if (IGNORED.has(name)) return false;

    switch (name) {
      case 'em':
        this.style = { ...this.style, italic: !this.style.italic };
        break;
      case 'emph':
        this.parseArg({ italic: !this.style.italic });
        break;
      case 'centering':
        this.align = 'center';
        break;
      case 'raggedright':
        this.align = 'left';
        break;
      case 'raggedleft':
        this.align = 'right';
        break;
      case 'par':
        this.endParagraph();
        break;
      case 'newline':
        this.addRun({ k: 'br', gap: 0 });
        break;
      case '\\':
        this.takeStar();
        this.addRun({ k: 'br', gap: this.length(this.readOptional(false)) ?? 0 });
        break;
      case 'hfill':
        this.addRun({ k: 'hfill' });
        break;
      case 'hspace': {
        this.takeStar();
        this.addRun({ k: 'space', width: toPoints(this.readText(), this.style.size) ?? 0 });
        break;
      }
      case 'vspace':
      case 'addvspace': {
        this.takeStar();
        this.vspace(toPoints(this.readText(), this.style.size));
        break;
      }
      case 'smallskip':
        this.vspace(3);
        break;
      case 'medskip':
        this.vspace(6);
        break;
      case 'bigskip':
        this.vspace(12);
        break;
      case 'newpage':
      case 'clearpage':
      case 'pagebreak':
        this.addBlock({ kind: 'pagebreak' });
        break;
      case 'item':
        this.item();
        break;
      case 'href': {
        const url = safeHref(this.readText(true));
        this.parseArg({ href: url });
        break;
      }
      case 'url': {
        const url = this.readText(true);
        this.group(() => {
          this.style = { ...this.style, href: safeHref(url), mono: true };
          this.addText(url);
        });
        break;
      }
      case 'textcolor':
      case 'color': {
        const model = plain(this.readOptional(true) ?? []) || null;
        const color = this.color(model, this.readText());
        if (name === 'color') {
          if (color) this.style = { ...this.style, color };
        } else {
          this.parseArg(color ? { color } : {});
        }
        break;
      }
      case 'titlerule':
        this.addBlock({ kind: 'rule', color: this.style.color ?? '#000000' });
        break;
      case 'today':
        this.addText(new Date().toLocaleDateString(undefined, { dateStyle: 'long' }));
        break;
      case 'rule': {
        this.readOptional(true);
        const width = toPoints(this.readText(), this.style.size);
        this.readGroup();
        this.addRun({ k: 'space', width: width ?? 0 });
        break;
      }
      default:
        this.noteUnsupported(name);
    }
    return false;
  }

  private expand(name: string, macro: Macro): void {
    if (this.expansions++ > MAX_EXPANSIONS || this.reader.depth > MAX_EXPANSION_DEPTH) {
      this.noteUnsupported(name);
      return;
    }
    const args: Token[][] = [];
    for (let count = 0; count < macro.args; count++) args.push(this.readGroup() ?? []);
    const expanded: Token[] = [];
    for (const token of macro.body) {
      if (token.k === 'param') expanded.push(...(args[token.n - 1] ?? []));
      else expanded.push(token);
    }
    this.reader.push(expanded);
  }

  // ---- Definitions and the preamble ---------------------------------------------------

  private definition(name: string): void {
    if (name === 'definecolor') {
      const key = this.readText().trim();
      const model = this.readText().trim();
      const spec = this.readText().trim();
      const color = this.color(model, spec);
      if (key && color) this.colors.set(key, color);
    } else if (name === 'setlength') {
      const target = this.readCommandName();
      const points = toPoints(this.readText(), this.style.size);
      if (points === null) return;
      if (target === 'parindent') this.parindent = points;
      if (target === 'parskip') this.parskip = points;
    } else {
      const target = this.readGroup();
      const macroName = target?.find((token) => token.k === 'cmd');
      const count = Number(plain(this.readOptional(true) ?? [])) || 0;
      // An optional default for the first argument isn't supported: its text is skipped.
      this.readOptional(true);
      const body = this.readGroup() ?? [];
      if (!macroName || macroName.k !== 'cmd') return;
      if (name === 'providecommand' && this.macros.has(macroName.name)) return;
      this.macros.set(macroName.name, { args: Math.min(count, 9), body });
    }
  }

  private color(model: string | null, spec: string): string | null {
    const value = spec.trim();
    if (!model) return this.colors.get(value) ?? own(NAMED_COLORS, value) ?? null;
    const numbers = value.split(',').map((part) => Number(part.trim()));
    if (/^html$/i.test(model)) return /^[0-9a-f]{6}$/i.test(value) ? `#${value.toLowerCase()}` : null;
    if (numbers.some((number) => Number.isNaN(number))) return null;
    if (model === 'RGB' && numbers.length === 3) return `#${numbers.map(hex).join('')}`;
    if (model === 'rgb' && numbers.length === 3) return `#${numbers.map((part) => hex(part * 255)).join('')}`;
    if (model === 'gray' && numbers.length === 1) return `#${hex(numbers[0] * 255).repeat(3)}`;
    if (model === 'cmyk' && numbers.length === 4) {
      const [c, m, y, k] = numbers;
      return `#${[c, m, y].map((part) => hex(255 * (1 - part) * (1 - k))).join('')}`;
    }
    return null;
  }

  private preamble(name: string): void {
    if (name === 'documentclass') {
      const options = plain(this.readOptional(true) ?? []);
      this.readGroup();
      const size = /\b(10|11|12)pt\b/.exec(options);
      if (size) this.fontSize = Number(size[1]);
      const paper = /\b(letter|a4|a5|legal|executive)paper\b/.exec(options);
      if (paper) this.paper = PAPERS[paper[1]];
      this.style = baseStyle(this.fontSize);
    } else if (name === 'usepackage') {
      const options = plain(this.readOptional(true) ?? []);
      if (this.readText().trim() === 'geometry') this.geometry(options);
    } else if (name === 'geometry') {
      this.geometry(this.readText());
    } else if (name === 'setlist') {
      const target = plain(this.readOptional(true) ?? []).trim() || 'all';
      const keys = this.readText();
      const read = (key: string) => {
        const found = new RegExp(`${key}\\s*=\\s*([^,]+)`).exec(keys);
        return found ? toPoints(found[1], SIZES[this.fontSize].normalsize) : null;
      };
      const layout: ListLayout = {};
      for (const key of ['leftmargin', 'topsep', 'itemsep', 'parsep'] as const) {
        const value = read(key);
        if (value !== null) layout[key] = value;
      }
      this.listLayout.set(target, { ...this.listLayout.get(target), ...layout });
    } else if (name === 'titleformat') {
      this.titleFormat();
    } else if (name === 'titlespacing') {
      this.takeStar();
      const target = this.readCommandName();
      this.readGroup();
      const em = SIZES[this.fontSize].normalsize;
      const before = toPoints(this.readText(), em);
      const after = toPoints(this.readText(), em);
      if (target && before !== null && after !== null) this.titleSpacing.set(target, { before, after });
    }
  }

  private readCommandName(): string | null {
    const found = (this.readGroup() ?? []).find((token) => token.k === 'cmd');
    return found && found.k === 'cmd' ? found.name : null;
  }

  private geometry(options: string): void {
    for (const match of options.matchAll(/(\w+)\s*=\s*([^,]+)/g)) {
      const points = toPoints(match[2], 10);
      if (points === null) continue;
      const key = match[1];
      if (key === 'margin') this.margin = { top: points, right: points, bottom: points, left: points };
      if (key === 'hmargin') this.margin = { ...this.margin, left: points, right: points };
      if (key === 'vmargin') this.margin = { ...this.margin, top: points, bottom: points };
      if (key === 'top' || key === 'bottom' || key === 'left' || key === 'right') {
        this.margin = { ...this.margin, [key]: points };
      }
    }
    const paper = /(letter|a4|a5|legal|executive)paper/.exec(options);
    if (paper) this.paper = PAPERS[paper[1]];
  }

  private titleFormat(): void {
    const target = this.readCommandName();
    this.readOptional(true);
    const format = this.readGroup() ?? [];
    this.readGroup();
    this.readGroup();
    this.readGroup();
    const after = this.readOptional(true) ?? [];

    const sizes = SIZES[this.fontSize];
    const spec: TitleFormat = { size: null, bold: false, color: null, rule: null, before: null, after: null };
    const scan = (tokens: Token[]) => {
      tokens.forEach((token, index) => {
        if (token.k !== 'cmd') return;
        if (token.name in sizes) spec.size = sizes[token.name];
        if (token.name === 'bfseries') spec.bold = true;
        if (token.name === 'color') {
          const next = tokens[index + 2];
          spec.color = this.color(null, next && next.k === 'text' ? next.v : '') ?? spec.color;
        }
      });
    };
    scan(format);
    scan(after);
    if (after.some((token) => token.k === 'cmd' && token.name === 'titlerule')) {
      spec.rule = spec.color ?? '#000000';
    }
    if (target) this.titleFormats.set(target, spec);
  }

  // ---- Sections, lists and environments -----------------------------------------------

  private section(level: number, name: string): void {
    this.endParagraph();
    this.takeStar();
    this.readOptional(true);
    const format = this.titleFormats.get(name);
    const sizes = SIZES[this.fontSize];
    const defaults = [0, sizes.Large, sizes.large, sizes.normalsize][level];
    const spacing = this.titleSpacing.get(name) ?? {
      before: [0, 14, 12, 10][level],
      after: [0, 8, 6, 4][level],
    };
    const style: RunStyle = {
      ...this.style,
      bold: format ? format.bold : true,
      size: format?.size ?? defaults,
      color: format?.color ?? this.style.color,
    };
    const runs = this.capture(() => {
      this.group(() => {
        this.style = style;
        this.parseArg();
      });
    });
    const before = this.takePending() + spacing.before;
    this.blocks.push({ kind: 'section', runs, before, after: spacing.after, rule: format?.rule ?? null });
  }

  private begin(env: string): void {
    if (env === 'document') {
      this.inDocument = true;
      this.sawDocument = true;
      this.style = baseStyle(this.fontSize);
      return;
    }
    if (!this.inDocument) return;
    if (env === 'center' || env === 'flushleft' || env === 'flushright') {
      this.endParagraph();
      this.group(() => {
        this.align = env === 'center' ? 'center' : env === 'flushright' ? 'right' : 'left';
        this.process({ kind: 'env', name: env });
        this.endParagraph();
      });
      return;
    }
    if (env === 'itemize' || env === 'enumerate' || env === 'description') {
      this.list(env);
      return;
    }
    this.noteUnsupported(env);
    this.group(() => this.process({ kind: 'env', name: env }));
  }

  private list(env: string): void {
    this.endParagraph();
    const depth = this.lists.length;
    const layout = {
      ...this.listLayout.get('all'),
      ...this.listLayout.get(env === 'enumerate' ? 'enumerate' : 'itemize'),
    };
    const topsep = layout.topsep ?? (depth === 0 ? 8 : 4);
    const list: ListBlock = {
      kind: 'list',
      ordered: env === 'enumerate',
      depth,
      leftMargin: layout.leftmargin ?? [25, 22, 19, 17][Math.min(depth, 3)],
      before: this.takePending() + topsep,
      itemGap: (layout.itemsep ?? (depth === 0 ? 4 : 2)) + (layout.parsep ?? (depth === 0 ? 4 : 2)),
      items: [],
    };
    const outer = this.blocks;
    this.blocks = [];
    this.lists.push(list);
    this.group(() => {
      this.process({ kind: 'env', name: env });
      this.endParagraph();
    });
    this.lists.pop();
    this.blocks = outer;
    if (list.items.length) this.blocks.push(list);
    this.pending = topsep;
  }

  private item(): void {
    const list = this.lists[this.lists.length - 1];
    if (!list) return;
    this.endParagraph();
    let label: Run[] | null = null;
    const next = this.skipSpaces();
    if (next && next.k === 'text' && next.v === '[') {
      this.reader.next();
      label = this.capture(() => this.process({ kind: 'bracket' }));
    }
    const entry: ListItem = { label, blocks: [] };
    list.items.push(entry);
    this.blocks = entry.blocks;
    this.pending = 0;
  }
}

function baseStyle(fontSize: number): RunStyle {
  return {
    bold: false,
    italic: false,
    mono: false,
    underline: false,
    smallCaps: false,
    size: SIZES[fontSize].normalsize,
    color: null,
    href: null,
  };
}

export function parseTex(source: string): TexDocument {
  const tokens = tokenize(source);
  try {
    const parser = new Parser(tokens, false);
    const doc = parser.run();
    if (parser.sawDocument) return doc;
    // No \begin{document}: treat the source as a fragment of body text.
    return new Parser(tokens, true).run();
  } catch (error) {
    if (!(error instanceof RangeError)) throw error;
    return {
      page: { width: 612, height: 792, margin: { top: 65, right: 65, bottom: 65, left: 65 }, fontSize: 10 },
      blocks: [],
      unsupported: ['too deeply nested to preview'],
    };
  }
}
