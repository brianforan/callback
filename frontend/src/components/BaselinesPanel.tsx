import {
  ActionIcon,
  Badge,
  Button,
  Card,
  FileButton,
  Grid,
  Group,
  Progress,
  Stack,
  Text,
  TextInput,
  Tooltip,
} from '@mantine/core';
import { Dropzone, type FileRejection } from '@mantine/dropzone';
import { notifications } from '@mantine/notifications';
import {
  IconEye,
  IconFileTypePdf,
  IconFileTypography,
  IconUpload,
  IconX,
} from '@tabler/icons-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import { MAX_BASELINES, ruleRows } from '../lib/baselines';
import { downloadFile, formatDay } from '../lib/format';
import { useSetVariant, useUploadBaseline, useVariantReport } from '../lib/queries';
import {
  MAX_PDF_BYTES,
  PDF_ONLY_NOTE,
  RESUME_ACCEPT,
  RESUME_ACCEPT_INPUT,
  rejectionMessage,
  resumeDropValidator,
  resumeFileProblem,
} from '../lib/resumeFiles';
import { SECTIONS } from '../lib/sections';
import type { Resume, ResumeEvidence, VariantSummary } from '../lib/types';
import { ResumePreviewModal } from './ResumePreviewModal';

interface BaselinesPanelProps {
  resumes: Resume[];
  onEditRules: (resume: Resume) => void;
}

function rejectUpload(rejections: FileRejection[]) {
  notifications.show({ color: 'red', message: rejectionMessage(rejections) });
}

const SHOWN_BACKED = 10;
const SHOWN_LISTED = 6;

function NameBadges({
  names,
  limit,
  variant,
}: {
  names: string[];
  limit: number;
  variant: 'light' | 'default';
}) {
  if (!names.length) {
    return (
      <Text size="xs" c="dimmed">
        None
      </Text>
    );
  }
  return (
    <Group gap={4}>
      {names.slice(0, limit).map((name) => (
        <Badge key={name} size="sm" variant={variant} tt="none" fw={500}>
          {name}
        </Badge>
      ))}
      {names.length > limit && (
        <Text size="xs" c="dimmed">
          +{names.length - limit} more
        </Text>
      )}
    </Group>
  );
}

function FitStrip({ fit, postings }: { fit: VariantSummary; postings: number }) {
  if (!postings) {
    return (
      <Text size="xs" c="dimmed">
        Save postings to see how well this baseline fits them.
      </Text>
    );
  }
  return (
    <Stack gap={4}>
      <Text size="xs" className="tabular">
        Passes <b>{fit.passes}</b> of {postings} saved {postings === 1 ? 'posting' : 'postings'} ·
        best fit for <b>{fit.best_for}</b>
      </Text>
      <Progress value={(fit.passes / postings) * 100} size="sm" />
    </Stack>
  );
}

function ResumeReads({ evidence }: { evidence: ResumeEvidence }) {
  return (
    <Stack gap={4}>
      <Text size="xs" fw={600} c="dimmed" tt="uppercase">
        What it shows
      </Text>
      <Text size="xs">
        {evidence.roles} dated {evidence.roles === 1 ? 'role' : 'roles'}
        {evidence.years !== null && `, ${evidence.years} years`}
        {evidence.degree ? `, ${evidence.degree}'s degree` : ', no degree found'}
      </Text>
      <NameBadges names={evidence.backed} limit={SHOWN_BACKED} variant="light" />
      {evidence.listed_only.length > 0 && (
        <>
          <Text size="xs" c="dimmed">
            Only listed
          </Text>
          <NameBadges names={evidence.listed_only} limit={SHOWN_LISTED} variant="default" />
        </>
      )}
    </Stack>
  );
}

// Why a baseline has no PDF to download yet, or null when it has one.
function pdfUnavailable(resume: Resume): string | null {
  if (resume.pdf_pages !== null) return null;
  return resume.pdf_error ? "Didn't compile to a PDF" : 'PDF still compiling';
}

