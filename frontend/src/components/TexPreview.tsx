import { Box, Text } from '@mantine/core';
import { useElementSize } from '@mantine/hooks';
import type { CSSProperties, ReactNode } from 'react';
import { useMemo } from 'react';

import {
  parseTex,
  type Block,
  type ListBlock,
  type ParagraphBlock,
  type Run,
  type SectionBlock,
  type TexDocument,
} from '../lib/texPreview';

// The PDF is set in Source Sans. This is the closest face most computers have.
const PAPER_FONT = "'Source Sans 3', 'Source Sans Pro', 'Helvetica Neue', Arial, sans-serif";
const MONO_FONT = "'SFMono-Regular', Menlo, Consolas, monospace";

interface Line {
  runs: Run[];
  gap: number;
}

function splitLines(runs: Run[]): Line[] {
  const lines: Line[] = [{ runs: [], gap: 0 }];
  for (const run of runs) {
    if (run.k === 'br') lines.push({ runs: [], gap: run.gap });
    else lines[lines.length - 1].runs.push(run);
  }
  return lines;
}

function RunView({ run, baseSize }: { run: Run; baseSize: number }) {
  if (run.k === 'space') {
    return <span style={{ display: 'inline-block', width: `${run.width}pt` }} />;
  }
  if (run.k !== 'text') return null;
  const { style } = run;
  const css: CSSProperties = {
    fontWeight: style.bold ? 700 : undefined,
    fontStyle: style.italic ? 'italic' : undefined,
    fontFamily: style.mono ? MONO_FONT : undefined,
    textDecoration: style.underline ? 'underline' : undefined,
    fontVariant: style.smallCaps ? 'small-caps' : undefined,
    fontSize: style.size !== baseSize ? `${style.size}pt` : undefined,
    color: style.color ?? undefined,
  };
  if (style.href) {
    return (
      <a href={style.href} target="_blank" rel="noreferrer noopener" style={{ ...css, color: css.color ?? 'inherit' }}>
        {run.text}
      </a>
    );
  }
  return <span style={css}>{run.text}</span>;
}

function RunsView({ runs, baseSize }: { runs: Run[]; baseSize: number }) {
  return (
    <>
      {runs.map((run, index) => (
        <RunView key={index} run={run} baseSize={baseSize} />
      ))}
    </>
  );
}

function trimmed(runs: Run[]): Run[] {
  const copy = runs.map((run) => (run.k === 'text' ? { ...run } : run));
  const first = copy[0];
  if (first && first.k === 'text') first.text = first.text.replace(/^ +/, '');
  const last = copy[copy.length - 1];
  if (last && last.k === 'text') last.text = last.text.replace(/ +$/, '');
  return copy;
}

// A line with \hfill is a row whose parts are pushed apart. Without one it is just text.
function LineView({ line, baseSize, first }: { line: Line; baseSize: number; first: boolean }) {
  const marginTop = first ? undefined : `${line.gap}pt`;
  const parts: Run[][] = [[]];
  for (const run of line.runs) {
    if (run.k === 'hfill') parts.push([]);
    else parts[parts.length - 1].push(run);
  }
  if (parts.length === 1) {
    return (
      <div style={{ marginTop, minHeight: '1.2em' }}>
        <RunsView runs={line.runs} baseSize={baseSize} />
      </div>
    );
  }
  return (
    <div style={{ marginTop, display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: '1em' }}>
      {parts.map((part, index) => (
        <span key={index}>
          <RunsView runs={trimmed(part)} baseSize={baseSize} />
        </span>
      ))}
    </div>
  );
}

function Lines({ runs, baseSize }: { runs: Run[]; baseSize: number }) {
  return (
    <>
      {splitLines(runs).map((line, index) => (
        <LineView key={index} line={line} baseSize={baseSize} first={index === 0} />
      ))}
    </>
  );
}

function ParagraphView({ block, baseSize }: { block: ParagraphBlock; baseSize: number }) {
  return (
    <div style={{ marginTop: `${block.before}pt`, textAlign: block.align, textIndent: `${block.indent}pt` }}>
      <Lines runs={block.runs} baseSize={baseSize} />
    </div>
  );
}

function SectionView({ block, baseSize }: { block: SectionBlock; baseSize: number }) {
  return (
    <div style={{ marginTop: `${block.before}pt`, marginBottom: `${block.after}pt` }}>
      <Lines runs={block.runs} baseSize={baseSize} />
      {block.rule && <div style={{ height: '0.4pt', background: block.rule, marginTop: '2pt' }} />}
    </div>
  );
}

