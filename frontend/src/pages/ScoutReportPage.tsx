import {
  Anchor,
  Badge,
  Button,
  Grid,
  Group,
  Paper,
  Progress,
  SimpleGrid,
  Stack,
  Table,
  Text,
  ThemeIcon,
  Title,
  Tooltip,
} from '@mantine/core';
import {
  IconAlertTriangle,
  IconArrowLeft,
  IconCircleCheck,
  IconInfoCircle,
} from '@tabler/icons-react';
import type { ReactNode } from 'react';
import { Link, useParams } from 'react-router-dom';

import { ChartCard } from '../components/charts';
import { Empty, PageError, PageLoader } from '../components/PageState';
import { LeadList } from '../components/ScoutLeadCard';
import { formatAmount, RunSteps } from '../components/ScoutRuns';
import { StatTile } from '../components/StatTile';
import { formatDateTime, formatUsd, pct } from '../lib/format';
import { isSearching, useScoutReport } from '../lib/queries';
import type { ScoutReport } from '../lib/types';

// One hue for every magnitude bar on this page: each bar is labelled, so color carries no identity.
const BAR_COLOR = 'moss';

const FINDING_STYLE = {
  good: { icon: IconCircleCheck, color: 'green', label: 'Good' },
  info: { icon: IconInfoCircle, color: 'blue', label: 'Note' },
  warning: { icon: IconAlertTriangle, color: 'yellow', label: 'Check' },
} as const;


