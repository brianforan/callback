import {
  Button,
  Group,
  Modal,
  MultiSelect,
  NumberInput,
  Stack,
  Switch,
  TagsInput,
  Text,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useEffect, useMemo, useState } from 'react';

import { LEVEL_OPTIONS, ROLE_FAMILY_OPTIONS } from '../lib/postings';
import { usePostings, useSetVariant } from '../lib/queries';
import type { PostingLevel, Resume, VariantRules } from '../lib/types';

const MAX_SKILLS = 30;

interface VariantRulesModalProps {
  resume: Resume | null;
  onClose: () => void;
}

export function VariantRulesModal({ resume, onClose }: VariantRulesModalProps) {
  const save = useSetVariant();
  const { data: postings } = usePostings();
  const [isVariant, setIsVariant] = useState(false);
  const [rules, setRules] = useState<VariantRules | null>(null);

  useEffect(() => {
    if (resume) {
      // Opening the editor is how a resume becomes a variant, so the switch starts on.
      setIsVariant(true);
      setRules(resume.variant_rules);
    }
  }, [resume]);

  // Suggest the technologies your saved postings name. Any other name can still be typed.
  const skills = useMemo(() => {
    const names = (postings ?? []).flatMap((posting) => posting.technologies.map((t) => t.name));
    return [...new Set(names)].sort((a, b) => a.localeCompare(b));
  }, [postings]);

  function update(changes: Partial<VariantRules>) {
    setRules((current) => (current ? { ...current, ...changes } : current));
  }

  function handleSave() {
    if (!resume || !rules) return;
    save.mutate(
      { id: resume.id, isVariant, rules },
      {
        onSuccess: (saved) => {
          notifications.show({
            color: 'green',
            message: saved.is_variant
              ? `${saved.label} is a baseline`
              : `${saved.label} is no longer a baseline`,
          });
          onClose();
        },
      },
    );
  }

  return (
    <Modal
      opened={resume !== null}
      onClose={onClose}
      title={resume ? `Baseline rules: ${resume.label}` : 'Baseline rules'}
      size="lg"
    >
      {rules && resume && (
        <Stack gap="sm">
          <Text size="xs" c="dimmed">
            A baseline is the resume you start from for one kind of job. Insights → Baselines checks
            each baseline against every saved posting and uses these rules to say which jobs it
            suits. A field you leave empty doesn't limit anything.
          </Text>
          <Switch
            label="Use this resume as a baseline"
            checked={isVariant}
            onChange={(event) => setIsVariant(event.currentTarget.checked)}
          />
          <MultiSelect
            label="Role types"
            description="Jobs of other types are skipped. Plain software engineer titles always pass."
            data={ROLE_FAMILY_OPTIONS}
            value={rules.role_families}
            onChange={(value) => update({ role_families: value })}
            clearable
          />
          <MultiSelect
            label="Levels"
            description="Jobs at other levels are skipped. Jobs with no stated level pass."
            data={LEVEL_OPTIONS}
            value={rules.levels}
            onChange={(value) => update({ levels: value as PostingLevel[] })}
            clearable
          />
          <TagsInput
            label="Focus skills"
            description="The job must name at least one of these."
            data={skills}
            value={rules.focus_skills}
            onChange={(value) => update({ focus_skills: value })}
            maxTags={MAX_SKILLS}
            clearable
          />
          <TagsInput
            label="Deal-breakers"
            description="Jobs that require any of these are skipped."
            data={skills}
            value={rules.deal_breakers}
            onChange={(value) => update({ deal_breakers: value })}
            maxTags={MAX_SKILLS}
            clearable
          />
          <Group grow align="flex-start">
            <NumberInput
              label="Most years asked"
              description="Skip jobs asking for more."
              min={0}
              max={60}
              value={rules.max_years ?? ''}
              onChange={(value) => update({ max_years: typeof value === 'number' ? value : null })}
            />
            <NumberInput
              label="Minimum score"
              description="Skip jobs this resume scores below."
              min={0}
              max={100}
              value={rules.min_score}
              onChange={(value) => update({ min_score: typeof value === 'number' ? value : 0 })}
            />
          </Group>
          <Switch
            label="Allow jobs that require a security clearance"
            checked={rules.allow_clearance}
            onChange={(event) => update({ allow_clearance: event.currentTarget.checked })}
          />
          <Group justify="flex-end" mt="xs">
            <Button variant="default" onClick={onClose}>
              Cancel
            </Button>
            <Button loading={save.isPending} onClick={handleSave}>
              Save
            </Button>
          </Group>
        </Stack>
      )}
    </Modal>
  );
}
