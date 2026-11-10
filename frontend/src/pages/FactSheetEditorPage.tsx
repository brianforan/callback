import {
  ActionIcon,
  Box,
  Button,
  Group,
  SegmentedControl,
  Stack,
  Text,
  Textarea,
  ThemeIcon,
  Title,
  Tooltip,
} from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { IconArrowLeft, IconFileText } from '@tabler/icons-react';
import { useDeferredValue, useState } from 'react';
import { Link } from 'react-router-dom';

import { MarkdownView } from '../components/MarkdownView';
import { PageError, PageLoader } from '../components/PageState';
import { StrandedDraftNotice } from '../components/StrandedDraftNotice';
import { FACT_SHEET_MAX_LENGTH, FACT_SHEET_PLACEHOLDER } from '../lib/factSheet';
import { formatDateTime } from '../lib/format';
import { SECTIONS } from '../lib/sections';
import { useFactSheetDraft } from '../lib/useFactSheetDraft';

const SPLIT_MIN_VIEWPORT = '(min-width: 1100px)';
const PANE_HEIGHT = 'calc(100dvh - 190px)';

type Pane = 'edit' | 'preview';

// The fact sheet beside its rendered Markdown. Saving replaces the one fact sheet tailoring reads.
export function FactSheetEditorPage() {
  const draft = useFactSheetDraft();
  const split = useMediaQuery(SPLIT_MIN_VIEWPORT) ?? false;
  const [pane, setPane] = useState<Pane>('edit');
  // The render trails typing slightly, so a long sheet never makes the text box stutter.
  const shown = useDeferredValue(draft.content);

  if (draft.isPending) return <PageLoader />;
  if (draft.isError || !draft.sheet) return <PageError />;
  const { sheet, content, dirty } = draft;

  const editor = (
    <Textarea
      aria-label="Fact sheet Markdown"
      placeholder={FACT_SHEET_PLACEHOLDER}
      value={content}
      maxLength={FACT_SHEET_MAX_LENGTH}
      onChange={(event) => draft.edit(event.currentTarget.value)}
      onKeyDown={draft.onKeyDown}
      spellCheck
      styles={{
        input: {
          height: PANE_HEIGHT,
          fontFamily: 'var(--mantine-font-family-monospace)',
          fontSize: 13,
          lineHeight: 1.5,
          resize: 'none',
        },
      }}
    />
  );
  const preview = (
    <Box
      p="md"
      style={{
        height: PANE_HEIGHT,
        overflowY: 'auto',
        border: '1px solid var(--mantine-color-default-border)',
        borderRadius: 'var(--mantine-radius-default)',
        background: 'var(--mantine-color-body)',
      }}
    >
      {shown.trim() ? (
        <MarkdownView source={shown} />
      ) : (
        <Text size="sm" c="dimmed">
          The render shows here as you type.
        </Text>
      )}
    </Box>
  );

  return (
    <Stack gap="sm">
      <Group justify="space-between" gap="sm">
        <Group gap="sm" wrap="nowrap" style={{ minWidth: 0, flex: '1 1 240px' }}>
          <Tooltip label="Back to the fact sheet">
            <ActionIcon
              component={Link}
              to={`${SECTIONS.resumes.to}?tab=facts`}
              variant="subtle"
              color="gray"
              aria-label="Back to the fact sheet"
            >
              <IconArrowLeft size={18} />
            </ActionIcon>
          </Tooltip>
          <ThemeIcon variant="light" color={SECTIONS.resumes.color} size="lg" radius="md">
            <IconFileText size={20} stroke={1.7} />
          </ThemeIcon>
          <div style={{ minWidth: 0 }}>
            <Title order={4}>Fact sheet</Title>
            <Text size="xs" c="dimmed">
              {dirty ? 'Unsaved changes' : sheet.updated_at ? `Saved ${formatDateTime(sheet.updated_at)}` : 'Not saved yet'}
            </Text>
          </div>
        </Group>
        <Group gap="xs">
          <Text size="xs" c="dimmed" className="tabular" style={{ whiteSpace: 'nowrap' }}>
            {content.length.toLocaleString()} of {FACT_SHEET_MAX_LENGTH.toLocaleString()}
          </Text>
          {!split && (
            <SegmentedControl
              size="xs"
              data={[
                { value: 'edit', label: 'Edit' },
                { value: 'preview', label: 'Preview' },
              ]}
              value={pane}
              onChange={(value) => setPane(value as Pane)}
            />
          )}
          <Button size="compact-sm" variant="default" disabled={!dirty} onClick={draft.reset}>
            Reset
          </Button>
          <Button
            size="compact-sm"
            disabled={!dirty}
            loading={draft.saving}
            onClick={draft.saveDraft}
          >
            Save
          </Button>
        </Group>
      </Group>

      {draft.stranded && (
        <StrandedDraftNotice names={draft.strandedNames} onDiscard={draft.discardStranded} />
      )}

      {split ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)', gap: 12 }}>
          {editor}
          {preview}
        </div>
      ) : pane === 'edit' ? (
        editor
      ) : (
        preview
      )}
    </Stack>
  );
}
