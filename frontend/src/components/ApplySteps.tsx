import {
  Anchor,
  Badge,
  Button,
  Group,
  Select,
  Stack,
  Text,
  Textarea,
  ThemeIcon,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconDownload, IconExternalLink, IconFileTypePdf, IconSend } from '@tabler/icons-react';
import { useState, type ReactNode } from 'react';

import { downloadFile, saveTextFile, todayIso } from '../lib/format';
import {
  fetchResumeDetail,
  useCreateApplication,
  useCreatePosting,
  usePostingDetail,
  useResumes,
  useStagePosting,
  useUnstagePosting,
} from '../lib/queries';
import { statusMeta } from '../lib/statuses';
import type {
  ApplicationStatus,
  BaselinePick,
  Listing,
  PostingDetail,
  Resume,
  TailorAdvice,
} from '../lib/types';
import { TailorBox } from './TailorPanel';

/** What the steps need about a job, whether it came from a board listing or a saved posting. */
export interface ApplyJob {
  postingId: string | null;
  company: string;
  title: string;
  url: string | null;
  location: string | null;
  source: string | null;
  text: string;
  baseline: BaselinePick | null;
  advice: TailorAdvice | null;
  appliedStatus: ApplicationStatus | null;
}

export function jobFromListing(listing: Listing): ApplyJob {
  return {
    postingId: listing.posting_id,
    company: listing.company,
    title: listing.title,
    url: listing.url,
    location: listing.location,
    source: listing.source,
    text: listing.description,
    baseline: listing.baseline,
    advice: listing.tailor_advice,
    appliedStatus: listing.application_status,
  };
}

export function jobFromPosting(posting: PostingDetail): ApplyJob {
  return {
    postingId: posting.id,
    company: posting.company,
    title: posting.title,
    url: posting.url,
    location: posting.location,
    source: posting.source,
    text: posting.raw_text,
    baseline: posting.baseline,
    advice: posting.tailor_advice,
    appliedStatus: posting.applications[0]?.status ?? null,
  };
}

function Step({ number, title, children }: { number: number; title: string; children: ReactNode }) {
  return (
    <Group align="flex-start" wrap="nowrap" gap="sm">
      <ThemeIcon radius="xl" size={26} variant="light" color="violet">
        <Text size="sm" fw={700}>
          {number}
        </Text>
      </ThemeIcon>
      <Stack gap="xs" style={{ flex: 1, minWidth: 0 }}>
        <Text fw={600}>{title}</Text>
        {children}
      </Stack>
    </Group>
  );
}

function resumeOptions(resumes: Resume[]) {
  const option = (resume: Resume) => ({ value: resume.id, label: resume.label });
  const baselines = resumes.filter((resume) => resume.is_variant);
  const others = resumes.filter((resume) => !resume.is_variant);
  return [
    ...(baselines.length ? [{ group: 'Baselines', items: baselines.map(option) }] : []),
    ...(others.length ? [{ group: 'Other versions', items: others.map(option) }] : []),
  ];
}

function pdfNote(resume: Resume | undefined): string | null {
  if (!resume || resume.pdf_pages !== null) return null;
  if (resume.pdf_error) return `No PDF: ${resume.pdf_error}`;
  return 'Making the PDF. It appears here in a moment.';
}

/**
 * Getting a resume for one job, in two steps: the resume to send, then applying. Downloading,
 * tailoring or saving notes saves the job and marks it ready to apply, so there's no separate
 * track or stage step. Nothing is ever submitted.
 */
