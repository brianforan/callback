import { Anchor, Badge, Paper, SimpleGrid, Stack, Table, Text } from '@mantine/core';
import { Link, useNavigate } from 'react-router-dom';

import { Empty, PageError, PageLoader } from '../components/PageState';
import { StatTile } from '../components/StatTile';
import { formatDateTime, formatUsd, pct } from '../lib/format';
import { isSearching, useScoutLeads, useScoutRuns } from '../lib/queries';
import type { ScoutRun } from '../lib/types';

// The fits a brief can give a lead, in order from closest to furthest.
const FITS = [
  { key: 'strong', label: 'strong' },
  { key: 'good', label: 'good' },
  { key: 'stretch', label: 'stretch' },
] as const;

const ENGINE_LABELS: Record<ScoutRun['engine'], string> = {
  agent: 'Agent',
  pipeline: 'Pipeline',
  companies: 'Companies',
};

const DASH = '–';

interface RunKpis {
  run: ScoutRun;
  finished: boolean;
  leads: number;
  tracked: number;
  applied: number;
  fits: string;
  minutes: number | null;
  perLeadUsd: number | null;
}

function minutesBetween(start: string | null, end: string | null): number | null {
  if (!start || !end) return null;
  const ms = new Date(end).getTime() - new Date(start).getTime();
  return Math.round((Math.max(0, ms) / 60_000) * 10) / 10;
}

function plural(count: number, one: string, many = `${one}s`): string {
  return `${count} ${count === 1 ? one : many}`;
}

function kpisFor(run: ScoutRun, appliedPostings: Set<string>): RunKpis {
  const leads = run.leads.length;
  const fits = FITS.map(({ key, label }) => ({
    label,
    count: run.leads.filter((lead) => lead.fit.trim().toLowerCase() === key).length,
  }))
    .filter(({ count }) => count > 0)
    .map(({ label, count }) => `${count} ${label}`)
    .join(' · ');
  return {
    run,
    finished: run.status === 'done',
    leads,
    tracked: run.leads.filter((lead) => lead.posting_id).length,
    applied: run.leads.filter((lead) => lead.posting_id && appliedPostings.has(lead.posting_id))
      .length,
    fits,
    minutes: minutesBetween(run.started_at, run.finished_at),
    perLeadUsd: leads ? run.cost_usd / leads : null,
  };
}

function StatusCell({ run }: { run: ScoutRun }) {
  if (isSearching(run)) {
    return (
      <Badge color="gray" variant="light" tt="none">
        {run.status === 'queued' ? 'Queued' : 'Running'}
      </Badge>
    );
  }
  const failed = run.status === 'failed';
  return (
    <Badge color={failed ? 'red' : 'green'} variant="light" tt="none">
      {failed ? 'Failed' : 'Finished'}
    </Badge>
  );
}

/** Totals across the finished runs shown, so a run can be read against the usual. */
function Totals({ rows }: { rows: RunKpis[] }) {
  const finished = rows.filter((row) => row.finished);
  const failed = rows.filter((row) => row.run.status === 'failed').length;
  const leads = finished.reduce((sum, row) => sum + row.leads, 0);
  const applied = finished.reduce((sum, row) => sum + row.applied, 0);
  const usd = rows.reduce((sum, row) => sum + row.run.cost_usd, 0);
  const finishedUsd = finished.reduce((sum, row) => sum + row.run.cost_usd, 0);

  return (
    <SimpleGrid cols={{ base: 2, md: 5 }} spacing="md">
      <StatTile
        label="Runs"
        value={String(finished.length)}
        note={failed ? `finished, ${failed} failed` : 'finished'}
      />
      <StatTile
        label="Leads"
        value={String(leads)}
        note={
          finished.length
            ? `${Math.round((leads / finished.length) * 10) / 10} a run`
            : undefined
        }
      />
      <StatTile
        label="Applied"
        value={String(applied)}
        note={leads ? `${pct(applied / leads)} of leads` : 'No leads yet'}
      />
      <StatTile
        label="Spent"
        value={formatUsd(usd)}
        note="measured, failed runs too"
      />
      <StatTile
        label="Per lead"
        value={leads ? formatUsd(finishedUsd / leads) : DASH}
        note={leads ? 'finished runs' : 'No leads yet'}
      />
    </SimpleGrid>
  );
}

