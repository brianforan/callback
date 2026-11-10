import {
  ActionIcon,
  Anchor,
  Badge,
  Button,
  Card,
  ColorSwatch,
  Group,
  SegmentedControl,
  Select,
  Stack,
  Table,
  Text,
  TextInput,
  Tooltip,
  UnstyledButton,
} from '@mantine/core';
import {
  IconArrowDown,
  IconArrowUp,
  IconDownload,
  IconExternalLink,
  IconSearch,
} from '@tabler/icons-react';
import { useMemo, useState, type CSSProperties } from 'react';

import { ScoutBadge } from '../components/ScoutBadge';
import { ApplicationDrawer } from '../components/ApplicationDrawer';
import { FocusCard, FocusPanel } from '../components/Focus';
import { Empty, PageError, PageLoader } from '../components/PageState';
import { StatusBadge } from '../components/StatusBadge';
import { daysAgo, exportApplicationsCsv, formatDay } from '../lib/format';
import { useApplications, useProfiles, useUpdateApplication } from '../lib/queries';
import { PageTitle, SECTIONS } from '../lib/sections';
import { STATUS_OPTIONS, STATUSES, statusMeta } from '../lib/statuses';
import type { Application, ApplicationStatus } from '../lib/types';

type SortKey = 'company' | 'applied_on';

function sortApplications(items: Application[], key: SortKey, ascending: boolean) {
  const direction = ascending ? 1 : -1;
  return [...items].sort((a, b) => direction * a[key].localeCompare(b[key]));
}

interface SortHeaderProps {
  label: string;
  column: SortKey;
  sort: { key: SortKey; ascending: boolean };
  onSort: (column: SortKey) => void;
}

function SortHeader({ label, column, sort, onSort }: SortHeaderProps) {
  const Arrow = sort.ascending ? IconArrowUp : IconArrowDown;
  return (
    <UnstyledButton onClick={() => onSort(column)} fz="sm" fw={700}>
      <Group gap={4} wrap="nowrap">
        {label}
        {sort.key === column && <Arrow size={12} />}
      </Group>
    </UnstyledButton>
  );
}

interface KpiFiltersProps {
  total: number;
  counts: Record<string, number>;
  selected: string[];
  onChange: (statuses: string[]) => void;
}

function KpiFilters({ total, counts, selected, onChange }: KpiFiltersProps) {
  const toggle = (value: string) =>
    onChange(
      selected.includes(value) ? selected.filter((item) => item !== value) : [...selected, value],
    );

  return (
    <div className="kpi-filters" role="group" aria-label="Filter by status">
      <UnstyledButton
        className="kpi-filter"
        style={{ '--kpi-color': 'var(--mantine-color-moss-filled)' } as CSSProperties}
        aria-pressed={selected.length === 0}
        onClick={() => onChange([])}
      >
        <Text size="sm" fw={600}>
          All
        </Text>
        <span className="kpi-filter-value tabular">{total}</span>
      </UnstyledButton>
      {STATUSES.map(({ value, label, color }) => {
        const count = counts[value] ?? 0;
        return (
          <UnstyledButton
            key={value}
            className="kpi-filter"
            style={{ '--kpi-color': `var(--mantine-color-${color}-filled)` } as CSSProperties}
            aria-pressed={selected.includes(value)}
            data-empty={count === 0}
            onClick={() => toggle(value)}
          >
            <Text size="sm" fw={600}>
              {label}
            </Text>
            <span className="kpi-filter-value tabular">{count}</span>
          </UnstyledButton>
        );
      })}
    </div>
  );
}

interface ViewProps {
  items: Application[];
  onOpen: (id: string) => void;
  onStatus: (id: string, status: ApplicationStatus) => void;
}

