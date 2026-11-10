import { Alert, Button, Group, Text } from '@mantine/core';

/** Says unsaved fact sheet edits from another profile are kept, and lets them be thrown away. */
export function StrandedDraftNotice({
  names,
  onDiscard,
}: {
  names: string[];
  onDiscard: () => void;
}) {
  return (
    <Alert color="yellow" variant="light" p="xs">
      <Group justify="space-between" gap="sm">
        <Text size="sm">
          Unsaved edits to the {names.length ? names.join(' and ') : 'other profile'} fact sheet
          are kept in this tab. Switch back to that profile to see and save them.
        </Text>
        <Button size="compact-xs" variant="default" onClick={onDiscard}>
          Discard them
        </Button>
      </Group>
    </Alert>
  );
}
