import {
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Grid,
  Group,
  Progress,
  SimpleGrid,
  Stack,
  Table,
  Text,
} from '@mantine/core';
import { IconInfoCircle } from '@tabler/icons-react';
import { Link } from 'react-router-dom';

import { formatDay, pct } from '../lib/format';
import { useBackfillRejectAnalyses, useRejectionSummary } from '../lib/queries';
import { areaLabel, ESTIMATE_BANDS, ESTIMATE_ORDER, STAGE_SHORT } from '../lib/rejections';
import type { RejectedTechnology, RejectionSummary, RejectStage } from '../lib/types';
import { FocusCard } from './Focus';
import { Empty, PageError, PageLoader } from './PageState';
import { GapActionBadge } from './RejectAnalysisPanel';
import { StatTile } from './StatTile';

const STAGES: RejectStage[] = ['applied', 'screening', 'interview', 'offer'];
const EARLY_DATA_THRESHOLD = 10;

function askedIn(row: RejectedTechnology): string {
  const parts = [];
  if (row.required) parts.push(`required in ${row.required}`);
  if (row.nice_to_have) parts.push(`nice to have in ${row.nice_to_have}`);
  if (row.mentioned) parts.push(`mentioned in ${row.mentioned}`);
  return parts.join(', ');
}

