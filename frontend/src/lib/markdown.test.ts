import { describe, expect, it } from 'vitest';

import { parseMarkdown } from './markdown';

/** Every link target the parser produced, anywhere in the document. */
function hrefs(source: string): string[] {
  const found: string[] = [];
  JSON.stringify(parseMarkdown(source), (key, value) => {
    if (key === 'href' && typeof value === 'string') found.push(value);
    return value;
  });
  return found;
}

describe('links', () => {
  it('keeps http, https and mailto links', () => {
    expect(
      hrefs('[a](https://acme.io/jobs) [b](http://acme.io) [c](mailto:hr@example.com)'),
    ).toEqual(['https://acme.io/jobs', 'http://acme.io', 'mailto:hr@example.com']);
  });

  it.each([
    'javascript:alert(1)',
    'JavaScript:alert(1)',
    'JAVASCRIPT:alert(1)',
    ' javascript:alert(1)',
    'data:text/html,<script>alert(1)</script>',
    'vbscript:msgbox(1)',
    'file:///etc/passwd',
    '//evil.example/x',
  ])('never makes a link from %s', (target) => {
    expect(hrefs(`[click](${target})`)).toEqual([]);
  });

  it('shows images as their alt text and loads nothing', () => {
    const blocks = parseMarkdown('![Company logo](https://tracker.example/pixel.png)');
    expect(hrefs('![Company logo](https://tracker.example/pixel.png)')).toEqual([]);
    expect(JSON.stringify(blocks)).toContain('Company logo');
    expect(JSON.stringify(blocks)).not.toContain('tracker.example');
  });
});

describe('guards against slow or deep input', () => {
  it.each([
    ['stray asterisks', '*a '.repeat(30_000)],
    ['stray underscores', '_'.repeat(100_000)],
    ['unclosed links', '[x]('.repeat(25_000)],
    ['blank lines', '\n'.repeat(100_000)],
    ['backticks', '`'.repeat(100_000)],
  ])('parses %s quickly', (_name, source) => {
    const started = performance.now();
    parseMarkdown(source);
    // Generous, so a slow machine doesn't fail it; the bugs these guard took tens of seconds.
    expect(performance.now() - started).toBeLessThan(2_000);
  });

  it('caps how deep quotes and lists nest', () => {
    const quoted = parseMarkdown(`${'> '.repeat(200)}deep`);
    let depth = 0;
    let block = quoted[0];
    while (block && block.t === 'quote') {
      depth += 1;
      block = block.c[0];
    }
    expect(depth).toBeLessThanOrEqual(9);
    expect(() => parseMarkdown(`${'  '.repeat(200)}- item`)).not.toThrow();
  });
});

describe('the fact sheet table', () => {
  it('reads a skills status table', () => {
    const [heading, table] = parseMarkdown(
      '## Skills status\n| Skill | Status |\n| --- | --- |\n| Python | Claimable |',
    );
    expect(heading.t).toBe('heading');
    expect(table.t).toBe('table');
    if (table.t === 'table') expect(table.rows).toHaveLength(1);
  });
});
