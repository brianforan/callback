import {
  Anchor,
  Autocomplete,
  Badge,
  Button,
  Collapse,
  Group,
  Loader,
  Modal,
  Paper,
  Select,
  Stack,
  Text,
  Title,
  Tooltip,
} from '@mantine/core';
import {
  IconChevronDown,
  IconChevronRight,
  IconPlayerPlay,
  IconReportAnalytics,
} from '@tabler/icons-react';
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';

import { formatDateTime, formatUsd } from '../lib/format';
import { isSearching, useScoutRuns, useStartScout } from '../lib/queries';
import type {
  Scout,
  ScoutRun,
  ScoutStep,
  ScoutStepKind,
} from '../lib/types';
import { Empty } from './PageState';
import { LeadList } from './ScoutLeadCard';

const ELAPSED_TICK_MS = 1_000;
// Matches the server's limit on a run's label.
const MAX_LABEL_LENGTH = 60;

/** A whole number as it is, anything else to one decimal place. */
export function formatAmount(amount: number): string {
  return Number.isInteger(amount) ? String(amount) : amount.toFixed(1);
}

/** Runs the user may still start today, or null when they have no daily cap. */
export function runsLeft(config: Scout): number | null {
  const { daily_runs: cap, runs_today: used } = config.allowance;
  return cap === null ? null : Math.max(cap - used, 0);
}

/** "1 of 2 runs left today", or that there's no daily limit. */
export function runsLeftText(config: Scout): string {
  const left = runsLeft(config);
  if (left === null) return 'No daily limit on runs.';
  return `${left} of ${config.allowance.daily_runs} ${
    config.allowance.daily_runs === 1 ? 'run' : 'runs'
  } left today.`;
}

/** Why a run can't start right now, or null when it can. */
function blockedReason(config: Scout, dirty: boolean, active: boolean): string | null {
  if (config.plan !== 'premium') return 'Scout needs Premium.';
  if (dirty) return 'Save your changes first. A run uses the saved settings.';
  if (!config.settings.updated_at || !config.settings.description.trim()) {
    return 'Describe the work you want and save before running.';
  }
  if (active) return 'A run is already going. It will show below.';
  if (runsLeft(config) === 0) {
    const cap = config.allowance.daily_runs;
    return `You've used all ${cap} runs for today. They reset at midnight UTC.`;
  }
  return null;
}

export function RunButton({
  config,
  dirty,
  color,
}: {
  config: Scout;
  dirty: boolean;
  color: string;
}) {
  const runs = useScoutRuns();
  const start = useStartScout();
  const [asking, setAsking] = useState(false);
  const [label, setLabel] = useState('');
  const active = isSearching(runs.data?.[0]);
  const reason = blockedReason(config, dirty, active);
  // Labels already used, to reuse one rather than spell it a little differently.
  const known = [...new Set((runs.data ?? []).map((run) => run.label).filter(Boolean))] as string[];
  const trimmed = label.trim();

  function ask() {
    // Asked every time, so a run without a label is a choice and not a forgotten field.
    setLabel('');
    setAsking(true);
  }

  function begin() {
    start.mutate(trimmed || null, { onSuccess: () => setAsking(false) });
  }

  return (
    <Stack gap={6}>
      <Tooltip label={reason} disabled={!reason} withArrow multiline w={260}>
        {/* A disabled button gets no pointer events, so the wrapper carries the tooltip. */}
        <span>
          <Button
            fullWidth
            color={color}
            leftSection={<IconPlayerPlay size={16} />}
            disabled={reason !== null}
            onClick={ask}
          >
            Run Scout
          </Button>
        </span>
      </Tooltip>
      <Text size="xs" c="dimmed">
        {reason ??
          `Uses your saved settings. ${runsLeftText(config)} A run stops before it spends ${formatUsd(
            config.allowance.run_max_usd,
          )}.`}
      </Text>
      <Modal opened={asking} onClose={() => setAsking(false)} title="Label this run?" centered>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            begin();
          }}
        >
          <Stack gap="sm">
            <Autocomplete
              data-autofocus
              description="Its leads are filed under the label, so you can filter them on the Leads tab. Leave it blank for no label."
              placeholder="e.g. Remote fintech"
              value={label}
              onChange={setLabel}
              data={known}
              maxLength={MAX_LABEL_LENGTH}
            />
            <Group justify="flex-end" gap="xs">
              <Button variant="subtle" color="gray" onClick={() => setAsking(false)}>
                Cancel
              </Button>
              <Button
                type="submit"
                color={color}
                leftSection={<IconPlayerPlay size={16} />}
                loading={start.isPending}
              >
                {trimmed ? 'Run with this label' : 'Run without a label'}
              </Button>
            </Group>
          </Stack>
        </form>
      </Modal>
    </Stack>
  );
}

function Elapsed({ since }: { since: string }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), ELAPSED_TICK_MS);
    return () => window.clearInterval(timer);
  }, []);
  const seconds = Math.max(0, Math.floor((now - new Date(since).getTime()) / 1000));
  const minutes = Math.floor(seconds / 60);
  return (
    <span className="tabular">
      {minutes}:{String(seconds % 60).padStart(2, '0')}
    </span>
  );
}

