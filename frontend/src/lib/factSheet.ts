// What the inline fact sheet panel and the side-by-side editor share, so they can't drift apart.

// Matches the server's limit on a fact sheet.
export const FACT_SHEET_MAX_LENGTH = 100_000;

// Tailoring reads the table under "## Skills status", so the example shows that format.
export const FACT_SHEET_PLACEHOLDER = [
  '## Skills status',
  '| Skill | Status |',
  '| --- | --- |',
  '| Python | Claimable |',
  '| Kubernetes | Inside the Example Corp bullet only |',
  '| Java | Never claim |',
].join('\n');

/** Unsaved text by the profile whose fact sheet it was typed into. */
export type Drafts = Record<string, string>;

export function keepDrafts(drafts: Drafts, keep: (profileId: string) => boolean): Drafts {
  return Object.fromEntries(Object.entries(drafts).filter(([profileId]) => keep(profileId)));
}

/**
 * The drafts once a save of `saved` to `profileId` has finished. The draft goes only if it's
 * still what was saved, so text typed while the save was in flight isn't lost.
 */
export function afterSave(drafts: Drafts, profileId: string, saved: string): Drafts {
  return drafts[profileId] === saved ? keepDrafts(drafts, (id) => id !== profileId) : drafts;
}
