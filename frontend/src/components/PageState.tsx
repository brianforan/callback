import { Center, Loader, Stack, Text, ThemeIcon } from '@mantine/core';
import { IconCloudOff, IconInbox } from '@tabler/icons-react';
import type { ReactNode } from 'react';

export function PageLoader() {
  return (
    <Center h={320}>
      <Loader />
    </Center>
  );
}

export function PageError() {
  return <Empty icon={<IconCloudOff size={20} />} title="Couldn't load this page" hint="Check that the API is running, then refresh." />;
}

interface EmptyProps {
  title: string;
  hint?: string;
  icon?: ReactNode;
}

export function Empty({ title, hint, icon }: EmptyProps) {
  return (
    <Center py="xl">
      <Stack align="center" gap={6} maw={360}>
        <ThemeIcon variant="light" color="gray" size="lg" radius="xl">
          {icon ?? <IconInbox size={20} />}
        </ThemeIcon>
        <Text fw={600} size="sm" ta="center">
          {title}
        </Text>
        {hint && (
          <Text size="xs" c="dimmed" ta="center">
            {hint}
          </Text>
        )}
      </Stack>
    </Center>
  );
}
