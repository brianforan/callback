import {
  ActionIcon,
  Anchor,
  Badge,
  Button,
  Divider,
  Drawer,
  Group,
  Menu,
  Popover,
  ScrollArea,
  Stack,
  Text,
  Title,
} from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import {
  IconBuildingOff,
  IconCheck,
  IconChevronDown,
  IconCircleDashed,
  IconClipboardList,
  IconExternalLink,
  IconEye,
  IconEyeOff,
  IconPlus,
  IconSend,
  IconTrash,
} from '@tabler/icons-react';
import { useState, type SyntheticEvent } from 'react';

import { ScoutBadge } from './ScoutBadge';
import { formatDay, todayIso } from '../lib/format';
import { fieldLabel, formatSalary } from '../lib/postings';
import {
  unhideListing,
  useCreatePosting,
  useDeletePosting,
  useHideListing,
  useUnhideListing,
} from '../lib/queries';
import { statusMeta } from '../lib/statuses';
import type { HiddenListing, HideListingPayload, Listing } from '../lib/types';
import { ApplySteps, jobFromListing } from './ApplySteps';
import { MatchChecklist, ResumeRecommendation } from './MatchEstimate';

function stop(event: SyntheticEvent) {
  // The buttons sit inside a clickable table row. React passes events from the menu's portal to
  // that row too, so they stop here.
  event.stopPropagation();
}

export interface ListingActionHandlers {
  openPosting: (postingId: string) => void;
  apply: (listing: Listing) => void;
}

function SavedMenu({
  listing,
  postingId,
  actions,
}: {
  listing: Listing;
  postingId: string;
  actions: ListingActionHandlers;
}) {
  const remove = useDeletePosting();
  const [opened, menu] = useDisclosure(false);
  const [confirming, setConfirming] = useState(false);
  const close = () => {
    menu.close();
    setConfirming(false);
  };
  const applied = listing.application_status ? statusMeta(listing.application_status) : null;

  return (
    <span onClick={stop} onKeyDown={stop}>
      <Popover
        opened={opened}
        onChange={(next) => !next && close()}
        position="bottom-end"
        withArrow
        shadow="md"
        width={250}
      >
        <Popover.Target>
          <Button
            size="compact-xs"
            variant="light"
            color={applied?.color}
            leftSection={<IconCheck size={12} />}
            rightSection={<IconChevronDown size={12} />}
            onClick={() => (opened ? close() : menu.open())}
          >
            {applied ? applied.label : 'Tracked'}
          </Button>
        </Popover.Target>
        <Popover.Dropdown>
          {confirming ? (
            <Stack gap="xs">
              <Text size="sm">
                This deletes the saved posting for {listing.company}. An application you logged
                against it stays, but loses its link to the posting.
              </Text>
              <Group justify="flex-end" gap="xs">
                <Button size="xs" variant="default" onClick={() => setConfirming(false)}>
                  Cancel
                </Button>
                <Button
                  size="xs"
                  color="red"
                  loading={remove.isPending}
                  onClick={() =>
                    remove.mutate(postingId, {
                      onSuccess: () => {
                        close();
                        notifications.show({ message: `Untracked ${listing.company}` });
                      },
                    })
                  }
                >
                  Untrack
                </Button>
              </Group>
            </Stack>
          ) : (
            <Stack gap={4}>
              <Button
                variant="subtle"
                color="gray"
                justify="flex-start"
                leftSection={<IconClipboardList size={14} />}
                onClick={() => {
                  close();
                  actions.openPosting(postingId);
                }}
              >
                Open posting
              </Button>
              <Button
                variant="subtle"
                color="gray"
                justify="flex-start"
                leftSection={<IconSend size={14} />}
                onClick={() => {
                  close();
                  actions.apply(listing);
                }}
              >
                {applied ? 'Log another application' : 'I applied'}
              </Button>
              <Button
                variant="subtle"
                color="red"
                justify="flex-start"
                leftSection={<IconTrash size={14} />}
                onClick={() => setConfirming(true)}
              >
                Untrack
              </Button>
            </Stack>
          )}
        </Popover.Dropdown>
      </Popover>
    </span>
  );
}

function hideJob(listing: Listing): [HideListingPayload, string] {
  return [
    // Only offered for a listing with a URL, which the server needs to match the job.
    {
      kind: 'listing',
      company: listing.company,
      title: listing.title,
      url: listing.url ?? undefined,
    },
    `Hid ${listing.title} at ${listing.company}`,
  ];
}

function hideCompany(listing: Listing): [HideListingPayload, string] {
  return [{ kind: 'company', company: listing.company }, `Hid every job at ${listing.company}`];
}

