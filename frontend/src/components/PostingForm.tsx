import { Autocomplete, Button, Group, SimpleGrid, Stack, Textarea, TextInput } from '@mantine/core';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';

import { todayIso } from '../lib/format';
import { useCreatePosting } from '../lib/queries';
import { SOURCE_SUGGESTIONS } from '../lib/statuses';

const MAX_TEXT = 50_000;

function blankToNull(value: string): string | null {
  return value.trim() || null;
}

export function PostingForm({ onDone }: { onDone: () => void }) {
  const create = useCreatePosting();
  const form = useForm({
    initialValues: {
      company: '',
      title: '',
      url: '',
      location: '',
      source: '',
      referrer: '',
      seen_on: todayIso(),
      raw_text: '',
    },
    validate: {
      company: (value) => (value.trim() ? null : 'Company is required'),
      title: (value) => (value.trim() ? null : 'Title is required'),
      seen_on: (value) => (value ? null : 'Pick a date'),
      url: (value) =>
        !value.trim() || /^https?:\/\//i.test(value.trim()) ? null : 'Must start with http(s)://',
      raw_text: (value) => (value.length <= MAX_TEXT ? null : 'Posting text is too long'),
    },
  });

  return (
    <form
      onSubmit={form.onSubmit((values) =>
        create.mutate(
          {
            company: values.company.trim(),
            title: values.title.trim(),
            url: blankToNull(values.url),
            location: blankToNull(values.location),
            source: blankToNull(values.source),
            referrer: blankToNull(values.referrer),
            raw_text: blankToNull(values.raw_text),
            seen_on: values.seen_on,
          },
          {
            onSuccess: () => {
              notifications.show({ color: 'green', message: `Saved ${values.company.trim()}` });
              onDone();
            },
          },
        ),
      )}
    >
      <Stack gap="sm">
        <SimpleGrid cols={{ base: 1, xs: 2 }} spacing="sm">
          <TextInput label="Company" required data-autofocus {...form.getInputProps('company')} />
          <TextInput label="Title" required {...form.getInputProps('title')} />
          <TextInput
            label="Posting URL"
            placeholder="The company's own careers link, if you have it"
            {...form.getInputProps('url')}
          />
          <TextInput label="Seen on" type="date" required {...form.getInputProps('seen_on')} />
          <Autocomplete
            label="Source"
            placeholder="Where you found it"
            data={SOURCE_SUGGESTIONS}
            {...form.getInputProps('source')}
          />
          <TextInput label="Location" placeholder="Remote, NYC…" {...form.getInputProps('location')} />
          <TextInput label="Referred by" {...form.getInputProps('referrer')} />
        </SimpleGrid>
        <Textarea
          label="Posting text"
          description="Paste the full posting. Keep its headings, because they separate required skills from nice-to-haves."
          autosize
          minRows={6}
          maxRows={16}
          {...form.getInputProps('raw_text')}
        />
        <Group justify="flex-end">
          <Button type="submit" loading={create.isPending}>
            Save posting
          </Button>
        </Group>
      </Stack>
    </form>
  );
}
