import {
  Accordion,
  Alert,
  Badge,
  Group,
  Progress,
  Stack,
  Table,
  Text,
  Tooltip,
} from '@mantine/core';

import { pct } from '../lib/format';
import { statusMeta } from '../lib/statuses';
import type { MatchEstimate, RequirementCheck, Verdict } from '../lib/types';

const CONFIDENCE_NOTES: Record<MatchEstimate['confidence'], string> = {
  low: 'Early data',
  medium: 'Some data',
  high: 'Good data',
};

function scoreColor(score: number): string {
  if (score >= 75) return 'green';
  if (score >= 50) return 'blue';
  return 'gray';
}

const VERDICT_META: Record<Verdict, { label: string; color: string }> = {
  met: { label: 'Met', color: 'green' },
  weak: { label: 'Weak', color: 'yellow' },
  missing: { label: 'Missing', color: 'red' },
  unknown: { label: 'Check', color: 'gray' },
};

const REQUIREMENT_LABELS: Record<RequirementCheck['requirement'], string> = {
  knockout: 'Filter',
  required: 'Required',
  nice_to_have: 'Nice to have',
  mentioned: 'Mentioned',
};

export function VerdictBadge({ verdict }: { verdict: Verdict }) {
  const meta = VERDICT_META[verdict];
  return (
    <Badge size="sm" variant="light" color={meta.color} tt="none" miw={64}>
      {meta.label}
    </Badge>
  );
}

