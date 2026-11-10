import {
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Center,
  Checkbox,
  Chip,
  Code,
  Collapse,
  Group,
  Loader,
  Modal,
  MultiSelect,
  NumberInput,
  Pagination,
  Select,
  SimpleGrid,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { useForm } from '@mantine/form';
import { useDisclosure } from '@mantine/hooks';
import {
  IconAdjustmentsHorizontal,
  IconEyeOff,
  IconPlus,
  IconSearch,
} from '@tabler/icons-react';
import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { ScoutBadge } from '../components/ScoutBadge';
import { FocusCard } from '../components/Focus';
import { HiddenListingsModal } from '../components/HiddenListingsModal';
import { ApplyListingModal } from '../components/ApplyListingModal';
import {
  ListingActionMenu,
  ListingDrawer,
  type ListingActionHandlers,
} from '../components/ListingDrawer';
import { LevelBadge, LevelHeader } from '../components/LevelBadge';
import { MatchBadge } from '../components/MatchEstimate';
import { Empty, PageError } from '../components/PageState';
import { PostingDrawer } from '../components/PostingDrawer';
import { PostingForm } from '../components/PostingForm';
import { StackHoverCard } from '../components/StackHoverCard';
import { formatDateTime, formatDay } from '../lib/format';
import {
  EMPLOYMENT_TYPE_OPTIONS,
  fieldLabel,
  formatSalary,
  hasSalary,
  LEVEL_OPTIONS,
  ROLE_FAMILY_OPTIONS,
  WORK_MODE_OPTIONS,
} from '../lib/postings';
import {
  showListingUpdate,
  useHiddenListings,
  useListings,
  useListingUpdate,
  useProfiles,
} from '../lib/queries';
import { PageTitle } from '../lib/sections';
import { STATUSES } from '../lib/statuses';
import type {
  Listing,
  ListingSearch,
  ListingSort,
  ListingSource,
  ListingTracking,
} from '../lib/types';

const DEFAULT_SEARCH = 'software engineer';
const DEFAULT_LOCATION = 'USA';

// The Source column is hidden for now, with its code kept for a later use.
const SHOW_SOURCE = false;

const STATE_BADGES: Record<Exclude<ListingSource['state'], 'ok'>, { color: string; label: string }> = {
  failed: { color: 'red', label: 'Failed' },
  needs_setup: { color: 'gray', label: 'Needs setup' },
  pending: { color: 'blue', label: 'Fetching' },
};

function ListingTable({
  listings,
  onOpen,
  actions,
}: {
  listings: Listing[];
  onOpen: (id: string) => void;
  actions: ListingActionHandlers;
}) {
  return (
    <FocusCard title="Job listings" p={0}>
      <Table.ScrollContainer minWidth={0}>
        <Table highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Match</Table.Th>
              <Table.Th>Company</Table.Th>
              <Table.Th>Title</Table.Th>
              <Table.Th>
                <LevelHeader />
              </Table.Th>
              <Table.Th>Location</Table.Th>
              <Table.Th>Salary</Table.Th>
              <Table.Th>Stack</Table.Th>
              {SHOW_SOURCE && <Table.Th>Source</Table.Th>}
              <Table.Th>Posted</Table.Th>
              <Table.Th>Status</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody className="tabular">
            {listings.map((listing) => (
              <Table.Tr
                key={listing.id}
                className="clickable-row"
                // Hidden listings only appear when asked for, and stay dimmed so they stand apart.
                style={listing.hidden ? { opacity: 0.55 } : undefined}
                tabIndex={0}
                onClick={() => onOpen(listing.id)}
                onKeyDown={(event) => {
                  // Enter on the row's own button belongs to that button.
                  if (event.key === 'Enter' && event.target === event.currentTarget) {
                    onOpen(listing.id);
                  }
                }}
              >
                <Table.Td>
                  <MatchBadge match={listing.match} />
                </Table.Td>
                <Table.Td fw={650} fz="md">
                  {listing.company}
                </Table.Td>
                <Table.Td fw={500}>
                  <Group gap={6} wrap="nowrap">
                    <span>{listing.title}</span>
                    {listing.from_scout && <ScoutBadge />}
                  </Group>
                </Table.Td>
                <Table.Td>
                  <LevelBadge level={listing.level} />
                </Table.Td>
                <Table.Td c="dimmed">{listing.location ?? '–'}</Table.Td>
                <Table.Td
                  fw={hasSalary(listing.salary_min, listing.salary_max) ? 650 : undefined}
                  c={hasSalary(listing.salary_min, listing.salary_max) ? 'teal' : 'dimmed'}
                  style={{ whiteSpace: 'nowrap' }}
                >
                  {formatSalary(listing.salary_min, listing.salary_max)}
                </Table.Td>
                <Table.Td>
                  <StackHoverCard technologies={listing.technologies} />
                </Table.Td>
                {SHOW_SOURCE && (
                  <Table.Td c="dimmed" style={{ whiteSpace: 'nowrap' }}>
                    {listing.source}
                  </Table.Td>
                )}
                <Table.Td c="dimmed" style={{ whiteSpace: 'nowrap' }}>
                  {listing.published_on ? formatDay(listing.published_on) : '–'}
                </Table.Td>
                <Table.Td>
                  <ListingActionMenu listing={listing} actions={actions} />
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </FocusCard>
  );
}

function SourceRow({ source }: { source: ListingSource }) {
  const badge = source.state === 'ok' ? null : STATE_BADGES[source.state];
  return (
    <Stack gap={2}>
      <Group gap={6} justify="space-between" wrap="nowrap">
        <Anchor href={source.site} target="_blank" rel="noopener noreferrer" size="sm" fw={500}>
          {source.name}
        </Anchor>
        {badge ? (
          <Badge size="sm" variant="light" color={badge.color} tt="none" fw={500}>
            {badge.label}
          </Badge>
        ) : (
          <Text size="sm" c="dimmed" className="tabular">
            {source.listings} matched
          </Text>
        )}
      </Group>
      {source.state === 'needs_setup' ? (
        <Text size="xs" c="dimmed">
          Set {source.missing_settings.map((name, index) => (
            <span key={name}>
              {index > 0 && ' and '}
              <Code>{name}</Code>
            </span>
          ))}{' '}
          in .env, then restart the API.
          {source.setup_url && (
            <>
              {' '}
              <Anchor href={source.setup_url} target="_blank" rel="noopener noreferrer" inherit>
                Get a key
              </Anchor>
            </>
          )}
        </Text>
      ) : (
        <>
          {source.message && (
            <Text size="xs" c="dimmed">
              {source.message}
            </Text>
          )}
          {source.state === 'ok' && source.fetched_at && (
            <Text size="xs" c="dimmed">
              Fetched {formatDateTime(source.fetched_at)}
            </Text>
          )}
        </>
      )}
    </Stack>
  );
}

function SourceList({ sources }: { sources: ListingSource[] }) {
  return (
    <FocusCard title="Sources">
      <Title order={5} mb="sm">
        Sources
      </Title>
      <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }} spacing="md" verticalSpacing="sm">
        {sources.map((source) => (
          <SourceRow key={source.id} source={source} />
        ))}
      </SimpleGrid>
    </FocusCard>
  );
}

function pageIds(data: ListingSearch): string[] {
  return data.listings.map((listing) => listing.id);
}

/**
 * While the server fetches newer results in the background, says so, then offers them in a
 * banner instead of reshuffling the rows being read. Nothing changes until it's clicked, unless
 * the newer results are the same as the ones shown.
 */
function FreshResults({
  search,
  current,
  currentAt,
  isPlaceholder,
}: {
  search: string;
  current: ListingSearch;
  currentAt: number;
  // True while the previous search's results stand in for this one's, which are still loading.
  isPlaceholder: boolean;
}) {
  // While the previous search stands in, its refreshing flag says nothing about this search, so
  // nothing is fetched or compared until this search's own results arrive.
  const update = useListingUpdate(search, current.refreshing && !isPlaceholder);
  const ready =
    update.data && !update.data.refreshing && update.dataUpdatedAt > currentAt ? update.data : null;
  const shown = pageIds(current);
  const same =
    ready !== null &&
    ready.total === current.total &&
    pageIds(ready).join() === shown.join();

  useEffect(() => {
    if (ready && same) showListingUpdate(search, ready);
  }, [ready, same, search]);

  if (isPlaceholder || !current.refreshing || same) return null;
  if (!ready) {
    return (
      <Group gap="xs">
        <Loader size="xs" />
        <Text size="sm" c="dimmed">
          Showing saved results while the latest jobs load.
        </Text>
      </Group>
    );
  }
  const added = pageIds(ready).filter((id) => !shown.includes(id)).length;
  return (
    <Alert color="blue" variant="light" py="xs">
      <Group justify="space-between" wrap="nowrap">
        <Text size="sm">
          The latest results are in
          {added ? `: ${added} new on this page, ${ready.total} in all` : `: ${ready.total} in all`}
          .
        </Text>
        <Button size="xs" variant="light" onClick={() => showListingUpdate(search, ready)}>
          Show them
        </Button>
      </Group>
    </Alert>
  );
}

function summary(data: ListingSearch): string {
  const { sources, total, unfiltered_total: unfiltered } = data;
  const live = sources.filter((source) => source.state === 'ok').length;
  const setup = sources.filter((source) => source.state === 'needs_setup').length;
  const failed = sources.filter((source) => source.state === 'failed').length;
  const count = total < unfiltered ? `${total} of ${unfiltered} listings` : `${total} listings`;
  const parts = [`${count} from ${live} of ${sources.length} sources`];
  if (setup) parts.push(`${setup} need setup`);
  if (failed) parts.push(`${failed} failed`);
  return `${parts.join(', ')}.`;
}

const LIST_FILTERS = [
  'level',
  'role_family',
  'work_mode',
  'employment_type',
  'source',
  'stack',
  'exclude_stack',
] as const;
const NUMBER_FILTERS = ['salary_min', 'years_max', 'posted_within_days', 'min_match'] as const;
const FLAG_FILTERS = ['has_salary', 'fits_baseline'] as const;

// Tracked jobs no longer on the boards join the results when any tracked state is picked.
const TRACKING_OPTIONS: { value: ListingTracking; label: string; color: string }[] = [
  { value: 'untracked', label: 'Not tracked', color: 'gray' },
  { value: 'tracked', label: 'Tracked', color: 'teal' },
  { value: 'staged', label: 'Ready to apply', color: 'violet' },
  ...STATUSES,
];

const SORT_OPTIONS: { value: ListingSort; label: string }[] = [
  { value: 'match', label: 'Best match' },
  { value: 'newest', label: 'Newest' },
  { value: 'salary', label: 'Highest salary' },
  { value: 'relevance', label: 'Keyword relevance' },
];
const POSTED_OPTIONS = [
  { value: '1', label: 'Past day' },
  { value: '3', label: 'Past 3 days' },
  { value: '7', label: 'Past week' },
  { value: '14', label: 'Past 2 weeks' },
  { value: '30', label: 'Past month' },
];

function formValues(params: URLSearchParams) {
  return {
    q: params.get('q') ?? DEFAULT_SEARCH,
    location: params.get('location') ?? DEFAULT_LOCATION,
    level: params.getAll('level'),
    role_family: params.getAll('role_family'),
    work_mode: params.getAll('work_mode'),
    employment_type: params.getAll('employment_type'),
    source: params.getAll('source'),
    stack: params.getAll('stack'),
    exclude_stack: params.getAll('exclude_stack'),
    salary_min: (params.get('salary_min') ?? '') as number | string,
    years_max: (params.get('years_max') ?? '') as number | string,
    min_match: (params.get('min_match') ?? '') as number | string,
    posted_within_days: params.get('posted_within_days'),
    has_salary: params.get('has_salary') === 'true',
    fits_baseline: params.get('fits_baseline') === 'true',
  };
}

type FormValues = ReturnType<typeof formValues>;

function toParams(values: FormValues, sort: string | null, tracking: string[]): URLSearchParams {
  // A cleared location stays in the URL as blank, so it doesn't return to the default.
  const next = new URLSearchParams({ q: values.q.trim(), location: values.location.trim() });
  for (const name of LIST_FILTERS) {
    for (const value of values[name]) next.append(name, value);
  }
  for (const name of NUMBER_FILTERS) {
    const value = values[name];
    if (value !== '' && value !== null) next.set(name, String(value));
  }
  for (const name of FLAG_FILTERS) {
    if (values[name]) next.set(name, 'true');
  }
  for (const value of tracking) next.append('tracking', value);
  if (sort) next.set('sort', sort);
  return next;
}

function activeFilters(values: FormValues): number {
  return (
    LIST_FILTERS.filter((name) => values[name].length > 0).length +
    NUMBER_FILTERS.filter((name) => values[name] !== '' && values[name] !== null).length +
    FLAG_FILTERS.filter((name) => values[name]).length
  );
}

/** Selected values stay listed even when the current results no longer contain them. */
function withSelected(options: string[], selected: string[]): string[] {
  return [...new Set([...selected, ...options])];
}

export function ListingsPage() {
  const [params, setParams] = useSearchParams();
  const applied = formValues(params);
  const tracking = params.getAll('tracking');
  const search = toParams(applied, params.get('sort'), tracking);
  if (params.get('page')) search.set('page', params.get('page') as string);
  const showHidden = params.get('show_hidden') === 'true';
  if (showHidden) search.set('show_hidden', 'true');
  // Tracked jobs are shared by every profile unless one profile's are picked.
  const trackedProfile = params.get('profile_id');
  if (trackedProfile) search.set('profile_id', trackedProfile);
  const { data: profiles } = useProfiles();
  const listings = useListings(search.toString(), applied.q.trim().length > 0);
  const [openId, setOpenId] = useState<string | null>(null);
  const [postingId, setPostingId] = useState<string | null>(null);
  const [applying, setApplying] = useState<Listing | null>(null);
  const [hiddenOpen, hiddenModal] = useDisclosure(false);
  const [adding, add] = useDisclosure(false);
  const hiddenRules = useHiddenListings();
  // One drawer or dialog at a time, so each replaces the listing it came from.
  const actions: ListingActionHandlers = {
    openPosting: (id) => {
      setOpenId(null);
      setPostingId(id);
    },
    apply: (listing) => {
      setOpenId(null);
      setApplying(listing);
    },
  };
  const form = useForm({ initialValues: applied });
  // The URL is the search. When it changes while the page stays open (Back, Forward, or the
  // sidebar link), the form follows it. Paging and sorting leave the form alone, so a half-typed
  // search isn't lost.
  const appliedKey = JSON.stringify(applied);
  const shownKey = useRef(appliedKey);
  useEffect(() => {
    if (shownKey.current === appliedKey) return;
    shownKey.current = appliedKey;
    const values = JSON.parse(appliedKey) as typeof applied;
    form.setValues(values);
    form.resetDirty(values);
  }, [appliedKey, form]);
  const filterCount = activeFilters(applied);
  const [filtersOpen, filters] = useDisclosure(filterCount > 0);
  const data = listings.data;
  // Keep the open listing while the drawer is open, even if saving it moves it out of the
  // results shown, such as under "Not tracked".
  const found = data?.listings.find((listing) => listing.id === openId) ?? null;
  const lastOpen = useRef<Listing | null>(null);
  if (found) lastOpen.current = found;
  const kept = lastOpen.current?.id === openId ? lastOpen.current : null;
  const open = openId ? (found ?? kept) : null;
  const technologies = (data?.technologies ?? []).map((row) => row.label);
  const sources = (data?.sources ?? [])
    .filter((source) => source.state !== 'needs_setup')
    .map((source) => source.name);

  const changeTracking = (values: string[]) => {
    const next = new URLSearchParams(params);
    next.delete('tracking');
    for (const value of values) next.append('tracking', value);
    next.delete('page');
    setParams(next);
  };

  const changeView = (
    name: 'sort' | 'page' | 'show_hidden' | 'profile_id',
    value: string | null,
  ) => {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value);
    else next.delete(name);
    // A new order or set of results starts back at the top.
    if (name !== 'page') next.delete('page');
    setParams(next);
  };

  return (
    <Stack>
      <Group justify="space-between">
        <PageTitle section="listings" />
        <Button size="xs" variant="default" leftSection={<IconPlus size={14} />} onClick={add.open}>
          Add posting
        </Button>
      </Group>

      <Card>
        <form
          onSubmit={form.onSubmit((values) =>
            setParams(toParams(values, params.get('sort'), tracking)),
          )}
        >
          <Stack gap="sm">
            <Group align="flex-end" gap="sm">
              <TextInput
                label="Keywords"
                placeholder='"platform engineer" (python OR go) -clearance'
                required
                maxLength={200}
                style={{ flex: '2 1 280px' }}
                {...form.getInputProps('q')}
              />
              <TextInput
                label="Location"
                placeholder="Philadelphia, USA…"
                maxLength={200}
                style={{ flex: '1 1 160px' }}
                {...form.getInputProps('location')}
              />
              <Button
                variant="default"
                leftSection={<IconAdjustmentsHorizontal size={14} />}
                onClick={filters.toggle}
                aria-expanded={filtersOpen}
              >
                Filters{filterCount > 0 ? ` (${filterCount})` : ''}
              </Button>
              <Button
                type="submit"
                leftSection={<IconSearch size={14} />}
                disabled={!form.values.q.trim()}
              >
                Search
              </Button>
            </Group>
            <Text size="xs" c="dimmed">
              Use <Code>"exact phrase"</Code>, <Code>OR</Code>, <Code>(groups)</Code>,{' '}
              <Code>-exclude</Code>, <Code>title:senior</Code> and <Code>company:name</Code>. Words
              must appear whole.
            </Text>

            <Collapse in={filtersOpen}>
              <Stack gap="sm">
                <SimpleGrid cols={{ base: 1, xs: 2, md: 3 }} spacing="sm">
                  <MultiSelect
                    label="Level"
                    data={LEVEL_OPTIONS}
                    clearable
                    {...form.getInputProps('level')}
                  />
                  <MultiSelect
                    label="Role type"
                    data={ROLE_FAMILY_OPTIONS}
                    clearable
                    {...form.getInputProps('role_family')}
                  />
                  <MultiSelect
                    label="Work mode"
                    data={WORK_MODE_OPTIONS}
                    clearable
                    {...form.getInputProps('work_mode')}
                  />
                  <MultiSelect
                    label="Job type"
                    data={EMPLOYMENT_TYPE_OPTIONS}
                    clearable
                    {...form.getInputProps('employment_type')}
                  />
                  <MultiSelect
                    label="Must have all of"
                    placeholder="Technologies"
                    data={withSelected(technologies, form.values.stack)}
                    searchable
                    clearable
                    {...form.getInputProps('stack')}
                  />
                  <MultiSelect
                    label="Exclude"
                    placeholder="Technologies"
                    data={withSelected(technologies, form.values.exclude_stack)}
                    searchable
                    clearable
                    {...form.getInputProps('exclude_stack')}
                  />
                  <MultiSelect
                    label="Source"
                    data={withSelected(sources, form.values.source)}
                    clearable
                    {...form.getInputProps('source')}
                  />
                  <Select
                    label="Posted"
                    placeholder="Any time"
                    data={POSTED_OPTIONS}
                    clearable
                    {...form.getInputProps('posted_within_days')}
                  />
                  <NumberInput
                    label="Minimum salary"
                    description="Keeps jobs with no stated salary"
                    min={0}
                    step={10_000}
                    prefix="$"
                    thousandSeparator=","
                    allowDecimal={false}
                    {...form.getInputProps('salary_min')}
                  />
                  <NumberInput
                    label="At most this many years required"
                    description="Keeps jobs with no stated years"
                    min={0}
                    max={60}
                    allowDecimal={false}
                    {...form.getInputProps('years_max')}
                  />
                  <NumberInput
                    label="Minimum match"
                    description="0 to 100"
                    min={0}
                    max={100}
                    allowDecimal={false}
                    disabled={data !== undefined && !data.has_profile}
                    {...form.getInputProps('min_match')}
                  />
                </SimpleGrid>
                <Group gap="lg">
                  <Checkbox
                    label="Only jobs with a stated salary"
                    {...form.getInputProps('has_salary', { type: 'checkbox' })}
                  />
                  <Checkbox
                    label="Only jobs a baseline fits"
                    {...form.getInputProps('fits_baseline', { type: 'checkbox' })}
                  />
                  <Button
                    variant="subtle"
                    size="compact-sm"
                    onClick={() => {
                      const blank = formValues(
                        new URLSearchParams({ q: form.values.q, location: form.values.location }),
                      );
                      form.setValues(blank);
                      setParams(toParams(blank, params.get('sort'), tracking));
                    }}
                  >
                    Clear filters
                  </Button>
                </Group>
              </Stack>
            </Collapse>
          </Stack>
        </form>
      </Card>

      <Group gap="sm">
        <Chip.Group multiple value={tracking} onChange={changeTracking}>
          <Group gap={6} role="group" aria-label="Filter by tracking">
            {TRACKING_OPTIONS.map(({ value, label, color }) => (
              <Chip key={value} value={value} size="sm" color={color} variant="light">
                {label}
                {/* An API from before the tracking filter sends no counts. */}
                {data?.tracking_counts && (
                  <Text span inherit fw={700} ml={6} className="tabular">
                    {data.tracking_counts[value] ?? 0}
                  </Text>
                )}
              </Chip>
            ))}
          </Group>
        </Chip.Group>
        {profiles && profiles.profiles.length > 1 && (
          <Select
            size="xs"
            w={190}
            placeholder="Tracked by any profile"
            aria-label="Filter tracked jobs by profile"
            data={profiles.profiles.map((profile) => ({ value: profile.id, label: profile.name }))}
            value={trackedProfile}
            onChange={(value) => changeView('profile_id', value)}
            clearable
          />
        )}
      </Group>

      {listings.isPending && (
        <Center h={240}>
          <Stack align="center" gap="xs">
            <Loader />
            <Text size="sm" c="dimmed">
              Asking the job boards. The first search can take up to 25 seconds.
            </Text>
          </Stack>
        </Center>
      )}
      {listings.isError && <PageError />}

      {data && (
        <>
          <Group justify="space-between" align="center">
            <Text size="sm" c="dimmed">
              {summary(data)}
              {data.hidden_total > 0 && (
                <>
                  {' '}
                  {showHidden
                    ? `Showing ${data.hidden_total} you hid.`
                    : `${data.hidden_total} hidden.`}{' '}
                  <Anchor
                    component="button"
                    type="button"
                    inherit
                    onClick={() => changeView('show_hidden', showHidden ? null : 'true')}
                  >
                    {showHidden ? 'Leave them out' : 'Show them'}
                  </Anchor>
                  .
                </>
              )}
              {!data.has_profile && ' Upload a resume to see match estimates.'}
            </Text>
            <Group gap="xs" wrap="nowrap">
              <Button
                variant="default"
                size="xs"
                leftSection={<IconEyeOff size={14} />}
                onClick={hiddenModal.open}
              >
                Hidden{hiddenRules.data?.length ? ` (${hiddenRules.data.length})` : ''}
              </Button>
              <Select
                aria-label="Sort by"
                size="xs"
                w={180}
                allowDeselect={false}
                data={SORT_OPTIONS}
                value={data.sort}
                onChange={(value) => changeView('sort', value)}
              />
            </Group>
          </Group>
          <FreshResults
            search={search.toString()}
            current={data}
            currentAt={listings.dataUpdatedAt}
            isPlaceholder={listings.isPlaceholderData}
          />
          {data.listings.length ? (
            <ListingTable listings={data.listings} onOpen={setOpenId} actions={actions} />
          ) : (
            <Card>
              <Empty
                title="No listings match this search"
                hint="Try fewer keywords or filters. Words must appear whole, so “engineer” won't match “engineers”."
              />
            </Card>
          )}
          {data.total > data.page_size && (
            <Group justify="center">
              <Pagination
                total={Math.ceil(data.total / data.page_size)}
                value={data.page}
                onChange={(page) => changeView('page', String(page))}
              />
            </Group>
          )}
          <SourceList sources={data.sources} />
        </>
      )}

      <ListingDrawer
        listing={open}
        onClose={() => setOpenId(null)}
        actions={actions}
        outdated={open !== null && found === null}
      />
      <PostingDrawer postingId={postingId} onClose={() => setPostingId(null)} />
      <Modal opened={adding} onClose={add.close} title="Add posting" size="lg">
        <PostingForm onDone={add.close} />
      </Modal>
      <ApplyListingModal listing={applying} onClose={() => setApplying(null)} />
      <HiddenListingsModal opened={hiddenOpen} onClose={hiddenModal.close} />
    </Stack>
  );
}
