import { Stack, Tabs } from '@mantine/core';
import { Navigate, Outlet, useLocation, useNavigate } from 'react-router-dom';

import { useMe } from '../lib/queries';
import { PageTitle } from '../lib/sections';

/** Admins only: users with their roles, plans and limits, what Callback's model use costs,
 * where the Companies catalog's listings come from, and the settings that tune API spend. */
export function AdminPage() {
  const { data: user } = useMe();
  const { pathname } = useLocation();
  const navigate = useNavigate();
  // The API refuses non-admins too; this just keeps them off an empty page.
  if (user && !user.is_admin) return <Navigate to="/" replace />;
  const tab = pathname.endsWith('/spend')
    ? 'spend'
    : pathname.endsWith('/boards')
      ? 'boards'
      : pathname.endsWith('/settings')
        ? 'settings'
        : 'users';

  return (
    <Stack>
      <PageTitle section="admin" />
      <Tabs value={tab} onChange={(value) => navigate(`/admin/${value}`)}>
        <Tabs.List>
          <Tabs.Tab value="users">Users</Tabs.Tab>
          <Tabs.Tab value="spend">Spend</Tabs.Tab>
          <Tabs.Tab value="boards">Boards</Tabs.Tab>
          <Tabs.Tab value="settings">Settings</Tabs.Tab>
        </Tabs.List>
      </Tabs>
      <Outlet />
    </Stack>
  );
}
