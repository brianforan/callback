import {
  Anchor,
  Badge,
  Button,
  Collapse,
  Group,
  Paper,
  Select,
  Stack,
  Text,
  Title,
} from '@mantine/core';
import { IconChevronDown, IconChevronRight, IconExternalLink } from '@tabler/icons-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import { CommunicationsPanel } from '../components/CommunicationsPanel';
import { Empty, PageError, PageLoader } from '../components/PageState';
import { hostOf, LeadCard } from '../components/ScoutLeadCard';
import { formatDay } from '../lib/format';
import { useScoutLeads, useUpdateApplication } from '../lib/queries';
import { STATUS_OPTIONS, STATUSES, statusMeta } from '../lib/statuses';
import type { ApplicationStatus, ScoutLeadItem } from '../lib/types';

function TrackedRow({ lead }: { lead: ScoutLeadItem }) {
  const update = useUpdateApplication();
  const [open, setOpen] = useState(false);
  const [talking, setTalking] = useState(false);
  const application = lead.application;
  if (!application) return null;

  return (
    <Paper withBorder p="md" radius="md">
      <Stack gap="sm">
        <Group justify="space-between" align="flex-start" wrap="wrap" gap="sm">
          <Stack gap={2} style={{ minWidth: 0 }}>
            <Text fw={600}>{lead.title}</Text>
            <Text size="sm" c="dimmed">
              {lead.company}
              {lead.location && ` · ${lead.location}`}
            </Text>
            <Text size="xs" c="dimmed">
              Applied {formatDay(application.applied_on)}
              {application.resume_label && ` with ${application.resume_label}`}
              {application.last_contact_on &&
                ` · last contact ${formatDay(application.last_contact_on)}`}
            </Text>
            {application.next_step && (
              <Text size="sm">
                Next: {application.next_step}
                {application.next_step_due && ` (due ${formatDay(application.next_step_due)})`}
              </Text>
            )}
          </Stack>
          <Group gap="xs" wrap="nowrap">
            <Select
              aria-label={`Status of ${lead.company}`}
              size="xs"
              w={150}
              data={STATUS_OPTIONS}
              value={application.status}
              allowDeselect={false}
              onChange={(value) =>
                value &&
                update.mutate({
                  id: application.id,
                  changes: { status: value as ApplicationStatus },
                })
              }
            />
          </Group>
        </Group>
        <Group gap="md">
          <Anchor href={lead.url} target="_blank" rel="noopener noreferrer nofollow" size="sm">
            <Group gap={4} wrap="nowrap">
              Posting on {hostOf(lead.url)} <IconExternalLink size={14} />
            </Group>
          </Anchor>
          <Anchor component={Link} to="/applications" size="sm">
            Open in Applications
          </Anchor>
          <Button
            variant="subtle"
            size="xs"
            color="gray"
            px={0}
            leftSection={talking ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
            onClick={() => setTalking((value) => !value)}
          >
            Communications ({application.communications})
          </Button>
          <Button
            variant="subtle"
            size="xs"
            color="gray"
            px={0}
            leftSection={open ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
            onClick={() => setOpen((value) => !value)}
          >
            {open ? 'Hide the brief' : 'Show the brief'}
          </Button>
        </Group>
        <Collapse in={talking}>
          {talking && <CommunicationsPanel applicationId={application.id} />}
        </Collapse>
        <Collapse in={open}>
          {open && <LeadCard runId={lead.run_id} lead={lead} showApply={false} />}
        </Collapse>
      </Stack>
    </Paper>
  );
}

/** Jobs applied to from Scout, grouped by where each application stands. */
export function ScoutTrackingTab() {
  const { data: leads, isPending, isError } = useScoutLeads();

  if (isPending) return <PageLoader />;
  if (isError || !leads) return <PageError />;
  const tracked = leads.filter((lead) => lead.application);
  if (!tracked.length) {
    return (
      <Empty
        title="Nothing applied to from Scout yet"
        hint="Press Mark as applied on a lead and it moves here."
      />
    );
  }

  return (
    <Stack gap="lg">
      {STATUSES.map((status) => {
        const group = tracked.filter((lead) => lead.application?.status === status.value);
        if (!group.length) return null;
        const meta = statusMeta(status.value);
        return (
          <Stack key={status.value} gap="sm">
            <Group gap="xs">
              <Title order={5}>{meta.label}</Title>
              <Badge color={meta.color} variant="light" radius="xl" className="tabular">
                {group.length}
              </Badge>
            </Group>
            {group.map((lead) => (
              <TrackedRow key={`${lead.run_id}-${lead.id}`} lead={lead} />
            ))}
          </Stack>
        );
      })}
    </Stack>
  );
}
