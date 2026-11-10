import { Badge, Group, Paper, Select, Stack, Switch, Table, Text, Tooltip } from '@mantine/core';

import { PageError, PageLoader } from '../components/PageState';
import { formatDay } from '../lib/format';
import { useAdminUsers, useMe, useUpdateAdminUser } from '../lib/queries';
import type { AdminUser, Role, User } from '../lib/types';

const ROLES: { value: Role; label: string }[] = [
  { value: 'member', label: 'Member' },
  { value: 'admin', label: 'Admin' },
];

/** Why a user's role can't be changed here, or null when it can. */
function roleLock(user: AdminUser, me: User | null | undefined): string | null {
  if (user.id === me?.id) return "You can't change your own role.";
  if (user.role === 'admin') {
    return "An admin's role can't be taken away here. On the server, run python -m app.cli set-role.";
  }
  return null;
}

function UserRow({ user, me }: { user: AdminUser; me: User | null | undefined }) {
  const update = useUpdateAdminUser();
  const lock = roleLock(user, me);
  const pending = update.isPending ? update.variables : undefined;
  const role = pending?.role ?? user.role;
  const plan = pending?.plan ?? user.plan;
  const unlimited = pending?.override_limits ?? user.override_limits;

  const roleSelect = (
    <Select
      size="xs"
      w={120}
      data={ROLES}
      value={role}
      allowDeselect={false}
      disabled={lock !== null || update.isPending}
      aria-label={`Role for ${user.email}`}
      onChange={(value) => value && update.mutate({ id: user.id, role: value as Role })}
    />
  );

  return (
    <Table.Tr>
      <Table.Td>
        <Group gap={6} wrap="nowrap">
          <Text size="sm" truncate title={user.email}>
            {user.email}
          </Text>
          {user.id === me?.id && (
            <Badge size="xs" variant="light" color="gray">
              You
            </Badge>
          )}
        </Group>
      </Table.Td>
      <Table.Td>
        {lock ? (
          <Tooltip label={lock} multiline w={240}>
            <div>{roleSelect}</div>
          </Tooltip>
        ) : (
          roleSelect
        )}
      </Table.Td>
      <Table.Td>
        <Switch
          checked={plan === 'premium'}
          disabled={update.isPending}
          label={plan === 'premium' ? 'Premium' : 'Free'}
          aria-label={`Premium for ${user.email}`}
          onChange={(event) =>
            update.mutate({ id: user.id, plan: event.currentTarget.checked ? 'premium' : 'free' })
          }
        />
      </Table.Td>
      <Table.Td>
        <Switch
          checked={unlimited}
          disabled={update.isPending}
          label={unlimited ? 'No daily limits' : 'Daily limits'}
          aria-label={`No daily limits for ${user.email}`}
          onChange={(event) =>
            update.mutate({ id: user.id, override_limits: event.currentTarget.checked })
          }
        />
      </Table.Td>
      <Table.Td className="tabular">{formatDay(user.created_at)}</Table.Td>
    </Table.Tr>
  );
}

/** The Admin panel's Users tab: everyone's role, plan and daily limits. */
export function AdminUsersTab() {
  const { data: me } = useMe();
  const { data: users, isPending, isError } = useAdminUsers();
  if (isPending) return <PageLoader />;
  if (isError || !users) return <PageError />;

  return (
    <Paper withBorder p="md" radius="md">
      <Stack gap="sm">
        <Text size="xs" c="dimmed">
          Admins see this panel and can make other admins. Taking an admin's role away is done on
          the server, with python -m app.cli set-role. Premium unlocks Scout. No daily limits
          lifts the per-day caps in Settings for that user, yours included. Changes take effect on
          the user's next page load.
        </Text>
        <Table.ScrollContainer minWidth={720}>
          <Table>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>User</Table.Th>
                <Table.Th>Role</Table.Th>
                <Table.Th>Plan</Table.Th>
                <Table.Th>Limits</Table.Th>
                <Table.Th>Joined</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {users.map((user) => (
                <UserRow key={user.id} user={user} me={me} />
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Stack>
    </Paper>
  );
}
