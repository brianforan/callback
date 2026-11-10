import { Badge, Tooltip } from '@mantine/core';
import { IconSparkles } from '@tabler/icons-react';

/** Marks a posting or application Scout found, wherever it shows in the app. */
export function ScoutBadge() {
  return (
    <Tooltip label="Found by Scout" withArrow>
      <Badge
        size="xs"
        variant="light"
        tt="none"
        leftSection={<IconSparkles size={11} />}
        style={{ flexShrink: 0 }}
      >
        Scout
      </Badge>
    </Tooltip>
  );
}
