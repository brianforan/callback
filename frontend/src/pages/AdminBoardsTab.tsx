import { Paper, SimpleGrid, Stack, Table, Text } from '@mantine/core';

import { PageError, PageLoader } from '../components/PageState';
import { StatTile } from '../components/StatTile';
import { formatDay } from '../lib/format';
import { useAdminCatalog } from '../lib/queries';

/** The Admin panel's Boards tab: where the Companies catalog's listings come from. */
export function AdminBoardsTab() {
  const { data: catalog, isPending, isError } = useAdminCatalog();
  if (isPending) return <PageLoader />;
  if (isError || !catalog) return <PageError />;

  return (
    <Stack>
      <SimpleGrid cols={{ base: 1, xs: 3 }} spacing="md">
        <StatTile label="Companies" value={catalog.companies.toLocaleString()} />
        <StatTile label="Agencies" value={catalog.agencies.toLocaleString()} />
        <StatTile label="Jobs" value={catalog.jobs.toLocaleString()} note="Each counted once" />
      </SimpleGrid>
      <Paper withBorder p="md" radius="md">
        <Stack gap="sm">
          <Text size="xs" c="dimmed">
            Every listing Job listings and Scout see is recorded once per job, with each place
            it's listed. Kept counts a Scout lead once for each place its job is listed, so a
            source that's often kept is worth searching. Nothing here turns a board on or off.
          </Text>
          {catalog.sources.length === 0 ? (
            <Text size="sm" c="dimmed">
              Nothing recorded yet. Search Job listings or run Scout.
            </Text>
          ) : (
            <Table.ScrollContainer minWidth={560}>
              <Table>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Source</Table.Th>
                    <Table.Th ta="right">Listings</Table.Th>
                    <Table.Th ta="right">Companies</Table.Th>
                    <Table.Th ta="right">Kept</Table.Th>
                    <Table.Th>Last kept</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {catalog.sources.map((source) => (
                    <Table.Tr key={source.source}>
                      <Table.Td>{source.name}</Table.Td>
                      <Table.Td ta="right" className="tabular">
                        {source.listings_recorded.toLocaleString()}
                      </Table.Td>
                      <Table.Td ta="right" className="tabular">
                        {source.companies.toLocaleString()}
                      </Table.Td>
                      <Table.Td ta="right" className="tabular">
                        {source.kept.toLocaleString()}
                      </Table.Td>
                      <Table.Td className="tabular">
                        {source.last_kept_at ? formatDay(source.last_kept_at) : '—'}
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          )}
        </Stack>
      </Paper>
    </Stack>
  );
}