function RunRow({ row }: { row: RunKpis }) {
  const navigate = useNavigate();
  const { run } = row;
  const active = isSearching(run);
  const report = `/scout/runs/${run.id}`;
  // A run still going has no figures yet, so its cells hold a dash rather than a zero.
  const figure = (value: string) => (active ? DASH : value);

  return (
    <Table.Tr
      style={{ cursor: active ? undefined : 'pointer' }}
      onClick={active ? undefined : () => navigate(report)}
    >
      <Table.Td>
        <Stack gap={2}>
          {active ? (
            <Text size="sm" fw={500}>
              {formatDateTime(run.created_at)}
            </Text>
          ) : (
            <Anchor
              component={Link}
              to={report}
              size="sm"
              fw={500}
              onClick={(event) => event.stopPropagation()}
            >
              {formatDateTime(run.created_at)}
            </Anchor>
          )}
          {run.label && (
            <Badge color="gray" variant="outline" tt="none" size="sm" w="fit-content">
              {run.label}
            </Badge>
          )}
          <Text size="xs" c="dimmed" lineClamp={1} maw={260} title={run.description}>
            {run.description}
          </Text>
        </Stack>
      </Table.Td>
      <Table.Td>
        <Stack gap={2} align="flex-start">
          <StatusCell run={run} />
          {run.stop_message && !active && (
            <Text size="xs" c="dimmed" lineClamp={1} maw={200} title={run.stop_message}>
              {run.stop_message}
            </Text>
          )}
        </Stack>
      </Table.Td>
      <Table.Td>
        <Text size="sm">{ENGINE_LABELS[run.engine]}</Text>
      </Table.Td>
      <Table.Td>
        <Text size="sm" className="tabular">
          {figure(run.target_leads ? `${row.leads} of ${run.target_leads}` : String(row.leads))}
        </Text>
        {!active && row.fits && (
          <Text size="xs" c="dimmed" style={{ whiteSpace: 'nowrap' }}>
            {row.fits}
          </Text>
        )}
      </Table.Td>
      <Table.Td className="tabular">
        <Text size="sm">{figure(String(row.applied))}</Text>
        {!active && (
          <Text size="xs" c="dimmed" style={{ whiteSpace: 'nowrap' }}>
            of {row.tracked} tracked
          </Text>
        )}
      </Table.Td>
      <Table.Td className="tabular">
        <Text size="sm">{figure(formatUsd(run.cost_usd))}</Text>
        {run.budget_usd !== null && (
          <Text size="xs" c="dimmed" style={{ whiteSpace: 'nowrap' }}>
            of {formatUsd(run.budget_usd)} allowed
          </Text>
        )}
      </Table.Td>
      <Table.Td className="tabular">
        <Text size="sm">
          {figure(row.perLeadUsd === null ? DASH : formatUsd(row.perLeadUsd))}
        </Text>
      </Table.Td>
      <Table.Td className="tabular">
        <Text size="sm" style={{ whiteSpace: 'nowrap' }}>
          {figure(row.minutes === null ? DASH : `${row.minutes} min`)}
        </Text>
      </Table.Td>
      <Table.Td className="tabular">
        <Text size="sm" style={{ whiteSpace: 'nowrap' }}>
          {figure(plural(run.searches, 'search', 'searches'))}
        </Text>
        {!active && (
          <Text size="xs" c="dimmed" style={{ whiteSpace: 'nowrap' }}>
            {plural(run.pages, 'page')} read
          </Text>
        )}
      </Table.Td>
    </Table.Tr>
  );
}

/** The Runs tab: every recent run with what it found, what it cost and what came of it. */
export function ScoutRunsTab() {
  const runs = useScoutRuns();
  const leads = useScoutLeads();

  if (runs.isPending || leads.isPending) return <PageLoader />;
  if (runs.isError || leads.isError || !runs.data || !leads.data) return <PageError />;

  if (!runs.data.length) {
    return (
      <Empty
        title="No runs yet"
        hint="Save your settings on the Search tab, then run Scout. Each run shows here."
      />
    );
  }

  // A lead counts as applied to when an application is logged against its posting.
  const appliedPostings = new Set(
    leads.data.flatMap((lead) => (lead.application && lead.posting_id ? [lead.posting_id] : [])),
  );
  const rows = runs.data.map((run) => kpisFor(run, appliedPostings));

  return (
    <Stack>
      <Text size="sm" c="dimmed" maw={720}>
        Your last {plural(rows.length, 'run')}, newest first. Totals count finished runs; spend
        counts every run. Open a run for its full report.
      </Text>
      <Totals rows={rows} />
      <Paper withBorder radius="md">
        <Table.ScrollContainer minWidth={980}>
          <Table highlightOnHover verticalSpacing="sm" horizontalSpacing="md">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Started</Table.Th>
                <Table.Th>Status</Table.Th>
                <Table.Th>Engine</Table.Th>
                <Table.Th>Leads</Table.Th>
                <Table.Th>Applied</Table.Th>
                <Table.Th>Cost</Table.Th>
                <Table.Th>Per lead</Table.Th>
                <Table.Th>Time</Table.Th>
                <Table.Th>Work</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {rows.map((row) => (
                <RunRow key={row.run.id} row={row} />
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Paper>
      <Text size="xs" c="dimmed">
        Cost is what the run's model and search time came to, measured call by call. A run stops
        before it spends past the amount allowed.
      </Text>
    </Stack>
  );
}