function ListView({ block, baseSize }: { block: ListBlock; baseSize: number }) {
  const bullet = block.depth === 0 ? '•' : '–';
  // The label sits in the margin, ending a little before the text starts.
  const labelWidth = Math.max(block.leftMargin - 4, 0);
  return (
    <div role="list" style={{ marginTop: `${block.before}pt` }}>
      {block.items.map((item, index) => (
        <div
          key={index}
          role="listitem"
          style={{
            position: 'relative',
            paddingLeft: `${block.leftMargin}pt`,
            marginTop: index === 0 ? undefined : `${block.itemGap}pt`,
          }}
        >
          <span
            style={{ position: 'absolute', left: 0, width: `${labelWidth}pt`, textAlign: 'right', whiteSpace: 'nowrap' }}
          >
            {item.label ? <RunsView runs={item.label} baseSize={baseSize} /> : block.ordered ? `${index + 1}.` : bullet}
          </span>
          <Blocks blocks={item.blocks} baseSize={baseSize} />
        </div>
      ))}
    </div>
  );
}

function BlockView({ block, baseSize }: { block: Block; baseSize: number }) {
  switch (block.kind) {
    case 'paragraph':
      return <ParagraphView block={block} baseSize={baseSize} />;
    case 'section':
      return <SectionView block={block} baseSize={baseSize} />;
    case 'list':
      return <ListView block={block} baseSize={baseSize} />;
    case 'rule':
      return <div style={{ height: '0.4pt', background: block.color, marginTop: '2pt' }} />;
    case 'pagebreak':
      return <div style={{ borderTop: '1px dashed #9aa1ab', margin: '8pt 0' }} />;
  }
}

function Blocks({ blocks, baseSize }: { blocks: Block[]; baseSize: number }): ReactNode {
  // A column, so a negative \vspace pulls the next block up instead of collapsing away.
  return (
    <div style={{ display: 'flex', flexDirection: 'column' }}>
      {blocks.map((block, index) => (
        <BlockView key={index} block={block} baseSize={baseSize} />
      ))}
    </div>
  );
}

function Paper({ doc, zoom }: { doc: TexDocument; zoom: number }) {
  const { page } = doc;
  const { margin } = page;
  return (
    <div
      style={{
        zoom,
        width: `${page.width}pt`,
        minHeight: `${page.height}pt`,
        boxSizing: 'border-box',
        padding: `${margin.top}pt ${margin.right}pt ${margin.bottom}pt ${margin.left}pt`,
        background: '#ffffff',
        color: '#000000',
        fontFamily: PAPER_FONT,
        fontSize: `${page.fontSize}pt`,
        lineHeight: 1.2,
        boxShadow: '0 1px 6px rgba(0, 0, 0, 0.35)',
      }}
    >
      <Blocks blocks={doc.blocks} baseSize={page.fontSize} />
    </div>
  );
}

export function TexPreview({ source }: { source: string }) {
  const doc = useMemo(() => parseTex(source), [source]);
  const { ref, width } = useElementSize();
  // 1 pt is 4/3 CSS pixels. Shrink the sheet to fit a narrow window, never enlarge it.
  const sheetWidth = (doc.page.width * 4) / 3;
  const zoom = width > 0 ? Math.min(1, width / sheetWidth) : 1;

  return (
    <Box ref={ref}>
      <Text size="xs" c="dimmed" mb="xs">
        A quick approximation: fonts and line breaks differ from the PDF, which is the exact version.
        {doc.unsupported.length > 0 && ` Left out: ${doc.unsupported.join(', ')}.`}
      </Text>
      <Box style={{ overflow: 'hidden' }}>
        <Paper doc={doc} zoom={zoom} />
      </Box>
    </Box>
  );
}

// The preview in a scrolling box of its own. Not a Mantine ScrollArea: that sizes its content to fit
// its child, so the sheet would widen its own container and never shrink to a narrow window.
export function TexPreviewPane({
  source,
  height,
  maxHeight,
}: {
  source: string;
  height?: string;
  maxHeight?: string;
}) {
  return (
    <div style={{ height, maxHeight, overflowY: 'auto', overflowX: 'hidden' }}>
      <TexPreview source={source} />
    </div>
  );
}
