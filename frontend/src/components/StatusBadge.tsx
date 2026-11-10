import { Badge } from '@mantine/core';

import { statusMeta } from '../lib/statuses';

export function StatusBadge({ status }: { status: string }) {
  const { label, color } = statusMeta(status);
  return (
    <Badge variant="light" color={color} size="sm" radius="sm">
      {label}
    </Badge>
  );
}
