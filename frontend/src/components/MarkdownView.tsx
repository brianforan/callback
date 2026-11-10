import { Typography } from '@mantine/core';
import type { ReactNode } from 'react';
import { useMemo } from 'react';

import {
  parseMarkdown,
  type Align,
  type Inline,
  type ListItem,
  type MdBlock,
} from '../lib/markdown';

function InlineView({ nodes }: { nodes: Inline[] }) {
  return (
    <>
      {nodes.map((node, index) => {
        switch (node.t) {
          case 'text':
            return node.v;
          case 'code':
            return <code key={index}>{node.v}</code>;
          case 'br':
            return <br key={index} />;
          case 'strong':
            return (
              <strong key={index}>
                <InlineView nodes={node.c} />
              </strong>
            );
          case 'em':
            return (
              <em key={index}>
                <InlineView nodes={node.c} />
              </em>
            );
          case 'del':
            return (
              <del key={index}>
                <InlineView nodes={node.c} />
              </del>
            );
          case 'link':
            return (
              <a key={index} href={node.href} target="_blank" rel="noreferrer noopener">
                <InlineView nodes={node.c} />
              </a>
            );
        }
      })}
    </>
  );
}

function TaskBox({ done }: { done: boolean }) {
  return (
    <span aria-label={done ? 'Done' : 'Not done'} style={{ marginRight: '0.4em' }}>
      {done ? '☑' : '☐'}
    </span>
  );
}

function ItemView({ item }: { item: ListItem }) {
  // An item that is only one paragraph reads as plain text, without a paragraph's spacing.
  const only = item.blocks.length === 1 && item.blocks[0].t === 'paragraph' ? item.blocks[0] : null;
  return (
    <li style={item.task === null ? undefined : { listStyle: 'none', marginLeft: '-1.2em' }}>
      {item.task !== null && <TaskBox done={item.task} />}
      {only ? <InlineView nodes={only.c} /> : <BlocksView blocks={item.blocks} />}
    </li>
  );
}

function alignStyle(align: Align) {
  return align ? { textAlign: align } : undefined;
}

function BlockView({ block }: { block: MdBlock }): ReactNode {
  switch (block.t) {
    case 'heading': {
      const Tag = `h${block.level}` as const;
      return (
        <Tag>
          <InlineView nodes={block.c} />
        </Tag>
      );
    }
    case 'paragraph':
      return (
        <p>
          <InlineView nodes={block.c} />
        </p>
      );
    case 'list': {
      const items = block.items.map((item, index) => <ItemView key={index} item={item} />);
      return block.ordered ? <ol start={block.start}>{items}</ol> : <ul>{items}</ul>;
    }
    case 'quote':
      return (
        <blockquote>
          <BlocksView blocks={block.c} />
        </blockquote>
      );
    case 'code':
      return (
        <pre>
          <code>{block.v}</code>
        </pre>
      );
    case 'rule':
      return <hr />;
    case 'table':
      return (
        <table>
          <thead>
            <tr>
              {block.head.map((cell, column) => (
                <th key={column} style={alignStyle(block.align[column])}>
                  <InlineView nodes={cell} />
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {block.rows.map((row, index) => (
              <tr key={index}>
                {row.map((cell, column) => (
                  <td key={column} style={alignStyle(block.align[column])}>
                    <InlineView nodes={cell} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      );
  }
}

function BlocksView({ blocks }: { blocks: MdBlock[] }) {
  return (
    <>
      {blocks.map((block, index) => (
        <BlockView key={index} block={block} />
      ))}
    </>
  );
}

// Markdown drawn as real elements, never as HTML, so nothing typed into it can add markup.
export function MarkdownView({ source }: { source: string }) {
  const blocks = useMemo(() => parseMarkdown(source), [source]);
  return (
    <Typography>
      <BlocksView blocks={blocks} />
    </Typography>
  );
}
