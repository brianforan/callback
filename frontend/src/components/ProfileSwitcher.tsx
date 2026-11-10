import { Button, Checkbox, Group, Menu, Modal, Stack, Text, TextInput } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconCheck, IconPlus, IconSelector, IconSettings, IconUserCircle } from '@tabler/icons-react';
import { useState, type FormEvent } from 'react';

import {
  useActivateProfile,
  useCreateProfile,
  useDeleteProfile,
  useProfiles,
  useRenameProfile,
} from '../lib/queries';
import type { Profile } from '../lib/types';
import { ConfirmDelete } from './ConfirmDelete';

const MAX_NAME = 60;

function ProfileRow({ profile, only }: { profile: Profile; only: boolean }) {
  const [name, setName] = useState(profile.name);
  const rename = useRenameProfile();
  const remove = useDeleteProfile();
  const changed = name.trim() !== profile.name && name.trim().length > 0;

  const save = (event: FormEvent) => {
    event.preventDefault();
    if (changed) rename.mutate({ id: profile.id, name: name.trim() });
  };

  return (
    <form onSubmit={save}>
      <Group gap="xs" wrap="nowrap" align="flex-end">
        <TextInput
          size="xs"
          flex={1}
          aria-label={`Name of ${profile.name}`}
          value={name}
          maxLength={MAX_NAME}
          onChange={(event) => setName(event.currentTarget.value)}
        />
        <Button size="xs" variant="default" type="submit" disabled={!changed} loading={rename.isPending}>
          Rename
        </Button>
        <ConfirmDelete
          label="Delete"
          message="Delete this profile with its resumes, fact sheet and Scout settings? Applications and saved jobs stay."
          loading={remove.isPending}
          disabled={only}
          onConfirm={() => remove.mutate(profile.id)}
        />
      </Group>
    </form>
  );
}

function ManageProfiles({ profiles }: { profiles: Profile[] }) {
  const [name, setName] = useState('');
  const [softwareSkills, setSoftwareSkills] = useState(false);
  const create = useCreateProfile();

  const add = (event: FormEvent) => {
    event.preventDefault();
    if (!name.trim()) return;
    create.mutate(
      { name: name.trim(), softwareSkills },
      {
        onSuccess: () => {
          setName('');
          setSoftwareSkills(false);
        },
      },
    );
  };

  return (
    <Stack>
      <Text size="sm" c="dimmed">
        Each profile has its own resumes, skills, fact sheet and Scout settings. Applications and
        saved jobs are shared, and you can filter them by profile.
      </Text>
      {profiles.map((profile) => (
        <ProfileRow key={profile.id} profile={profile} only={profiles.length === 1} />
      ))}
      <form onSubmit={add}>
        <Group gap="xs" wrap="nowrap" align="flex-end">
          <TextInput
            size="xs"
            flex={1}
            label="New profile"
            placeholder="e.g. Data analyst"
            value={name}
            maxLength={MAX_NAME}
            onChange={(event) => setName(event.currentTarget.value)}
          />
          <Button
            size="xs"
            type="submit"
            leftSection={<IconPlus size={14} />}
            disabled={!name.trim()}
            loading={create.isPending}
          >
            Add and switch
          </Button>
        </Group>
        <Checkbox
          mt="xs"
          size="xs"
          label="Use the built-in software skills"
          description="For a software job search. Add your own skills on Resumes → Skills."
          checked={softwareSkills}
          onChange={(event) => setSoftwareSkills(event.currentTarget.checked)}
        />
      </form>
    </Stack>
  );
}

/** Shows the profile in use and switches between them. Sits at the foot of the navbar. */
export function ProfileSwitcher() {
  const { data } = useProfiles();
  const activate = useActivateProfile();
  const [managing, manage] = useDisclosure(false);
  if (!data) return null;
  const active = data.profiles.find((profile) => profile.id === data.active_id);

  return (
    <>
      <Menu position="top-start" width={200} shadow="md">
        <Menu.Target>
          <Button
            variant="light"
            color="gray"
            size="xs"
            fullWidth
            justify="space-between"
            leftSection={<IconUserCircle size={16} />}
            rightSection={<IconSelector size={14} />}
            loading={activate.isPending}
            aria-label="Switch profile"
          >
            <Text size="xs" fw={600} truncate>
              {active?.name ?? 'Profile'}
            </Text>
          </Button>
        </Menu.Target>
        <Menu.Dropdown>
          <Menu.Label>Profiles</Menu.Label>
          {data.profiles.map((profile) => (
            <Menu.Item
              key={profile.id}
              rightSection={profile.id === data.active_id ? <IconCheck size={14} /> : null}
              onClick={() => profile.id !== data.active_id && activate.mutate(profile.id)}
            >
              {profile.name}
            </Menu.Item>
          ))}
          <Menu.Divider />
          <Menu.Item leftSection={<IconSettings size={14} />} onClick={manage.open}>
            Manage profiles
          </Menu.Item>
        </Menu.Dropdown>
      </Menu>
      <Modal opened={managing} onClose={manage.close} title="Profiles">
        <ManageProfiles profiles={data.profiles} />
      </Modal>
    </>
  );
}
