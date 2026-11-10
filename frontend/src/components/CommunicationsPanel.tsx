import {
  ActionIcon,
  Badge,
  Button,
  Checkbox,
  Group,
  Paper,
  SegmentedControl,
  Select,
  SimpleGrid,
  Stack,
  Text,
  TextInput,
  Textarea,
  Tooltip,
} from '@mantine/core';
import {
  IconMail,
  IconMessage,
  IconNote,
  IconPencil,
  IconPhone,
  IconPlus,
  IconTrash,
  IconUsers,
} from '@tabler/icons-react';
import { useState } from 'react';

import { formatDay, todayIso } from '../lib/format';
import {
  useAddCommunication,
  useCommunications,
  useDeleteCommunication,
  useUpdateCommunication,
} from '../lib/queries';
import type { Communication, CommunicationKind, CommunicationPayload, Direction } from '../lib/types';

const KINDS: { value: CommunicationKind; label: string; icon: typeof IconMail }[] = [
  { value: 'email', label: 'Email', icon: IconMail },
  { value: 'call', label: 'Call', icon: IconPhone },
  { value: 'interview', label: 'Interview', icon: IconUsers },
  { value: 'message', label: 'Message', icon: IconMessage },
  { value: 'note', label: 'Note', icon: IconNote },
];

const DIRECTIONS: { value: Direction; label: string }[] = [
  { value: 'inbound', label: 'From them' },
  { value: 'outbound', label: 'From me' },
];

function blank(): CommunicationPayload {
  return {
    kind: 'email',
    direction: 'inbound',
    occurred_on: todayIso(),
    contact: '',
    summary: '',
    next_step: '',
    next_step_due: null,
    next_step_done: false,
  };
}

function dueText(due: string | null): { text: string; color: string } | null {
  if (!due) return null;
  const today = todayIso();
  if (due < today) return { text: `Overdue, ${formatDay(due)}`, color: 'red' };
  if (due === today) return { text: 'Due today', color: 'orange' };
  return { text: `Due ${formatDay(due)}`, color: 'gray' };
}

