import {
  Anchor,
  Badge,
  Button,
  Card,
  Checkbox,
  Grid,
  Group,
  SimpleGrid,
  Stack,
  Table,
  Text,
  ThemeIcon,
} from '@mantine/core';
import {
  IconClockHour4,
  IconMessageCircle,
  IconSend,
  IconUsers,
  type Icon,
} from '@tabler/icons-react';
import { Link } from 'react-router-dom';

import { ChartCard, Funnel, RateBars, WeeklyChart } from '../components/charts';
import { FocusCard } from '../components/Focus';
import { Empty, PageError, PageLoader } from '../components/PageState';
import { formatDay, formatWeek, pct } from '../lib/format';
import {
  useFinishNextStep,
  useNextSteps,
  useOverview,
  useUpdateApplication,
} from '../lib/queries';
import { PageTitle } from '../lib/sections';

interface StatTileProps {
  label: string;
  value: string | number;
  hint: string;
  icon: Icon;
}

function StatTile({ label, value, hint, icon: TileIcon }: StatTileProps) {
  return (
    <Card>
      <Group justify="space-between" wrap="nowrap" align="flex-start">
        <Text size="sm" fw={600} c="dimmed">
          {label}
        </Text>
        <ThemeIcon variant="light" size="lg" radius="md">
          <TileIcon size={20} stroke={1.7} />
        </ThemeIcon>
      </Group>
      <Text fz={36} fw={700} lh={1.2} className="tabular">
        {value}
      </Text>
      <Text size="xs" c="dimmed">
        {hint}
      </Text>
    </Card>
  );
}

// Waits past these many days read as overdue, then as likely lost.
const WAIT_WARN_DAYS = 14;
const WAIT_LATE_DAYS = 21;

function waitColor(days: number): string {
  if (days >= WAIT_LATE_DAYS) return 'red';
  return days >= WAIT_WARN_DAYS ? 'orange' : 'yellow';
}

/** Open next steps from logged communications: overdue first, then the soonest. */
function NextSteps() {
  const { data: steps } = useNextSteps();
  const finish = useFinishNextStep();
  if (!steps?.length) return null;
  return (
    <Stack gap={6} mb="md">
      <Text size="xs" fw={700} c="dimmed" tt="uppercase">
        Next steps
      </Text>
      {steps.map((step) => (
        <Group key={step.communication_id} gap="sm" wrap="nowrap" justify="space-between">
          <Group gap="sm" wrap="nowrap" style={{ minWidth: 0 }}>
            <Checkbox
              size="xs"
              aria-label={`Done: ${step.next_step}`}
              disabled={finish.isPending}
              onChange={() =>
                finish.mutate({ id: step.communication_id, applicationId: step.application_id })
              }
            />
            <div style={{ minWidth: 0 }}>
              <Text size="sm" lineClamp={1}>
                {step.next_step}
              </Text>
              <Text size="xs" c="dimmed" lineClamp={1}>
                {step.company} · {step.role_title}
              </Text>
            </div>
          </Group>
          {step.next_step_due && (
            <Badge
              size="sm"
              variant="light"
              color={step.overdue ? 'red' : 'gray'}
              tt="none"
              style={{ flexShrink: 0 }}
            >
              {step.overdue ? 'Overdue, ' : 'Due '}
              {formatDay(step.next_step_due)}
            </Badge>
          )}
        </Group>
      ))}
    </Stack>
  );
}