function TableView({ items, onOpen, onStatus }: ViewProps) {
  const [sort, setSort] = useState<{ key: SortKey; ascending: boolean }>({
    key: 'applied_on',
    ascending: false,
  });
  const sorted = useMemo(() => sortApplications(items, sort.key, sort.ascending), [items, sort]);
  const toggleSort = (key: SortKey) =>
    setSort((current) => ({
      key,
      ascending: current.key === key ? !current.ascending : key === 'company',
    }));

  return (
    <FocusCard title="Applications" p={0}>
      <Table.ScrollContainer minWidth={0}>
        <Table highlightOnHover stickyHeader>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>
                <SortHeader label="Company" column="company" sort={sort} onSort={toggleSort} />
              </Table.Th>
              <Table.Th>Role</Table.Th>
              <Table.Th w={150}>Status</Table.Th>
              <Table.Th>
                <SortHeader label="Applied" column="applied_on" sort={sort} onSort={toggleSort} />
              </Table.Th>
              <Table.Th>Source</Table.Th>
              <Table.Th>Resume</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {sorted.map((application) => (
              <Table.Tr
                key={application.id}
                className="clickable-row"
                onClick={() => onOpen(application.id)}
              >
                <Table.Td>
                  <Group gap={6} wrap="nowrap">
                    <Text size="md" fw={650} lineClamp={1} style={{ flex: 1, minWidth: 0 }}>
                      {application.company}
                    </Text>
                    {/* Fixed-width slots keep the badge and link at the same x on every row. */}
                    <div style={{ width: 92, flexShrink: 0 }}>
                      {application.from_scout && <ScoutBadge />}
                    </div>
                    <div style={{ width: 22, flexShrink: 0 }}>
                      {application.job_url && (
                        <Tooltip label="Open posting">
                          <ActionIcon
                            component="a"
                            href={application.job_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            size="xs"
                            variant="subtle"
                            color="gray"
                            aria-label={`Open ${application.company} posting`}
                            onClick={(event) => event.stopPropagation()}
                          >
                            <IconExternalLink size={12} />
                          </ActionIcon>
                        </Tooltip>
                      )}
                    </div>
                  </Group>
                </Table.Td>
                <Table.Td>
                  <Text size="sm" fw={500} lineClamp={1}>
                    {application.role_title}
                  </Text>
                  {application.location && (
                    <Text size="xs" c="dimmed" lineClamp={1}>
                      {application.location}
                    </Text>
                  )}
                </Table.Td>
                <Table.Td onClick={(event) => event.stopPropagation()}>
                  <Select
                    size="xs"
                    aria-label={`Status for ${application.company}`}
                    data={STATUS_OPTIONS}
                    value={application.status}
                    leftSection={
                      <ColorSwatch
                        size={10}
                        withShadow={false}
                        color={`var(--mantine-color-${statusMeta(application.status).color}-filled)`}
                      />
                    }
                    styles={{ input: { fontWeight: 600 } }}
                    allowDeselect={false}
                    onChange={(value) => value && onStatus(application.id, value as ApplicationStatus)}
                  />
                </Table.Td>
                <Table.Td>
                  <Text size="sm" fw={500} className="tabular" style={{ whiteSpace: 'nowrap' }}>
                    {formatDay(application.applied_on)}
                  </Text>
                  <Text size="xs" c="dimmed">
                    {daysAgo(application.applied_on)}
                  </Text>
                </Table.Td>
                <Table.Td c="dimmed">{application.source ?? '–'}</Table.Td>
                <Table.Td>
                  {application.resume_label ? (
                    <Badge variant="light" color={SECTIONS.resumes.color} tt="none" fw={500}>
                      {application.resume_label}
                    </Badge>
                  ) : (
                    <Text size="sm" c="dimmed">
                      –
                    </Text>
                  )}
                  {application.recommended_resume_id &&
                    application.recommended_resume_id !== application.resume_id && (
                      <Text size="xs" c="dimmed" mt={2}>
                        Suggested: {application.recommended_resume_label}
                      </Text>
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

function BoardView({ items, onOpen, onStatus }: ViewProps) {
  const [dropTarget, setDropTarget] = useState<ApplicationStatus | null>(null);

  return (
    <div className="board">
      {STATUSES.map(({ value, label }) => {
        const members = items.filter((application) => application.status === value);
        return (
          <FocusPanel key={value} title={label}>
            <div
              className="board-column"
              data-drop-target={dropTarget === value}
              onDragOver={(event) => {
                event.preventDefault();
                setDropTarget(value);
              }}
              onDragLeave={() => setDropTarget(null)}
              onDrop={(event) => {
                event.preventDefault();
                setDropTarget(null);
                const id = event.dataTransfer.getData('text/plain');
                const dragged = items.find((application) => application.id === id);
                if (dragged && dragged.status !== value) onStatus(id, value);
              }}
            >
              <Group justify="space-between" mb="xs" px={4}>
                <StatusBadge status={value} />
                <Text size="xs" c="dimmed" aria-label={`${members.length} in ${label}`}>
                  {members.length}
                </Text>
              </Group>
              <Stack gap={6}>
                {members.map((application) => (
                  <Card
                    key={application.id}
                    p="xs"
                    className="board-card"
                    draggable
                    onDragStart={(event) =>
                      event.dataTransfer.setData('text/plain', application.id)
                    }
                    onClick={() => onOpen(application.id)}
                  >
                    <Text size="sm" fw={500} lineClamp={1}>
                      {application.company}
                    </Text>
                    <Text size="xs" lineClamp={1}>
                      {application.role_title}
                    </Text>
                    <Text size="xs" c="dimmed" mt={4}>
                      {daysAgo(application.applied_on)}
                    </Text>
                  </Card>
                ))}
              </Stack>
            </div>
          </FocusPanel>
        );
      })}
    </div>
  );
}

export function ApplicationsPage() {
  const { data: applications, isPending, isError } = useApplications();
  const { data: profiles } = useProfiles();
  const update = useUpdateApplication();

  const [view, setView] = useState('table');
  const [search, setSearch] = useState('');
  const [statuses, setStatuses] = useState<string[]>([]);
  const [resumeId, setResumeId] = useState<string | null>(null);
  // Applications are shared by every profile, so all of them show until one is picked.
  const [profileId, setProfileId] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);

  // Taken from the applications, since the resume list holds only the active profile's.
  const resumeOptions = useMemo(() => {
    const labels = new Map<string, string>();
    for (const application of applications ?? []) {
      if (profileId && application.profile_id !== profileId) continue;
      if (application.resume_id && application.resume_label) {
        labels.set(application.resume_id, application.resume_label);
      }
    }
    return [...labels].map(([value, label]) => ({ value, label }));
  }, [applications, profileId]);

  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return (applications ?? []).filter(
      (application) =>
        (!statuses.length || statuses.includes(application.status)) &&
        (!profileId || application.profile_id === profileId) &&
        (!resumeId || application.resume_id === resumeId) &&
        (!needle ||
          [application.company, application.role_title, application.location, application.notes]
            .join(' ')
            .toLowerCase()
            .includes(needle)),
    );
  }, [applications, search, statuses, profileId, resumeId]);

  if (isPending) return <PageLoader />;
  if (isError) return <PageError />;

  const counts: Record<string, number> = {};
  applications.forEach(({ status }) => {
    counts[status] = (counts[status] ?? 0) + 1;
  });

  const handleStatus = (id: string, status: ApplicationStatus) =>
    update.mutate({ id, changes: { status } });

  return (
    <Stack>
      <Group justify="space-between">
        <PageTitle section="applications" />
        <Group gap="xs">
          <SegmentedControl
            size="xs"
            value={view}
            onChange={setView}
            data={[
              { value: 'table', label: 'Table' },
              { value: 'board', label: 'Board' },
            ]}
          />
          <Button
            size="xs"
            variant="default"
            leftSection={<IconDownload size={14} />}
            disabled={!visible.length}
            onClick={() => exportApplicationsCsv(visible)}
          >
            Export CSV
          </Button>
        </Group>
      </Group>

      <KpiFilters
        total={applications.length}
        counts={counts}
        selected={statuses}
        onChange={setStatuses}
      />

      <Group gap="sm">
        <TextInput
          size="xs"
          w={240}
          placeholder="Search company, role, notes"
          aria-label="Search applications"
          leftSection={<IconSearch size={14} />}
          value={search}
          onChange={(event) => setSearch(event.currentTarget.value)}
        />
        {profiles && profiles.profiles.length > 1 && (
          <Select
            size="xs"
            w={170}
            placeholder="All profiles"
            aria-label="Filter by profile"
            data={profiles.profiles.map((profile) => ({ value: profile.id, label: profile.name }))}
            value={profileId}
            onChange={(value) => {
              setProfileId(value);
              setResumeId(null);
            }}
            clearable
          />
        )}
        <Select
          size="xs"
          w={190}
          placeholder="All resumes"
          aria-label="Filter by resume"
          data={resumeOptions}
          value={resumeId}
          onChange={setResumeId}
          clearable
        />
      </Group>

      {visible.length === 0 ? (
        <Card>
          <Empty
            title={applications.length ? 'Nothing matches these filters' : 'No applications yet'}
            hint={
              applications.length
                ? 'Clear a filter to see more.'
                : 'Use “Log application” at the top to add your first one.'
            }
          />
        </Card>
      ) : view === 'table' ? (
        <TableView items={visible} onOpen={setOpenId} onStatus={handleStatus} />
      ) : (
        <BoardView items={visible} onOpen={setOpenId} onStatus={handleStatus} />
      )}

      <Text size="xs" c="dimmed">
        Showing {visible.length} of {applications.length}.{' '}
        {view === 'board' && 'Drag a card to change its status.'}
        {view === 'table' && (
          <Anchor size="xs" component="button" onClick={() => setView('board')}>
            Switch to board
          </Anchor>
        )}
      </Text>

      <ApplicationDrawer
        application={applications.find((application) => application.id === openId) ?? null}
        onClose={() => setOpenId(null)}
      />
    </Stack>
  );
}
