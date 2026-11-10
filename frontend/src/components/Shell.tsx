import {
  ActionIcon,
  AppShell,
  Badge,
  Burger,
  Button,
  Center,
  Group,
  Modal,
  NavLink,
  Stack,
  Text,
  ThemeIcon,
  Tooltip,
  useComputedColorScheme,
  useMantineColorScheme,
} from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import {
  IconCrown,
  IconLogout,
  IconMoon,
  IconPhoneIncoming,
  IconPlus,
  IconSun,
} from '@tabler/icons-react';
import type { ReactNode } from 'react';
import { NavLink as RouterLink, useLocation } from 'react-router-dom';

import { useApplications, useLogout, usePostings, useResumes } from '../lib/queries';
import { SECTIONS } from '../lib/sections';
import type { User } from '../lib/types';
import { ApplicationForm } from './ApplicationForm';
import { FocusProvider } from './Focus';
import { ProfileSwitcher } from './ProfileSwitcher';

const LINKS = Object.values(SECTIONS);

export function Shell({ user, children }: { user: User; children: ReactNode }) {
  const [navOpen, nav] = useDisclosure(false);
  const [adding, add] = useDisclosure(false);
  const { pathname } = useLocation();
  const logout = useLogout();
  const { setColorScheme } = useMantineColorScheme();
  const scheme = useComputedColorScheme('light');
  // Job listings counts the jobs you've tracked, since its live results have no standing count.
  const counts: Partial<Record<string, number>> = {
    [SECTIONS.applications.to]: useApplications().data?.length,
    [SECTIONS.listings.to]: usePostings().data?.length,
    [SECTIONS.resumes.to]: useResumes().data?.length,
  };

  return (
    <AppShell
      header={{ height: 56 }}
      navbar={{ width: 220, breakpoint: 'sm', collapsed: { mobile: !navOpen } }}
      padding="md"
    >
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between" wrap="nowrap">
          <Group gap="xs" wrap="nowrap">
            <Burger
              opened={navOpen}
              onClick={nav.toggle}
              hiddenFrom="sm"
              size="sm"
              aria-label="Toggle navigation"
            />
            <ThemeIcon size="md" radius="md">
              <IconPhoneIncoming size={16} />
            </ThemeIcon>
            <Text fw={700}>Callback</Text>
          </Group>
          <Button size="xs" leftSection={<IconPlus size={14} />} onClick={add.open}>
            Log application
          </Button>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p="xs">
        <Stack justify="space-between" h="100%">
          <Stack gap={2}>
            {LINKS.filter(({ to }) => to !== SECTIONS.admin.to || user.is_admin).map(
              ({ to, label, icon: Icon, color }) => {
              const active = to === '/' ? pathname === '/' : pathname.startsWith(to);
              const count = counts[to];
              return (
                <NavLink
                  key={to}
                  component={RouterLink}
                  to={to}
                  label={label}
                  color={color}
                  active={active}
                  fw={active ? 600 : 500}
                  leftSection={
                    <ThemeIcon
                      variant={active ? 'filled' : 'light'}
                      color={color}
                      size="md"
                      radius="md"
                    >
                      <Icon size={16} stroke={1.8} />
                    </ThemeIcon>
                  }
                  rightSection={
                    // One fixed-width, centred slot so the crown and the count badges share a column.
                    <Center w={36}>
                      {to === SECTIONS.scout.to ? (
                        // A crown instead of a word, so the label keeps its full width.
                        <Tooltip label="Premium" position="right">
                          <ThemeIcon
                            variant="transparent"
                            color={color}
                            size="sm"
                            aria-label="Premium feature"
                          >
                            <IconCrown size={14} stroke={1.8} />
                          </ThemeIcon>
                        </Tooltip>
                      ) : count === undefined ? null : (
                        <Badge
                          size="sm"
                          variant={active ? 'filled' : 'light'}
                          color={color}
                          radius="xl"
                          className="tabular"
                          aria-label={`${count} ${label.toLowerCase()}`}
                        >
                          {count}
                        </Badge>
                      )}
                    </Center>
                  }
                  onClick={nav.close}
                />
              );
            })}
          </Stack>
          <Stack gap="xs" px="xs" pb="xs">
            <ProfileSwitcher />
            <Group justify="space-between" wrap="nowrap">
              <Text size="xs" c="dimmed" truncate title={user.email}>
                {user.email}
              </Text>
              <Group gap={4} wrap="nowrap">
                <Tooltip label={scheme === 'dark' ? 'Light mode' : 'Dark mode'}>
                  <ActionIcon
                    variant="subtle"
                    color="gray"
                    aria-label="Toggle color scheme"
                    onClick={() => setColorScheme(scheme === 'dark' ? 'light' : 'dark')}
                  >
                    {scheme === 'dark' ? <IconSun size={16} /> : <IconMoon size={16} />}
                  </ActionIcon>
                </Tooltip>
                <Tooltip label="Sign out">
                  <ActionIcon
                    variant="subtle"
                    color="gray"
                    aria-label="Sign out"
                    loading={logout.isPending}
                    onClick={() => logout.mutate()}
                  >
                    <IconLogout size={16} />
                  </ActionIcon>
                </Tooltip>
              </Group>
            </Group>
          </Stack>
        </Stack>
      </AppShell.Navbar>

      <AppShell.Main className="app-main">
        <FocusProvider>{children}</FocusProvider>
      </AppShell.Main>

      <Modal opened={adding} onClose={add.close} title="Log application" size="lg">
        <ApplicationForm onDone={add.close} />
      </Modal>
    </AppShell>
  );
}