function Technologies({ summary }: { summary: RejectionSummary }) {
  return (
    <FocusCard title="Technologies to focus on">
      <Text fw={600} size="sm">
        Technologies to focus on
      </Text>
      <Text size="xs" c="dimmed" mb="sm">
        Named in rejected postings and not fully backed by the resume you sent. The badge says
        whether it's a resume fix or something to learn, from your current fact sheet.
      </Text>
      {summary.technologies.length === 0 ? (
        <Text size="sm" c="dimmed">
          No technology gaps yet.
        </Text>
      ) : (
        <Table.ScrollContainer minWidth={520}>
          <Table verticalSpacing={6}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Technology</Table.Th>
                <Table.Th w={180}>Share of rejections</Table.Th>
                <Table.Th w={150} ta="right">
                  Next step
                </Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {summary.technologies.map((row) => {
                const share = summary.analyzed ? row.total / summary.analyzed : 0;
                return (
                  <Table.Tr key={row.name}>
                    <Table.Td>
                      <Text size="sm" fw={500}>
                        {row.name}
                      </Text>
                      <Text size="xs" c="dimmed">
                        {askedIn(row)}
                        {row.weak ? ` · thin on the resume ${row.weak}×` : ''}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      <Group gap={6} wrap="nowrap">
                        <Progress value={share * 100} w={90} size="sm" />
                        <Text size="xs" className="tabular" c="dimmed">
                          {pct(share)}
                        </Text>
                      </Group>
                    </Table.Td>
                    <Table.Td ta="right">
                      <GapActionBadge action={row.action} />
                    </Table.Td>
                  </Table.Tr>
                );
              })}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}
    </FocusCard>
  );
}

function Companies({ summary }: { summary: RejectionSummary }) {
  const estimated = ESTIMATE_ORDER.reduce((total, band) => total + (summary.estimates[band] ?? 0), 0);
  return (
    <FocusCard title="The companies" h="100%">
      <Text fw={600} size="sm">
        The companies
      </Text>
      <Text size="xs" c="dimmed" mb="sm">
        A rough estimate, per report, of how much each rejection may have been about your profile.
        Estimates can be wrong.
      </Text>
      {estimated === 0 ? (
        <Text size="sm" c="dimmed">
          No estimates yet. Reports written from now on, with the company researched, have one.
        </Text>
      ) : (
        <Group gap={6} mb="sm">
          {ESTIMATE_ORDER.filter((band) => summary.estimates[band]).map((band) => (
            <Badge key={band} variant="light" color={ESTIMATE_BANDS[band].color} tt="none">
              {ESTIMATE_BANDS[band].label}: {summary.estimates[band]}
            </Badge>
          ))}
        </Group>
      )}
      {summary.repeat_companies.length > 0 && (
        <Stack gap="sm">
          <Text size="sm" fw={500}>
            Rejected you more than once
          </Text>
          {summary.repeat_companies.map((row) => (
            <Stack key={row.company} gap={2}>
              <Group justify="space-between" wrap="nowrap">
                <Text size="sm" fw={500}>
                  {row.company}
                </Text>
                <Text size="xs" c="dimmed" className="tabular">
                  {row.rejections} rejections
                </Text>
              </Group>
              <Text size="xs" c="dimmed">
                {[row.what_it_does, row.size].filter(Boolean).join(' · ') || 'Not researched yet'}
              </Text>
              {row.recent_events.map((event) => (
                <Text key={event} size="xs" c="dimmed">
                  {event}
                </Text>
              ))}
            </Stack>
          ))}
        </Stack>
      )}
    </FocusCard>
  );
}

function Experience({ summary }: { summary: RejectionSummary }) {
  return (
    <FocusCard title="Experience to build" h="100%">
      <Text fw={600} size="sm">
        Experience to build
      </Text>
      <Text size="xs" c="dimmed" mb="sm">
        What rejected postings asked for beyond named tools, that your resume showed weakly.
      </Text>
      {summary.experience.length === 0 ? (
        <Text size="sm" c="dimmed">
          Nothing yet. This fills in once reports with the posting's text are written.
        </Text>
      ) : (
        <Stack gap="sm">
          {summary.experience.map((row) => (
            <Stack key={row.area} gap={2}>
              <Group justify="space-between" wrap="nowrap">
                <Text size="sm" fw={500}>
                  {areaLabel(row.area)}
                </Text>
                <Text size="xs" c="dimmed" className="tabular">
                  {row.count} of {summary.analyzed}
                </Text>
              </Group>
              {row.examples.map((example) => (
                <Text key={`${example.application_id}-${example.need}`} size="xs" c="dimmed">
                  {example.company}: {example.need}
                </Text>
              ))}
            </Stack>
          ))}
        </Stack>
      )}
    </FocusCard>
  );
}

function Recent({ summary }: { summary: RejectionSummary }) {
  if (!summary.recent.length) return null;
  return (
    <FocusCard title="Rejections">
      <Text fw={600} size="sm" mb="sm">
        Rejections
      </Text>
      <Table.ScrollContainer minWidth={640}>
        <Table verticalSpacing={6} highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th w={110}>Applied</Table.Th>
              <Table.Th>Job</Table.Th>
              <Table.Th w={130}>Stage</Table.Th>
              <Table.Th w={70}>Score</Table.Th>
              <Table.Th>Top focus</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {summary.recent.map((row) => (
              <Table.Tr key={row.application_id}>
                <Table.Td>
                  <Text size="sm">{formatDay(row.applied_on)}</Text>
                </Table.Td>
                <Table.Td>
                  <Anchor component={Link} to={`/insights/rejections/${row.application_id}`} size="sm">
                    {row.company} · {row.role_title}
                  </Anchor>
                </Table.Td>
                <Table.Td>
                  <Text size="sm">{row.stage ? STAGE_SHORT[row.stage] : '–'}</Text>
                </Table.Td>
                <Table.Td>
                  <Text size="sm" className="tabular">
                    {row.score ?? '–'}
                  </Text>
                </Table.Td>
                <Table.Td>
                  {row.status === 'done' ? (
                    <Text size="sm">{row.top_focus ?? '–'}</Text>
                  ) : (
                    <Badge
                      size="sm"
                      variant="light"
                      color={row.status === 'failed' ? 'yellow' : 'gray'}
                      tt="none"
                    >
                      {row.status === 'failed' ? 'Report incomplete' : 'Analyzing'}
                    </Badge>
                  )}
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </FocusCard>
  );
}

/** Every rejection in the active profile, added up into what to work on next. */
export function RejectionInsights() {
  const { data: summary, isLoading, isError } = useRejectionSummary();
  const backfill = useBackfillRejectAnalyses();

  if (isLoading) return <PageLoader />;
  if (isError || !summary) return <PageError />;
  if (!summary.rejections) {
    return (
      <Card>
        <Empty
          title="No rejections yet"
          hint="When you mark an application rejected, its posting is compared with the resume you sent and your fact sheet, and the report shows up here."
        />
      </Card>
    );
  }

  const stageNote = STAGES.filter((stage) => summary.stages[stage])
    .map((stage) => `${STAGE_SHORT[stage]} ${summary.stages[stage]}`)
    .join(' · ');

  return (
    <Stack>
      {summary.not_analyzed > 0 && (
        <Alert variant="light" icon={<IconInfoCircle size={16} />}>
          <Group justify="space-between" wrap="nowrap">
            <Text size="sm">
              {summary.not_analyzed} earlier rejection
              {summary.not_analyzed === 1 ? ' has' : 's have'} no report.{' '}
              {summary.daily_limit === null
                ? 'You have no daily limit, so they all run now.'
                : `Up to ${summary.daily_limit} run a day; the rest wait for the next day.`}
            </Text>
            <Button size="xs" loading={backfill.isPending} onClick={() => backfill.mutate()}>
              Analyze them
            </Button>
          </Group>
        </Alert>
      )}
      {summary.analyzed > 0 && summary.analyzed < EARLY_DATA_THRESHOLD && (
        <Text size="xs" c="dimmed">
          Early data: {summary.analyzed} report{summary.analyzed === 1 ? '' : 's'} so far. Patterns
          firm up as more come in.
        </Text>
      )}

      <SimpleGrid cols={{ base: 2, sm: 4 }}>
        <StatTile label="Rejections" value={String(summary.rejections)} note={stageNote} />
        <StatTile
          label="Analyzed"
          value={String(summary.analyzed)}
          note={summary.waiting ? `${summary.waiting} in progress` : undefined}
        />
        <StatTile
          label="Average score"
          value={summary.average_score === null ? '–' : String(Math.round(summary.average_score))}
          note="Checklist score of the resume sent"
        />
        <StatTile
          label="Resume screen"
          value={summary.rejections ? pct(summary.stages.applied / summary.rejections) : '–'}
          note="Rejected before any call"
        />
      </SimpleGrid>

      <Grid>
        <Grid.Col span={{ base: 12, md: 7 }}>
          <Technologies summary={summary} />
        </Grid.Col>
        <Grid.Col span={{ base: 12, md: 5 }}>
          <Experience summary={summary} />
        </Grid.Col>
      </Grid>

      <Companies summary={summary} />

      <Recent summary={summary} />
    </Stack>
  );
}
