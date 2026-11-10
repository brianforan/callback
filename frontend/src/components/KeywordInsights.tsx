import { ActionIcon, Badge, Card, Grid, Group, Stack, Table, Text, Tooltip } from '@mantine/core';
import { IconEyeOff, IconX } from '@tabler/icons-react';

import { formatDay, formatWeek, pct } from '../lib/format';
import { useIgnoreTerm, useKeywordTrends, useRestoreTerm } from '../lib/queries';
import type { KeywordRow, KeywordTrends } from '../lib/types';
import { ChartCard, ShareTrendChart } from './charts';
import { FocusCard } from './Focus';
import { Empty, PageError, PageLoader } from './PageState';

function IgnoreButton({ term }: { term: string }) {
  const ignore = useIgnoreTerm();
  return (
    <Tooltip label="Ignore this skill">
      <ActionIcon
        size="sm"
        variant="subtle"
        color="gray"
        aria-label={`Ignore ${term}`}
        loading={ignore.isPending}
        onClick={() => ignore.mutate(term)}
      >
        <IconEyeOff size={14} />
      </ActionIcon>
    </Tooltip>
  );
}

function UsageTable({ rows }: { rows: KeywordRow[] }) {
  return (
    <Table.ScrollContainer minWidth={0} mah={420}>
      <Table>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Skill</Table.Th>
            <Table.Th ta="right">Applications</Table.Th>
            <Table.Th ta="right">Mentions</Table.Th>
            <Table.Th>Last used</Table.Th>
            <Table.Th />
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody className="tabular">
          {rows.map((row) => (
            <Table.Tr key={row.term}>
              <Table.Td fw={500}>{row.term}</Table.Td>
              <Table.Td ta="right">{pct(row.share)}</Table.Td>
              <Table.Td ta="right">{row.mentions}</Table.Td>
              <Table.Td>{formatDay(row.last_used)}</Table.Td>
              <Table.Td>
                <IgnoreButton term={row.term} />
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

function ChangeTable({ rows }: { rows: KeywordRow[] }) {
  return (
    <Table.ScrollContainer minWidth={0} mah={320}>
      <Table>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Skill</Table.Th>
            <Table.Th ta="right">Earliest third</Table.Th>
            <Table.Th ta="right">Latest third</Table.Th>
            <Table.Th>Last used</Table.Th>
            <Table.Th />
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody className="tabular">
          {rows.map((row) => (
            <Table.Tr key={row.term}>
              <Table.Td fw={500}>{row.term}</Table.Td>
              <Table.Td ta="right">{pct(row.early_share ?? 0)}</Table.Td>
              <Table.Td ta="right">{pct(row.recent_share ?? 0)}</Table.Td>
              <Table.Td>{formatDay(row.last_used)}</Table.Td>
              <Table.Td>
                <IgnoreButton term={row.term} />
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

function ChangeCard({
  title,
  subtitle,
  rows,
  trends,
}: {
  title: string;
  subtitle: string;
  rows: KeywordRow[];
  trends: KeywordTrends;
}) {
  const enough = trends.applications >= trends.min_applications;
  return (
    <FocusCard title={title} h="100%">
      <Text fw={600} size="sm">
        {title}
      </Text>
      <Text size="xs" c="dimmed" mb="sm">
        {subtitle}
      </Text>
      {rows.length ? (
        <ChangeTable rows={rows} />
      ) : (
        <Empty
          title={enough ? 'None so far' : 'Not enough applications yet'}
          hint={
            enough
              ? 'No skill has moved enough between your earliest and latest applications.'
              : `Needs ${trends.min_applications} applications with a resume. You have ${trends.applications}.`
          }
        />
      )}
    </FocusCard>
  );
}

function IgnoredList({ terms }: { terms: string[] }) {
  const restore = useRestoreTerm();
  return (
    <Card>
      <Text fw={600} size="sm">
        Ignored skills
      </Text>
      <Text size="xs" c="dimmed" mb="sm">
        Left out of the skill trends above.
      </Text>
      <Group gap={6}>
        {terms.map((term) => (
          <Badge
            key={term}
            variant="default"
            tt="none"
            fw={500}
            rightSection={
              <ActionIcon
                size="xs"
                variant="transparent"
                color="gray"
                aria-label={`Stop ignoring ${term}`}
                onClick={() => restore.mutate(term)}
              >
                <IconX size={12} />
              </ActionIcon>
            }
          >
            {term}
          </Badge>
        ))}
      </Group>
    </Card>
  );
}

export function KeywordInsights() {
  const { data: trends, isPending, isError } = useKeywordTrends();

  if (isPending) return <PageLoader />;
  if (isError) return <PageError />;

  if (!trends.applications) {
    return (
      <Stack>
        <Card>
          <Empty
            title="No skill trends yet"
            hint="Trends come from the known skills in the resumes linked to your applications."
          />
        </Card>
        {trends.ignored.length > 0 && <IgnoredList terms={trends.ignored} />}
      </Stack>
    );
  }

  return (
    <Stack>
      <Text fw={600}>Skills over time</Text>
      <Grid gutter="sm">
        <Grid.Col span={12}>
          <ChartCard
            title="Skills over time"
            subtitle="Share of each week's applications whose resume named the skills that moved most"
            columns={['Week', ...trends.chart_terms]}
            rows={trends.weekly.map((week) => [
              formatWeek(week.week_start),
              ...trends.chart_terms.map((term) =>
                term in week.shares ? pct(week.shares[term]) : '–',
              ),
            ])}
          >
            <ShareTrendChart data={trends.weekly} names={trends.chart_terms} />
          </ChartCard>
        </Grid.Col>
        <Grid.Col span={{ base: 12, lg: 6 }}>
          <ChangeCard
            title="Faded"
            subtitle="Common in your earliest applications, much less so in your latest"
            rows={trends.faded}
            trends={trends}
          />
        </Grid.Col>
        <Grid.Col span={{ base: 12, lg: 6 }}>
          <ChangeCard
            title="Rising"
            subtitle="Common in your latest applications, much less so in your earliest"
            rows={trends.rising}
            trends={trends}
          />
        </Grid.Col>
        <Grid.Col span={12}>
          <FocusCard title="Most used skills" h="100%">
            <Text fw={600} size="sm">
              Most used skills
            </Text>
            <Text size="xs" c="dimmed" mb="sm">
              Share of {trends.applications} applications, then total mentions across them
            </Text>
            <UsageTable rows={trends.most_used} />
          </FocusCard>
        </Grid.Col>
      </Grid>
      {trends.ignored.length > 0 && <IgnoredList terms={trends.ignored} />}
    </Stack>
  );
}
