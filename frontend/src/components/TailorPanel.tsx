import { Badge, Button, Group, Loader, Paper, Stack, Text } from '@mantine/core';
import { IconWand } from '@tabler/icons-react';

import { formatDateTime } from '../lib/format';
import { isTailoring, useStartTailoring, useTailorStatus } from '../lib/queries';
import type { TailorAdvice, TailorGap, TailorJob } from '../lib/types';

const MAX_ATTEMPTS = 2;
// The daily count only matters once it's close to running out.
const SHOW_LEFT_AT = 5;

function Reasons({ reasons }: { reasons: string[] }) {
  return (
    <Stack gap={2}>
      {reasons.map((reason) => (
        <Text key={reason} size="xs">
          • {reason}
        </Text>
      ))}
    </Stack>
  );
}

function JobStatus({ job }: { job: TailorJob }) {
  if (job.status === 'queued') {
    return (
      <Group gap="xs">
        <Badge color="gray" variant="light" tt="none">
          Queued
        </Badge>
        <Text size="xs" c="dimmed">
          Waiting its turn. Tailorings run one at a time.
        </Text>
      </Group>
    );
  }
  if (job.status === 'running') {
    return (
      <Group gap="xs" wrap="nowrap">
        <Loader size="xs" color="violet" />
        <Text size="xs">
          Tailoring, attempt {Math.max(job.attempts, 1)} of {MAX_ATTEMPTS}. This takes a minute
          or two.
        </Text>
      </Group>
    );
  }
  if (job.status === 'done') {
    return (
      <Stack gap={4}>
        <Group gap="xs">
          <Badge color="green" variant="light" tt="none">
            Tailored
          </Badge>
          <Text size="xs" c="dimmed">
            {job.finished_at && formatDateTime(job.finished_at)}
          </Text>
        </Group>
        <Text size="xs">
          It passed the checks: the same jobs and dates, only skills your fact sheet or baselines
          back, and no lower a score. It's now your resume for this job.
        </Text>
        {job.reasons.length > 0 && <Reasons reasons={job.reasons} />}
      </Stack>
    );
  }
  return (
    <Stack gap={4}>
      <Group gap="xs">
        <Badge color="red" variant="light" tt="none">
          Not tailored
        </Badge>
        <Text size="xs" c="dimmed">
          {job.finished_at && formatDateTime(job.finished_at)}
          {job.attempts > 0 && `, after ${job.attempts} ${job.attempts === 1 ? 'try' : 'tries'}`}
        </Text>
      </Group>
      <Reasons reasons={job.reasons} />
      <Text size="xs" c="dimmed">
        Nothing was saved. Your resume above is unchanged.
      </Text>
    </Stack>
  );
}

function joinNames(names: string[]) {
  if (names.length < 2) return names.join('');
  return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
}

function gapLine(gap: TailorGap) {
  if (gap.source === 'baseline') return `${gap.name}, which your ${gap.baseline} baseline shows`;
  return `${gap.name}, which your fact sheet says you can claim`;
}

/** Whether tailoring is worth it here, in plain words. */
function Advice({ advice, label }: { advice: TailorAdvice; label: string }) {
  const cantAdd = advice.unfixable.length ? (
    <Text size="sm">
      This job also asks for {joinNames(advice.unfixable)}, which your fact sheet doesn't let you
      claim, so tailoring can't add {advice.unfixable.length === 1 ? 'it' : 'them'}.
    </Text>
  ) : null;
  if (advice.gaps.length) {
    return (
      <>
        <Text size="sm" fw={500}>
          Tailoring could make this a better fit
        </Text>
        <Text size="sm">
          It can add what this job asks for that your {label} baseline leaves out:
        </Text>
        {advice.gaps.map((gap) => (
          <Text key={gap.name} size="sm">
            • {gapLine(gap)}
          </Text>
        ))}
        {cantAdd}
      </>
    );
  }
  return (
    <>
      <Text size="sm" fw={500}>
        Your {label} baseline is ready to send as it is
      </Text>
      {cantAdd ?? (
        <Text size="sm">
          It already covers what this job asks for as well as your fact sheet allows.
        </Text>
      )}
      <Text size="sm">Tailoring would only reword it.</Text>
    </>
  );
}

/**
 * The optional second pass: whether tailoring could help, a button to start it, and its status.
 * A job that isn't saved yet is saved first, since tailoring works on saved postings.
 */
export function TailorBox({
  postingId,
  advice,
  baselineLabel,
  hasText,
  ensurePosting,
}: {
  postingId: string | null;
  advice: TailorAdvice | null;
  baselineLabel: string;
  hasText: boolean;
  ensurePosting: () => Promise<string>;
}) {
  const { data } = useTailorStatus(postingId);
  const start = useStartTailoring();
  const job = data?.job ?? null;
  const left =
    data && data.daily_limit !== null ? Math.max(data.daily_limit - data.used_today, 0) : null;
  const running = isTailoring(job);
  const blocked =
    advice?.missing ??
    (!hasText ? "This job has no description to tailor to." : null) ??
    (left === 0 ? 'No tailorings left today. They reset at midnight UTC.' : null);
  const worthIt = Boolean(advice?.gaps.length);
  const finished = job?.status === 'failed' || job?.status === 'done';

  async function handleStart() {
    try {
      start.mutate(await ensurePosting());
    } catch {
      // The failed request already showed its error.
    }
  }

  return (
    <Paper withBorder p="sm" radius="md">
      <Stack gap="xs">
        <Group justify="space-between" wrap="nowrap" align="flex-start">
          <Stack gap={4}>
            {blocked && !running ? (
              <>
                <Text size="sm" fw={500}>
                  Tailoring isn't ready yet
                </Text>
                <Text size="sm" c="orange">
                  {blocked}
                </Text>
              </>
            ) : (
              advice?.ready && <Advice advice={advice} label={baselineLabel} />
            )}
          </Stack>
          <Button
            color={worthIt ? 'violet' : 'gray'}
            variant={worthIt ? 'light' : 'default'}
            leftSection={<IconWand size={16} />}
            style={{ flexShrink: 0 }}
            disabled={Boolean(blocked) || running}
            loading={start.isPending}
            onClick={handleStart}
          >
            {finished ? 'Tailor again' : worthIt ? 'Tailor' : 'Tailor anyway'}
          </Button>
        </Group>
        <Text size="xs" c="dimmed">
          Tailoring has Claude rewrite your baseline for this job, claiming only what your fact
          sheet allows. Every result is checked before it's saved. Nothing is sent to the
          employer.
        </Text>
        {data && left !== null && left <= SHOW_LEFT_AT && (
          <Text size="xs" c="dimmed">
            {left} of {data.daily_limit} tailorings left today.
          </Text>
        )}
        {job && <JobStatus job={job} />}
      </Stack>
    </Paper>
  );
}
