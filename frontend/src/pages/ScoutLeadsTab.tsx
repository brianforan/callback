import {
  Button,
  Chip,
  Group,
  MultiSelect,
  Popover,
  SegmentedControl,
  Stack,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import {
  IconArrowBackUp,
  IconCalendarOff,
  IconClock,
  IconSend,
  IconX,
} from '@tabler/icons-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import { Empty, PageError, PageLoader } from '../components/PageState';
import { AgencyNote, isAgencyLead, LeadCard } from '../components/ScoutLeadCard';
import { formatDateTime, todayIso } from '../lib/format';
import {
  loadPostingDetail,
  useCreateApplication,
  useSetLeadState,
  useScoutLeads,
} from '../lib/queries';
import type { LeadState, ScoutLeadItem } from '../lib/types';

const FILTERS: { value: LeadState; label: string; empty: string }[] = [
  { value: 'new', label: 'New', empty: 'No new leads. Run Scout from the Search tab.' },
  { value: 'later', label: 'Later', empty: 'Nothing saved for later.' },
  { value: 'dismissed', label: 'Not for me', empty: 'Nothing turned down.' },
  { value: 'expired', label: 'Expired', empty: 'No expired listings.' },
];

// Quick reasons. The agent is told them on later runs, so it skips jobs like these.
const REASONS = [
  'Pay too low',
  'Wrong location',
  'Wrong level',
  'Not the work I want',
  'Agency',
  'Company',
  'Stack',
];

// Filter values: a label is "label:" and its text, so leads from runs without one ("none") can't
// collide with a label of the same word.
const NO_LABEL = 'none';
const LABEL_PREFIX = 'label:';

function matchesLabels(lead: ScoutLeadItem, picked: string[]): boolean {
  if (picked.length === 0) return true;
  return picked.includes(lead.label ? `${LABEL_PREFIX}${lead.label}` : NO_LABEL);
}

function DismissButton({ lead }: { lead: ScoutLeadItem }) {
  const setState = useSetLeadState();
  const [open, setOpen] = useState(false);
  const [picked, setPicked] = useState<string[]>([]);
  const [note, setNote] = useState('');
  const reason = [...picked, note.trim()].filter(Boolean).join('; ');

  function confirm() {
    setState.mutate(
      { runId: lead.run_id, leadId: lead.id, state: 'dismissed', reason },
      {
        onSuccess: () => {
          setOpen(false);
          notifications.show({ message: `Moved ${lead.company} to Not for me` });
        },
      },
    );
  }

  return (
    <Popover opened={open} onChange={setOpen} width={320} position="bottom-start" withArrow shadow="md">
      <Popover.Target>
        <Button
          variant="default"
          leftSection={<IconX size={16} />}
          onClick={() => setOpen((value) => !value)}
        >
          Not for me
        </Button>
      </Popover.Target>
      <Popover.Dropdown>
        <Stack gap="sm">
          <Text size="sm" fw={500}>
            Why not? Future runs skip jobs like it.
          </Text>
          <Chip.Group multiple value={picked} onChange={setPicked}>
            <Group gap={6}>
              {REASONS.map((item) => (
                <Chip key={item} value={item} size="xs" variant="light">
                  {item}
                </Chip>
              ))}
            </Group>
          </Chip.Group>
          <TextInput
            placeholder="Anything else (optional)"
            value={note}
            maxLength={200}
            onChange={(event) => setNote(event.currentTarget.value)}
          />
          <Text size="xs" c="dimmed">
            It's hidden from Job listings too, and its saved posting is removed. Restore undoes
            both.
          </Text>
          <Group justify="flex-end" gap="xs">
            <Button variant="subtle" color="gray" size="xs" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button size="xs" color="red" loading={setState.isPending} onClick={confirm}>
              Not for me
            </Button>
          </Group>
        </Stack>
      </Popover.Dropdown>
    </Popover>
  );
}

function LeadActions({ lead }: { lead: ScoutLeadItem }) {
  const setState = useSetLeadState();
  const createApplication = useCreateApplication();
  // The posting is read only when applying, not watched here: watching it made every lead on the
  // tab load its posting at once, which queued clicks such as Not for me behind ~170 requests.
  const [loadingPosting, setLoadingPosting] = useState(false);

  function move(state: LeadState) {
    setState.mutate({ runId: lead.run_id, leadId: lead.id, state });
  }

  async function applied() {
    if (!lead.posting_id) return;
    setLoadingPosting(true);
    let posting;
    try {
      posting = await loadPostingDetail(lead.posting_id);
    } catch {
      // The query cache already showed the error, so nothing is logged without its resume.
      return;
    } finally {
      setLoadingPosting(false);
    }
    createApplication.mutate(
      {
        company: lead.company,
        role_title: lead.title,
        job_url: lead.url,
        location: lead.location || null,
        source: 'SmartSearch',
        status: 'applied',
        applied_on: todayIso(),
        resume_id: posting.staged_resume_id ?? posting.baseline?.resume_id ?? null,
        posting_id: lead.posting_id,
        notes: null,
      },
      {
        onSuccess: () =>
          notifications.show({
            color: 'green',
            message: `Logged your application to ${lead.company}. It's in Tracking now.`,
          }),
      },
    );
  }

  if (lead.state === 'dismissed' || lead.state === 'expired') {
    return (
      <Group gap="xs">
        <Button
          variant="light"
          leftSection={<IconArrowBackUp size={16} />}
          loading={setState.isPending}
          onClick={() => move('new')}
        >
          Restore
        </Button>
        {lead.decline_reason && (
          <Text size="sm" c="dimmed">
            Turned down: {lead.decline_reason}
          </Text>
        )}
      </Group>
    );
  }

  return (
    <Group gap="xs">
      {/* An action, not a state: green and a tick would read as already applied. */}
      <Button
        variant="light"
        leftSection={<IconSend size={16} />}
        loading={loadingPosting || createApplication.isPending}
        disabled={!lead.posting_id}
        onClick={() => void applied()}
      >
        Mark as applied
      </Button>
      {lead.state === 'later' ? (
        <Button
          variant="default"
          leftSection={<IconArrowBackUp size={16} />}
          loading={setState.isPending}
          onClick={() => move('new')}
        >
          Back to new
        </Button>
      ) : (
        <Button
          variant="default"
          leftSection={<IconClock size={16} />}
          loading={setState.isPending}
          onClick={() => move('later')}
        >
          Later
        </Button>
      )}
      <Button
        variant="default"
        leftSection={<IconCalendarOff size={16} />}
        loading={setState.isPending}
        onClick={() =>
          setState.mutate(
            { runId: lead.run_id, leadId: lead.id, state: 'expired' },
            {
              onSuccess: () =>
                notifications.show({ message: `Moved ${lead.company} to Expired` }),
            },
          )
        }
      >
        Listing expired
      </Button>
      <DismissButton lead={lead} />
      <Text size="xs" c="dimmed">
        Found {formatDateTime(lead.found_at)}
        {lead.label && ` · ${lead.label}`} ·{' '}
        <Link to={`/scout/runs/${lead.run_id}`}>run report</Link>
      </Text>
    </Group>
  );
}

function LeadGroup({ leads }: { leads: ScoutLeadItem[] }) {
  return (
    <>
      {leads.map((lead) => (
        <LeadCard
          key={`${lead.run_id}-${lead.id}`}
          runId={lead.run_id}
          lead={lead}
          actions={<LeadActions lead={lead} />}
          showApply={lead.state !== 'dismissed' && lead.state !== 'expired'}
        />
      ))}
    </>
  );
}

/** Leads waiting on you, across every run. Applying moves one to Tracking. */
export function ScoutLeadsTab() {
  const { data: leads, isPending, isError } = useScoutLeads();
  const [filter, setFilter] = useState<LeadState>('new');
  const [pickedLabels, setPickedLabels] = useState<string[]>([]);

  if (isPending) return <PageLoader />;
  if (isError || !leads) return <PageError />;

  const labels = [...new Set(leads.map((lead) => lead.label).filter(Boolean))].sort(
    (a, b) => (a as string).localeCompare(b as string),
  ) as string[];
  // Leads from runs made before labels, or started without one, have none to pick.
  const hasUnlabeled = leads.some((lead) => !lead.label);
  const labelOptions = [
    ...labels.map((label) => ({ value: `${LABEL_PREFIX}${label}`, label })),
    ...(hasUnlabeled ? [{ value: NO_LABEL, label: 'No label' }] : []),
  ];
  const open = leads.filter((lead) => !lead.application && matchesLabels(lead, pickedLabels));
  const shown = open.filter((lead) => lead.state === filter);
  const direct = shown.filter((lead) => !isAgencyLead(lead));
  const agency = shown.filter(isAgencyLead);
  const empty = FILTERS.find((item) => item.value === filter)?.empty ?? '';

  return (
    <Stack gap="md">
      {labels.length > 0 && (
        <MultiSelect
          aria-label="Filter by label"
          placeholder={pickedLabels.length ? undefined : 'All labels'}
          data={labelOptions}
          value={pickedLabels}
          onChange={setPickedLabels}
          clearable
          searchable
          maw={480}
        />
      )}
      <SegmentedControl
        w="fit-content"
        value={filter}
        onChange={(value) => setFilter(value as LeadState)}
        data={FILTERS.map((item) => ({
          value: item.value,
          label: `${item.label} (${open.filter((lead) => lead.state === item.value).length})`,
        }))}
      />
      {shown.length === 0 ? (
        <Empty title={empty} />
      ) : (
        <>
          <LeadGroup leads={direct} />
          {agency.length > 0 && (
            <Stack gap="sm">
              <Title order={4}>Through agencies ({agency.length})</Title>
              <AgencyNote />
              <LeadGroup leads={agency} />
            </Stack>
          )}
        </>
      )}
    </Stack>
  );
}
