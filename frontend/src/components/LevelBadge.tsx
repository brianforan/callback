import { Badge, Group, HoverCard, Stack, Text, Tooltip } from '@mantine/core';
import { IconInfoCircle } from '@tabler/icons-react';

import { fieldLabel, levelCode, levelColor, LEVELS } from '../lib/postings';
import type { PostingLevel } from '../lib/types';

function CodeBadge({ level }: { level: PostingLevel }) {
  return (
    <Badge
      variant="light"
      color={levelColor(level)}
      miw={38}
      fw={700}
      tt="none"
      className="tabular"
    >
      {levelCode(level)}
    </Badge>
  );
}

export function LevelBadge({ level }: { level: PostingLevel | null }) {
  if (!level) {
    return (
      <Text size="sm" c="dimmed">
        –
      </Text>
    );
  }
  return (
    <Tooltip label={fieldLabel(level)}>
      <span aria-label={fieldLabel(level)}>
        <CodeBadge level={level} />
      </span>
    </Tooltip>
  );
}

/** The Level column header, with the code key on hover. */
export function LevelHeader() {
  return (
    <HoverCard position="bottom-start" width={180} shadow="md" openDelay={120} withArrow>
      <HoverCard.Target>
        <Group gap={4} wrap="nowrap" style={{ cursor: 'help' }}>
          Level
          <IconInfoCircle size={14} stroke={1.8} aria-label="Level key" />
        </Group>
      </HoverCard.Target>
      <HoverCard.Dropdown>
        <Stack gap={6}>
          {LEVELS.map((level) => (
            <Group key={level} gap="sm" wrap="nowrap">
              <CodeBadge level={level} />
              <Text size="sm">{fieldLabel(level)}</Text>
            </Group>
          ))}
        </Stack>
      </HoverCard.Dropdown>
    </HoverCard>
  );
}
