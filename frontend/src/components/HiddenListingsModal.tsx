import { Anchor, Button, Divider, Group, Modal, Stack, Text, Title } from '@mantine/core';
import { IconEye } from '@tabler/icons-react';

import { formatDateTime } from '../lib/format';
import { useHiddenListings, useUnhideListing } from '../lib/queries';
import type { HiddenListing } from '../lib/types';
import { Empty, PageError, PageLoader } from './PageState';

function HiddenRow({ row }: { row: HiddenListing }) {
  const unhide = useUnhideListing();
  return (
    <Group justify="space-between" wrap="nowrap" gap="sm">
      <Stack gap={0} style={{ minWidth: 0 }}>
        {row.kind === 'company' ? (
          <Text size="sm" fw={500} truncate>
            {row.company}
          </Text>
        ) : (
          <Text size="sm" truncate>
            <Text span fw={500} inherit>
              {row.company}
            </Text>{' '}
            ·{' '}
            {row.url ? (
              <Anchor href={row.url} target="_blank" rel="noopener noreferrer" inherit>
                {row.title}
              </Anchor>
            ) : (
              row.title
            )}
          </Text>
        )}
        <Text size="xs" c="dimmed">
          Hidden {formatDateTime(row.created_at)}
        </Text>
      </Stack>
      <Button
        size="compact-xs"
        variant="default"
        leftSection={<IconEye size={12} />}
        loading={unhide.isPending}
        onClick={() => unhide.mutate(row.id)}
      >
        Unhide
      </Button>
    </Group>
  );
}

function Section({ title, hint, rows }: { title: string; hint: string; rows: HiddenListing[] }) {
  return (
    <Stack gap="xs">
      <Stack gap={0}>
        <Title order={5}>{title}</Title>
        <Text size="xs" c="dimmed">
          {hint}
        </Text>
      </Stack>
      {rows.length ? (
        rows.map((row) => <HiddenRow key={row.id} row={row} />)
      ) : (
        <Text size="sm" c="dimmed">
          None.
        </Text>
      )}
    </Stack>
  );
}

export function HiddenListingsModal({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  const hidden = useHiddenListings();
  const rows = hidden.data ?? [];
  return (
    <Modal opened={opened} onClose={onClose} title="Hidden from job listings" size="lg">
      {hidden.isPending && <PageLoader />}
      {hidden.isError && <PageError />}
      {hidden.data &&
        (rows.length ? (
          <Stack gap="md">
            <Section
              title="Companies"
              hint="Every job at these companies is left out, on every board."
              rows={rows.filter((row) => row.kind === 'company')}
            />
            <Divider />
            <Section
              title="Jobs you won't apply to"
              hint="Also hidden when the same company posts the same title on another board."
              rows={rows.filter((row) => row.kind === 'listing')}
            />
          </Stack>
        ) : (
          <Empty
            title="Nothing hidden"
            hint="Use the hide button on a listing to leave out one job or a whole company."
          />
        ))}
    </Modal>
  );
}