/** Hides by a rule, then offers Undo. Resolves true once the rule is saved. */
function useHideWithUndo() {
  const hide = useHideListing();

  async function run([payload, done]: [HideListingPayload, string]): Promise<boolean> {
    let rule: HiddenListing;
    try {
      rule = await hide.mutateAsync(payload);
    } catch {
      // The query client already shows the error.
      return false;
    }
    // The row disappears once the results refresh, so Undo calls the API directly.
    const id = `hidden-${rule.id}`;
    notifications.show({
      id,
      // Longer than the usual notice, so there's time to reach Undo.
      autoClose: 10_000,
      message: (
        <Group justify="space-between" gap="xs" wrap="nowrap">
          <Text size="sm">{done}</Text>
          <Anchor
            component="button"
            type="button"
            size="sm"
            onClick={() => {
              notifications.hide(id);
              unhideListing(rule.id).catch((error: Error) =>
                notifications.show({ color: 'red', message: error.message }),
              );
            }}
          >
            Undo
          </Anchor>
        </Group>
      ),
    });
    return true;
  }

  return { run, isPending: hide.isPending };
}

function HideMenu({ listing }: { listing: Listing }) {
  const hide = useHideWithUndo();
  const [opened, menu] = useDisclosure(false);
  const run = async (rule: [HideListingPayload, string]) => {
    if (await hide.run(rule)) menu.close();
  };

  return (
    <span onClick={stop} onKeyDown={stop}>
      <Popover
        opened={opened}
        onChange={(next) => !next && menu.close()}
        position="bottom-end"
        withArrow
        shadow="md"
        width={320}
      >
        <Popover.Target>
          <ActionIcon
            variant="subtle"
            color="gray"
            aria-label="Hide from results"
            onClick={() => (opened ? menu.close() : menu.open())}
          >
            <IconEyeOff size={14} />
          </ActionIcon>
        </Popover.Target>
        <Popover.Dropdown>
          <Stack gap={4}>
            <Button
              variant="subtle"
              color="gray"
              justify="flex-start"
              leftSection={<IconEyeOff size={14} />}
              disabled={hide.isPending || !listing.url}
              onClick={() => run(hideJob(listing))}
            >
              Won't apply to this job
            </Button>
            <Button
              variant="subtle"
              color="gray"
              justify="flex-start"
              leftSection={<IconBuildingOff size={14} />}
              disabled={hide.isPending}
              onClick={() => run(hideCompany(listing))}
            >
              <Text inherit truncate>
                Hide every job at {listing.company}
              </Text>
            </Button>
            <Text size="xs" c="dimmed" px="xs">
              Bring them back from Hidden, above the results.
            </Text>
          </Stack>
        </Popover.Dropdown>
      </Popover>
    </span>
  );
}

function unhideNotice(listing: Listing, kind: HiddenListing['kind']): string {
  return kind === 'company'
    ? `Showing jobs at ${listing.company} again`
    : `Showing ${listing.title} again`;
}

function UnhideButton({ listing }: { listing: Listing }) {
  const unhide = useUnhideListing();
  if (!listing.hidden) return null;
  const { id, kind } = listing.hidden;
  return (
    <Button
      size="compact-xs"
      variant="light"
      color="gray"
      leftSection={<IconEye size={12} />}
      loading={unhide.isPending}
      onClick={(event) => {
        stop(event);
        unhide.mutate(id, {
          onSuccess: () => notifications.show({ message: unhideNotice(listing, kind) }),
        });
      }}
    >
      {kind === 'company' ? 'Unhide company' : 'Unhide'}
    </Button>
  );
}

export function ListingActions({
  listing,
  actions,
}: {
  listing: Listing;
  actions: ListingActionHandlers;
}) {
  return (
    <Group gap={4} wrap="nowrap">
      {listing.posting_id ? (
        <SavedMenu listing={listing} postingId={listing.posting_id} actions={actions} />
      ) : (
        <NewListingActions listing={listing} actions={actions} />
      )}
      {listing.hidden ? <UnhideButton listing={listing} /> : <HideMenu listing={listing} />}
    </Group>
  );
}

function trackPayload(listing: Listing) {
  return {
    company: listing.company,
    title: listing.title,
    url: listing.url,
    location: listing.location,
    source: listing.source,
    raw_text: listing.description || null,
    referrer: null,
    seen_on: todayIso(),
  };
}