function BaselineCard({
  resume,
  fit,
  postings,
  onEditRules,
}: {
  resume: Resume;
  fit: VariantSummary | undefined;
  postings: number;
  onEditRules: (resume: Resume) => void;
}) {
  const upload = useUploadBaseline();
  const setVariant = useSetVariant();
  const [previewing, setPreviewing] = useState(false);
  const noPdf = pdfUnavailable(resume);

  function handleReplace(file: File | null) {
    if (!file) return;
    const problem = resumeFileProblem(file);
    if (problem) {
      notifications.show({ color: 'red', message: problem });
      return;
    }
    upload.mutate(
      { file, label: resume.label, replaces: resume.id },
      {
        onSuccess: (saved) =>
          notifications.show({ color: 'green', message: `Replaced ${saved.label}` }),
      },
    );
  }

  const meta = [
    resume.filename,
    resume.source_format === 'pdf' ? 'PDF' : null,
    formatDay(resume.created_at),
    resume.pdf_pages !== null
      ? `${resume.pdf_pages} ${resume.pdf_pages === 1 ? 'page' : 'pages'}`
      : null,
    `${resume.word_count.toLocaleString()} words`,
    `${resume.application_count} ${resume.application_count === 1 ? 'application' : 'applications'}`,
  ].filter(Boolean);

  return (
    <Card withBorder h="100%">
      <Stack gap="sm" h="100%">
        <Group justify="space-between" align="flex-start" wrap="nowrap" gap="xs">
          <div style={{ minWidth: 0 }}>
            <Text fw={650} size="md" lineClamp={1}>
              {resume.label}
            </Text>
            <Text size="xs" c="dimmed">
              {meta.join(' · ')}
            </Text>
          </div>
          <Group gap={2} wrap="nowrap">
            <Tooltip label="Preview">
              <ActionIcon
                variant="subtle"
                color="gray"
                aria-label={`Preview ${resume.label}`}
                onClick={() => setPreviewing(true)}
              >
                <IconEye size={18} />
              </ActionIcon>
            </Tooltip>
            <Tooltip label={noPdf ?? 'Download PDF'}>
              <ActionIcon
                variant="subtle"
                color="gray"
                aria-label={`Download ${resume.label} as PDF`}
                // data-disabled keeps the tooltip working, which a disabled button would block.
                data-disabled={noPdf !== null || undefined}
                onClick={() => {
                  if (noPdf === null) downloadFile(`/api/resumes/${resume.id}/pdf`);
                }}
              >
                <IconFileTypePdf size={18} />
              </ActionIcon>
            </Tooltip>
          </Group>
        </Group>

        {fit && <FitStrip fit={fit} postings={postings} />}

        <Stack gap={2}>
          <Text size="xs" fw={600} c="dimmed" tt="uppercase">
            Suits
          </Text>
          {ruleRows(resume.variant_rules).map((row) => (
            <Group key={row.label} gap="xs" wrap="nowrap" align="baseline">
              <Text size="xs" c="dimmed" w={96} style={{ flexShrink: 0 }}>
                {row.label}
              </Text>
              <Text size="xs">{row.value}</Text>
            </Group>
          ))}
        </Stack>

        {fit && <ResumeReads evidence={fit.evidence} />}

        <div style={{ flexGrow: 1 }} />
        <Group gap={6}>
          <Button size="compact-sm" variant="light" onClick={() => onEditRules(resume)}>
            Rules
          </Button>
          {resume.source_format === 'pdf' ? (
            <Tooltip label="A PDF has no LaTeX to edit. Replace its file instead.">
              {/* data-disabled keeps the tooltip working, which a disabled button would block. */}
              <Button size="compact-sm" variant="default" data-disabled>
                Edit
              </Button>
            </Tooltip>
          ) : (
            <Button
              component={Link}
              to={`${SECTIONS.resumes.to}/editor/${resume.id}`}
              size="compact-sm"
              variant="default"
            >
              Edit
            </Button>
          )}
          <FileButton onChange={handleReplace} accept={RESUME_ACCEPT_INPUT}>
            {(props) => (
              <Button {...props} size="compact-sm" variant="default" loading={upload.isPending}>
                Replace file
              </Button>
            )}
          </FileButton>
          <Button
            size="compact-sm"
            variant="subtle"
            color="gray"
            loading={setVariant.isPending}
            onClick={() =>
              setVariant.mutate({ id: resume.id, isVariant: false, rules: resume.variant_rules })
            }
          >
            Remove
          </Button>
        </Group>
      </Stack>
      <ResumePreviewModal
        resumeId={previewing ? resume.id : null}
        onClose={() => setPreviewing(false)}
      />
    </Card>
  );
}

