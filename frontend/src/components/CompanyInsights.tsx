import {
  Accordion,
  Anchor,
  Badge,
  Button,
  Card,
  Group,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title,
} from '@mantine/core';
import { IconArrowLeft, IconDownload } from '@tabler/icons-react';
import { Link, useParams } from 'react-router-dom';

import { formatDateTime } from '../lib/format';
import { useCompanyDetail, useMe, useMyCompanies } from '../lib/queries';
import type { CompanyCard } from '../lib/types';
import { WeeklyValueChart } from './charts';
import { label, Snapshot } from './CompanyResearchPanel';
import { Empty, PageError, PageLoader } from './PageState';

function facts(card: CompanyCard): string {
  return (
    [
      label(card.ownership ?? undefined),
      label(card.stage ?? undefined),
      card.headcount ? `about ${card.headcount.toLocaleString()} people` : null,
      card.trend,
    ]
      .filter(Boolean)
      .join(' · ') || 'Ownership and size unknown'
  );
}

function hiring(card: CompanyCard): string {
  if (!card.can_see_hiring) return "Callback can't see this company's hiring directly.";
  return `${card.open_roles ?? 0} open roles on its own board, ${card.open_engineering ?? 0} in engineering.`;
}

/** Exports of every snapshot and event, for the admin and other tools. Company facts only. */
function Exports() {
  const { data: me } = useMe();
  if (!me?.is_admin) return null;
  return (
    <Group gap="xs">
      <Text size="xs" c="dimmed">
        Admin exports, company facts only:
      </Text>
      <Button
        component="a"
        href="/api/company-research/export.jsonl"
        download
        size="xs"
        variant="default"
        leftSection={<IconDownload size={14} />}
      >
        All snapshots (JSONL)
      </Button>
      <Button
        component="a"
        href="/api/company-research/events.csv"
        download
        size="xs"
        variant="default"
        leftSection={<IconDownload size={14} />}
      >
        All events (CSV)
      </Button>
    </Group>
  );
}

/** Researched companies the user applied to. */
export function CompanyInsights() {
  const companies = useMyCompanies();
  if (companies.isPending) return <PageLoader />;
  if (companies.isError) return <PageError />;

  return (
    <Stack>
      <Text size="sm" c="dimmed">
        Companies you've applied to that Callback has researched. A company is researched when it
        rejects an application, or from <b>Research this company</b> in an application. Facts are
        shared by everyone using Callback; this list is yours.
      </Text>
      <Exports />
      {companies.data.length === 0 ? (
        <Empty
          title="No researched companies yet"
          hint="They appear here after a rejection, or after researching a company from an application."
        />
      ) : (
        <SimpleGrid cols={{ base: 1, md: 2 }}>
          {companies.data.map((card) => (
            <Card key={card.id} withBorder component={Link} to={`/insights/companies/${card.id}`}>
              <Stack gap={4}>
                <Group justify="space-between" wrap="nowrap">
                  <Text fw={600}>{card.name}</Text>
                  <Text size="xs" c="dimmed">
                    {card.snapshots} snapshot{card.snapshots === 1 ? '' : 's'}
                  </Text>
                </Group>
                <Text size="sm">{facts(card)}</Text>
                <Text size="sm" c="dimmed">
                  {hiring(card)}
                </Text>
                {card.recent_events.map((event) => (
                  <Text key={event} size="xs" c="dimmed">
                    {event}
                  </Text>
                ))}
                {card.researched_at && (
                  <Text size="xs" c="dimmed">
                    Researched {formatDateTime(card.researched_at)}
                  </Text>
                )}
              </Stack>
            </Card>
          ))}
        </SimpleGrid>
      )}
    </Stack>
  );
}