export function DashboardPage() {
  const { data, isPending, isError } = useOverview();
  const update = useUpdateApplication();

  if (isPending) return <PageLoader />;
  if (isError) return <PageError />;

  const { totals, funnel, weekly, resumes, stale } = data;
  if (totals.applications === 0) {
    return (
      <Stack>
        <PageTitle section="dashboard" />
        <Card>
          <Empty
            title="No applications yet"
            hint="Log your first application with the button at the top. Charts fill in as you go."
          />
        </Card>
      </Stack>
    );
  }

  const usedResumes = resumes
    .filter((resume) => resume.applications > 0)
    .map((resume) => ({
      label: resume.label,
      rate: resume.callback_rate,
      applications: resume.applications,
      callbacks: resume.callbacks,
    }));

  return (
    <Stack>
      <PageTitle section="dashboard" />

      <SimpleGrid cols={{ base: 2, md: 4 }} spacing="sm">
        <StatTile
          label="Applications"
          icon={IconSend}
          value={totals.applications}
          hint={`${totals.this_week} this week · ${totals.active} active`}
        />
        <StatTile
          label="Callback rate"
          icon={IconMessageCircle}
          value={pct(totals.callback_rate)}
          hint={`${totals.callbacks} of ${totals.applications} heard back`}
        />
        <StatTile
          label="Interviews"
          icon={IconUsers}
          value={totals.interviews}
          hint={`${totals.offers} ${totals.offers === 1 ? 'offer' : 'offers'}`}
        />
        <StatTile
          label="Median time to callback"
          icon={IconClockHour4}
          value={totals.median_days_to_callback === null ? '–' : `${totals.median_days_to_callback}d`}
          hint="From applying to first response"
        />
      </SimpleGrid>

      <Grid gutter="sm">
        <Grid.Col span={{ base: 12, md: 8 }}>
          <ChartCard
            title="Weekly activity"
            subtitle="Callbacks are counted in the week you applied"
            columns={['Week of', 'Applications', 'Callbacks']}
            rows={weekly.map((point) => [
              formatWeek(point.week_start),
              point.applications,
              point.callbacks,
            ])}
          >
            <WeeklyChart data={weekly} />
          </ChartCard>
        </Grid.Col>
        <Grid.Col span={{ base: 12, md: 4 }}>
          <FocusCard title="Funnel" h="100%">
            <Text fw={600} size="sm">
              Funnel
            </Text>
            <Text size="xs" c="dimmed" mb="md">
              Furthest stage each application reached
            </Text>
            <Funnel stages={funnel} />
          </FocusCard>
        </Grid.Col>

        <Grid.Col span={{ base: 12, md: 7 }}>
          <FocusCard title="Worth a follow-up" h="100%">
            <Text fw={600} size="sm">
              Worth a follow-up
            </Text>
            <Text size="xs" c="dimmed" mb="sm">
              Still at Applied with nothing logged in a while, oldest first
            </Text>
            <NextSteps />
            {stale.length === 0 ? (
              <Empty title="Nothing waiting too long" />
            ) : (
              <Table.ScrollContainer minWidth={0}>
                <Table>
                  <Table.Tbody>
                    {stale.map((item) => (
                      <Table.Tr key={item.id}>
                        <Table.Td>
                          <Text size="sm" fw={650} lineClamp={1}>
                            {item.company}
                          </Text>
                          <Text size="xs" c="dimmed" lineClamp={1}>
                            {item.role_title}
                          </Text>
                        </Table.Td>
                        <Table.Td className="tabular" style={{ whiteSpace: 'nowrap' }}>
                          <Group gap="xs" wrap="nowrap">
                            <Badge
                              variant="light"
                              color={waitColor(item.days_waiting)}
                              fw={700}
                              aria-label={`Waiting ${item.days_waiting} days`}
                            >
                              {item.days_waiting}d
                            </Badge>
                            <Text size="xs" c="dimmed">
                              {item.last_contact_on
                                ? `last contact ${formatDay(item.last_contact_on)}`
                                : formatDay(item.applied_on)}
                            </Text>
                          </Group>
                        </Table.Td>
                        <Table.Td ta="right">
                          <Button
                            size="compact-xs"
                            variant="default"
                            onClick={() => update.mutate({ id: item.id, changes: { status: 'ghosted' } })}
                          >
                            Mark ghosted
                          </Button>
                        </Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </Table.ScrollContainer>
            )}
          </FocusCard>
        </Grid.Col>
        <Grid.Col span={{ base: 12, md: 5 }}>
          {usedResumes.length ? (
            <ChartCard
              title="Callback rate by resume"
              columns={['Resume', 'Applications', 'Callbacks', 'Rate']}
              rows={usedResumes.map((row) => [row.label, row.applications, row.callbacks, pct(row.rate)])}
            >
              <RateBars data={usedResumes} />
            </ChartCard>
          ) : (
            <Card h="100%">
              <Text fw={600} size="sm" mb="sm">
                Callback rate by resume
              </Text>
              <Empty title="No resumes linked yet" hint="Attach a resume to an application to compare versions." />
            </Card>
          )}
        </Grid.Col>
      </Grid>

      <Group justify="flex-end">
        <Anchor component={Link} to="/insights" size="sm">
          See resume insights →
        </Anchor>
      </Group>
    </Stack>
  );
}
