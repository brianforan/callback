import { Badge, Card, Grid, Group, Stack, Table, Text } from '@mantine/core';

import { formatDay, pct } from '../lib/format';
import { useOverview, useResumeInsights } from '../lib/queries';
import type { EvidenceLevel, ResumeEvidence, SkillDemand } from '../lib/types';
import { ChartCard, RateBars, type RateRow } from './charts';
import { FocusCard } from './Focus';
import { KeywordInsights } from './KeywordInsights';
import { VerdictBadge } from './MatchEstimate';
import { Empty, PageError, PageLoader } from './PageState';

const EVIDENCE_LABELS: Record<EvidenceLevel, string> = {
  experience: 'In a dated role',
  project: 'In a project',
  listed: 'Only listed',
  none: 'Not on resume',
};

function months(count: number): string {
  if (!count) return '–';
  return count < 12 ? `${count} mo` : `${(count / 12).toFixed(1)} yr`;
}

function DemandTable({ rows }: { rows: SkillDemand[] }) {
  return (
    <Table.ScrollContainer minWidth={0} mah={420}>
      <Table>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Skill</Table.Th>
            <Table.Th ta="right">Required in</Table.Th>
            <Table.Th ta="right">Named in</Table.Th>
            <Table.Th>Your latest resume</Table.Th>
            <Table.Th ta="right">Time</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody className="tabular">
          {rows.map((row) => (
            <Table.Tr key={row.name}>
              <Table.Td fw={500}>{row.name}</Table.Td>
              <Table.Td ta="right">{row.required_in}</Table.Td>
              <Table.Td ta="right">{row.postings}</Table.Td>
              <Table.Td>
                <Group gap={6} wrap="nowrap">
                  <VerdictBadge verdict={row.verdict} />
                  <Text size="xs" c="dimmed">
                    {EVIDENCE_LABELS[row.evidence]}
                  </Text>
                </Group>
              </Table.Td>
              <Table.Td ta="right">{months(row.months)}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

function NameBadges({ names, variant }: { names: string[]; variant: 'light' | 'default' }) {
  if (!names.length) {
    return (
      <Text size="xs" c="dimmed">
        None
      </Text>
    );
  }
  return (
    <Group gap={4}>
      {names.map((name) => (
        <Badge key={name} size="sm" variant={variant} tt="none" fw={500}>
          {name}
        </Badge>
      ))}
    </Group>
  );
}

export function EvidenceCard({ resume }: { resume: ResumeEvidence }) {
  return (
    <Card withBorder h="100%">
      <Group justify="space-between" mb={4}>
        <Text fw={600} size="sm">
          {resume.label}
        </Text>
        <Text size="xs" c="dimmed">
          {formatDay(resume.uploaded_at)}
        </Text>
      </Group>
      <Text size="xs" c="dimmed" mb="xs">
        {resume.roles} dated {resume.roles === 1 ? 'role' : 'roles'}
        {resume.years !== null && `, ${resume.years} years`}
        {resume.degree ? `, ${resume.degree}'s degree` : ', no degree found'}.{' '}
        {resume.sections.length
          ? `Sections: ${resume.sections.join(', ')}.`
          : 'No sections found, so every skill counts as only listed.'}
      </Text>
      <Text size="xs" fw={500} mb={2}>
        Backed by a role or project
      </Text>
      <NameBadges names={resume.backed} variant="light" />
      <Text size="xs" fw={500} mt="xs" mb={2}>
        Only listed
      </Text>
      <NameBadges names={resume.listed_only} variant="default" />
    </Card>
  );
}

const EARLY_DATA_THRESHOLD = 20;

export function ResumeInsights() {
  const overview = useOverview();
  const insights = useResumeInsights();

  if (overview.isPending || insights.isPending) return <PageLoader />;
  if (overview.isError || insights.isError) return <PageError />;

  const { totals, resumes } = overview.data;
  const { demand, latest_resume, postings, resumes: evidence } = insights.data;

  const resumeRates: RateRow[] = resumes
    .filter((resume) => resume.applications > 0)
    .map((resume) => ({
      label: resume.label,
      rate: resume.callback_rate,
      applications: resume.applications,
      callbacks: resume.callbacks,
    }));

  return (
    <Stack>
      {totals.applications < EARLY_DATA_THRESHOLD && (
        <Text size="xs" c="dimmed">
          With {totals.applications} {totals.applications === 1 ? 'application' : 'applications'},
          these are early signals rather than conclusions. Applications still waiting for a reply
          count as no callback yet.
        </Text>
      )}

      <Text fw={600}>Requirements</Text>
      <Grid gutter="sm">
        <Grid.Col span={12}>
          <FocusCard title="What postings ask for" h="100%">
            <Text fw={600} size="sm">
              What postings ask for
            </Text>
            <Text size="xs" c="dimmed" mb="sm">
              Skills across your {postings} {postings === 1 ? 'posting' : 'postings'}, most often
              required first, checked against {latest_resume ?? 'your latest resume'} the way an
              ATS would: met when a role or project names it, weak when it's only listed.
            </Text>
            {demand.length ? (
              <DemandTable rows={demand} />
            ) : (
              <Empty
                title="No posting stacks yet"
                hint="Add postings with their text, and the skills they ask for show up here."
              />
            )}
          </FocusCard>
        </Grid.Col>
      </Grid>

      <Text fw={600}>What each resume shows</Text>
      <Text size="xs" c="dimmed" mt={-8}>
        What the parser read from your newest resumes. A skill only in a skills list is weaker
        evidence than one in a dated role.
      </Text>
      {evidence.length ? (
        <Grid gutter="sm">
          {evidence.map((resume) => (
            <Grid.Col key={resume.resume_id} span={{ base: 12, md: 6, lg: 4 }}>
              <EvidenceCard resume={resume} />
            </Grid.Col>
          ))}
        </Grid>
      ) : (
        <Card>
          <Empty title="No resumes yet" hint="Upload a resume to see what an ATS would read." />
        </Card>
      )}

      <Text fw={600}>Callbacks</Text>
      <Grid gutter="sm">
        <Grid.Col span={12}>
          {resumeRates.length ? (
            <ChartCard
              title="Which resumes worked"
              subtitle="Share of applications that reached screening or beyond"
              columns={['Resume', 'Applications', 'Callbacks', 'Interviews', 'Rate']}
              rows={resumes
                .filter((resume) => resume.applications > 0)
                .map((resume) => [
                  resume.label,
                  resume.applications,
                  resume.callbacks,
                  resume.interviews,
                  pct(resume.callback_rate),
                ])}
            >
              <RateBars data={resumeRates} />
            </ChartCard>
          ) : (
            <Card h="100%">
              <Empty title="Link resumes to applications" hint="Then this compares callback rates across versions." />
            </Card>
          )}
        </Grid.Col>
      </Grid>

    </Stack>
  );
}

