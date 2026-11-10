import { Card, Grid, Group, Stack, Table, Text } from '@mantine/core';

import { formatDay, formatWeek, pct } from '../lib/format';
import { fieldLabel, formatSalary, LEVELS } from '../lib/postings';
import { useOverview, usePostingTrends } from '../lib/queries';
import type { LabelCount, PostingTrends, SourcePerformance } from '../lib/types';
import {
  ChartCard,
  LevelMixChart,
  RateBars,
  ShareTrendChart,
  WeeklyValueChart,
  type RateRow,
} from './charts';
import { FocusCard } from './Focus';
import { Empty, PageError, PageLoader } from './PageState';

const EARLY_DATA_THRESHOLD = 20;
const STACK_CHART_SERIES = 5;

function CountList({ title, rows }: { title: string; rows: LabelCount[] }) {
  const total = rows.reduce((sum, row) => sum + row.count, 0);
  return (
    <FocusCard title={title} h="100%">
      <Text fw={600} size="sm" mb="sm">
        {title}
      </Text>
      <Stack gap={6}>
        {rows.map((row) => (
          <Group key={row.label} justify="space-between" wrap="nowrap">
            <Text size="sm">{row.label === 'not_set' ? 'Not set' : fieldLabel(row.label)}</Text>
            <Text size="sm" className="tabular" c="dimmed">
              {row.count} · {pct(total ? row.count / total : 0)}
            </Text>
          </Group>
        ))}
      </Stack>
    </FocusCard>
  );
}

