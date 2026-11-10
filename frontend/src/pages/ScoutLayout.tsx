import { Badge, Group, Stack, Tabs } from '@mantine/core';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';

import { useScoutLeads } from '../lib/queries';
import { PageTitle, SECTIONS } from '../lib/sections';

// In the order of the work: set up and run a search, review its leads, track what you apply to,
// then look back over past runs.
const TABS = [
  { value: 'search', label: 'Search' },
  { value: 'leads', label: 'Leads' },
  { value: 'tracking', label: 'Tracking' },
  { value: 'runs', label: 'Runs' },
] as const;

/**
 * Scout as its own module: leads waiting on you, the jobs you've applied to from it, and
 * the search itself. Postings and applications are the app's own, so they also show elsewhere.
 */
export function ScoutLayout() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const { data: leads } = useScoutLeads();
  const tab = TABS.find((item) => pathname.endsWith(`/${item.value}`))?.value ?? 'search';
  const counts: Record<string, number | undefined> = {
    leads: leads?.filter((lead) => !lead.application && lead.state === 'new').length,
    tracking: leads?.filter((lead) => lead.application).length,
  };

  return (
    <Stack>
      <Group gap="sm">
        <PageTitle section="scout" />
        <Badge variant="light" color={SECTIONS.scout.color}>
          Premium
        </Badge>
      </Group>
      <Tabs value={tab} onChange={(value) => navigate(`/scout/${value}`)}>
        <Tabs.List>
          {TABS.map((item) => (
            <Tabs.Tab
              key={item.value}
              value={item.value}
              rightSection={
                counts[item.value] ? (
                  <Badge size="xs" variant="light" radius="xl" className="tabular">
                    {counts[item.value]}
                  </Badge>
                ) : null
              }
            >
              {item.label}
            </Tabs.Tab>
          ))}
        </Tabs.List>
      </Tabs>
      <Outlet />
    </Stack>
  );
}
