import { notifications } from '@mantine/notifications';
import type { KeyboardEvent } from 'react';
import { useEffect, useState } from 'react';

import { afterSave, keepDrafts, type Drafts } from './factSheet';
import { useFactSheet, useProfiles, useSaveFactSheet } from './queries';

/**
 * The active profile's fact sheet with a draft on top, for both fact sheet editors.
 *
 * Drafts are kept per profile. Switching profiles loads another sheet, and a draft typed into the
 * previous one stays here, out of sight, so it's never saved over the sheet now showing. Saving
 * sends the profile the draft belongs to, and keeps whatever was typed while the save was in
 * flight.
 */
export function useFactSheetDraft() {
  const { data: sheet, isPending, isError } = useFactSheet();
  const save = useSaveFactSheet();
  const profiles = useProfiles();
  // Empty until the first keystroke, so the saved text shows without being copied into state.
  const [drafts, setDrafts] = useState<Drafts>({});

  const saved = sheet?.content ?? '';
  const draft = sheet ? (drafts[sheet.profile_id] ?? null) : null;
  const strandedIds = sheet ? Object.keys(drafts).filter((id) => id !== sheet.profile_id) : [];
  const stranded = strandedIds.length > 0;
  const strandedNames = strandedIds
    .map((id) => profiles.data?.profiles.find((profile) => profile.id === id)?.name)
    .filter((name): name is string => Boolean(name));
  const content = draft ?? saved;
  const dirty = draft !== null && draft !== saved;

  useEffect(() => {
    if (!dirty && !stranded) return undefined;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty, stranded]);

  function edit(text: string) {
    if (!sheet) return;
    const profileId = sheet.profile_id;
    setDrafts((current) => ({ ...current, [profileId]: text }));
  }

  function reset() {
    if (!sheet) return;
    const profileId = sheet.profile_id;
    setDrafts((current) => keepDrafts(current, (id) => id !== profileId));
  }

  function discardStranded() {
    if (!sheet) return;
    const profileId = sheet.profile_id;
    setDrafts((current) => keepDrafts(current, (id) => id === profileId));
  }

  function saveDraft() {
    if (!sheet || !dirty || save.isPending) return;
    const profileId = sheet.profile_id;
    const text = content;
    save.mutate(
      { content: text, profileId },
      {
        onSuccess: () => {
          setDrafts((current) => afterSave(current, profileId, text));
          notifications.show({ color: 'green', message: 'Fact sheet saved' });
        },
      },
    );
  }

  /** Cmd+S or Ctrl+S in the text box saves. */
  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
      event.preventDefault();
      saveDraft();
    }
  }

  return {
    sheet,
    isPending,
    isError,
    content,
    dirty,
    stranded,
    strandedNames,
    saving: save.isPending,
    edit,
    reset,
    discardStranded,
    saveDraft,
    onKeyDown,
  };
}