function PostingTrendsView({ trends, sources }: { trends: PostingTrends; sources: SourcePerformance[] }) {
  if (!trends.applied_postings) {
    return (
      <Card>
        <Empty
          title="No applied postings yet"
          hint="Trends cover postings linked to an application, dated by when you applied."
        />
      </Card>
    );
  }

  const topStack = trends.stack.slice(0, STACK_CHART_SERIES).map((row) => row.name);
  const referralRates: RateRow[] = trends.referrals.map((row) => ({
    label: row.source,
    rate: row.callback_rate,
    applications: row.applications,
    callbacks: row.callbacks,
  }));
  const sourceRates: RateRow[] = sources.map((source) => ({
    label: source.source,
    rate: source.callback_rate,
    applications: source.applications,
    callbacks: source.callbacks,
  }));
  const coverageRates: RateRow[] = trends.coverage_callbacks.map((row) => ({
    label: row.source,
    rate: row.callback_rate,
    applications: row.applications,
    callbacks: row.callbacks,
  }));
  const recommendationRates: RateRow[] = trends.recommendation_callbacks.map((row) => ({
    label: row.source,
    rate: row.callback_rate,
    applications: row.applications,
    callbacks: row.callbacks,
  }));
  const levelsUsed = [...LEVELS, 'not_set'].filter((level) =>
    trends.weekly.some((week) => week.levels[level]),
  );

  return (
    <Stack>
      {trends.applied_postings < EARLY_DATA_THRESHOLD && (
        <Text size="xs" c="dimmed">
          With {trends.applied_postings} applied{' '}
          {trends.applied_postings === 1 ? 'posting' : 'postings'}, these are early signals rather
          than conclusions.
        </Text>
      )}
      <Grid gutter="sm">
        <Grid.Col span={12}>
          <ChartCard
            title="Level over time"
            subtitle="Postings you applied to each week, by level"
            columns={['Week', ...levelsUsed.map((level) => (level === 'not_set' ? 'Not set' : fieldLabel(level)))]}
            rows={trends.weekly.map((week) => [
              formatWeek(week.week_start),
              ...levelsUsed.map((level) => week.levels[level] ?? 0),
            ])}
          >
            <LevelMixChart data={trends.weekly} levels={levelsUsed} />
          </ChartCard>
        </Grid.Col>
        <Grid.Col span={{ base: 12, md: 6 }}>
          <ChartCard
            title="Years required"
            subtitle="Median years of experience asked for each week"
            columns={['Week', 'Median years']}
            rows={trends.weekly.map((week) => [formatWeek(week.week_start), week.median_years ?? '–'])}
          >
            <WeeklyValueChart
              data={trends.weekly.map((week) => ({
                week_start: week.week_start,
                value: week.median_years,
              }))}
              name="Median years"
              format={(value) => String(value)}
              emptyText="None of these postings list years of experience."
            />
          </ChartCard>
        </Grid.Col>
        <Grid.Col span={{ base: 12, md: 6 }}>
          <ChartCard
            title="Salary"
            subtitle="Median midpoint of the listed salary each week"
            columns={['Week', 'Median salary']}
            rows={trends.weekly.map((week) => [
              formatWeek(week.week_start),
              formatSalary(week.median_salary, week.median_salary),
            ])}
          >
            <WeeklyValueChart
              data={trends.weekly.map((week) => ({
                week_start: week.week_start,
                value: week.median_salary,
              }))}
              name="Median salary"
              format={(value) => formatSalary(value, value)}
              emptyText="None of these postings list a salary."
            />
          </ChartCard>
        </Grid.Col>
        <Grid.Col span={12}>
          <ChartCard
            title="Stack over time"
            subtitle={`Share of each week's postings that use your ${topStack.length} most common technologies`}
            columns={['Technology', 'Postings', 'Share', 'Required', 'First seen', 'Last seen']}
            rows={trends.stack.map((row) => [
              row.name,
              row.postings,
              pct(row.share),
              row.required,
              formatDay(row.first_seen),
              formatDay(row.last_seen),
            ])}
          >
            <ShareTrendChart data={trends.stack_weekly} names={topStack} />
          </ChartCard>
        </Grid.Col>
        <Grid.Col span={{ base: 12, md: 6 }}>
          {trends.scored_postings ? (
            <ChartCard
              title="Required stack match"
              subtitle={`Median share of required technologies your resume named. ${pct(trends.median_coverage ?? 0)} across ${trends.scored_postings} postings.`}
              columns={['Week', 'Median match']}
              rows={trends.weekly.map((week) => [
                formatWeek(week.week_start),
                week.median_coverage === null ? '–' : pct(week.median_coverage),
              ])}
            >
              <WeeklyValueChart
                data={trends.weekly.map((week) => ({
                  week_start: week.week_start,
                  value: week.median_coverage,
                }))}
                name="Median match"
                format={pct}
                emptyText="None of these postings could be scored yet."
              />
            </ChartCard>
          ) : (
            <Card h="100%">
              <Empty
                title="Nothing to compare yet"
                hint="Needs an application with a resume, for a posting that lists required technologies."
              />
            </Card>
          )}
        </Grid.Col>
        <Grid.Col span={{ base: 12, md: 6 }}>
          <FocusCard title="Repeated stack gaps" h="100%">
            <Text fw={600} size="sm">
              Repeated stack gaps
            </Text>
            <Text size="xs" c="dimmed" mb="sm">
              Required technologies your resume didn't name. This reflects the resume text, not what
              you know.
            </Text>
            {trends.gaps.length ? (
              <Table.ScrollContainer minWidth={0} mah={280}>
                <Table>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>Technology</Table.Th>
                      <Table.Th ta="right">Not named</Table.Th>
                      <Table.Th ta="right">Required in</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody className="tabular">
                    {trends.gaps.map((gap) => (
                      <Table.Tr key={gap.name}>
                        <Table.Td fw={500}>{gap.name}</Table.Td>
                        <Table.Td ta="right">{gap.missing_in}</Table.Td>
                        <Table.Td ta="right">{gap.required_in}</Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </Table.ScrollContainer>
            ) : (
              <Empty title="No gaps found" hint="Your resumes name every required technology so far." />
            )}
          </FocusCard>
        </Grid.Col>
        {coverageRates.length > 0 && (
          <Grid.Col span={12}>
            <ChartCard
              title="Callbacks by stack match"
              subtitle="Each application is scored against the resume it sent. A correlation, not a cause."
              columns={['Match', 'Applications', 'Callbacks', 'Rate']}
              rows={coverageRates.map((row) => [
                row.label,
                row.applications,
                row.callbacks,
                pct(row.rate),
              ])}
            >
              <RateBars data={coverageRates} />
            </ChartCard>
          </Grid.Col>
        )}
        {recommendationRates.length > 0 && (
          <Grid.Col span={12}>
            <ChartCard
              title="Suggested resume followed"
              subtitle="Callbacks when the resume sent was the one suggested at logging time, and when it wasn't. Small counts, and your choice isn't random."
              columns={['Resume sent', 'Applications', 'Callbacks', 'Rate']}
              rows={recommendationRates.map((row) => [
                row.label,
                row.applications,
                row.callbacks,
                pct(row.rate),
              ])}
            >
              <RateBars data={recommendationRates} />
            </ChartCard>
          </Grid.Col>
        )}
        <Grid.Col span={{ base: 12, md: 4 }}>
          <CountList title="Levels" rows={trends.levels} />
        </Grid.Col>
        <Grid.Col span={{ base: 12, md: 4 }}>
          <CountList title="Role types" rows={trends.role_families} />
        </Grid.Col>
        <Grid.Col span={{ base: 12, md: 4 }}>
          <CountList title="Source types" rows={trends.source_kinds} />
        </Grid.Col>
        <Grid.Col span={{ base: 12, md: 6 }}>
          <ChartCard
            title="Referrals"
            subtitle="Callback rate with and without a referral"
            columns={['Group', 'Applications', 'Callbacks', 'Rate']}
            rows={referralRates.map((row) => [
              row.label,
              row.applications,
              row.callbacks,
              pct(row.rate),
            ])}
          >
            <RateBars data={referralRates} />
          </ChartCard>
        </Grid.Col>
        <Grid.Col span={{ base: 12, md: 6 }}>
          <ChartCard
            title="Which sources worked"
            subtitle="Callback rate by where you found the job"
            columns={['Source', 'Applications', 'Callbacks', 'Rate']}
            rows={sourceRates.map((row) => [row.label, row.applications, row.callbacks, pct(row.rate)])}
          >
            <RateBars data={sourceRates} />
          </ChartCard>
        </Grid.Col>
        <Grid.Col span={12}>
          <FocusCard title="Companies" h="100%">
            <Text fw={600} size="sm" mb="sm">
              Companies
            </Text>
            <Table.ScrollContainer minWidth={0} mah={320}>
              <Table>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Company</Table.Th>
                    <Table.Th ta="right">Postings</Table.Th>
                    <Table.Th ta="right">Applications</Table.Th>
                    <Table.Th>Last applied</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody className="tabular">
                  {trends.companies.map((row) => (
                    <Table.Tr key={row.company}>
                      <Table.Td fw={500}>{row.company}</Table.Td>
                      <Table.Td ta="right">{row.postings}</Table.Td>
                      <Table.Td ta="right">{row.applications}</Table.Td>
                      <Table.Td>{row.last_applied ? formatDay(row.last_applied) : '–'}</Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          </FocusCard>
        </Grid.Col>
      </Grid>
    </Stack>
  );
}

export function PostingInsights() {
  const trends = usePostingTrends();
  const overview = useOverview();

  if (trends.isPending || overview.isPending) return <PageLoader />;
  if (trends.isError || overview.isError) return <PageError />;

  return <PostingTrendsView trends={trends.data} sources={overview.data.sources} />;
}
