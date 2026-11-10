import { describe, expect, it } from 'vitest';

import { parseTex } from './texPreview';

const wrap = (body: string) => `\\documentclass{article}\\begin{document}${body}\\end{document}`;

function hrefs(body: string): string[] {
  const found: string[] = [];
  JSON.stringify(parseTex(wrap(body)), (key, value) => {
    if (key === 'href' && typeof value === 'string') found.push(value);
    return value;
  });
  return found;
}

describe('links in a resume preview', () => {
  it('keeps web and mail links', () => {
    expect(hrefs('\\href{https://acme.io}{Acme} \\url{http://acme.io/cv} \\href{mailto:me@example.com}{me}')).toEqual([
      'https://acme.io',
      'http://acme.io/cv',
      'mailto:me@example.com',
    ]);
  });

  it.each(['javascript:alert(1)', 'JavaScript:alert(1)', ' javascript:alert(1)', 'data:text/html,x'])(
    'never makes a link from %s',
    (target) => {
      expect(hrefs(`\\href{${target}}{click} \\url{${target}}`)).toEqual([]);
    },
  );
});

describe('guards against runaway input', () => {
  it('stops a macro that expands itself', () => {
    const started = performance.now();
    expect(() => parseTex(wrap('\\newcommand{\\loop}{\\loop x}\\loop'))).not.toThrow();
    expect(performance.now() - started).toBeLessThan(2_000);
  });

  it('reads a very long resume quickly', () => {
    const started = performance.now();
    parseTex(wrap('\\textbf{Python} and \\emph{AWS}. '.repeat(5_000)));
    expect(performance.now() - started).toBeLessThan(2_000);
  });
});