function NewListingActions({
  listing,
  actions,
}: {
  listing: Listing;
  actions: ListingActionHandlers;
}) {
  const create = useCreatePosting();

  return (
    <Group gap={4} wrap="nowrap" onClick={stop} onKeyDown={stop}>
      <Button
        size="compact-xs"
        variant="default"
        leftSection={<IconPlus size={12} />}
        loading={create.isPending}
        onClick={() =>
          create.mutate(trackPayload(listing), {
            onSuccess: () =>
              notifications.show({ color: 'green', message: `Saved ${listing.company}` }),
          })
        }
      >
        Track
      </Button>
      <Button
        size="compact-xs"
        variant="default"
        leftSection={<IconSend size={12} />}
        onClick={() => actions.apply(listing)}
      >
        Applied
      </Button>
    </Group>
  );
}

/** Where a listing stands, shown on the row menu's trigger like a select's value. */
function listingState(listing: Listing) {
  if (listing.hidden) {
    return {
      label: listing.hidden.kind === 'company' ? 'Company hidden' : 'Hidden',
      color: 'gray',
      icon: IconEyeOff,
    };
  }
  if (listing.application_status) {
    const { label, color } = statusMeta(listing.application_status);
    return { label, color, icon: IconSend };
  }
  if (listing.staged) {
    return { label: 'Ready to apply', color: 'violet', icon: IconCheck };
  }
  if (listing.posting_id) {
    return { label: 'Tracked', color: 'teal', icon: IconCheck };
  }
  return { label: 'Not tracked', color: 'gray', icon: IconCircleDashed };
}

/** Every row action in one menu, so the table keeps a single narrow column for them. */
export function ListingActionMenu({
  listing,
  actions,
}: {
  listing: Listing;
  actions: ListingActionHandlers;
}) {
  const create = useCreatePosting();
  const remove = useDeletePosting();
  const unhide = useUnhideListing();
  const hide = useHideWithUndo();
  const [opened, setOpened] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const state = listingState(listing);
  const StateIcon = state.icon;
  const { posting_id: postingId, hidden } = listing;
  const busy = create.isPending || remove.isPending || unhide.isPending || hide.isPending;
  const close = () => {
    setOpened(false);
    setConfirming(false);
  };

  return (
    <span onClick={stop} onKeyDown={stop}>
      <Menu
        opened={opened}
        onChange={(next) => (next ? setOpened(true) : close())}
        position="bottom-end"
        shadow="md"
        width={260}
        withinPortal
      >
        <Menu.Target>
          <Button
            className="action-select"
            size="compact-sm"
            variant="light"
            color={state.color}
            justify="space-between"
            miw={156}
            loading={busy}
            leftSection={<StateIcon size={14} stroke={1.8} />}
            rightSection={<IconChevronDown size={14} className="action-select-chevron" />}
            aria-label={`Actions for ${listing.title} at ${listing.company}: ${state.label}`}
          >
            {state.label}
          </Button>
        </Menu.Target>
        <Menu.Dropdown>
          {confirming && postingId ? (
            <Stack gap="xs" p="xs">
              <Text size="sm">
                This deletes the saved posting for {listing.company}. An application you logged
                against it stays, but loses its link to the posting.
              </Text>
              <Group justify="flex-end" gap="xs">
                <Button size="xs" variant="default" onClick={() => setConfirming(false)}>
                  Cancel
                </Button>
                <Button
                  size="xs"
                  color="red"
                  loading={remove.isPending}
                  onClick={() =>
                    remove.mutate(postingId, {
                      onSuccess: () => {
                        close();
                        notifications.show({ message: `Untracked ${listing.company}` });
                      },
                    })
                  }
                >
                  Untrack
                </Button>
              </Group>
            </Stack>
          ) : (
            <>
              <Menu.Label>Track</Menu.Label>
              {postingId ? (
                <Menu.Item
                  leftSection={<IconClipboardList size={14} />}
                  onClick={() => actions.openPosting(postingId)}
                >
                  Open posting
                </Menu.Item>
              ) : (
                <Menu.Item
                  leftSection={<IconPlus size={14} />}
                  onClick={() =>
                    create.mutate(trackPayload(listing), {
                      onSuccess: () =>
                        notifications.show({ color: 'green', message: `Saved ${listing.company}` }),
                    })
                  }
                >
                  Track
                </Menu.Item>
              )}
              <Menu.Item
                leftSection={<IconSend size={14} />}
                onClick={() => actions.apply(listing)}
              >
                {listing.application_status ? 'Log another application' : 'I applied'}
              </Menu.Item>
              {postingId && (
                <Menu.Item
                  color="red"
                  leftSection={<IconTrash size={14} />}
                  closeMenuOnClick={false}
                  onClick={() => setConfirming(true)}
                >
                  Untrack
                </Menu.Item>
              )}

              <Menu.Divider />
              <Menu.Label>Hide</Menu.Label>
              {hidden ? (
                <Menu.Item
                  leftSection={<IconEye size={14} />}
                  onClick={() =>
                    unhide.mutate(hidden.id, {
                      onSuccess: () =>
                        notifications.show({ message: unhideNotice(listing, hidden.kind) }),
                    })
                  }
                >
                  {hidden.kind === 'company' ? 'Unhide company' : 'Unhide'}
                </Menu.Item>
              ) : (
                <>
                  <Menu.Item
                    leftSection={<IconEyeOff size={14} />}
                    disabled={!listing.url}
                    onClick={() => hide.run(hideJob(listing))}
                  >
                    Won't apply to this job
                  </Menu.Item>
                  <Menu.Item
                    leftSection={<IconBuildingOff size={14} />}
                    onClick={() => hide.run(hideCompany(listing))}
                  >
                    <Text inherit truncate>
                      Hide every job at {listing.company}
                    </Text>
                  </Menu.Item>
                </>
              )}
            </>
          )}
        </Menu.Dropdown>
      </Menu>
    </span>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <Group gap={6} wrap="nowrap" align="baseline">
      <Text size="xs" c="dimmed" w={88}>
        {label}
      </Text>
      <Text size="sm">{value}</Text>
    </Group>
  );
}

