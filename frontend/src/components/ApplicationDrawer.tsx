import { Anchor, Button, Divider, Drawer, Group, Stack, Text, Timeline, Title } from '@mantine/core';
import { IconArrowRight, IconExternalLink } from '@tabler/icons-react';
import { Link } from 'react-router-dom';

import { ScoutBadge } from './ScoutBadge';
import { formatDateTime } from '../lib/format';
import { useDeleteApplication } from '../lib/queries';
import { statusMeta } from '../lib/statuses';
import type { Application } from '../lib/types';
import { ApplicationForm } from './ApplicationForm';
import { CommunicationsPanel } from './CommunicationsPanel';
import { CompanyResearchPanel } from './CompanyResearchPanel';
import { ConfirmDelete } from './ConfirmDelete';
import { StatusBadge } from './StatusBadge';

interface Props {
  application: Application | null;
  onClose: () => void;
}

export function ApplicationDrawer({ application, onClose }: Props) {
  const remove = useDeleteApplication();

  return (
    <Drawer
      opened={application !== null}
      onClose={onClose}
      position="right"
      size="lg"
      title={application ? `${application.company} · ${application.role_title}` : ''}
    >
      {application && (
        <Stack gap="md">
          <Group justify="space-between">
            <Group gap="xs">
              <StatusBadge status={application.status} />
              {application.from_scout && <ScoutBadge />}
            </Group>
            {application.job_url && (
              <Anchor href={application.job_url} target="_blank" rel="noopener noreferrer" size="sm">
                <Group gap={4} wrap="nowrap">
                  View posting <IconExternalLink size={14} />
                </Group>
              </Anchor>
            )}
          </Group>

          {application.status === 'rejected' && (
            <Button
              component={Link}
              to={`/insights/rejections/${application.id}`}
              variant="light"
              rightSection={<IconArrowRight size={16} />}
            >
              Open the rejection report
            </Button>
          )}

          <ApplicationForm
            key={`${application.id}-${application.updated_at}`}
            application={application}
          />

          <Divider />
          <Title order={5}>The company</Title>
          <CompanyResearchPanel applicationId={application.id} />

          <Divider />
          <Title order={5}>Communications</Title>
          <CommunicationsPanel applicationId={application.id} />

          <Divider />
          <Title order={5}>Timeline</Title>
          <Timeline
            active={application.events.length - 1}
            bulletSize={14}
            lineWidth={2}
            aria-label="Status history"
          >
            {application.events.map((event) => (
              <Timeline.Item key={event.occurred_at} title={statusMeta(event.status).label}>
                <Text size="xs" c="dimmed">
                  {formatDateTime(event.occurred_at)}
                </Text>
              </Timeline.Item>
            ))}
          </Timeline>

          <Divider />
          <Group justify="flex-end">
            <ConfirmDelete
              label="Delete application"
              message="This also removes its status history and communications."
              loading={remove.isPending}
              onConfirm={() => remove.mutate(application.id, { onSuccess: onClose })}
            />
          </Group>
        </Stack>
      )}
    </Drawer>
  );
}