function Section({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return (
    <Paper withBorder p="md" radius="md">
      <Stack gap="sm">
        <div>
          <Title order={5}>{title}</Title>
          {hint && (
            <Text size="xs" c="dimmed">
              {hint}
            </Text>
          )}
        </div>
        {children}
      </Stack>
    </Paper>
  );
}

function Findings({ report }: { report: ScoutReport }) {
  if (!report.findings.length) return null;
  return (
    <Section title="Findings" hint="Worked out by plain rules from what the run recorded.">
      <Stack gap="sm">
        {report.findings.map((finding) => {
          const style = FINDING_STYLE[finding.level];
          const Icon = style.icon;
          return (
            <Group key={finding.title} gap="sm" wrap="nowrap" align="flex-start">
              <ThemeIcon variant="light" color={style.color} size="md" radius="xl">
                <Icon size={16} aria-label={style.label} />
              </ThemeIcon>
              <div style={{ minWidth: 0 }}>
                <Text size="sm" fw={600}>
                  {finding.title}
                </Text>
                <Text size="sm" c="dimmed">
                  {finding.detail}
                </Text>
              </div>
            </Group>
          );
        })}
      </Stack>
    </Section>
  );
}

/** How much of a limit was used: dollars for the spending ceiling, a count for the rest. */
function usedOf(limit: ScoutReport['limits'][number], joiner: string): string {
  if (limit.unit === 'USD') return `${formatUsd(limit.used)}${joiner}${formatUsd(limit.limit)}`;
  return `${formatAmount(limit.used)}${joiner}${formatAmount(limit.limit)} ${limit.unit}`;
}

function LimitsCard({ report }: { report: ScoutReport }) {
  const rows = report.limits.map((limit) => [
    limit.label,
    usedOf(limit, ' of '),
    pct(Math.min(1, limit.used / limit.limit)),
    limit.stopped_run ? 'Stopped the run' : '',
  ]);
  return (
    <ChartCard
      title="Limits used"
      subtitle="How much of each limit the run spent. The one that ended it is marked."
      columns={['Limit', 'Used', 'Share', '']}
      rows={rows}
    >
      <Stack gap="md">
        {report.limits.map((limit) => {
          const share = Math.min(100, (limit.used / limit.limit) * 100);
          return (
            <Stack key={limit.key} gap={4}>
              <Group justify="space-between" wrap="nowrap" gap="xs">
                <Group gap={6} wrap="nowrap">
                  <Text size="sm">{limit.label}</Text>
                  {limit.stopped_run && (
                    <Badge size="xs" variant="light" color="gray" tt="none">
                      Stopped the run
                    </Badge>
                  )}
                </Group>
                <Text size="sm" className="tabular" style={{ whiteSpace: 'nowrap' }}>
                  {usedOf(limit, ' / ')}
                </Text>
              </Group>
              <Tooltip label={`${Math.round(share)}% used`} withArrow>
                <Progress
                  value={share}
                  color={BAR_COLOR}
                  size="md"
                  radius="xl"
                  aria-label={`${limit.label}: ${Math.round(share)}% used`}
                />
              </Tooltip>
            </Stack>
          );
        })}
      </Stack>
    </ChartCard>
  );
}

function CostCard({ report }: { report: ScoutReport }) {
  if (!report.costs.length) {
    return (
      <Section title="Where the money went">
        <Text size="sm" c="dimmed">
          No cost was recorded for this run.
        </Text>
      </Section>
    );
  }
  const largest = report.costs[0].usd;
  const rows = report.costs.map((part) => [part.label, formatUsd(part.usd), pct(part.share)]);
  const tokens = report.run;
  return (
    <ChartCard
      title="Where the money went"
      subtitle={`${formatUsd(report.cost_usd)} in all`}
      columns={['Part', 'Cost', 'Share']}
      rows={rows}
    >
      <Stack gap="sm">
        {report.costs.map((part) => (
          <Stack key={part.key} gap={4}>
            <Group justify="space-between" wrap="nowrap" gap="xs">
              <Text size="sm">{part.label}</Text>
              <Text size="sm" className="tabular" style={{ whiteSpace: 'nowrap' }}>
                {formatUsd(part.usd)} · {pct(part.share)}
              </Text>
            </Group>
            {/* Bars are scaled to the largest part, so small parts stay visible. */}
            <Tooltip label={`${formatUsd(part.usd)}, ${pct(part.share)} of the run`} withArrow>
              <Progress
                value={(part.usd / largest) * 100}
                color={BAR_COLOR}
                size="md"
                radius="xl"
                aria-label={`${part.label}: ${formatUsd(part.usd)}`}
              />
            </Tooltip>
          </Stack>
        ))}
        <Text size="xs" c="dimmed">
          {tokens.searches} searches and {tokens.pages} pages over {report.requests} model{' '}
          {report.requests === 1 ? 'step' : 'steps'}.
        </Text>
      </Stack>
    </ChartCard>
  );
}

function QueriesCard({ report }: { report: ScoutReport }) {
  return (
    <Section
      title={`Searches (${report.queries.length})`}
      hint="Each query, in order, with how many of its results the run hadn't seen before."
    >
      {report.queries.length === 0 ? (
        <Text size="sm" c="dimmed">
          No searches were recorded.
        </Text>
      ) : (
        <Table.ScrollContainer minWidth={520}>
          <Table verticalSpacing={6}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th w={60}>Step</Table.Th>
                <Table.Th>Query</Table.Th>
                <Table.Th w={80} ta="right">
                  Results
                </Table.Th>
                <Table.Th w={70} ta="right">
                  New
                </Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody className="tabular">
              {report.queries.map((query, index) => (
                <Table.Tr key={index}>
                  <Table.Td c="dimmed">{query.request}</Table.Td>
                  <Table.Td style={{ overflowWrap: 'anywhere' }}>
                    <Group gap={6} wrap="nowrap" align="flex-start">
                      <Text size="sm">{query.query}</Text>
                      {query.repeated && (
                        <Badge size="xs" color="yellow" variant="light" tt="none">
                          Repeat
                        </Badge>
                      )}
                    </Group>
                  </Table.Td>
                  <Table.Td ta="right">{query.results ?? '–'}</Table.Td>
                  <Table.Td ta="right" fw={query.new === 0 ? 400 : 600}>
                    {query.new ?? '–'}
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}
    </Section>
  );
}

function PagesCard({ report }: { report: ScoutReport }) {
  return (
    <Section
      title={`Pages opened (${report.pages.length})`}
      hint="Postings and company pages it read, and whether each became or backed a lead."
    >
      {report.pages.length === 0 ? (
        <Text size="sm" c="dimmed">
          It didn't open any pages.
        </Text>
      ) : (
        <Table.ScrollContainer minWidth={520}>
          <Table verticalSpacing={6}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th w={60}>Step</Table.Th>
                <Table.Th>Page</Table.Th>
                <Table.Th w={110}>Result</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {report.pages.map((page, index) => (
                <Table.Tr key={index}>
                  <Table.Td c="dimmed" className="tabular">
                    {page.request}
                  </Table.Td>
                  <Table.Td style={{ overflowWrap: 'anywhere' }}>
                    <Anchor
                      href={page.url}
                      target="_blank"
                      rel="noopener noreferrer nofollow"
                      size="sm"
                    >
                      {page.host}
                    </Anchor>
                    <Text size="xs" c="dimmed" lineClamp={1}>
                      {page.url}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    {page.became_lead ? (
                      <Badge color="green" variant="light" tt="none">
                        Lead
                      </Badge>
                    ) : (
                      <Text size="sm" c="dimmed">
                        Not kept
                      </Text>
                    )}
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}
    </Section>
  );
}

function ProblemsCard({ report }: { report: ScoutReport }) {
  if (!report.refused.length && !report.errors.length) return null;
  return (
    <Section title="Refused leads and failures">
      {report.refused.length > 0 && (
        <Stack gap={4}>
          <Text size="xs" fw={600} c="dimmed" tt="uppercase">
            Refused leads
          </Text>
          {report.refused.map((detail, index) => (
            <Text key={index} size="sm">
              • {detail}
            </Text>
          ))}
        </Stack>
      )}
      {report.errors.length > 0 && (
        <Stack gap={4}>
          <Text size="xs" fw={600} c="dimmed" tt="uppercase">
            Failed searches and page reads
          </Text>
          {report.errors.map((error) => (
            <Text key={error.name} size="sm">
              • {error.name} <span className="tabular">({error.count})</span>
            </Text>
          ))}
        </Stack>
      )}
    </Section>
  );
}

export function ScoutReportPage() {
  const { runId } = useParams();
  const { data: report, isPending, isError } = useScoutReport(runId);

  if (isPending) return <PageLoader />;
  if (isError || !report) return <PageError />;
  const { run } = report;
  const target = report.limits.find((limit) => limit.key === 'leads')?.limit;
  const minutesLimit = report.limits.find((limit) => limit.key === 'minutes')?.limit;
  const failed = run.status === 'failed';

  return (
    <Stack>
      <Group justify="space-between" wrap="wrap">
        <Button
          component={Link}
          to="/scout/runs"
          variant="subtle"
          color="gray"
          size="xs"
          px={0}
          leftSection={<IconArrowLeft size={14} />}
        >
          All runs
        </Button>
      </Group>
      <Stack gap={4}>
        <Group gap="sm">
          <Title order={3}>Run report</Title>
          <Badge
            variant="light"
            color={failed ? 'red' : isSearching(run) ? 'gray' : 'green'}
            tt="none"
          >
            {failed ? 'Failed' : isSearching(run) ? 'Running' : 'Finished'}
          </Badge>
          {run.label && (
            <Badge variant="outline" color="gray" tt="none">
              {run.label}
            </Badge>
          )}
        </Group>
        <Text size="sm" c="dimmed">
          Started {formatDateTime(run.created_at)}
          {run.stop_message && ` · ${run.stop_message}`}
        </Text>
        <Text size="sm" fs="italic" maw={900}>
          “{run.description}”
        </Text>
      </Stack>

      {isSearching(run) ? (
        <Empty title="This run is still going" hint="The report fills in when it ends." />
      ) : (
        <>
          <SimpleGrid cols={{ base: 2, md: 5 }} spacing="md">
            <StatTile
              label="Leads"
              value={target ? `${run.leads.length} of ${target}` : String(run.leads.length)}
              note={`${run.leads.filter((lead) => lead.posting_id).length} tracked`}
            />
            <StatTile
              label="Cost"
              value={formatUsd(report.cost_usd)}
              note={run.budget_usd ? `of ${formatUsd(run.budget_usd)} allowed` : undefined}
            />
            <StatTile
              label="Per lead"
              value={report.cost_per_lead_usd === null ? '–' : formatUsd(report.cost_per_lead_usd)}
              note={report.cost_per_lead_usd === null ? 'No leads' : undefined}
            />
            <StatTile
              label="Time"
              value={report.minutes_used === null ? '–' : `${report.minutes_used} min`}
              note={minutesLimit ? `of ${minutesLimit} allowed` : undefined}
            />
            <StatTile
              label="Steps"
              value={String(report.requests)}
              note={
                report.nudges
                  ? `${report.nudges} ${report.nudges === 1 ? 'nudge' : 'nudges'} to keep going`
                  : 'No nudges needed'
              }
            />
          </SimpleGrid>

          <Findings report={report} />

          <Grid gutter="md">
            <Grid.Col span={{ base: 12, md: 6 }}>
              <LimitsCard report={report} />
            </Grid.Col>
            <Grid.Col span={{ base: 12, md: 6 }}>
              <CostCard report={report} />
            </Grid.Col>
          </Grid>

          <Stack gap="sm">
            <Title order={4}>Leads ({run.leads.length})</Title>
            {report.lead_hosts.length > 0 && (
              <Text size="xs" c="dimmed">
                From{' '}
                {report.lead_hosts
                  .map((host) => `${host.name} (${host.count})`)
                  .join(', ')}
              </Text>
            )}
            <LeadList run={run} empty={<Empty title="No leads this run" />} />
          </Stack>

          <QueriesCard report={report} />
          <PagesCard report={report} />
          <ProblemsCard report={report} />
          <Section title="Full step log" hint="Everything the agent did, in order.">
            <RunSteps steps={run.steps} />
          </Section>
        </>
      )}
    </Stack>
  );
}