/** One company: its newest snapshot, its hiring by week, and every snapshot over time. */
export function CompanyPage() {
  const { companyId } = useParams();
  const detail = useCompanyDetail(companyId);
  if (detail.isPending) return <PageLoader />;
  if (detail.isError) return <PageError />;
  const company = detail.data;
  const [latest] = company.history;

  return (
    <Stack>
      <Anchor component={Link} to="/insights/companies" size="sm">
        <Group gap={4}>
          <IconArrowLeft size={14} /> Companies
        </Group>
      </Anchor>
      <Group justify="space-between">
        <Title order={3}>{company.name}</Title>
        {company.domain && (
          <Text size="sm" c="dimmed">
            {company.domain}
          </Text>
        )}
      </Group>
      <Text size="sm">{facts(company)}</Text>

      <Card withBorder>
        <Stack gap="sm">
          <Group justify="space-between">
            <Text fw={600}>Hiring on its own board</Text>
            {company.can_see_hiring && (
              <Button
                component="a"
                href={`/api/companies/${company.id}/hiring-history.csv`}
                download
                size="xs"
                variant="default"
                leftSection={<IconDownload size={14} />}
              >
                Weekly CSV
              </Button>
            )}
          </Group>
          <Text size="sm" c="dimmed">
            {hiring(company)}
            {company.median_days_open !== null &&
              ` Roles stay listed a median of ${company.median_days_open} days.`}
            {company.reposts_last_year > 0 &&
              ` ${company.reposts_last_year} roles reposted in the last year.`}
          </Text>
          {company.can_see_hiring && (
            <WeeklyValueChart
              data={company.weeks.map((week) => ({
                week_start: week.week_start,
                value: week.open_engineering,
              }))}
              name="Open engineering roles"
              format={(value) => String(value)}
              emptyText="No weeks of hiring history yet."
            />
          )}
        </Stack>
      </Card>

      {latest && (
        <Card withBorder>
          <Stack gap="sm">
            <Text fw={600}>
              Newest snapshot{latest.finished_at ? `, ${formatDateTime(latest.finished_at)}` : ''}
            </Text>
            <Snapshot research={latest} />
          </Stack>
        </Card>
      )}

      <Card withBorder>
        <Stack gap="sm">
          <Text fw={600}>Snapshots over time</Text>
          <Text size="xs" c="dimmed">
            A new snapshot is added beside the old ones, so changes show.
          </Text>
          <Table fz="sm">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Researched</Table.Th>
                <Table.Th>Headcount</Table.Th>
                <Table.Th>Trend</Table.Th>
                <Table.Th>Stage</Table.Th>
                <Table.Th>Events</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody className="tabular">
              {company.history.map((snapshot) => (
                <Table.Tr key={snapshot.id}>
                  <Table.Td>{snapshot.finished_at ? formatDateTime(snapshot.finished_at) : '–'}</Table.Td>
                  <Table.Td>{snapshot.size.headcount ? snapshot.size.headcount.toLocaleString() : 'Unknown'}</Table.Td>
                  <Table.Td>{label(snapshot.size.trend) || 'Unknown'}</Table.Td>
                  <Table.Td>{label(snapshot.funding.stage) || 'Unknown'}</Table.Td>
                  <Table.Td>{snapshot.events.length}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          {company.history.length > 1 && (
            <Accordion variant="contained">
              {company.history.slice(1).map((snapshot) => (
                <Accordion.Item key={snapshot.id} value={snapshot.id}>
                  <Accordion.Control>
                    <Group gap="xs">
                      <Text size="sm">
                        {snapshot.finished_at ? formatDateTime(snapshot.finished_at) : 'Earlier'}
                      </Text>
                      <Badge size="xs" variant="light" tt="none">
                        Earlier snapshot
                      </Badge>
                    </Group>
                  </Accordion.Control>
                  <Accordion.Panel>
                    <Snapshot research={snapshot} />
                  </Accordion.Panel>
                </Accordion.Item>
              ))}
            </Accordion>
          )}
        </Stack>
      </Card>
    </Stack>
  );
}
