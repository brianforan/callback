import {
  ActionIcon,
  Button,
  Group,
  Modal,
  SegmentedControl,
  Stack,
  Text,
  Textarea,
  TextInput,
  ThemeIcon,
  Title,
  Tooltip,
} from '@mantine/core';
import { useDisclosure, useMediaQuery } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import { IconArrowLeft, IconDownload, IconFileText } from '@tabler/icons-react';
import { useDeferredValue, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';

import { Empty, PageError, PageLoader } from '../components/PageState';
import { TexPreviewPane } from '../components/TexPreview';
import { saveTextFile } from '../lib/format';
import { useResumeDetail, useSaveResumeSource, useUploadResume } from '../lib/queries';
import { MAX_TEX_BYTES } from '../lib/resumeFiles';
import { SECTIONS } from '../lib/sections';

const SPLIT_MIN_VIEWPORT = '(min-width: 1100px)';
const PANE_HEIGHT = 'calc(100dvh - 190px)';
const MONO_FONT = "'SFMono-Regular', Menlo, Consolas, monospace";

type Pane = 'edit' | 'preview';

// Edits live in this tab until they're downloaded or saved. Saving as a new version never changes
// the resume that was opened, so every version stays as it was uploaded. A baseline can also be
// saved in place, keeping its id, so what picks or stages it follows the edit.
export function ResumeEditorPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const upload = useUploadResume();
  const saveSource = useSaveResumeSource();
  const [naming, { open: openNaming, close: closeNaming }] = useDisclosure(false);
  const [confirming, { open: openConfirm, close: closeConfirm }] = useDisclosure(false);
  const [name, setName] = useState('');
  const detail = useResumeDetail(id ?? null);
  const split = useMediaQuery(SPLIT_MIN_VIEWPORT) ?? false;
  const [pane, setPane] = useState<Pane>('edit');
  // Null until the first keystroke, so the saved resume shows without being copied into state.
  const [edited, setEdited] = useState<string | null>(null);

  const original = detail.data?.source_tex ?? '';
  const source = edited ?? original;
  const dirty = edited !== null && edited !== original;
  const bytes = useMemo(() => new TextEncoder().encode(source).length, [source]);
  // The preview trails typing slightly, so a long resume never makes the text box stutter.
  const shown = useDeferredValue(source);

  useEffect(() => {
    if (!dirty) return undefined;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);

  if (id && detail.isError) return <PageError />;
  if (id && detail.isPending) return <PageLoader />;
  if (detail.data?.source_format === 'pdf') {
    return (
      <Stack align="center">
        <Empty
          title="A PDF can't be edited here"
          hint="This resume was uploaded as a PDF, so there's no LaTeX to edit. Upload a .tex version to edit or tailor it."
        />
        <Button
          component={Link}
          to={SECTIONS.resumes.to}
          variant="default"
          leftSection={<IconArrowLeft size={16} />}
        >
          Back to resumes
        </Button>
      </Stack>
    );
  }

  const filename = detail.data?.filename ?? 'resume.tex';
  const label = detail.data?.label ?? 'Scratch';
  // A longer source can be edited here but not saved.
  const tooLarge = bytes > MAX_TEX_BYTES;
  // Saving unchanged text would only hand back the version that's already there.
  const canSave = source.trim() !== '' && !tooLarge && (id ? dirty : true);
  // Only a baseline is saved over in place. Other versions stay as they were sent.
  const baseline = id && detail.data?.is_variant ? detail.data : null;
  const applications = baseline?.application_count ?? 0;

  function openSave() {
    setName(detail.data ? `${detail.data.label} (edited)` : '');
    openNaming();
  }

  function handleSave() {
    const file = new File([source], filename, { type: 'text/x-tex' });
    upload.mutate(
      // A version goes beside the resume it was edited from, whichever profile is active now.
      { file, label: name, profileId: detail.data?.profile_id },
      {
        onSuccess: (saved) => {
          notifications.show({ color: 'green', message: `Saved ${saved.label} as a version` });
          closeNaming();
          // The saved version is what the editor now shows, so there's nothing unsaved left.
          setEdited(null);
          navigate(`${SECTIONS.resumes.to}/editor/${saved.id}`, { replace: true });
        },
      },
    );
  }

  function handleSaveInPlace() {
    if (!baseline) return;
    saveSource.mutate(
      { id: baseline.id, source },
      {
        onSuccess: (saved) => {
          notifications.show({
            color: 'green',
            message: `Saved ${saved.label}. Its PDF is being made again.`,
          });
          closeConfirm();
          // The saved text is what the editor now shows, so there's nothing unsaved left.
          setEdited(null);
        },
      },
    );
  }

  const editor = (
    <Textarea
      aria-label="LaTeX source"
      placeholder="Paste or type LaTeX here"
      value={source}
      onChange={(event) => setEdited(event.currentTarget.value)}
      spellCheck={false}
      autoCapitalize="off"
      autoCorrect="off"
      styles={{
        input: {
          height: PANE_HEIGHT,
          fontFamily: MONO_FONT,
          fontSize: 13,
          lineHeight: 1.5,
          resize: 'none',
        },
      }}
    />
  );
  const preview = <TexPreviewPane source={shown} height={PANE_HEIGHT} />;

  return (
    <Stack gap="sm">
      <Group justify="space-between" gap="sm">
        <Group gap="sm" wrap="nowrap" style={{ minWidth: 0, flex: '1 1 240px' }}>
          <Tooltip label="Back to Resumes">
            <ActionIcon
              component={Link}
              to={SECTIONS.resumes.to}
              variant="subtle"
              color="gray"
              aria-label="Back to Resumes"
            >
              <IconArrowLeft size={18} />
            </ActionIcon>
          </Tooltip>
          <ThemeIcon variant="light" color={SECTIONS.resumes.color} size="lg" radius="md">
            <IconFileText size={20} stroke={1.7} />
          </ThemeIcon>
          <div style={{ minWidth: 0 }}>
            <Title order={4} lineClamp={1}>
              {label}
            </Title>
            <Text size="xs" c="dimmed">
              {baseline
                ? 'Baseline. Save keeps it in place, with its name and rules.'
                : 'Edits stay in this tab until you save or download them.'}
            </Text>
          </div>
        </Group>
        <Group gap="xs">
          <Text
            size="xs"
            c={tooLarge ? 'red' : 'dimmed'}
            className="tabular"
            style={{ whiteSpace: 'nowrap' }}
          >
            {(bytes / 1024).toFixed(1)} KB
            {tooLarge && ' · over the 64 KB limit for resumes'}
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
          <Button
            size="compact-sm"
            variant="default"
            disabled={!dirty}
            onClick={() => setEdited(null)}
          >
            Reset
          </Button>
          <Button
            size="compact-sm"
            variant="default"
            leftSection={<IconDownload size={14} />}
            disabled={!source.trim()}
            onClick={() => saveTextFile(filename, source, 'text/x-tex;charset=utf-8')}
          >
            Download .tex
          </Button>
          <Button
            size="compact-sm"
            variant={baseline ? 'default' : 'filled'}
            disabled={!canSave}
            onClick={openSave}
          >
            Save as new version
          </Button>
          {baseline && (
            <Button size="compact-sm" disabled={!canSave} onClick={openConfirm}>
              Save
            </Button>
          )}
        </Group>
      </Group>

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

      <Modal opened={confirming} onClose={closeConfirm} title={`Save over ${label}?`} size="sm">
        <Stack gap="sm">
          <Text size="sm">
            The baseline keeps its name, rules and place. Postings that pick it, and any staged
            with it, use the edited version. Its PDF is made again from the new LaTeX.
          </Text>
          {applications > 0 && (
            <Text size="sm" c="orange">
              {applications === 1 ? '1 application was' : `${applications} applications were`}{' '}
              logged with this resume and will show the edited version, not the one you sent. To
              keep that one, save as a new version instead.
            </Text>
          )}
          <Group justify="flex-end" gap="xs">
            <Button variant="default" onClick={closeConfirm}>
              Cancel
            </Button>
            <Button data-autofocus loading={saveSource.isPending} onClick={handleSaveInPlace}>
              Save
            </Button>
          </Group>
        </Stack>
      </Modal>

      <Modal opened={naming} onClose={closeNaming} title="Save as a new version" size="sm">
        <form
          onSubmit={(event) => {
            event.preventDefault();
            handleSave();
          }}
        >
          <Stack gap="sm">
            <TextInput
              label="Name"
              description="Leave it blank to name it after the file."
              maxLength={120}
              data-autofocus
              value={name}
              onChange={(event) => setName(event.currentTarget.value)}
            />
            <Text size="xs" c="dimmed">
              {id
                ? 'The version you opened stays as it is.'
                : 'It is added to All versions on the Resumes page.'}
            </Text>
            <Group justify="flex-end" gap="xs">
              <Button variant="default" onClick={closeNaming}>
                Cancel
              </Button>
              <Button type="submit" loading={upload.isPending}>
                Save
              </Button>
            </Group>
          </Stack>
        </form>
      </Modal>
    </Stack>
  );
}
