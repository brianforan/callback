import { Button, Group, Popover, Text } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconTrash } from '@tabler/icons-react';

interface Props {
  label: string;
  message: string;
  loading?: boolean;
  disabled?: boolean;
  onConfirm: () => void;
}

export function ConfirmDelete({ label, message, loading, disabled, onConfirm }: Props) {
  const [opened, { close, toggle }] = useDisclosure(false);

  return (
    <Popover opened={opened} onChange={(next) => !next && close()} withArrow shadow="md" width={240}>
      <Popover.Target>
        <Button
          size="xs"
          variant="subtle"
          color="red"
          disabled={disabled}
          leftSection={<IconTrash size={14} />}
          onClick={toggle}
        >
          {label}
        </Button>
      </Popover.Target>
      <Popover.Dropdown>
        <Text size="sm" mb="xs">
          {message}
        </Text>
        <Group justify="flex-end" gap="xs">
          <Button size="xs" variant="default" onClick={close}>
            Cancel
          </Button>
          <Button
            size="xs"
            color="red"
            loading={loading}
            onClick={() => {
              onConfirm();
              close();
            }}
          >
            Delete
          </Button>
        </Group>
      </Popover.Dropdown>
    </Popover>
  );
}
