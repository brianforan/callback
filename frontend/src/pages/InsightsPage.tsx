import { Stack, Tabs } from '@mantine/core';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';

import { PageTitle } from '../lib/sections';

export function InsightsPage() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const tab = pathname.endsWith('/postings')
    ? 'postings'
    : pathname.endsWith('/variants')
      ? 'variants'
      : pathname.includes('/rejections')
        ? 'rejections'
        : pathname.includes('/companies')
          ? 'companies'
          : 'resumes';

  return (
    <Stack>
      <PageTitle section="insights" />
      <Tabs value={tab} onChange={(value) => navigate(`/insights/${value}`)}>
        <Tabs.List>
          <Tabs.Tab value="resumes">Resumes</Tabs.Tab>
          <Tabs.Tab value="variants">Baselines</Tabs.Tab>
          <Tabs.Tab value="postings">Postings</Tabs.Tab>
          <Tabs.Tab value="rejections">Rejections</Tabs.Tab>
          <Tabs.Tab value="companies">Companies</Tabs.Tab>
        </Tabs.List>
      </Tabs>
      <Outlet />
    </Stack>
  );
}
