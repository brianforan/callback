import {
  Badge,
  Button,
  Card,
  Group,
  Stack,
  Switch,
  TagsInput,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { IconPencil, IconPlus } from '@tabler/icons-react';
import { useState, type FormEvent } from 'react';

import {
  useAddSkill,
  useDeleteSkill,
  useEditSkill,
  useSetSoftwareSkills,
  useSkills,
} from '../lib/queries';
import type { SkillTerm } from '../lib/types';
import { ConfirmDelete } from './ConfirmDelete';
import { PageError, PageLoader } from './PageState';

const MAX_NAME = 80;
const MAX_ALIASES = 10;

function postingsLabel(count: number) {
  if (!count) return 'In no saved postings yet';
  return count === 1 ? 'In 1 saved posting' : `In ${count} saved postings`;
}

/** A skill's name and other spellings, for adding one or editing it. */
function TermForm({
  initial,
  submitLabel,
  loading,
  onSubmit,
  onCancel,
}: {
  initial?: SkillTerm;
  submitLabel: string;
  loading: boolean;
  onSubmit: (name: string, aliases: string[], reset: () => void) => void;
  onCancel?: () => void;
}) {
  const [name, setName] = useState(initial?.name ?? '');
  const [aliases, setAliases] = useState<string[]>(initial?.aliases ?? []);
  const reset = () => {
    setName('');
    setAliases([]);
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (name.trim()) onSubmit(name.trim(), aliases, reset);
  };

  return (
    <form onSubmit={submit}>
      <Group gap="xs" align="flex-end" wrap="wrap">
        <TextInput
          size="xs"
          w={220}
          label={initial ? undefined : 'Skill'}
          aria-label={initial ? `Name of ${initial.name}` : undefined}
          placeholder="e.g. Accounts Payable"
          value={name}
          maxLength={MAX_NAME}
          onChange={(event) => setName(event.currentTarget.value)}
        />
        <TagsInput
          size="xs"
          miw={220}
          flex={1}
          label={initial ? undefined : 'Other spellings (optional)'}
          aria-label={initial ? `Other spellings of ${initial.name}` : undefined}
          placeholder={aliases.length ? '' : 'e.g. AP, then Enter'}
          value={aliases}
          onChange={setAliases}
          maxTags={MAX_ALIASES}
          clearable
        />
        <Button
          size="xs"
          type="submit"
          leftSection={initial ? undefined : <IconPlus size={14} />}
          disabled={!name.trim()}
          loading={loading}
        >
          {submitLabel}
        </Button>
        {onCancel && (
          <Button size="xs" variant="default" onClick={onCancel}>
            Cancel
          </Button>
        )}
      </Group>
    </form>
  );
}

function TermRow({ term }: { term: SkillTerm }) {
  const [editing, setEditing] = useState(false);
  const edit = useEditSkill();
  const remove = useDeleteSkill();

  if (editing) {
    return (
      <TermForm
        initial={term}
        submitLabel="Save"
        loading={edit.isPending}
        onSubmit={(name, aliases) =>
          edit.mutate({ id: term.id, name, aliases }, { onSuccess: () => setEditing(false) })
        }
        onCancel={() => setEditing(false)}
      />
    );
  }

  return (
    <Group justify="space-between" wrap="nowrap" gap="sm">
      <Stack gap={2} style={{ minWidth: 0 }}>
        <Group gap={6} wrap="wrap">
          <Text size="sm" fw={500}>
            {term.name}
          </Text>
          {term.aliases.map((alias) => (
            <Badge key={alias} size="sm" variant="light" color="gray">
              {alias}
            </Badge>
          ))}
        </Group>
        <Text size="xs" c="dimmed">
          {postingsLabel(term.postings)}
        </Text>
      </Stack>
      <Group gap="xs" wrap="nowrap">
        <Button
          size="compact-xs"
          variant="default"
          leftSection={<IconPencil size={12} />}
          onClick={() => setEditing(true)}
        >
          Edit
        </Button>
        <ConfirmDelete
          label="Delete"
          message={`Delete ${term.name}? Saved postings stop listing it once they're read again.`}
          loading={remove.isPending}
          onConfirm={() => remove.mutate(term.id)}
        />
      </Group>
    </Group>
  );
}

/** The active profile's skills: the built-in software list and the user's own terms. */
export function SkillsPanel() {
  const skills = useSkills();
  const setSoftware = useSetSoftwareSkills();
  const add = useAddSkill();

  if (skills.isPending) return <PageLoader />;
  if (skills.isError || !skills.data) return <PageError />;
  const { software_skills, software_count, terms, max_terms } = skills.data;

  return (
    <Stack>
      <Text size="sm" c="dimmed">
        Skills are what match scores, missing skills and Insights look for in postings and
        resumes. Add the skills of your field, and the other ways postings write them, such as AP
        for Accounts Payable. New postings use them at once; saved ones are read again within a
        minute or two.
      </Text>

      <Card>
        <Switch
          checked={software_skills}
          disabled={setSoftware.isPending}
          onChange={(event) => setSoftware.mutate(event.currentTarget.checked)}
          label="Use the built-in software skills"
          description={`${software_count} languages, frameworks, databases, clouds and tools, such as Python, React, PostgreSQL and AWS, with their usual spellings.`}
        />
      </Card>

      <Card>
        <Stack gap="md">
          <Group justify="space-between" align="baseline">
            <Title order={5}>Your skills</Title>
            <Text size="xs" c="dimmed">
              {terms.length} of {max_terms}
            </Text>
          </Group>
          <TermForm
            submitLabel="Add"
            loading={add.isPending}
            onSubmit={(name, aliases, reset) => add.mutate({ name, aliases }, { onSuccess: reset })}
          />
          {terms.length ? (
            terms.map((term) => <TermRow key={term.id} term={term} />)
          ) : (
            <Text size="sm" c="dimmed">
              None yet.{' '}
              {software_skills
                ? 'The built-in software skills are in use.'
                : 'Add a few, or turn on the built-in software skills, so jobs can be scored.'}
            </Text>
          )}
        </Stack>
      </Card>
    </Stack>
  );
}
