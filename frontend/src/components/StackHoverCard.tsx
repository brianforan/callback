import { Badge, Group, HoverCard, Stack, Text } from '@mantine/core';
import { IconStack2 } from '@tabler/icons-react';

import type { PostingTechnology } from '../lib/types';

const GROUPS: {
  requirement: PostingTechnology['requirement'];
  label: string;
  variant: 'light' | 'default';
  color?: string;
}[] = [
  { requirement: 'required', label: 'Required', variant: 'light', color: 'indigo' },
  { requirement: 'nice_to_have', label: 'Nice to have', variant: 'light', color: 'gray' },
  { requirement: 'mentioned', label: 'Mentioned', variant: 'default' },
];

/** A count in the row that opens the whole stack, grouped by how strongly the job asks for it. */
export function StackHoverCard({ technologies }: { technologies: PostingTechnology[] }) {
  if (!technologies.length) {
    return (
      <Text size="sm" c="dimmed">
        –
      </Text>
    );
  }
  const required = technologies.filter((item) => item.requirement === 'required').length;

  return (
    <HoverCard position="bottom-start" width={320} shadow="md" openDelay={120} withArrow>
      <HoverCard.Target>
        <Badge
          className="stack-trigger tabular"
          variant="light"
          color="gray"
          size="lg"
          tt="none"
          fw={600}
          leftSection={<IconStack2 size={14} stroke={1.8} />}
          aria-label={`${technologies.length} technologies, ${required} required`}
        >
          {technologies.length}
          {required > 0 && (
            <Text span inherit c="indigo" ml={6}>
              {required} req
            </Text>
          )}
        </Badge>
      </HoverCard.Target>
      <HoverCard.Dropdown>
        <Stack gap="sm">
          {GROUPS.map(({ requirement, label, variant, color }) => {
            const members = technologies.filter((item) => item.requirement === requirement);
            if (!members.length) return null;
            return (
              <Stack key={requirement} gap={6}>
                <Text size="xs" fw={650} c="dimmed" tt="uppercase" lts="0.04em">
                  {label} · {members.length}
                </Text>
                <Group gap={4}>
                  {members.map((technology) => (
                    <Badge
                      key={technology.name}
                      size="sm"
                      variant={variant}
                      color={color}
                      tt="none"
                      fw={500}
                    >
                      {technology.name}
                    </Badge>
                  ))}
                </Group>
              </Stack>
            );
          })}
        </Stack>
      </HoverCard.Dropdown>
    </HoverCard>
  );
}
