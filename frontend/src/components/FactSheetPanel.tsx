import { Button, Card, Group, Stack, Text, Textarea } from '@mantine/core';
import { IconColumns2 } from '@tabler/icons-react';

import { FACT_SHEET_MAX_LENGTH, FACT_SHEET_PLACEHOLDER } from '../lib/factSheet';
import { formatDateTime } from '../lib/format';
import { useFactSheetDraft } from '../lib/useFactSheetDraft';
import { PageError, PageLoader } from './PageState';
import { StrandedDraftNotice } from './StrandedDraftNotice';

export function FactSheetPanel() {
  const draft = useFactSheetDraft();

  if (draft.isPending) return <PageLoader />;
  if (draft.isError || !draft.sheet) return <PageError />;
  const { sheet, content, dirty } = draft;

  return (
    <Card>
      <Stack gap="sm">
        <Group justify="space-between" align="flex-start" wrap="nowrap" gap="md">
          <Text size="sm" c="dimmed">
            What you can truthfully claim, in your own words: skills you can and can't claim, the
            facts and numbers behind each role, and pre-approved bullets. Tailoring will treat this
            as the rules for rewording a baseline, so anything not here stays off a tailored
            resume. Markdown is fine.
          </Text>
          <Button
            component="a"
            href="/resumes/fact-sheet"
            target="_blank"
            rel="noopener"
            variant="default"
            size="compact-sm"
            leftSection={<IconColumns2 size={14} />}
            style={{ flexShrink: 0 }}
          >
            Side-by-side editor
          </Button>
        </Group>
        {draft.stranded && (
          <StrandedDraftNotice names={draft.strandedNames} onDiscard={draft.discardStranded} />
        )}
        <Textarea
          aria-label="Fact sheet Markdown"
          autosize
          minRows={16}
          maxRows={40}
          maxLength={FACT_SHEET_MAX_LENGTH}
          value={content}
          onChange={(event) => draft.edit(event.currentTarget.value)}
          onKeyDown={draft.onKeyDown}
          placeholder={FACT_SHEET_PLACEHOLDER}
          styles={{ input: { fontFamily: 'var(--mantine-font-family-monospace)', fontSize: 13 } }}
        />
        <Group justify="space-between">
          <Text size="xs" c="dimmed">
            {dirty
              ? 'Unsaved changes'
              : sheet.updated_at
                ? `Saved ${formatDateTime(sheet.updated_at)}`
                : 'Not saved yet'}{' '}
            · {content.length.toLocaleString()} of {FACT_SHEET_MAX_LENGTH.toLocaleString()}{' '}
            characters
          </Text>
          <Group gap="xs">
            <Button variant="default" disabled={!dirty} onClick={draft.reset}>
              Reset
            </Button>
            <Button disabled={!dirty} loading={draft.saving} onClick={draft.saveDraft}>
              Save
            </Button>
          </Group>
        </Group>
      </Stack>
    </Card>
  );
}
