import { Button, Group, NumberInput, Paper, Stack, Table, Text } from '@mantine/core';
import { useState } from 'react';

import { PageError, PageLoader } from '../components/PageState';
import { formatDateTime } from '../lib/format';
import { useAppConfig, useResetAppConfig, useUpdateAppConfig } from '../lib/queries';
import type { AppSetting } from '../lib/types';

function SettingRow({ setting }: { setting: AppSetting }) {
  const update = useUpdateAppConfig();
  const reset = useResetAppConfig();
  const [draft, setDraft] = useState<number | string>(setting.value);
  const value = typeof draft === 'number' ? draft : Number.NaN;
  const whole = setting.kind === 'int';
  const valid =
    Number.isFinite(value) &&
    value >= setting.minimum &&
    value <= setting.maximum &&
    (!whole || Number.isInteger(value));
  const busy = update.isPending || reset.isPending;

  return (
    <Table.Tr>
      <Table.Td>
        <Text size="sm">{setting.label}</Text>
        <Text size="xs" c="dimmed">
          {setting.help}
        </Text>
      </Table.Td>
      <Table.Td>
        <NumberInput
          size="xs"
          w={130}
          value={draft}
          onChange={setDraft}
          min={setting.minimum}
          max={setting.maximum}
          allowDecimal={!whole}
          decimalScale={whole ? 0 : 2}
          suffix={` ${setting.unit}`}
          error={valid ? undefined : `${setting.minimum} to ${setting.maximum}`}
          aria-label={setting.label}
        />
      </Table.Td>
      <Table.Td>
        <Text size="xs" c="dimmed">
          {setting.saved
            ? `Default ${setting.default}. Changed ${formatDateTime(setting.updated_at ?? '')}${
                setting.updated_by ? ` by ${setting.updated_by}` : ''
              }.`
            : 'Default'}
        </Text>
      </Table.Td>
      <Table.Td>
        <Group gap="xs" wrap="nowrap" justify="flex-end">
          <Button
            size="xs"
            disabled={!valid || value === setting.value || busy}
            loading={update.isPending}
            onClick={() => update.mutate({ key: setting.key, value })}
          >
            Save
          </Button>
          <Button
            size="xs"
            variant="default"
            disabled={!setting.saved || busy}
            loading={reset.isPending}
            onClick={() => reset.mutate(setting.key)}
          >
            Use default
          </Button>
        </Group>
      </Table.Td>
    </Table.Tr>
  );
}

/** The Admin panel's Settings tab: the limits that tune what Callback spends on the API. */
export function AdminSettingsTab() {
  const { data: settings, isPending, isError } = useAppConfig();
  if (isPending) return <PageLoader />;
  if (isError || !settings) return <PageError />;

  return (
    <Paper withBorder p="md" radius="md">
      <Stack gap="sm">
        <Text size="xs" c="dimmed">
          What Callback may spend on model calls, per user. A change takes effect at once, for
          everyone, with no deploy. Daily counts reset at midnight UTC. A user whose daily limits
          are lifted in Users isn't held to the daily ones.
        </Text>
        <Table.ScrollContainer minWidth={720}>
          <Table>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Setting</Table.Th>
                <Table.Th>Value</Table.Th>
                <Table.Th>Default</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {settings.map((setting) => (
                // Keyed by value too, so a saved or reset value starts a fresh draft.
                <SettingRow key={`${setting.key}:${setting.value}`} setting={setting} />
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Stack>
    </Paper>
  );
}