export function ApplySteps({ job }: { job: ApplyJob }) {
  const [createdId, setCreatedId] = useState<string | null>(null);
  const postingId = job.postingId ?? createdId;
  const { data: posting } = usePostingDetail(postingId);
  const createPosting = useCreatePosting();
  const stage = useStagePosting();
  const unstage = useUnstagePosting();
  const createApplication = useCreateApplication();
  const [choosing, setChoosing] = useState(false);
  const [notes, setNotes] = useState<string | null>(null);

  const baseline = posting?.baseline ?? job.baseline;
  const stagedId = posting?.staged_at ? posting.staged_resume_id : null;
  const chosenId = stagedId ?? baseline?.resume_id ?? null;
  const { data: resumes } = useResumes(chosenId);
  const chosen = resumes?.find((resume) => resume.id === chosenId);
  const applied = posting ? posting.applications[0]?.status ?? null : job.appliedStatus;
  const noteText = notes ?? posting?.staged_notes ?? '';
  const busy = createPosting.isPending || stage.isPending;

  async function ensurePosting(): Promise<string> {
    if (postingId) return postingId;
    const created = await createPosting.mutateAsync({
      company: job.company,
      title: job.title,
      url: job.url,
      location: job.location,
      source: job.source,
      raw_text: job.text || null,
      referrer: null,
      seen_on: todayIso(),
    });
    setCreatedId(created.id);
    return created.id;
  }

  /** Save the job as ready to apply with this resume and the notes, unless it already is. */
  async function markReady(resumeId: string, force = false): Promise<string> {
    const id = await ensurePosting();
    if (force || stagedId !== resumeId) {
      await stage.mutateAsync({ id, resumeId, notes: noteText });
    }
    return id;
  }

  async function run(action: () => Promise<unknown>) {
    try {
      await action();
    } catch {
      // The failed request already showed its error.
    }
  }

  async function download(kind: 'pdf' | 'tex') {
    if (!chosen) return;
    await run(async () => {
      await markReady(chosen.id);
      if (kind === 'pdf') {
        downloadFile(`/api/resumes/${chosen.id}/pdf`);
      } else {
        // A plain request, not a mutation, so nothing else reports it if it fails.
        try {
          const detail = await fetchResumeDetail(chosen.id);
          saveTextFile(detail.filename, detail.source_tex, 'text/x-tex;charset=utf-8');
        } catch (error) {
          notifications.show({
            color: 'red',
            title: "Couldn't download the .tex",
            message: (error as Error).message,
          });
        }
      }
    });
  }

  async function handleApplied() {
    await run(async () => {
      const id = await ensurePosting();
      await createApplication.mutateAsync({
        company: job.company,
        role_title: job.title,
        job_url: job.url,
        location: job.location,
        source: job.source,
        status: 'applied',
        applied_on: todayIso(),
        resume_id: chosenId,
        posting_id: id,
        notes: null,
      });
      notifications.show({ color: 'green', message: `Logged your application to ${job.company}` });
    });
  }

  if (applied) {
    const meta = statusMeta(applied);
    return (
      <Group gap="xs">
        <Text size="sm">You've applied to this job.</Text>
        <Badge color={meta.color} variant="light" tt="none">
          {meta.label}
        </Badge>
      </Group>
    );
  }

  const isBaseline = chosenId !== null && chosenId === baseline?.resume_id;
  const note = pdfNote(chosen);

  return (
    <Stack gap="lg">
      <Step number={1} title="Your resume for this job">
        {!baseline && !stagedId ? (
          <Text size="sm" c="dimmed">
            Add a baseline on the Resumes page, and the best fit for each job shows here.
          </Text>
        ) : (
          <>
            <Group gap="xs">
              <Text size="sm">
                <b>{chosen?.label ?? posting?.staged_resume_label ?? baseline?.label}</b>
                {isBaseline && baseline && (
                  <>
                    {baseline.covered ? ' fits this job best' : ' is the closest of your baselines'}
                    , {baseline.score}/100
                  </>
                )}
              </Text>
              {posting?.staged_at && (
                <Badge color="violet" variant="light" tt="none">
                  Ready to apply
                </Badge>
              )}
            </Group>
            {isBaseline && baseline && !baseline.covered && (
              <Text size="xs" c="dimmed">
                None of your baselines' rules fit this job: {baseline.reasons.join(' ')}
              </Text>
            )}
            <Group gap="xs">
              <Button
                leftSection={<IconFileTypePdf size={16} />}
                color="violet"
                disabled={!chosen || chosen.pdf_pages === null}
                loading={busy}
                onClick={() => download('pdf')}
              >
                Download PDF
              </Button>
              <Button
                variant="default"
                leftSection={<IconDownload size={16} />}
                disabled={!chosen || chosen.source_format === 'pdf'}
                onClick={() => download('tex')}
              >
                .tex
              </Button>
              {!choosing && (
                <Anchor component="button" size="sm" onClick={() => setChoosing(true)}>
                  Use another resume
                </Anchor>
              )}
            </Group>
            {note && (
              <Text size="xs" c={chosen?.pdf_error ? 'orange' : 'dimmed'}>
                {note}
              </Text>
            )}
            {choosing && (
              <Select
                label="Resume to send"
                data={resumeOptions(resumes ?? [])}
                value={chosenId}
                searchable
                onChange={(value) => {
                  setChoosing(false);
                  if (value) void run(() => markReady(value));
                }}
              />
            )}
            {!isBaseline && baseline && (
              <Anchor
                component="button"
                size="sm"
                style={{ alignSelf: 'flex-start' }}
                onClick={() => run(() => markReady(baseline.resume_id))}
              >
                Use the {baseline.label} baseline instead
              </Anchor>
            )}
            {baseline && (
              <TailorBox
                postingId={postingId}
                advice={posting?.tailor_advice ?? job.advice}
                baselineLabel={baseline.label}
                hasText={Boolean(job.text.trim())}
                ensurePosting={ensurePosting}
              />
            )}
          </>
        )}
      </Step>

      <Step number={2} title="Apply">
        {job.url && (
          <Anchor href={job.url} target="_blank" rel="noopener noreferrer" size="sm">
            <Group gap={4} wrap="nowrap">
              Open the job posting <IconExternalLink size={14} />
            </Group>
          </Anchor>
        )}
        <Textarea
          label="Notes for the application form"
          description="What to have at hand when you fill it in, such as your title of record."
          autosize
          minRows={2}
          maxRows={8}
          maxLength={20_000}
          value={noteText}
          onChange={(event) => setNotes(event.currentTarget.value)}
        />
        <Group justify="space-between">
          <Group gap="xs">
            <Button
              variant="default"
              disabled={!chosenId || notes === null}
              loading={stage.isPending}
              onClick={() =>
                chosenId &&
                run(async () => {
                  await markReady(chosenId, true);
                  setNotes(null);
                })
              }
            >
              Save notes
            </Button>
            {posting?.staged_at && (
              <Button
                variant="subtle"
                color="gray"
                loading={unstage.isPending}
                onClick={() => posting && unstage.mutate(posting.id)}
              >
                Not ready after all
              </Button>
            )}
          </Group>
          <Button
            color="green"
            leftSection={<IconSend size={16} />}
            loading={createApplication.isPending}
            onClick={handleApplied}
          >
            I applied
          </Button>
        </Group>
        <Text size="xs" c="dimmed">
          Downloading a resume saves this job as ready to apply. I applied logs it with the resume
          above. Nothing is sent for you.
        </Text>
      </Step>
    </Stack>
  );
}