export function Checklist({ checks }: { checks: RequirementCheck[] }) {
  if (!checks.length) return null;
  return (
    <Table.ScrollContainer minWidth={0}>
      <Table verticalSpacing={4}>
        <Table.Tbody>
          {checks.map((check) => (
            <Table.Tr key={`${check.kind}-${check.requirement}-${check.name}`}>
              <Table.Td w={76}>
                <VerdictBadge verdict={check.verdict} />
              </Table.Td>
              <Table.Td>
                <Group gap={6} wrap="nowrap">
                  <Text size="sm" fw={500}>
                    {check.name}
                  </Text>
                  <Text size="xs" c="dimmed">
                    {REQUIREMENT_LABELS[check.requirement]}
                  </Text>
                </Group>
                <Text size="xs" c="dimmed">
                  {check.detail}
                </Text>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? '' : 's'}`;
}

export function MatchBadge({ match }: { match: MatchEstimate | null }) {
  if (!match) return <Text size="sm">–</Text>;
  return (
    <Tooltip
      label={`${match.knocked_out ? 'Likely filtered out by a hard requirement. ' : ''}${CONFIDENCE_NOTES[match.confidence]}. Open the job to see why.`}
    >
      <Badge
        variant="filled"
        color={scoreColor(match.score)}
        size="lg"
        miw={44}
        tt="none"
        fw={700}
        className="tabular"
      >
        {match.score}
      </Badge>
    </Tooltip>
  );
}

function NameList({ label, names }: { label: string; names: string[] }) {
  if (!names.length) return null;
  return (
    <Group gap={6} align="center">
      <Text size="xs" c="dimmed" w={110}>
        {label}
      </Text>
      {names.map((name) => (
        <Badge key={name} variant="default" tt="none" fw={500}>
          {name}
        </Badge>
      ))}
    </Group>
  );
}

export function MatchDetails({ match }: { match: MatchEstimate | null }) {
  if (!match) {
    return (
      <Text size="sm" c="dimmed">
        Upload a resume to get an estimate.
      </Text>
    );
  }
  const { best_resume: best, callback } = match;
  return (
    <Stack gap="sm">
      <Group gap="xs" align="baseline">
        <Text fz={28} fw={700} lh={1} className="tabular">
          {match.score}
        </Text>
        <Text size="sm" c="dimmed">
          out of 100 for <b>{best.label}</b>
          {match.resumes > 1 && `, the best fit of ${plural(match.resumes, 'resume')}`}.{' '}
          {CONFIDENCE_NOTES[match.confidence]}.
        </Text>
      </Group>

      {match.knocked_out && (
        <Alert color="red" variant="light" p="xs">
          <Text size="sm">
            This job has a hard requirement your resume doesn't show, so the score is capped. An ATS
            or recruiter would likely filter on it.
          </Text>
        </Alert>
      )}

      <div>
        <Text size="sm" fw={600}>
          Requirements checklist
        </Text>
        <Text size="xs" c="dimmed" mb={4}>
          Met means a role or project backs it. Weak means it's only listed, or has fewer years than
          asked.{match.resume_years !== null && ` Your dated roles cover ${match.resume_years} years.`}
        </Text>
        <Checklist checks={match.checklist} />
      </div>

      <Text size="sm" fw={600}>
        Score parts
      </Text>
      <Stack gap={8}>
        {match.parts.map((part) => (
          <div key={part.label}>
            <Group justify="space-between" gap="xs" mb={2}>
              <Text size="sm" fw={500}>
                {part.label}
              </Text>
              <Text size="xs" c="dimmed" className="tabular">
                {part.score}
              </Text>
            </Group>
            <Progress value={part.score} size="sm" color={scoreColor(part.score)} />
            <Text size="xs" c="dimmed" mt={2}>
              {part.detail}
            </Text>
          </div>
        ))}
      </Stack>

      {best.required_total + best.nice_total > 0 && (
        <Text size="sm">
          {best.label} meets {best.required_matched} of {best.required_total} required
          {best.required_weak > 0 && `, with ${best.required_weak} more only weakly shown`}
          {best.nice_total > 0 && `, and names ${best.nice_matched} of ${best.nice_total} nice to have`}.
        </Text>
      )}
      {match.faded_required.length > 0 && (
        <Stack gap={2}>
          <NameList label="In older resumes" names={match.faded_required} />
          <Text size="xs" c="dimmed">
            These are required here, and you've named them before, but not in {match.latest_resume},
            your latest resume.
          </Text>
        </Stack>
      )}

      {callback && (
        <Stack gap={4}>
          <Text size="sm">
            Callback estimate: <b>{pct(callback.rate)}</b>
          </Text>
          <Text size="xs" c="dimmed">
            {callback.similar_applications > 0
              ? `${plural(callback.similar_callbacks, 'callback')} from the ${plural(callback.similar_applications, 'closest application')}, blended with your overall ${callback.callbacks} of ${callback.applications}.`
              : `No similar applications yet, so this is your overall ${callback.callbacks} of ${callback.applications}, adjusted for how few that is.`}{' '}
            Applications still waiting count as no callback yet.
          </Text>
          {callback.closest.map((item) => (
            <Group key={`${item.company}-${item.title}`} gap={6} wrap="nowrap">
              <Badge size="xs" variant="light" color={statusMeta(item.status).color} tt="none">
                {statusMeta(item.status).label}
              </Badge>
              <Text size="xs" truncate>
                {item.company} · {item.title} · {pct(item.similarity)} similar
              </Text>
            </Group>
          ))}
        </Stack>
      )}
    </Stack>
  );
}

/** Which resume to send, in one line, from the checklist that backs it. */
export function ResumeRecommendation({ match }: { match: MatchEstimate | null }) {
  if (!match) return null;
  const { best_resume: best } = match;
  const missing = match.missing_required;
  return (
    <Stack gap={4}>
      <Group gap="xs" align="center" wrap="wrap">
        <Text size="sm" fw={600}>
          Send
        </Text>
        <Badge variant="light" color="green" tt="none" fw={600} size="lg">
          {best.label}
        </Badge>
        {best.required_total > 0 ? (
          <Text size="sm" c="dimmed">
            meets {best.required_matched} of {best.required_total} required
            {best.required_weak > 0 && `, ${best.required_weak} only weakly shown`}
          </Text>
        ) : (
          <Text size="sm" c="dimmed">
            This job states no required stack to compare.
          </Text>
        )}
      </Group>
      {missing.length > 0 && (
        <Text size="xs" c="dimmed">
          Not on it: {missing.join(', ')}.
        </Text>
      )}
      {match.knocked_out && (
        <Text size="xs" c="red">
          A hard requirement isn't on this resume, so expect a filter.
        </Text>
      )}
    </Stack>
  );
}

/** The match details folded away under one line, so the steps to apply stay on top. */
export function MatchChecklist({ match }: { match: MatchEstimate | null }) {
  return (
    <Accordion variant="contained" radius="md" chevronPosition="left">
      <Accordion.Item value="checklist">
        <Accordion.Control>
          <Group gap="xs">
            <Text size="sm" fw={500}>
              How this job matches your resumes
            </Text>
            {match && (
              <Badge color={scoreColor(match.score)} variant="light">
                {match.score}/100
              </Badge>
            )}
          </Group>
        </Accordion.Control>
        <Accordion.Panel>
          <MatchDetails match={match} />
        </Accordion.Panel>
      </Accordion.Item>
    </Accordion>
  );
}
