import { describe, expect, it } from 'vitest';

import { afterSave, keepDrafts } from './factSheet';

describe('drafts after a save', () => {
  it('clears the draft that was saved', () => {
    expect(afterSave({ a: 'saved text' }, 'a', 'saved text')).toEqual({});
  });

  it('keeps what was typed while the save was in flight', () => {
    // Cmd+S sent "saved text", then the person kept typing.
    expect(afterSave({ a: 'saved text and more' }, 'a', 'saved text')).toEqual({
      a: 'saved text and more',
    });
  });

  it("leaves other profiles' drafts alone", () => {
    expect(afterSave({ a: 'x', b: 'y' }, 'a', 'x')).toEqual({ b: 'y' });
  });

  it('keeps only the drafts asked for', () => {
    expect(keepDrafts({ a: 'x', b: 'y' }, (id) => id === 'b')).toEqual({ b: 'y' });
  });
});