export function ListingDrawer({
  listing,
  onClose,
  actions,
  outdated = false,
}: {
  listing: Listing | null;
  onClose: () => void;
  actions: ListingActionHandlers;
  // The listing left the results shown after it was saved, so its track state is out of date.
  outdated?: boolean;
}) {
  return (
    <Drawer
      opened={listing !== null}
      onClose={onClose}
      position="right"
      size="lg"
      title={
        listing ? (
          <Group gap="xs" wrap="nowrap">
            <span>{`${listing.company} · ${listing.title}`}</span>
            {listing.from_scout && <ScoutBadge />}
          </Group>
        ) : (
          'Listing'
        )
      }
    >
      {listing && (
        <Stack gap="md">
          <Group justify="space-between">
            {outdated ? (
              <Text size="sm" c="dimmed">
                Saved. It no longer matches the filters shown.
              </Text>
            ) : (
              <ListingActions listing={listing} actions={actions} />
            )}
            {listing.url && (
              <Anchor href={listing.url} target="_blank" rel="noopener noreferrer" size="sm">
                <Group gap={4} wrap="nowrap">
                  View on {listing.source} <IconExternalLink size={14} />
                </Group>
              </Anchor>
            )}
          </Group>

          <ApplySteps key={listing.id} job={jobFromListing(listing)} />

          <Divider />
          <Stack gap={4}>
            <Fact label="Location" value={listing.location ?? '–'} />
            <Fact label="Level" value={fieldLabel(listing.level)} />
            <Fact label="Role type" value={fieldLabel(listing.role_family)} />
            <Fact label="Work mode" value={fieldLabel(listing.work_mode)} />
            <Fact label="Job type" value={fieldLabel(listing.employment_type)} />
            <Fact label="Years" value={listing.years_required?.toString() ?? '–'} />
            <Fact label="Salary" value={formatSalary(listing.salary_min, listing.salary_max)} />
            <Fact
              label="Posted"
              value={listing.published_on ? formatDay(listing.published_on) : '–'}
            />
          </Stack>

          <ResumeRecommendation match={listing.match} />
          <MatchChecklist match={listing.match} />

          <Divider />
          <Title order={5}>Stack by requirement</Title>
          {listing.technologies.length ? (
            <Stack gap={6}>
              {(['required', 'nice_to_have', 'mentioned'] as const).map((requirement) => {
                const names = listing.technologies.filter(
                  (technology) => technology.requirement === requirement,
                );
                return names.length ? (
                  <Group key={requirement} gap={6} align="center">
                    <Text size="xs" c="dimmed" w={88}>
                      {fieldLabel(requirement)}
                    </Text>
                    {names.map((technology) => (
                      <Badge key={technology.name} variant="default" tt="none" fw={500}>
                        {technology.name}
                      </Badge>
                    ))}
                  </Group>
                ) : null;
              })}
            </Stack>
          ) : (
            <Text size="sm" c="dimmed">
              No known technologies found.
            </Text>
          )}

          {listing.description && (
            <>
              <Divider />
              <Title order={5}>Listing text</Title>
              <ScrollArea.Autosize mah={420}>
                <Text size="xs" style={{ whiteSpace: 'pre-wrap' }}>
                  {listing.description}
                </Text>
              </ScrollArea.Autosize>
            </>
          )}
        </Stack>
      )}
    </Drawer>
  );
}
