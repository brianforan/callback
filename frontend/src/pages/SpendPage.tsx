import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Collapse,
  Group,
  NumberInput,
  Paper,
  SimpleGrid,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
  Tooltip,
} from '@mantine/core';
import {
  IconAlertTriangle,
  IconChevronDown,
  IconChevronRight,
  IconPlugConnected,
  IconTrash,
} from '@tabler/icons-react';
import { Fragment, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';

import { PageError, PageLoader } from '../components/PageState';
import { formatDateTime, formatDay, todayIso } from '../lib/format';
import { useAddTopUp, useDeleteTopUp, useSpend } from '../lib/queries';
import type { Spend, SpendJob, SpendTokens } from '../lib/types';

const JOB_KINDS: Record<SpendJob['kind'], string> = {
  scout: 'Scout',
  tailoring: 'Tailoring',
  rejection_analysis: 'Rejection report',
  company_research: 'Company research',
};

/** Rejection reports and company research are measured but not matched to the bill. */
function billable(job: SpendJob): boolean {
  return job.kind === 'scout' || job.kind === 'tailoring';
}

function usd(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return '–';
  if (value !== 0 && Math.abs(value) < 0.01) return `$${value.toFixed(4)}`;
  return `$${value.toFixed(digits)}`;
}

function count(value: number): string {
  return value.toLocaleString();
}

function Tile({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <Paper withBorder p="md" radius="md">
      <Text size="xs" c="dimmed" tt="uppercase" fw={600}>
        {label}
      </Text>
      <Text fz={26} fw={700} className="tabular" lh={1.2} mt={4}>
        {value}
      </Text>
      {note && (
        <Text size="xs" c="dimmed" mt={2}>
          {note}
        </Text>
      )}
    </Paper>
  );
}

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

function Setup({ spend }: { spend: Spend }) {
  const { setup } = spend;
  return (
    <Stack gap="xs">
      {!setup.usage_synced_at && (
        <Alert color="blue" variant="light" icon={<IconPlugConnected size={18} />} title="Connect Anthropic billing">
          <Text size="sm">
            Callback's own measurements show below already. To compare them with what Anthropic
            billed, add <code>ANTHROPIC_ADMIN_API_KEY</code> to <code>.env</code>, and{' '}
            <code>BILLING_API_KEY_ID</code> and <code>BILLING_WORKSPACE_ID</code> for the key
            Callback uses, then run <code>make up</code>. The billing container checks every few
            minutes.
          </Text>
        </Alert>
      )}
      {setup.last_error && (
        <Alert color="red" variant="light" icon={<IconAlertTriangle size={18} />} title="Billing sync failed">
          {setup.last_error}
        </Alert>
      )}
      {setup.usage_synced_at && (!setup.api_key_filter || !setup.workspace_filter) && (
        <Alert color="yellow" variant="light" icon={<IconAlertTriangle size={18} />}>
          {!setup.api_key_filter && 'Billed usage per run counts every API key in your organization. '}
          {!setup.workspace_filter && 'Billed dollars per day count every workspace. '}
          Set BILLING_API_KEY_ID and BILLING_WORKSPACE_ID to count only Callback.
        </Alert>
      )}
      {setup.usage_synced_at && (
        <Text size="xs" c="dimmed">
          Billed usage checked {formatDateTime(setup.usage_synced_at)}
          {setup.cost_synced_at && ` · billed dollars checked ${formatDateTime(setup.cost_synced_at)}`}
          . Anthropic's reports trail by a few minutes, and dollars come by the day.
        </Text>
      )}
    </Stack>
  );
}

function TopUps({ spend }: { spend: Spend }) {
  const add = useAddTopUp();
  const remove = useDeleteTopUp();
  const [amount, setAmount] = useState<number | string>('');
  const [day, setDay] = useState(todayIso());
  const [note, setNote] = useState('');

  return (
    <Section
      title="Balance"
      hint="Enter each top-up you make in the Console. The balance is those, less what Anthropic billed your whole organization since the first one."
    >
      <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="md">
        <Tile label="Balance" value={usd(spend.balance.balance_usd)} />
        <Tile label="Topped up" value={usd(spend.balance.topups_usd)} />
        <Tile
          label="Billed since"
          value={usd(spend.balance.billed_since_usd)}
          note={spend.balance.since ? `From ${formatDay(spend.balance.since)}` : 'No top-ups yet'}
        />
      </SimpleGrid>
      <Group gap="xs" align="flex-end" wrap="wrap">
        <NumberInput
          label="Top-up"
          prefix="$"
          min={0.01}
          decimalScale={2}
          w={120}
          value={amount}
          onChange={setAmount}
        />
        <TextInput label="On" type="date" w={160} value={day} onChange={(e) => setDay(e.currentTarget.value)} />
        <TextInput
          label="Note"
          placeholder="Optional"
          maxLength={200}
          w={200}
          value={note}
          onChange={(e) => setNote(e.currentTarget.value)}
        />
        <Button
          loading={add.isPending}
          disabled={typeof amount !== 'number' || amount <= 0 || !day}
          onClick={() =>
            typeof amount === 'number' &&
            add.mutate(
              { amount_usd: amount, added_on: day, note },
              { onSuccess: () => { setAmount(''); setNote(''); } },
            )
          }
        >
          Add
        </Button>
      </Group>
      {spend.balance.topups.length > 0 && (
        <Table>
          <Table.Tbody className="tabular">
            {spend.balance.topups.map((topup) => (
              <Table.Tr key={topup.id}>
                <Table.Td>{formatDay(topup.added_on)}</Table.Td>
                <Table.Td>{usd(topup.amount_usd)}</Table.Td>
                <Table.Td c="dimmed">{topup.note}</Table.Td>
                <Table.Td ta="right">
                  <ActionIcon
                    variant="subtle"
                    color="gray"
                    aria-label="Delete top-up"
                    loading={remove.isPending}
                    onClick={() => remove.mutate(topup.id)}
                  >
                    <IconTrash size={14} />
                  </ActionIcon>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
    </Section>
  );
}

function Quote({ spend }: { spend: Spend }) {
  const { quote } = spend;
  return (
    <Section
      title="What a budget buys"
      hint={`From ${quote.runs_measured} measured ${quote.runs_measured === 1 ? 'run' : 'runs'}, at the typical (median) cost. Runs vary with their limits.`}
    >
      <SimpleGrid cols={{ base: 2, md: 4 }} spacing="md">
        <Tile label="Typical run" value={usd(quote.median_run_usd)} note={`Mean ${usd(quote.mean_run_usd)}`} />
        <Tile label="1 in 10 runs over" value={usd(quote.p90_run_usd)} note={`Most so far ${usd(quote.max_run_usd)}`} />
        <Tile label="Per lead" value={usd(quote.median_per_lead_usd)} />
        <Tile label="Per tailoring" value={usd(quote.median_tailoring_usd)} />
      </SimpleGrid>
      <Table>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Budget</Table.Th>
            <Table.Th ta="right">Scout runs</Table.Th>
            <Table.Th ta="right">Leads</Table.Th>
            <Table.Th ta="right">Tailorings</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody className="tabular">
          {quote.budgets.map((budget) => (
            <Table.Tr key={budget.budget_usd}>
              <Table.Td fw={600}>{usd(budget.budget_usd, 0)}</Table.Td>
              <Table.Td ta="right">about {budget.runs}</Table.Td>
              <Table.Td ta="right">about {budget.leads}</Table.Td>
              <Table.Td ta="right">about {budget.tailorings}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Section>
  );
}

function Days({ spend }: { spend: Spend }) {
  const rows = spend.days.filter((day) => day.measured_usd > 0 || (day.billed_usd ?? 0) > 0);
  return (
    <Section
      title="By day"
      hint="What Callback measured, and what Anthropic billed for Callback's workspace. A gap of more than a few percent means a price in tailor's settings is off."
    >
      {rows.length === 0 ? (
        <Text size="sm" c="dimmed">
          No spending in the last month.
        </Text>
      ) : (
        <Table.ScrollContainer minWidth={420}>
          <Table>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Day</Table.Th>
                <Table.Th ta="right">Measured</Table.Th>
                <Table.Th ta="right">Billed</Table.Th>
                <Table.Th ta="right">Difference</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody className="tabular">
              {rows.map((day) => {
                const gap =
                  day.billed_usd !== null && day.billed_usd > 0
                    ? (day.measured_usd - day.billed_usd) / day.billed_usd
                    : null;
                return (
                  <Table.Tr key={day.day}>
                    <Table.Td>{formatDay(day.day)}</Table.Td>
                    <Table.Td ta="right">{usd(day.measured_usd, 4)}</Table.Td>
                    <Table.Td ta="right">{usd(day.billed_usd, 4)}</Table.Td>
                    <Table.Td ta="right" c={gap !== null && Math.abs(gap) > 0.05 ? 'orange' : 'dimmed'}>
                      {gap === null ? '–' : `${gap > 0 ? '+' : ''}${(gap * 100).toFixed(1)}%`}
                    </Table.Td>
                  </Table.Tr>
                );
              })}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}
    </Section>
  );
}

function Users({ spend }: { spend: Spend }) {
  return (
    <Section title="By user" hint="Everything each person's runs and tailorings have cost, as Callback measured it. Leads counts each job once, however many runs found it.">
      <Table.ScrollContainer minWidth={560}>
        <Table>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>User</Table.Th>
              <Table.Th ta="right">Runs</Table.Th>
              <Table.Th ta="right">Leads</Table.Th>
              <Table.Th ta="right">Tailorings</Table.Th>
              <Table.Th ta="right">Reports</Table.Th>
              <Table.Th ta="right">Research</Table.Th>
              <Table.Th ta="right">Typical run</Table.Th>
              <Table.Th ta="right">Per lead</Table.Th>
              <Table.Th ta="right">Total</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody className="tabular">
            {spend.users.map((user) => (
              <Table.Tr key={user.email}>
                <Table.Td>{user.email}</Table.Td>
                <Table.Td ta="right">{user.runs}</Table.Td>
                <Table.Td ta="right">{user.leads}</Table.Td>
                <Table.Td ta="right">{user.tailorings}</Table.Td>
                <Table.Td ta="right">{user.analyses}</Table.Td>
                <Table.Td ta="right">{user.research}</Table.Td>
                <Table.Td ta="right">{usd(user.median_run_usd)}</Table.Td>
                <Table.Td ta="right">{usd(user.per_lead_usd)}</Table.Td>
                <Table.Td ta="right" fw={600}>
                  {usd(user.measured_usd)}
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </Section>
  );
}

const TOKEN_ROWS: { key: keyof SpendTokens; label: string }[] = [
  { key: 'input_tokens', label: 'Input' },
  { key: 'cache_creation_input_tokens', label: 'Cache writes' },
  { key: 'cache_read_input_tokens', label: 'Cache reads' },
  { key: 'output_tokens', label: 'Output' },
  { key: 'web_search_requests', label: 'Web searches' },
];

function JobDetail({ job }: { job: SpendJob }) {
  return (
    <Stack gap="md" py="xs">
      <Stack gap={4}>
        <Text size="xs" fw={700} c="dimmed" tt="uppercase">
          Measured by Callback, and billed by Anthropic
        </Text>
        <Table>
          <Table.Thead>
            <Table.Tr>
              <Table.Th />
              <Table.Th ta="right">Measured</Table.Th>
              <Table.Th ta="right">Billed</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody className="tabular">
            {TOKEN_ROWS.map((row) => (
              <Table.Tr key={row.key}>
                <Table.Td>{row.label}</Table.Td>
                <Table.Td ta="right">{count(job.measured[row.key])}</Table.Td>
                <Table.Td ta="right">{job.billed ? count(job.billed[row.key]) : '–'}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
        <Text size="xs" c="dimmed">
          {job.billed
            ? `Billed from Anthropic's usage report for ${formatDateTime(job.billed.window_start)} to ${formatDateTime(job.billed.window_end)}.${
                job.billed.shared_with
                  ? ` ${job.billed.shared_with} other Callback ${job.billed.shared_with === 1 ? 'job' : 'jobs'} ran in those minutes, and are counted in too.`
                  : ''
              }`
            : billable(job)
              ? 'Not checked against Anthropic yet. It is about ten minutes after a job ends.'
              : "Measured by Callback; rejection reports and company research aren't matched to Anthropic's report."}
        </Text>
      </Stack>
      {job.requests.length > 0 && (
        <Stack gap={4}>
          <Text size="xs" fw={700} c="dimmed" tt="uppercase">
            Each model request
          </Text>
          <Table.ScrollContainer minWidth={640}>
            <Table>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Step</Table.Th>
                  <Table.Th>Stage</Table.Th>
                  <Table.Th ta="right">Input</Table.Th>
                  <Table.Th ta="right">Cache writes</Table.Th>
                  <Table.Th ta="right">Cache reads</Table.Th>
                  <Table.Th ta="right">Output</Table.Th>
                  <Table.Th ta="right">Searches</Table.Th>
                  <Table.Th ta="right">Pages</Table.Th>
                  <Table.Th ta="right">Seconds</Table.Th>
                  <Table.Th ta="right">Cost</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody className="tabular">
                {job.requests.map((request) => (
                  <Table.Tr key={request.request}>
                    <Table.Td>{request.request}</Table.Td>
                    <Table.Td c="dimmed">
                      {request.stage
                        ? `${request.stage}, ${request.model.replace('claude-', '')}`
                        : 'agent'}
                    </Table.Td>
                    <Table.Td ta="right">{count(request.input_tokens)}</Table.Td>
                    <Table.Td ta="right">{count(request.cache_creation_input_tokens)}</Table.Td>
                    <Table.Td ta="right">{count(request.cache_read_input_tokens)}</Table.Td>
                    <Table.Td ta="right">{count(request.output_tokens)}</Table.Td>
                    <Table.Td ta="right">{request.web_search_requests}</Table.Td>
                    <Table.Td ta="right">{request.web_fetch_requests}</Table.Td>
                    <Table.Td ta="right">{request.seconds}</Table.Td>
                    <Table.Td ta="right" fw={600}>
                      {usd(request.cost_usd, 4)}
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        </Stack>
      )}
    </Stack>
  );
}

function Jobs({ spend }: { spend: Spend }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <Section
      title="Model calls"
      hint="The latest 100. Open one for its tokens against Anthropic's report, and Scout runs step by step."
    >
      <Table.ScrollContainer minWidth={720}>
        <Table>
          <Table.Thead>
            <Table.Tr>
              <Table.Th w={28} />
              <Table.Th>When</Table.Th>
              <Table.Th>What</Table.Th>
              <Table.Th>User</Table.Th>
              <Table.Th ta="right">Leads</Table.Th>
              <Table.Th ta="right">Cost</Table.Th>
              <Table.Th>Billed</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody className="tabular">
            {spend.jobs.map((job) => {
              const expanded = open === job.id;
              return (
                <Fragment key={job.id}>
                  <Table.Tr className="clickable-row" onClick={() => setOpen(expanded ? null : job.id)}>
                    <Table.Td>{expanded ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}</Table.Td>
                    <Table.Td style={{ whiteSpace: 'nowrap' }}>{formatDateTime(job.created_at)}</Table.Td>
                    <Table.Td>
                      {job.kind === 'scout' ? (
                        <Anchor to={`/scout/runs/${job.id}`} label="Scout" />
                      ) : (
                        JOB_KINDS[job.kind]
                      )}
                      {job.status === 'failed' && (
                        <Badge size="xs" color="red" variant="light" ml={6} tt="none">
                          Failed
                        </Badge>
                      )}
                    </Table.Td>
                    <Table.Td>{job.email}</Table.Td>
                    <Table.Td ta="right">{job.kind === 'scout' ? job.leads : '–'}</Table.Td>
                    <Table.Td ta="right" fw={600}>
                      {usd(job.measured_usd, 4)}
                    </Table.Td>
                    <Table.Td>
                      {job.billed ? (
                        <Tooltip label="Checked against Anthropic's usage report">
                          <Badge size="xs" color="green" variant="light" tt="none">
                            Checked{job.billed.shared_with ? ', shared minutes' : ''}
                          </Badge>
                        </Tooltip>
                      ) : !billable(job) ? (
                        <Text size="xs" c="dimmed">
                          –
                        </Text>
                      ) : (
                        <Text size="xs" c="dimmed">
                          Pending
                        </Text>
                      )}
                    </Table.Td>
                  </Table.Tr>
                  <Table.Tr>
                    <Table.Td colSpan={7} p={0} style={{ borderBottom: expanded ? undefined : 'none' }}>
                      <Collapse in={expanded}>{expanded && <JobDetail job={job} />}</Collapse>
                    </Table.Td>
                  </Table.Tr>
                </Fragment>
              );
            })}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </Section>
  );
}

/** A link that doesn't also toggle the row it sits in. */
function Anchor({ to, label }: { to: string; label: string }) {
  return (
    <Link to={to} onClick={(event) => event.stopPropagation()}>
      {label}
    </Link>
  );
}

/** The owner's view of what Callback's model use costs, measured and billed. */
/** The Admin panel's Spend tab. */
export function SpendTab() {
  const { data: spend, isPending, isError } = useSpend();
  if (isPending) return <PageLoader />;
  if (isError || !spend) return <PageError />;
  return (
    <Stack>
      <Setup spend={spend} />
      <TopUps spend={spend} />
      <Quote spend={spend} />
      <Days spend={spend} />
      <Users spend={spend} />
      <Jobs spend={spend} />
    </Stack>
  );
}
