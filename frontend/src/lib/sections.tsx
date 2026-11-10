import { Group, ThemeIcon, Title } from '@mantine/core';
import {
  IconBriefcase,
  IconChartBar,
  IconFileText,
  IconLayoutDashboard,
  IconShieldLock,
  IconSparkles,
  IconWorldSearch,
} from '@tabler/icons-react';

// Every section shares the app's one accent. Color is left for data that means something, like a status.
export const SECTIONS = {
  dashboard: { to: '/', label: 'Dashboard', icon: IconLayoutDashboard, color: 'moss' },
  applications: {
    to: '/applications',
    label: 'Applications',
    icon: IconBriefcase,
    color: 'moss',
  },
  listings: { to: '/listings', label: 'Job listings', icon: IconWorldSearch, color: 'moss' },
  scout: { to: '/scout', label: 'Scout', icon: IconSparkles, color: 'moss' },
  resumes: { to: '/resumes', label: 'Resumes', icon: IconFileText, color: 'moss' },
  insights: { to: '/insights', label: 'Insights', icon: IconChartBar, color: 'moss' },
  // Shown only to admins: users, their roles and plans, and spend.
  admin: { to: '/admin', label: 'Admin', icon: IconShieldLock, color: 'moss' },
} as const;

export type SectionKey = keyof typeof SECTIONS;

export function PageTitle({ section }: { section: SectionKey }) {
  const { label, icon: Icon, color } = SECTIONS[section];
  return (
    <Group gap="sm" wrap="nowrap">
      <ThemeIcon variant="light" color={color} size="lg" radius="md">
        <Icon size={20} stroke={1.7} />
      </ThemeIcon>
      <Title order={3}>{label}</Title>
    </Group>
  );
}