function EntryForm({
  initial,
  saving,
  onSave,
  onCancel,
}: {
  initial: CommunicationPayload;
  saving: boolean;
  onSave: (payload: CommunicationPayload) => void;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState(initial);
  const update = (changes: Partial<CommunicationPayload>) => setDraft({ ...draft, ...changes });
  const isNote = draft.kind === 'note';

  return (
    <Paper withBorder p="sm" radius="md">
      <Stack gap="sm">
        <Select
          label="What"
          data={KINDS.map(({ value, label }) => ({ value, label }))}
          value={draft.kind}
          allowDeselect={false}
          onChange={(value) =>
            value &&
            update({
              kind: value as CommunicationKind,
              direction: value === 'note' ? 'none' : draft.direction === 'none' ? 'inbound' : draft.direction,
            })
          }
        />
        {!isNote && (
          <SegmentedControl
            data={DIRECTIONS}
            value={draft.direction === 'none' ? 'inbound' : draft.direction}
            onChange={(value) => update({ direction: value as Direction })}
            fullWidth
          />
        )}
        <SimpleGrid cols={{ base: 1, xs: 2 }} spacing="sm">
          <TextInput
            label="When"
            type="date"
            required
            value={draft.occurred_on}
            onChange={(event) => update({ occurred_on: event.currentTarget.value })}
          />
          <TextInput
            label="Who"
            placeholder="e.g. Dana, recruiter"
            maxLength={200}
            value={draft.contact}
            onChange={(event) => update({ contact: event.currentTarget.value })}
          />
        </SimpleGrid>
        <Textarea
          label="What happened"
          placeholder="e.g. Asked for a phone screen next week, sent three time slots."
          autosize
          minRows={2}
          maxRows={8}
          maxLength={5000}
          value={draft.summary}
          onChange={(event) => update({ summary: event.currentTarget.value })}
        />
        <SimpleGrid cols={{ base: 1, xs: 2 }} spacing="sm">
          <TextInput
            label="Next step"
            placeholder="e.g. Send a thank-you note"
            maxLength={300}
            value={draft.next_step}
            onChange={(event) => update({ next_step: event.currentTarget.value })}
          />
          <TextInput
            label="Due"
            type="date"
            disabled={!draft.next_step.trim()}
            value={draft.next_step_due ?? ''}
            onChange={(event) => update({ next_step_due: event.currentTarget.value || null })}
          />
        </SimpleGrid>
        <Group justify="flex-end" gap="xs">
          <Button variant="subtle" color="gray" size="xs" onClick={onCancel}>
            Cancel
          </Button>
          <Button
            size="xs"
            loading={saving}
            disabled={!draft.occurred_on}
            onClick={() =>
              onSave({
                ...draft,
                next_step_due: draft.next_step.trim() ? draft.next_step_due : null,
              })
            }
          >
            Save
          </Button>
        </Group>
      </Stack>
    </Paper>
  );
}

function Entry({ applicationId, entry }: { applicationId: string; entry: Communication }) {
  const update = useUpdateCommunication(applicationId);
  const remove = useDeleteCommunication(applicationId);
  const [editing, setEditing] = useState(false);
  const kind = KINDS.find((item) => item.value === entry.kind) ?? KINDS[0];
  const Icon = kind.icon;
  const due = entry.next_step_done ? null : dueText(entry.next_step_due);

  if (editing) {
    return (
      <EntryForm
        initial={entry}
        saving={update.isPending}
        onCancel={() => setEditing(false)}
        onSave={(payload) =>
          update.mutate({ id: entry.id, changes: payload }, { onSuccess: () => setEditing(false) })
        }
      />
    );
  }

  return (
    <Group gap="sm" wrap="nowrap" align="flex-start">
      <Icon size={18} style={{ flexShrink: 0, marginTop: 2 }} aria-label={kind.label} />
      <Stack gap={4} style={{ minWidth: 0, flex: 1 }}>
        <Group gap="xs" justify="space-between" wrap="nowrap">
          <Group gap="xs" wrap="wrap">
            <Text size="sm" fw={600}>
              {kind.label}
            </Text>
            {entry.direction !== 'none' && (
              <Badge size="xs" variant="light" color="gray" tt="none">
                {entry.direction === 'inbound' ? 'From them' : 'From me'}
              </Badge>
            )}
            <Text size="xs" c="dimmed">
              {formatDay(entry.occurred_on)}
              {entry.contact && ` · ${entry.contact}`}
            </Text>
          </Group>
          <Group gap={2} wrap="nowrap">
            <Tooltip label="Edit">
              <ActionIcon variant="subtle" color="gray" size="sm" aria-label="Edit" onClick={() => setEditing(true)}>
                <IconPencil size={14} />
              </ActionIcon>
            </Tooltip>
            <Tooltip label="Delete">
              <ActionIcon
                variant="subtle"
                color="gray"
                size="sm"
                aria-label="Delete"
                loading={remove.isPending}
                onClick={() => remove.mutate(entry.id)}
              >
                <IconTrash size={14} />
              </ActionIcon>
            </Tooltip>
          </Group>
        </Group>
        {entry.summary && (
          <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
            {entry.summary}
          </Text>
        )}
        {entry.next_step && (
          <Group gap="xs" wrap="nowrap">
            <Checkbox
              size="xs"
              checked={entry.next_step_done}
              aria-label={`Done: ${entry.next_step}`}
              onChange={(event) =>
                update.mutate({
                  id: entry.id,
                  changes: { next_step_done: event.currentTarget.checked },
                })
              }
            />
            <Text size="sm" td={entry.next_step_done ? 'line-through' : undefined}>
              Next: {entry.next_step}
            </Text>
            {due && (
              <Badge size="xs" variant="light" color={due.color} tt="none">
                {due.text}
              </Badge>
            )}
          </Group>
        )}
      </Stack>
    </Group>
  );
}

/**
 * Every contact about one application, newest first, with a form to log another. Logged contact
 * restarts the application's follow-up clock, and open next steps show on the Dashboard.
 */
export function CommunicationsPanel({ applicationId }: { applicationId: string }) {
  const { data: entries, isPending } = useCommunications(applicationId);
  const add = useAddCommunication(applicationId);
  const [adding, setAdding] = useState(false);

  return (
    <Stack gap="sm">
      {adding ? (
        <EntryForm
          initial={blank()}
          saving={add.isPending}
          onCancel={() => setAdding(false)}
          onSave={(payload) => add.mutate(payload, { onSuccess: () => setAdding(false) })}
        />
      ) : (
        <Button
          variant="light"
          size="xs"
          w="fit-content"
          leftSection={<IconPlus size={14} />}
          onClick={() => setAdding(true)}
        >
          Log a communication
        </Button>
      )}
      {isPending ? null : entries && entries.length > 0 ? (
        <Stack gap="md">
          {entries.map((entry) => (
            <Entry key={entry.id} applicationId={applicationId} entry={entry} />
          ))}
        </Stack>
      ) : (
        !adding && (
          <Text size="sm" c="dimmed">
            Nothing logged yet. Emails, calls, interviews and notes go here.
          </Text>
        )
      )}
    </Stack>
  );
}
