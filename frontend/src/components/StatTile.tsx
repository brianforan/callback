import { Paper, Text } from '@mantine/core';

/** One headline figure with its label and an optional line of context. */
export function StatTile({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <Paper withBorder p="md" radius="md">
      <Text size="xs" c="dimmed" tt="uppercase" fw={600}>
        {label}
      </Text>
      <Text fz={26} fw={700} className="tabular" lh={1.2} mt={4}>
        {value}
      </Text>
      {note && (
        <Text size="xs" c="dimmed" mt={2}>
          {note}
        </Text>
      )}
    </Paper>
  );
}