function RunStatus({ run }: { run: ScoutRun }) {
  if (run.status === 'queued') {
    return (
      <Group gap="xs">
        <Badge color="gray" variant="light" tt="none">
          Queued
        </Badge>
        <Text size="sm" c="dimmed">
          Waiting its turn. Runs go one at a time.
        </Text>
      </Group>
    );
  }
  if (run.status === 'running') {
    return (
      <Group gap="xs" wrap="nowrap">
        <Loader size="xs" />
        <Text size="sm">
          Searching, <Elapsed since={run.started_at ?? run.created_at} /> so far. Leads show here
          when the run ends.
        </Text>
      </Group>
    );
  }
  const failed = run.status === 'failed';
  return (
    <Stack gap={4}>
      <Group gap="xs">
        <Badge color={failed ? 'red' : 'green'} variant="light" tt="none">
          {failed ? 'Failed' : 'Finished'}
        </Badge>
        <Text size="xs" c="dimmed">
          {run.finished_at && formatDateTime(run.finished_at)}
        </Text>
      </Group>
      {run.stop_message && <Text size="sm">{run.stop_message}</Text>}
      {!failed && (
        <Text size="xs" c="dimmed" className="tabular">
          {run.leads.length} {run.leads.length === 1 ? 'lead' : 'leads'}, {run.searches}{' '}
          {run.searches === 1 ? 'search' : 'searches'}, {run.pages}{' '}
          {run.pages === 1 ? 'page' : 'pages'} read, {formatUsd(run.cost_usd)}.
        </Text>
      )}
    </Stack>
  );
}

const STEP_LABELS: Record<ScoutStepKind, { label: string; color: string }> = {
  note: { label: 'Thought', color: 'gray' },
  search: { label: 'Searched', color: 'blue' },
  results: { label: 'Results', color: 'gray' },
  fetch: { label: 'Opened', color: 'violet' },
  lead: { label: 'Kept', color: 'green' },
  skipped: { label: 'Refused', color: 'yellow' },
  error: { label: 'Failed', color: 'red' },
  stop: { label: 'Stopped', color: 'dark' },
};

function StepRow({ step }: { step: ScoutStep }) {
  const { label, color } = STEP_LABELS[step.kind];
  const detail =
    step.kind === 'fetch' ? (
      <Anchor href={step.detail} target="_blank" rel="noopener noreferrer nofollow" size="sm">
        {step.detail}
      </Anchor>
    ) : (
      <Text size="sm" c={step.kind === 'note' ? 'dimmed' : undefined}>
        {step.kind === 'search' ? `“${step.detail}”` : step.detail}
      </Text>
    );
  return (
    <Group gap="xs" wrap="nowrap" align="flex-start">
      <Badge color={color} variant="light" tt="none" w={84} style={{ flexShrink: 0 }}>
        {label}
      </Badge>
      <div style={{ minWidth: 0, overflowWrap: 'anywhere' }}>{detail}</div>
    </Group>
  );
}

/** Everything the agent did, in order, split by the model request it happened in. */
export function RunSteps({ steps }: { steps: ScoutStep[] }) {
  const [open, setOpen] = useState(false);
  if (!steps.length) return null;
  const searches = steps.filter((step) => step.kind === 'search').length;
  return (
    <Stack gap="xs">
      <Button
        variant="subtle"
        size="xs"
        color="gray"
        px={0}
        w="fit-content"
        leftSection={open ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
        onClick={() => setOpen((value) => !value)}
      >
        What it did: {steps.length} steps, {searches} {searches === 1 ? 'search' : 'searches'}
      </Button>
      <Collapse in={open}>
        <Stack gap={6}>
          {steps.map((step, index) => (
            // Steps never reorder, so their position is a stable key.
            <Stack key={index} gap={6}>
              {step.request > 0 && step.request !== steps[index - 1]?.request && (
                <Text size="xs" c="dimmed" fw={600} mt={index ? 'xs' : 0}>
                  Step {step.request}
                </Text>
              )}
              <StepRow step={step} />
            </Stack>
          ))}
        </Stack>
      </Collapse>
    </Stack>
  );
}

/** The latest run, or one picked from recent runs, with its leads. */
export function ScoutRuns() {
  const { data: runs } = useScoutRuns();
  const [picked, setPicked] = useState<string | null>(null);

  if (!runs) return null;
  const run = runs.find((item) => item.id === picked) ?? runs[0];

  return (
    <Stack gap="md">
      <Group justify="space-between" align="flex-end">
        <Title order={4}>Runs</Title>
        {runs.length > 1 && (
          <Select
            aria-label="Pick a run"
            size="xs"
            w={260}
            value={run.id}
            onChange={(value) => setPicked(value)}
            allowDeselect={false}
            data={runs.map((item) => ({
              value: item.id,
              label: `${formatDateTime(item.created_at)}${item.label ? ` · ${item.label}` : ''} · ${
                item.leads.length
              } ${item.leads.length === 1 ? 'lead' : 'leads'}`,
            }))}
          />
        )}
      </Group>
      {!run ? (
        <Empty
          title="No runs yet"
          hint="Save your settings, then run Scout. Leads it finds show here."
        />
      ) : (
        <>
          <Paper withBorder p="md" radius="md">
            <Stack gap="xs">
              <Group justify="space-between" wrap="nowrap">
                <Text size="xs" c="dimmed">
                  Started {formatDateTime(run.created_at)}
                </Text>
                {!isSearching(run) && (
                  <Button
                    component={Link}
                    to={`/scout/runs/${run.id}`}
                    size="xs"
                    variant="light"
                    leftSection={<IconReportAnalytics size={14} />}
                  >
                    Open report
                  </Button>
                )}
              </Group>
              {run.label && (
                <Badge variant="light" color="gray" tt="none" w="fit-content">
                  {run.label}
                </Badge>
              )}
              <Text size="sm" fs="italic" lineClamp={2}>
                “{run.description}”
              </Text>
              <RunStatus run={run} />
              <RunSteps steps={run.steps} />
            </Stack>
          </Paper>
          <LeadList
            run={run}
            empty={
              run.status === 'done' && (
                <Empty
                  title="No leads this time"
                  hint="Try a broader description, more title families, or a longer time window."
                />
              )
            }
          />
        </>
      )}
    </Stack>
  );
}
