import {
  ActionIcon,
  Code,
  Group,
  Modal,
  ScrollArea,
  SegmentedControl,
  Stack,
  Text,
  Tooltip,
} from '@mantine/core';
import { useLocalStorage, useMediaQuery } from '@mantine/hooks';
import { IconExternalLink } from '@tabler/icons-react';

import { useResumeDetail } from '../lib/queries';
import { readableOutline } from '../lib/resumeFiles';
import { PageError, PageLoader } from './PageState';
import { TexPreviewPane } from './TexPreview';

type Mode = 'preview' | 'source' | 'split';

const MODES: Mode[] = ['preview', 'source', 'split'];

// The stored value is JSON, and anything that isn't one of the modes means the default.
function readMode(stored: string | undefined): Mode {
  try {
    const value: unknown = JSON.parse(stored ?? '');
    return MODES.find((mode) => mode === value) ?? 'preview';
  } catch {
    return 'preview';
  }
}

// A letter sheet is 816 CSS pixels wide, and the modal's own padding is 16 on each side. At this
// width the sheet fills the modal with no space either side.
const SHEET_PX = 816;
const PADDING_PX = 16;
const SINGLE_WIDTH = SHEET_PX + 2 * PADDING_PX;

// Source on the left takes what is left of the window, so it needs a viewport this wide to be
// readable beside the sheet.
const SPLIT_MIN_VIEWPORT = '(min-width: 1500px)';
const SPLIT_WIDTH = 'min(96vw, 1700px)';
const PANE_HEIGHT = '72vh';

interface ResumePreviewModalProps {
  // The resume to show, or null to keep the modal closed.
  resumeId: string | null;
  onClose: () => void;
}

export function ResumePreviewModal({ resumeId, onClose }: ResumePreviewModalProps) {
  const resume = useResumeDetail(resumeId);
  const wide = useMediaQuery(SPLIT_MIN_VIEWPORT) ?? false;
  const [stored, setStored] = useLocalStorage<Mode>({
    key: 'resume-preview-mode',
    defaultValue: 'preview',
    getInitialValueInEffect: false,
    deserialize: readMode,
  });
  // A saved side-by-side choice falls back to the sheet alone on a window too narrow for it.
  const mode: Mode = stored === 'split' && !wide ? 'preview' : stored;

  const options = [
    { value: 'preview', label: 'Preview' },
    { value: 'source', label: 'Source' },
    ...(wide ? [{ value: 'split', label: 'Side by side' }] : []),
  ];

  return (
    <Modal
      opened={resumeId !== null}
      onClose={onClose}
      title={resume.data?.label ?? 'Resume'}
      size={mode === 'split' ? SPLIT_WIDTH : SINGLE_WIDTH}
      padding={PADDING_PX}
    >
      {resume.isError ? (
        <PageError />
      ) : resume.isPending ? (
        <PageLoader />
      ) : resume.data.source_format === 'pdf' ? (
        <Stack gap="sm">
          <Text size="sm" c="dimmed">
            The text read from this PDF, which is what matching uses. Headings in capitals are the
            sections it found. If dates or sections look wrong here, matching for this resume is
            less exact.
          </Text>
          <ScrollArea.Autosize mah={PANE_HEIGHT}>
            <Code block>{readableOutline(resume.data.source_tex)}</Code>
          </ScrollArea.Autosize>
        </Stack>
      ) : (
        <Stack gap="sm">
          <Group justify="space-between">
            <SegmentedControl
              size="xs"
              w="fit-content"
              data={options}
              value={mode}
              onChange={(value) => setStored(value as Mode)}
            />
            <Tooltip label="Edit in a new tab">
              <ActionIcon
                component="a"
                href={`/resumes/editor/${resume.data.id}`}
                target="_blank"
                rel="noopener"
                variant="subtle"
                color="gray"
                aria-label={`Edit ${resume.data.label} in a new tab`}
              >
                <IconExternalLink size={18} />
              </ActionIcon>
            </Tooltip>
          </Group>
          {mode === 'split' ? (
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: `minmax(0, 1fr) ${SHEET_PX}px`,
                gap: 'var(--mantine-spacing-sm)',
              }}
            >
              <ScrollArea.Autosize mah={PANE_HEIGHT}>
                <Code block>{resume.data.source_tex}</Code>
              </ScrollArea.Autosize>
              <TexPreviewPane source={resume.data.source_tex} maxHeight={PANE_HEIGHT} />
            </div>
          ) : mode === 'source' ? (
            <ScrollArea.Autosize mah={PANE_HEIGHT}>
              <Code block>{resume.data.source_tex}</Code>
            </ScrollArea.Autosize>
          ) : (
            <TexPreviewPane source={resume.data.source_tex} maxHeight={PANE_HEIGHT} />
          )}
        </Stack>
      )}
    </Modal>
  );
}