function EmptySlot({ onEditRules }: { onEditRules: (resume: Resume) => void }) {
  const upload = useUploadBaseline();
  const [label, setLabel] = useState('');

  function handleDrop(files: File[]) {
    upload.mutate(
      { file: files[0], label },
      {
        onSuccess: (saved) => {
          setLabel('');
          notifications.show({ color: 'green', message: `Saved ${saved.label} as a baseline` });
          // Set its rules right away, since a baseline without them suits every job.
          onEditRules(saved);
        },
      },
    );
  }

  return (
    <Card withBorder h="100%">
      <Stack gap="xs">
        <TextInput
          size="xs"
          label="Name"
          placeholder="e.g. Backend & AWS"
          maxLength={120}
          value={label}
          onChange={(event) => setLabel(event.currentTarget.value)}
        />
        <Dropzone
          onDrop={handleDrop}
          onReject={rejectUpload}
          maxSize={MAX_PDF_BYTES}
          validator={resumeDropValidator}
          multiple={false}
          accept={RESUME_ACCEPT}
          loading={upload.isPending}
        >
          <Group justify="center" gap="sm" mih={64} style={{ pointerEvents: 'none' }}>
            <Dropzone.Accept>
              <IconUpload size={24} stroke={1.5} />
            </Dropzone.Accept>
            <Dropzone.Reject>
              <IconX size={24} stroke={1.5} />
            </Dropzone.Reject>
            <Dropzone.Idle>
              <IconFileTypography size={24} stroke={1.5} />
            </Dropzone.Idle>
            <div>
              <Text size="sm" fw={500}>
                Drop a baseline .tex or PDF
              </Text>
              <Text size="xs" c="dimmed">
                {PDF_ONLY_NOTE}
              </Text>
            </div>
          </Group>
        </Dropzone>
      </Stack>
    </Card>
  );
}

export function BaselinesPanel({ resumes, onEditRules }: BaselinesPanelProps) {
  const baselines = resumes
    .filter((resume) => resume.is_variant)
    .sort((a, b) => a.label.localeCompare(b.label));
  const empty = Math.max(0, MAX_BASELINES - baselines.length);
  // The report is extra detail: the cards still show their rules if it's slow or fails.
  const { data: report } = useVariantReport();

  return (
    <Stack gap="sm">
      <Text size="sm" c="dimmed">
        Keep up to {MAX_BASELINES} baselines, one for each kind of job you go for, such as backend
        or full-stack. Each has rules for the jobs it suits. Insights → Baselines checks every
        saved posting against each one and says which to start from.
      </Text>
      <Grid gutter="sm">
        {baselines.map((resume) => (
          <Grid.Col key={resume.id} span={{ base: 12, md: 6, lg: 4 }}>
            <BaselineCard
              resume={resume}
              fit={report?.variants.find((variant) => variant.resume_id === resume.id)}
              postings={report?.postings ?? 0}
              onEditRules={onEditRules}
            />
          </Grid.Col>
        ))}
        {Array.from({ length: empty }, (_, index) => (
          <Grid.Col key={`empty-${index}`} span={{ base: 12, md: 6, lg: 4 }}>
            <EmptySlot onEditRules={onEditRules} />
          </Grid.Col>
        ))}
      </Grid>
    </Stack>
  );
}
